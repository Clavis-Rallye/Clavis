"""Deletion of the guest accounts of a run.

A guest joins one run with its code, which creates a ``role=guest`` User and a
TeamMember. The guest accounts are deleted after the run ends. The hard delete
keeps the history of the run: every foreign key to ``users.id`` that holds it
(team_members.user_id, scores, feed and chat authors, ...) is ``ON DELETE SET
NULL``, so team rosters, submissions and scores stay. The departure rows and
the reset tokens cascade.

``team_members`` keeps its own copy of the display name. It goes with the
account: ``blank_guest_member_names`` replaces it and keeps the row, so the
team size and the guest count of the run stay.

The locks follow the order of every path of a member row
(``services/team_dissolution.py``), see ``delete_run_guests``.
"""

from collections.abc import Collection, Sequence
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run_departure import RunDeparture
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.repositories.team import id_chunks, lock_team_ids
from app.services.gdpr import ANONYMIZED_MEMBER_NAME
from app.services.ws_manager import manager


async def delete_run_guests(
    db: AsyncSession,
    run_id: UUID,
    organization_id: UUID,
    due_run_ids: Collection[UUID],
) -> int:
    """Delete the guest accounts of a run and return how many were deleted.

    A guest of the run is a ``role=guest`` user of the organization with a
    member row in a team of ``run_id``, or with a ``run_departures`` row of it
    (leaving deletes the member row). The departure rows go with the account
    (``ON DELETE CASCADE``). Does not commit. The open sockets of the deleted
    accounts close after the caller's commit.

    Another run that is not due keeps the account until its own sweep takes
    it. It keeps the account through a member row in a live team of it (a
    team marked as deleted is no place in a run), or through a departure from
    it (the departure row refuses a new join from the same browser, and it
    would go with the account). ``due_run_ids`` are the runs the sweep treats
    as due in this pass (ended past the period, or abandoned). The sweep
    decides that, here it is only read. A due run keeps no account, or runs
    that are all due would keep each other's guests for ever.

    The DELETE repeats the organization and the role of the select. A row that
    is no guest of this organization any more is not deleted and not counted.

    The locks come in the order of ``team_dissolution``: the account rows,
    then the team rows of their member rows, then the member rows and the
    departures read again. A guest who left has no team to hold. An account
    row is deleted only after these locks, so its ``ON DELETE SET NULL``
    reaches only member rows whose teams this transaction holds, and no
    removal or dissolution can hold such a row at that moment.

    No lock is waited for (``SKIP LOCKED``). The sweep still holds the rows of
    the runs before in the same transaction, so a wait could close a cycle
    with a request. An account or team that another transaction holds belongs
    to a guest in the middle of an act, and the account stays for the next
    sweep. The same holds for the full lock on the account row right before
    the delete: an account that a row in flight points to stays.
    """
    member_user_ids = (
        select(TeamMember.user_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(Team.run_id == run_id)
        .where(TeamMember.user_id.is_not(None))
    )
    departed_user_ids = select(RunDeparture.user_id).where(RunDeparture.run_id == run_id)
    member_elsewhere_user_ids = (
        select(TeamMember.user_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(Team.run_id != run_id)
        .where(Team.run_id.not_in(due_run_ids))
        .where(Team.is_deleted.is_(False))
        .where(TeamMember.user_id.is_not(None))
    )
    departed_elsewhere_user_ids = (
        select(RunDeparture.user_id).where(RunDeparture.run_id != run_id).where(RunDeparture.run_id.not_in(due_run_ids))
    )

    # Step 1, the accounts: members of the run and guests who left it, under
    # FOR NO KEY UPDATE as ``lock_account_row`` takes it. A join, a leave or an
    # erasure of these accounts waits from here on, and a removal can still
    # write a departure record. The filters on runs that are not due only spare
    # the lock of an account that stays anyway. Step 3 decides under the locks.
    guest_ids_stmt = (
        select(User.id)
        .where(User.organization_id == organization_id)
        .where(User.role == UserRole.GUEST)
        .where(or_(User.id.in_(member_user_ids), User.id.in_(departed_user_ids)))
        .where(User.id.not_in(member_elsewhere_user_ids))
        .where(User.id.not_in(departed_elsewhere_user_ids))
        .order_by(User.id)
        .with_for_update(key_share=True, skip_locked=True, of=User)
    )
    guest_ids = list((await db.execute(guest_ids_stmt)).scalars())
    if not guest_ids:
        return 0

    # A guest who left has no member row. Steps 2 and 3 pass it through to the
    # delete on its departure record.

    # Step 2, the teams their member rows are in, in this run and in others.
    member_team_ids = select(TeamMember.team_id).where(TeamMember.user_id.in_(guest_ids))
    held_team_ids = set(await lock_team_ids(db, Team.id.in_(member_team_ids), skip_locked=True))

    # Step 3, the member rows as they are now. A row of a held team cannot move
    # or go, and a held account gets no new row. An account stays when one of
    # its member rows is in a team not held, or in a live team of another run
    # that is not due. A team marked as deleted keeps no account but is held
    # too, because the delete writes its rows. The runs are compared in the
    # database: the ids that come back are not of the type of ``run_id``.
    member_rows = (
        await db.execute(
            select(
                TeamMember.user_id,
                TeamMember.team_id,
                (Team.run_id == run_id).label("in_run"),
                and_(
                    Team.run_id != run_id,
                    Team.run_id.not_in(due_run_ids),
                    Team.is_deleted.is_(False),
                ).label("in_run_not_due"),
            )
            .join(Team, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id.in_(guest_ids))
        )
    ).all()
    in_run = {row.user_id for row in member_rows if row.in_run}
    in_run_not_due = {row.user_id for row in member_rows if row.in_run_not_due}
    in_team_not_held = {row.user_id for row in member_rows if row.team_id not in held_team_ids}
    # The departures are read after the member rows. A removal writes both in
    # one commit, so a member row that went since step 1 left a departure that
    # is visible here, in this run and in a run that is not due alike. An
    # account with neither is not of this run.
    departure_rows = (
        await db.execute(
            select(
                RunDeparture.user_id,
                (RunDeparture.run_id == run_id).label("in_run"),
                and_(RunDeparture.run_id != run_id, RunDeparture.run_id.not_in(due_run_ids)).label("in_run_not_due"),
            ).where(RunDeparture.user_id.in_(guest_ids))
        )
    ).all()
    departed = {row.user_id for row in departure_rows if row.in_run}
    departed_not_due = {row.user_id for row in departure_rows if row.in_run_not_due}
    candidates = sorted(((in_run | departed) & set(guest_ids)) - in_team_not_held - in_run_not_due - departed_not_due)
    if not candidates:
        return 0

    # The delete needs the full account row lock. A row written for the guest
    # right now (an answer, a chat message) holds a key-share lock on it and
    # may wait for a team held here. So this lock is not waited for either,
    # and an account that does not give it stays for the next sweep.
    deletable = list(
        (
            await db.execute(
                select(User.id)
                .where(User.id.in_(candidates))
                .order_by(User.id)
                .with_for_update(skip_locked=True, of=User)
            )
        ).scalars()
    )
    if not deletable:
        return 0

    # What the select saw is not trusted for the row the DELETE reaches. The ids
    # come from the DELETE, so only the sockets of deleted accounts close.
    deleted_ids = list(
        (
            await db.execute(
                delete(User)
                .where(User.id.in_(deletable))
                .where(User.organization_id == organization_id)
                .where(User.role == UserRole.GUEST)
                .returning(User.id)
            )
        ).scalars()
    )
    # The sockets close after the caller's commit.
    if deleted_ids:
        manager.close_accounts_after_commit(db, deleted_ids)
    return len(deleted_ids)


async def blank_guest_member_names(db: AsyncSession, run_ids: Sequence[UUID]) -> int:
    """Replace the display name of every guest member row of the runs and return the rows changed.

    A guest member row has ``is_guest`` set or no account (``user_id`` NULL
    after ``ON DELETE SET NULL``). The second branch also reaches guests whose
    account was deleted without a replacement of their name. Rows of
    registered members are not touched. A row that already carries the label
    is skipped, so a second call changes nothing. Does not commit.

    The team rows are locked before their member rows, as on every path of a
    member row (``services/team_dissolution``). The caller still holds the
    rows of ``delete_run_guests``, so no lock is waited for: a team that
    another transaction holds keeps its names until the next sweep, which finds
    them by the same condition.
    """
    if not run_ids:
        return 0
    guest_rows = (
        or_(TeamMember.is_guest.is_(True), TeamMember.user_id.is_(None)),
        TeamMember.display_name != ANONYMIZED_MEMBER_NAME,
    )
    held_team_ids = await lock_team_ids(
        db,
        Team.run_id.in_(run_ids),
        Team.id.in_(select(TeamMember.team_id).where(*guest_rows)),
        skip_locked=True,
    )
    changed = 0
    for team_ids in id_chunks(held_team_ids):
        result = await db.execute(
            update(TeamMember)
            .where(TeamMember.team_id.in_(team_ids), *guest_rows)
            .values(display_name=ANONYMIZED_MEMBER_NAME)
            .execution_options(synchronize_session=False)
        )
        changed += result.rowcount or 0  # type: ignore[attr-defined]
    return changed
