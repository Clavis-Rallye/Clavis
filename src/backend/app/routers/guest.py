"""
Guest join router.

Provides the endpoint for anonymous guests to join a run using a join code.
No authentication is required for this endpoint.
"""

import random
import string
from datetime import UTC, datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from jwt import PyJWTError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import (
    create_access_token,
    decode_token,
    is_session_that_may_act,
    set_auth_cookie,
    token_session_id,
)
from app.client_address import client_address
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.run import RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.repositories.organization import OrganizationRepository
from app.repositories.run import RunRepository
from app.repositories.run_departure import RunDepartureRepository
from app.repositories.team import TeamRepository
from app.repositories.team_member import TeamMemberRepository
from app.repositories.user import UserRepository
from app.routers.schemas import (
    ErrorResponse,
    GuestJoinByCodeRequest,
    GuestJoinCreateRequest,
    GuestJoinRequest,
    GuestJoinResponse,
)
from app.routers.teams import (
    ensure_team_name_free,
    is_team_name_conflict,
    run_not_active_error_code,
    team_name_taken_error,
)
from app.services import team_code_rate_limit
from app.services.audit import audit_user_created
from app.services.notify import notify_join_blocked, notify_team_members_changed
from app.services.team_dissolution import MemberTeamsBusyError, lock_account, lock_member_teams, team_busy_error

log = structlog.get_logger(__name__)

router = APIRouter()


def _generate_guest_display_name() -> str:
    """Generate a default guest display name with a random 4-digit suffix."""
    suffix = "".join(random.choices(string.digits, k=4))
    return f"Guest{suffix}"


def _generate_team_color() -> str:
    """Generate a random hex color for the team."""
    # Channel values from 80 up avoid colors that are too dark.
    r = random.randint(80, 220)
    g = random.randint(80, 220)
    b = random.randint(80, 220)
    return f"#{r:02X}{g:02X}{b:02X}"


@router.post(
    "/{code}",
    response_model=GuestJoinResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Successfully joined run as guest"},
        403: {
            "description": "Guest left this run and may not re-join, or the organization is archived (error_code ORG_ARCHIVED)",
            "model": ErrorResponse,
        },
        404: {
            "description": "Run not found",
            "model": ErrorResponse,
        },
        409: {
            "description": (
                "A team with this name already exists in this run (error_code TEAM_NAME_TAKEN), or the "
                "team of the session is being changed by other requests (error_code TEAM_BUSY)"
            ),
            "model": ErrorResponse,
        },
        422: {
            "description": "Run is not active or team capacity reached",
            "model": ErrorResponse,
        },
    },
)
async def guest_join(
    code: str,
    request: Request,
    response: Response,
    body: GuestJoinRequest,
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
) -> GuestJoinResponse:
    """Join a run as an anonymous guest, in a new team or in an existing one.

    The path holds the join code of the run. The caller needs no account. The body
    states the `mode`:

    - `create` makes a new team. The `team_name` is optional. Without it the team
      stays unnamed until it is named. The response carries the share code
      of the new team.
    - `join` joins the team with the given `team_code`.

    A caller with a valid session cookie of this run's organization keeps that
    account and gets no second guest and no new cookie. The same call again
    returns the team the account is already in and adds no member. Otherwise the
    route creates a guest account, sets the session cookie and emits the audit
    event `user_created`.

    A new member sends a member-change event to the coordination of the run.
    A join that finds the run not started or paused is refused and broadcasts
    `join_blocked` to the coordination.

    The route refuses with these answers:

    - 404 when the join code is unknown, or when the team code of mode `join` is
      unknown or the team no longer exists.
    - 403 with `ORG_ARCHIVED` when the organization is archived.
    - 403 with `RUN_DEPARTED` when this guest left the run before. A guest who
      left cannot join again.
    - 422 with an error code that names the state when the run is not active.
    - 422 with `TEAM_LIMIT_REACHED` when mode `create` finds the run at its team
      limit.
    - 422 with `TEAM_FULL` when mode `join` finds the team at its member limit.
    - 422 with `ALREADY_IN_OTHER_TEAM` when the account is in another team of the
      run.
    - 409 with `TEAM_NAME_TAKEN` when the run has a team of this name.
    - 409 with `TEAM_BUSY` when other requests are changing the team of the session.
    - 429 with a `Retry-After` of 60 seconds after too many failed team codes from
      one client for this run. The client then gets 429 even for a correct code.
    """
    client_ip = client_address(request)

    # The lookup is unscoped, because a guest does not know the organization.
    # Archived organizations are included, so that their runs get the explicit
    # ORG_ARCHIVED 403 below and not a 404 that looks like an unknown code.
    # Every other caller of the unscoped lookup gets archived runs as not found.
    run = await RunRepository.get_by_join_code_unscoped(db, code, include_archived_orgs=True)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found. Please check the join code.",
        )

    # A run of an archived organization issues no new guest session.
    if await OrganizationRepository.is_archived(db, run.organization_id):
        raise CodedHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This organization is archived.",
            error_code="ORG_ARCHIVED",
        )

    if run.state != RunState.ACTIVE:
        state_messages = {
            RunState.CONFIGURED: "Run has not started yet.",
            RunState.SCHEDULED: "Run has not started yet.",
            RunState.PAUSED: "Run is currently paused.",
            RunState.ENDED: "Run has already ended.",
        }
        message = state_messages.get(run.state, "Run is not accepting new participants.")
        error_code = run_not_active_error_code(run.state)
        # Tell the coordination live about the refused attempt before the
        # refusal answers. This does nothing for a reason that nobody watches.
        await notify_join_blocked(run.id, error_code)
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=message,
            error_code=error_code,
        )

    org_id = run.organization_id
    run_repo = RunRepository(db, organization_id=org_id)
    team_repo = TeamRepository(db, org_id)
    team_member_repo = TeamMemberRepository(db, org_id)
    max_members_per_team = run.settings.get("max_members_per_team", 10)

    # The run row is the first row lock of every path that takes it (lock order
    # in ``services/team_dissolution``: run, account, team, member, departure),
    # so it is taken here, before the account of a caller with a session and
    # before the team of a replay. Only a creation under a team limit needs it,
    # for the count below; a join by code and a creation without a limit take
    # no run row and do not queue on it. ``create_team`` takes the same two
    # rows in the same order, so the two cannot wait on each other in a ring.
    if isinstance(body, GuestJoinCreateRequest) and run.max_teams is not None:
        await run_repo.lock_row(run.id)

    # Resolve an authenticated user before the target team is picked. A reload
    # of the team-name page sends mode='create' again, and a user who is a
    # member of a team in this run then gets that team back and no second one.
    existing_user: User | None = None
    # The session a reused account was recognized by: its id and the session
    # version its token was accepted with, for the token of the answer.
    presented_session_id: str | None = None
    presented_session_version = 0
    if access_token:
        try:
            payload = decode_token(access_token)
            user_id_raw = payload.get("sub")
            if user_id_raw:
                from uuid import UUID as _UUID

                user = await UserRepository.get_by_id_unscoped(db, _UUID(str(user_id_raw)))
                # Only a full, current session is an identity to reuse. A stale
                # or restricted (2fa_setup) token counts as absent.
                if (
                    user is not None
                    and user.is_active
                    and user.organization_id == org_id
                    and is_session_that_may_act(payload, access_token, user)
                ):
                    # The account row is locked and read again before anything
                    # is decided for it (lock order in
                    # ``services/team_dissolution``). An account that
                    # was erased or deactivated since the read above is no
                    # identity to reuse, like a stale token; from here on only
                    # the account as read under the lock is used.
                    account = await lock_account(db, user.id)
                    if (
                        account is not None
                        and account.organization_id == org_id
                        and is_session_that_may_act(payload, access_token, account)
                    ):
                        existing_user = account
                        presented_session_id = token_session_id(payload, access_token)
                        presented_session_version = payload.get("sv", 0)
        except (PyJWTError, ValueError, KeyError):
            existing_user = None

    # A guest who left their team may not re-enter this run under the same
    # identity, in both modes: joining a team by code and creating a new one.
    # A fresh anonymous visitor cannot be told from a new participant, so the
    # block holds only for the session and identity of the departed guest.
    async def ensure_not_departed() -> None:
        if existing_user is None or existing_user.role != UserRole.GUEST:
            return
        departure_repo = RunDepartureRepository(db, organization_id=org_id)
        if await departure_repo.has_departed(user_id=existing_user.id, run_id=run.id):
            raise CodedHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Du hast diesen Run verlassen und kannst ihm nicht erneut beitreten.",
                # The code lets the join screen say why, and not invite a retry
                # that can never work.
                error_code="RUN_DEPARTED",
            )

    await ensure_not_departed()

    target_team: Team | None = None
    # Set on an idempotent reuse, so that no second TeamMember row is added.
    skip_member_create = False

    if isinstance(body, GuestJoinCreateRequest):
        # Idempotency backstop: the same user in the same run with mode='create'
        # gets the existing team and keeps the existing TeamMember.
        if existing_user is not None:
            replaying_user_id = existing_user.id

            async def read_team_ids() -> set[UUID]:
                team_id = await team_member_repo.team_id_by_user_and_run(replaying_user_id, run.id)
                return set() if team_id is None else {team_id}

            # The team of the membership is locked before it is reused, and the
            # membership is read again under that lock: a team that
            # was removed, or a member who was removed or moved, since the
            # first read is seen here.
            # The membership kept moving between teams in every round of
            # the lock: a conflict the client may send again, nothing written.
            try:
                locked_teams = await lock_member_teams(db, read_team_ids, organization_id=org_id)
            except MemberTeamsBusyError as exc:
                raise team_busy_error() from exc
            current_team_id = await team_member_repo.team_id_by_user_and_run(replaying_user_id, run.id)
            current_state = locked_teams.get(current_team_id) if current_team_id is not None else None
            if current_state is not None and not current_state.is_deleted:
                target_team = await team_repo.get_by_id(current_state.id)
            # Reuse only a live team the account is in now.
            skip_member_create = target_team is not None
            if target_team is None:
                # No membership under the lock: the request goes on as a first
                # creation, after the departure is asked again. A removal
                # writes the departure in the commit that removes the member
                # row, so it is visible now if the row went that way.
                await ensure_not_departed()

        if target_team is None:
            # The run's row is locked before count_by_run. With room for one
            # more team, two concurrent creations must not both pass the
            # count and both insert. The lock serializes them on the run row and
            # holds until this request's commit, so the loser re-counts after
            # the winner. The row is held already: it was taken as the first
            # row lock of this request, before the account and before the team
            # of a replay, and this statement only states the lock next to the
            # count that depends on it. The acts that hold a run row and go on
            # to team rows (the removal and the move of a member, the removal
            # of a team, leaving) take the run row first as well, so this path
            # and they wait for each other at the run row and nowhere else.
            if run.max_teams is not None:
                await run_repo.lock_row(run.id)
                team_count = await team_repo.count_by_run(run.id)
                if team_count >= run.max_teams:
                    raise CodedHTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="Maximum number of teams has been reached for this run.",
                        error_code="TEAM_LIMIT_REACHED",
                    )
            # Trim the name, then check it against the run before the insert. A
            # name of only whitespace carries no more information than none, so
            # the team stays name-pending and gets no empty name that the index
            # would then guard.
            team_name = body.team_name.strip() if body.team_name is not None else None
            team_name = team_name or None
            if team_name is not None:
                await ensure_team_name_free(db, organization_id=org_id, run_id=run.id, name=team_name)
            share_code = await team_repo.generate_unique_share_code(run.id)
            target_team = Team(
                # None for a name-pending team.
                name=team_name,
                color=body.team_color or _generate_team_color(),
                share_code=share_code,
                run_id=run.id,
                organization_id=org_id,
            )
            db.add(target_team)
            try:
                await db.flush()
            except IntegrityError as exc:
                await db.rollback()
                if is_team_name_conflict(exc):
                    raise team_name_taken_error() from exc
                raise
            log.info(
                "team.create",
                team_id=str(target_team.id),
                run_id=str(run.id),
                org_id=str(org_id),
                share_code_length=len(share_code),
                name_pending=team_name is None,
            )

    elif isinstance(body, GuestJoinByCodeRequest):
        # One admission per client and run: the question to the limiter, the
        # lookup of the code and the record of a failure or the reset form one
        # step, so that no other join of this client and run interleaves. A
        # blocked client gets 429 even for a correct code.
        async with team_code_rate_limit.admission(client_ip, run.id) as admission:
            if admission.blocked:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed team-code attempts. Please wait 1 minute.",
                    headers={"Retry-After": "60"},
                )
            target_team = await team_repo.get_by_share_code(run.id, body.team_code)
            if target_team is None:
                if not admission.record_failure():
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Too many failed team-code attempts. Please wait 1 minute.",
                        headers={"Retry-After": "60"},
                    )
                log.info(
                    "team.join_attempt",
                    outcome="code_unknown",
                    run_id=str(run.id),
                    org_id=str(org_id),
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Team-Code unbekannt.",
                )
            # A successful lookup resets the rate-limit bucket. This happens here,
            # inside the admission, and not after the team lock below: that
            # lock may wait for a dissolution or another join, and the
            # admission must not hold the client's lock across a database
            # wait. The lookup above is a plain read and waits for no row
            # lock, so the lock in memory is never held while this request
            # waits for a row (lock inventory in ``services/team_dissolution``).
            # A team that turns out dissolved under the row lock below
            # therefore records no failure: its code did resolve.
            admission.reset()
        # The team's row is locked and read again before anything is decided
        # on it: a team that dissolved after
        # the code lookup is an unknown code, with the present answer, and no
        # guest or member row is written. That holds for a guest who is in the
        # team already and sends its code again, too: the replay takes the
        # same lock and reads the same things, it only skips the capacity
        # check and the insert. With one free place, two concurrent joins must
        # not both count below the limit and both insert: the lock holds until
        # this request's commit, so the second join waits for the winner and
        # then counts the winner's member. The only row lock taken before this
        # one is the row of the caller's own account, and no path that holds a
        # team lock waits for an account lock, so this cannot deadlock. The
        # admission above is closed by now.
        locked_team = await team_repo.lock_live_row(target_team.id)
        if locked_team is None:
            log.info(
                "team.join_attempt",
                outcome="team_dissolved",
                run_id=str(run.id),
                org_id=str(org_id),
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team-Code unbekannt.",
            )
        # A user who is a member of this team already gets a no-op success
        # (page reload, replay from sessionStorage). A user in a different team
        # of the same run gets a 422 with a clear message, and not a 500 from
        # the unique constraint. The membership is read under the locks,
        # and for the first time: nothing about it was decided before them.
        if existing_user is not None:
            current_team_id = await team_member_repo.team_id_by_user_and_run(existing_user.id, run.id)
            if current_team_id == target_team.id:
                skip_member_create = True
            elif current_team_id is not None:
                raise CodedHTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Du bist bereits in einem anderen Team in diesem Run.",
                    error_code="ALREADY_IN_OTHER_TEAM",
                )
            else:
                # No membership: the departure is asked again after it. A
                # removal writes both in one commit, so a guest who was taken
                # out of a team since the first check is refused here.
                await ensure_not_departed()
        # An idempotent replay skips the capacity check, because the user counts already.
        if not skip_member_create and locked_team.member_count >= max_members_per_team:
            raise CodedHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Dieses Team ist voll.",
                error_code="TEAM_FULL",
            )
    else:
        # The Pydantic discriminator makes this unreachable.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid join mode.",
        )

    # A request with a valid auth cookie for a user of this run's organization
    # reuses that user as the team member and gets no fresh guest token.
    if existing_user is not None:
        display_name = body.display_name or existing_user.display_name
        guest_user = existing_user
        # A reused user gets no new cookie and no second "user_created" audit.
        is_pre_existing_user = True
    else:
        display_name = body.display_name or _generate_guest_display_name()
        guest_user = User(
            email=None,
            display_name=display_name,
            password_hash="",
            role=UserRole.GUEST,
            organization_id=org_id,
            is_active=True,
            totp_enabled=False,
        )
        db.add(guest_user)
        await db.flush()
        is_pre_existing_user = False

    # An idempotent re-create adds no member.
    if not skip_member_create:
        team_member = TeamMember(
            team_id=target_team.id,
            user_id=guest_user.id,
            display_name=display_name,
            is_guest=guest_user.role == UserRole.GUEST,
            organization_id=org_id,
            joined_at=datetime.now(UTC),
        )
        db.add(team_member)

    await db.commit()
    await db.refresh(guest_user)
    await db.refresh(target_team)
    # The run page of the coordination names the new member at once.
    if not skip_member_create:
        await notify_team_members_changed(run.id, target_team.id)

    # A new guest gets a new session: a token of its own, and the cookie
    # below. A reused account starts no second session here: the token of the
    # answer continues the session that asked, with that session's id, so the
    # sign-out of that session ends this token too. It
    # carries the session version the presented token was accepted with, not
    # the one the refresh above read: a password change that committed since
    # then ended the session, and its token must not come back current.
    # The app reads no token from this answer; the field stays for a client
    # that authenticates with it.
    if is_pre_existing_user:
        token = create_access_token(
            user_id=guest_user.id,
            role=guest_user.role.value,
            organization_id=org_id,
            session_version=presented_session_version,
            session_id=presented_session_id,
        )
    else:
        token = create_access_token(
            user_id=guest_user.id,
            role=guest_user.role.value,
            organization_id=org_id,
            session_version=guest_user.session_version,
        )
    if not is_pre_existing_user:
        set_auth_cookie(response, token)
        audit_user_created(
            actor_user=guest_user,
            actor_ip=client_ip,
            new_user=guest_user,
            organization_id=org_id,
        )

    log.info(
        "team.joined",
        team_id=str(target_team.id),
        run_id=str(run.id),
        org_id=str(org_id),
        user_id=str(guest_user.id),
        outcome="success",
    )

    return GuestJoinResponse(
        token=token,
        user_id=guest_user.id,
        display_name=guest_user.display_name,
        team_id=target_team.id,
        team_name=target_team.name,
        team_share_code=target_team.share_code,
        run_id=run.id,
    )
