"""GDPR erasure (Article 17) and retention of accounts, under DSGVO and BDSG.

Holds the deletion classes (Löschklassen) with their default periods, the
anonymization of accounts, which removes the personal data and keeps the row
for referential integrity, and the grace window of admin-deleted accounts.
Every anonymization is audited.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.run import Run, RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.repositories.run_departure import RunDepartureRepository
from app.repositories.user import UserRepository
from app.services.audit import audit_data_deletion_requested, record_audit_event, target_from_user
from app.services.team_dissolution import (
    DissolutionCause,
    MemberTeamsBusyError,
    dissolve_team_if_empty,
    lock_member_runs_and_accounts,
    lock_member_teams,
    team_busy_error,
)

# What a team member row shows once its name is removed. ``team_members`` keeps
# its own copy of the display name, so every path that removes a person's name
# must write this label there too. The row itself stays: the team size and the
# guest count of a run are statistics.
ANONYMIZED_MEMBER_NAME = "Deleted member"

# The marker of an anonymized account, see ``anonymize_user``.
ANONYMIZED_EMAIL_PREFIX = "deleted-user-"
ANONYMIZED_EMAIL_SUFFIX = "@anonymized.local"


class DeletionClass(StrEnum):
    """Deletion classes (Löschklassen): how long DSGVO and BDSG let each kind of personal data stay.

    ``description`` names the data of each class and the start its period
    counts from. ``retention_days`` holds the default period.
    """

    LK_A = "LK_A"
    LK_B = "LK_B"
    LK_C = "LK_C"
    LK_D = "LK_D"
    LK_E = "LK_E"
    LK_F = "LK_F"
    LK_G = "LK_G"
    LK_H = "LK_H"

    @property
    def retention_days(self) -> int:
        """The default period of this class in days. Raises ``ValueError`` for a class without one."""
        retention_map = {
            # A guest's account is their only way back into a finished run: the
            # result, the scoreboard, the feedback prompt. 90 days matches LK_B
            # (the media those answers consist of), so nothing goes half-missing.
            DeletionClass.LK_A: 90,
            DeletionClass.LK_B: 90,
            DeletionClass.LK_C: 365,
            DeletionClass.LK_D: 0,
            DeletionClass.LK_E: 90,
            DeletionClass.LK_F: 365,
            DeletionClass.LK_G: 30,
            # The free text of a run goes with the last names of the run (LK_C).
            # The rows stay, so every count and every score stays.
            DeletionClass.LK_H: 365,
        }

        retention = retention_map.get(self)
        if retention is None:
            raise ValueError(f"Unknown deletion class: {self}")

        return retention

    @property
    def description(self) -> str:
        """A human-readable description of what data belongs to this class."""
        descriptions = {
            DeletionClass.LK_A: "Guest sessions and guest display names (90 days after run ends)",
            DeletionClass.LK_B: "Chat messages and raw media (90 days after run ends)",
            DeletionClass.LK_C: "Run data, anonymized not deleted (1 year after run ends)",
            DeletionClass.LK_D: "Registered user accounts + linked PII (immediate on request, GDPR Art. 17)",
            DeletionClass.LK_E: "Server, access, and error logs (90 days after creation)",
            DeletionClass.LK_F: "Audit logs (1 year after creation)",
            DeletionClass.LK_G: "Admin-deleted accounts in grace window (restorable 30 days, then anonymized)",
            DeletionClass.LK_H: "Answer text, quiz choices, score comments and feedback comments (1 year after run ends)",
        }
        return descriptions.get(self, "Unknown deletion class")


# Audit entries of an erasure that wait for the commit of its transaction, kept
# on the session that runs it. The audit log is a file and not part of the
# transaction: an entry written before the commit would stay when the caller
# rolls back, and would name a dissolution that never happened.
_AUDIT_AFTER_COMMIT = "gdpr_audit_after_commit"


def _audit_after_commit(session: AsyncSession, emit: Callable[[], None]) -> None:
    """Write an audit entry once the transaction of ``session`` has committed, and never if it rolls back."""
    session.sync_session.info.setdefault(_AUDIT_AFTER_COMMIT, []).append(emit)


@event.listens_for(Session, "after_commit")
def _emit_waiting_audit_entries(session: Session) -> None:
    # A released savepoint reports a commit too; only the end of the whole transaction counts.
    if session.in_nested_transaction():
        return
    for emit in session.info.pop(_AUDIT_AFTER_COMMIT, ()):
        emit()


@event.listens_for(Session, "after_rollback")
def _drop_waiting_audit_entries(session: Session) -> None:
    # The lock rounds of an erasure roll back to a savepoint between them: that undoes no dissolution.
    if session.in_nested_transaction():
        return
    session.info.pop(_AUDIT_AFTER_COMMIT, None)


async def _remove_from_live_teams(session: AsyncSession, user_ids: list[UUID], actor: dict[str, Any]) -> None:
    """Take the accounts' member rows out of the teams of runs that have not ended.

    A row goes as in a removal: a guest gets the departure record, and a team
    without member rows dissolves.

    The caller holds the run and account locks (``team_dissolution``, steps 1
    and 2). The team rows come next, in one ascending round. Then the member
    rows are read again under these locks, and only the fresh rows are
    deleted. The round locks the teams of every member row of the accounts,
    ended runs included: the caller writes the neutral name into the rows that
    stay, and a member row is written only under the lock of its team.
    """
    live_rows = (
        select(TeamMember)
        .join(Team, Team.id == TeamMember.team_id)
        .join(Run, Run.id == Team.run_id)
        .where(TeamMember.user_id.in_(user_ids), Run.state != RunState.ENDED)
    )

    async def read_team_ids() -> set[UUID]:
        return set(
            (await session.execute(select(TeamMember.team_id).where(TeamMember.user_id.in_(user_ids)))).scalars()
        )

    locked = await lock_member_teams(session, read_team_ids, organization_id=None)
    if not locked:
        return

    # populate_existing: a member row this session saw before the locks must
    # show the team it belongs to now, not the one it had then.
    fresh = live_rows.order_by(TeamMember.team_id, TeamMember.id).execution_options(populate_existing=True)
    for member in list((await session.execute(fresh)).scalars().all()):
        team = await session.get(Team, member.team_id)
        if team is None or member.user_id is None or locked[team.id].is_deleted:
            continue
        await session.delete(member)
        if member.is_guest:
            await RunDepartureRepository(session, organization_id=team.organization_id).record(
                user_id=member.user_id, run_id=team.run_id
            )
        result = await dissolve_team_if_empty(session, team)
        if result is not None:
            _audit_team_dissolved_after_commit(
                session,
                actor=actor,
                target={
                    "entity": "team",
                    "id": str(team.id),
                    "name": team.name,
                    "run_id": str(team.run_id),
                    **result.audit_fields(DissolutionCause.LAST_MEMBER_ERASED),
                },
                organization_id=team.organization_id,
            )


def _audit_team_dissolved_after_commit(
    session: AsyncSession, *, actor: dict[str, Any], target: dict[str, Any], organization_id: UUID
) -> None:
    """Audit event ``team.dissolved`` of an erasure, written when the caller's transaction has committed."""

    def emit() -> None:
        # Looked up when it runs: the name ``record_audit_event`` of this module.
        record_audit_event(event="team.dissolved", actor=actor, target=target, organization_id=organization_id)

    _audit_after_commit(session, emit)


async def anonymize_user(
    session: AsyncSession,
    user_id: UUID,
    actor: dict[str, Any],
) -> bool:
    """Erase the personal data of one account (GDPR Art. 17) and keep its row.

    The row stays for referential integrity and the audit trail. Removed:

    - email → "deleted-user-{uuid}@anonymized.local"
    - display_name → "Deleted User"
    - password_hash → ""
    - totp_secret, totp_recovery_codes, webauthn_credentials → NULL
    - team_members.display_name of every team of the user → ``ANONYMIZED_MEMBER_NAME``

    Kept: id (referential integrity), role (history), organization_id
    (scoping), created_at (retention periods), deleted_at and is_deleted
    (soft delete, set here).

    ``actor`` is ``actor_from_user`` for an admin, a system actor dict for the
    sweep. Returns whether the user was found.

    This function serves the request that erases one account. It raises 409
    ``TEAM_BUSY`` (``CodedHTTPException``) when the member rows kept changing
    teams, or kept appearing in other runs, while their teams or runs were
    locked (``lock_member_teams`` or ``lock_member_runs_and_accounts`` gave
    up). Nothing is written, and the request may be sent again.
    """
    try:
        return await anonymize_users(session, [user_id], actor) == 1
    except MemberTeamsBusyError as exc:
        raise team_busy_error() from exc


async def anonymize_users(session: AsyncSession, user_ids: list[UUID], actor: dict[str, Any]) -> int:
    """Anonymize several accounts in the caller's transaction and return how many were found.

    One lock order for one account and for many (``team_dissolution``), the
    first three in ascending id order:

    1. the run rows of their member rows,
    2. the account rows (``lock_member_runs_and_accounts``),
    3. the team rows of their member rows,
    4. the member rows.

    The runs come first because an erasure can dissolve a team, and a
    dissolution rewrites the stored results of the run under that lock. A join
    still in flight waits at the account lock and then finds the account
    erased. A join that came first is finished, and its member row is found.

    The audit entry ``team.dissolved`` is written after the caller's commit
    and dropped when the caller rolls back.

    Raises ``MemberTeamsBusyError`` when the run or team locks give up. The
    sweep fails on it as on any other error. ``anonymize_user`` turns it into
    a 409.
    """
    # Soft-deleted accounts are included: the grace sweep anonymizes exactly those.
    users = await lock_member_runs_and_accounts(session, user_ids)
    if not users:
        return 0

    found_ids = [user.id for user in users]
    organization_ids = {user.id: user.organization_id for user in users}
    # Capture the denormalized targets before anonymization rewrites the PII
    targets = {user.id: target_from_user(user) for user in users}

    for user in users:
        user.email = f"{ANONYMIZED_EMAIL_PREFIX}{user.id}{ANONYMIZED_EMAIL_SUFFIX}"
        user.display_name = "Deleted User"
        user.password_hash = ""  # Empty password hash (account cannot be logged into)
        user.totp_secret = None
        user.totp_recovery_codes = None
        user.webauthn_credentials = None
        # Someone other than its owner removed the second factor, so no token
        # minted before this may read as current. The database raises the
        # version, because the row read above may be older than another
        # revocation of the account. This transaction holds the account row
        # already (``lock_member_runs_and_accounts``), so the UPDATE takes no
        # new lock.
        await UserRepository.raise_session_version(session, user)

        if not user.is_deleted:
            user.is_deleted = True
            user.deleted_at = datetime.now(UTC)

        user.is_active = False

        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_password_hash = None

    # In a run that has not ended, the person leaves the team as in a removal,
    # and an empty team dissolves. In an ended run the member row stays,
    # because the team is part of the result.
    await _remove_from_live_teams(session, found_ids, actor)

    # The team rosters hold their own copy of the name. It goes with the
    # account. The rows that are left are in teams locked above.
    await session.execute(
        update(TeamMember)
        .where(TeamMember.user_id.in_(found_ids), TeamMember.display_name != ANONYMIZED_MEMBER_NAME)
        .values(display_name=ANONYMIZED_MEMBER_NAME)
    )

    await session.flush()

    for one_id in found_ids:
        audit_data_deletion_requested(
            actor=actor,
            target=targets[one_id],
            organization_id=organization_ids[one_id],
            reason="gdpr_anonymization",
        )

    return len(found_ids)


async def check_deletion_due(
    session: AsyncSession,
    deletion_class: DeletionClass,
    reference_date: datetime | None = None,
) -> list[UUID]:
    """The ids of the users whose retention period of ``deletion_class`` has passed.

    - Only soft-deleted users count (``is_deleted`` set, ``deleted_at`` not NULL).
      Active users are never included.
    - The period counts from ``deleted_at``.
    - Users that are already anonymized (email ends with ``@anonymized.local``)
      are excluded.
    - LK_D (on request) has a period of 0 days, so every soft-deleted user is due.

    ``reference_date`` defaults to now. Tests pass a fixed date.
    """
    if reference_date is None:
        reference_date = datetime.now(UTC)

    retention_days = deletion_class.retention_days
    cutoff_date = reference_date - timedelta(days=retention_days)

    stmt = select(User.id).where(
        User.is_deleted == True,  # noqa: E712
        User.deleted_at.isnot(None),
        User.deleted_at <= cutoff_date,
        ~User.email.endswith("@anonymized.local"),  # Not already anonymized
    )

    result = await session.execute(stmt)
    return list(result.scalars().all())


# Grace-delete (LK_G): admin-deleted accounts stay restorable for the grace
# window, then the retention sweep anonymizes them. An explicit GDPR Art. 17
# erasure request bypasses the grace window (LK_D, immediate), see the
# ``mode`` parameter of the user delete endpoint.

GRACE_DELETE_DAYS = DeletionClass.LK_G.retention_days

# Actor dict for audit events emitted by the scheduled retention sweep, which
# acts without a user context.
SYSTEM_RETENTION_ACTOR: dict[str, Any] = {"system": "retention_sweep", "ip": "internal"}


async def restore_user(session: AsyncSession, user_id: UUID) -> str:
    """Restore a soft-deleted user within the grace window.

    Returns one of:

    - ``"restored"``: the user is no longer deleted and stays deactivated,
    - ``"not_found"``: no user with this id,
    - ``"not_deleted"``: the user is not soft-deleted,
    - ``"already_anonymized"``: the grace window has passed. Anonymization
      cannot be undone, so no restore is possible.
    """
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        return "not_found"
    if user.email is not None and user.email.endswith("@anonymized.local"):
        return "already_anonymized"
    if not user.is_deleted:
        return "not_deleted"

    user.is_deleted = False
    user.deleted_at = None
    # The account stays deactivated. A delete keeps no record of the state
    # before it, so a restore that activated would reopen an account that an
    # admin had deactivated on purpose, for example a leaver's. "Aktivieren" in
    # the user row is the deliberate second step.

    await session.flush()
    return "restored"


async def sweep_expired_grace_deletions(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """Anonymize every soft-deleted user whose grace window has passed, and return how many.

    Called by the daily retention sweep. Does not commit.
    """
    due_user_ids = await check_deletion_due(
        session=session,
        deletion_class=DeletionClass.LK_G,
        reference_date=reference_date,
    )

    return await anonymize_users(session, due_user_ids, dict(SYSTEM_RETENTION_ACTOR))
