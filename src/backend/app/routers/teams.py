"""
Team routes of a run, under ``/api/orgs/{slug}/runs/{run_id}/teams/``.

The acts that can dissolve a team (leave, remove a member, move a member,
remove the team) follow the lock order and the retry of
``services/team_dissolution``.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import authenticate_again, get_org_from_slug
from app.auth.rbac import RoleLevel, require_role
from app.client_address import client_address
from app.config import settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.organization import Organization
from app.models.run import RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.repositories.run import RunRepository
from app.repositories.run_departure import RunDepartureRepository
from app.repositories.team import TeamRepository
from app.repositories.team_member import TeamMemberRepository
from app.routers.schemas import (
    ErrorResponse,
    JoinByCodeRequest,
    TeamConflictResponse,
    TeamCreateRequest,
    TeamLeaveRequest,
    TeamListResponse,
    TeamMemberMoveRequest,
    TeamMemberResponse,
    TeamRenameRequest,
    TeamResponse,
    TeamStageAdvanceRequest,
)
from app.services import team_code_rate_limit
from app.services.audit import actor_from_user, record_audit_event
from app.services.notify import dispatch_team_dissolved, notify_join_blocked, notify_team_members_changed
from app.services.team_dissolution import (
    DissolutionCause,
    DissolutionResult,
    LockRetryExhaustedError,
    dissolve_team,
    dissolve_team_if_empty,
    lock_account,
    require_dissolution_confirmed,
    retry_on_lock_failure,
    team_busy_error,
)
from app.services.ws_manager import manager as ws_manager

router = APIRouter()
log = structlog.get_logger(__name__)


def _can_see_share_code(current_user: User, is_team_member: bool) -> bool:
    """Whether the caller may see the team's share code.

    A member of the team may, in any role. Staff from coordinator up may too,
    so that they can give the code again to a team that lost it.
    """
    if is_team_member:
        return True
    return RoleLevel[current_user.role.name] >= RoleLevel.COORDINATOR


async def _ensure_guest_not_departed(
    db: AsyncSession,
    org_id: UUID,
    current_user: User,
    run_id: UUID,
) -> None:
    """Refuse with 403 a guest who left a team of this run before.

    A guest who left may not join the run again. Registered participants are
    not blocked.
    """
    if current_user.role != UserRole.GUEST:
        return
    departure_repo = RunDepartureRepository(db, organization_id=org_id)
    if await departure_repo.has_departed(user_id=current_user.id, run_id=run_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guests cannot re-join a run after leaving their team.",
        )


async def _lock_acting_account(db: AsyncSession, current_user: User) -> User:
    """Lock the caller's account row and return the account as it is now.

    Step 2 of the lock order in ``services/team_dissolution``, for every path
    here that writes for the caller's own account. The request was
    authenticated before this lock: an account that was erased or deactivated
    since then gets the answer the authentication gives, and nothing is
    written for it. Callers use the account returned here from then on.
    """
    account = await lock_account(db, current_user.id)
    if account is None:
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
            error_code="ACCOUNT_DISABLED",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return account


async def _with_lock_retry[T](
    request: Request,
    db: AsyncSession,
    org: Organization,
    current_user: User,
    min_role: RoleLevel,
    act: Callable[[], Awaitable[T]],
) -> T:
    """Run one act that can dissolve a team, and run it again when it loses a deadlock.

    The lock order in ``services/team_dissolution`` is the rule, and this is
    the backstop for a path that the order missed. ``act`` is the whole
    transaction of the request, from its first read to its commit, so a new
    attempt reads every check again. Before a new attempt takes a lock, the
    checks of the dependencies run again. The request is authenticated again
    with its token (``authenticate_again``), so an expired token, a sign-out,
    a password change, a lockout, a deactivated account or an archived
    organization gets the answer of the authentication. Then the role and the
    organization are read again. After the last attempt the caller gets a
    conflict, and nothing is written.
    """
    org_id, actor_id = org.id, current_user.id

    async def read_request_again() -> None:
        actor = await authenticate_again(request, db, actor_id)
        if actor.is_deleted:
            raise CodedHTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account is disabled",
                error_code="ACCOUNT_DISABLED",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if RoleLevel[actor.role.value.upper()] < min_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {min_role.name.lower()} role or higher",
            )
        fresh_org = await db.get(Organization, org_id, populate_existing=True)
        if fresh_org is None or fresh_org.archived_at is not None:
            raise CodedHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This organization is archived.",
                error_code="ORG_ARCHIVED",
            )

    try:
        return await retry_on_lock_failure(db, act, before_retry=read_request_again)
    except LockRetryExhaustedError as exc:
        raise team_busy_error() from exc


# Team names are unique per run. The partial unique index of migration 027 is
# the authority. The three write paths (create and rename here, the guest
# create in ``guest.py``) check first, so the caller gets a coded 409 and not a
# 500. When two requests pass the check at the same time, the violation of the
# index gets the same answer.
TEAM_NAME_INDEX = "ix_teams_run_id_lower_name"
TEAM_NAME_TAKEN = "TEAM_NAME_TAKEN"


def team_name_taken_error() -> CodedHTTPException:
    """The single 409 every team-name write path raises."""
    return CodedHTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A team with this name already exists in this run.",
        error_code=TEAM_NAME_TAKEN,
    )


_RUN_STATE_ERROR_CODES: dict[RunState, str] = {
    RunState.CONFIGURED: "RUN_NOT_STARTED",
    RunState.SCHEDULED: "RUN_NOT_STARTED",
    RunState.PAUSED: "RUN_PAUSED",
    RunState.ENDED: "RUN_ENDED",
}


def run_not_active_error_code(run_state: RunState) -> str:
    """The error code for a run that is not active.

    A refused join or team creation carries it beside its English detail, so
    the frontend can branch on the code and not on the text.
    """
    return _RUN_STATE_ERROR_CODES.get(run_state, "RUN_NOT_STARTED")


def is_team_name_conflict(exc: IntegrityError) -> bool:
    """True when this IntegrityError comes from the team-name index.

    Any other constraint keeps its own error. Mapping every IntegrityError of
    the request to TEAM_NAME_TAKEN would hide unrelated database problems
    behind a message about the name.
    """
    return TEAM_NAME_INDEX in str(getattr(exc, "orig", exc))


async def ensure_team_name_free(
    db: AsyncSession,
    *,
    organization_id: UUID,
    run_id: UUID,
    name: str,
    exclude_team_id: UUID | None = None,
) -> None:
    """Raise the coded 409 when this run already has a team with this name.

    Compares `lower(trim(name))`, exactly what the index compares, and skips
    soft-deleted teams, so a deleted team frees its name again. Pass the team's
    own id as `exclude_team_id` on a rename, so a team does not block itself.
    """
    stmt = (
        select(Team.id)
        .where(Team.organization_id == organization_id)
        .where(Team.run_id == run_id)
        .where(Team.deleted_at.is_(None))
        .where(func.lower(Team.name) == name.strip().lower())
        .limit(1)
    )
    if exclude_team_id is not None:
        stmt = stmt.where(Team.id != exclude_team_id)
    result = await db.execute(stmt)
    if result.scalars().first() is not None:
        raise team_name_taken_error()


@router.post(
    "/",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Team created successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        409: {
            "description": "A team with this name already exists in this run (error_code TEAM_NAME_TAKEN)",
            "model": ErrorResponse,
        },
        422: {
            "description": "Run not in active state, user already in another team, or max teams limit reached",
            "model": ErrorResponse,
        },
    },
)
async def create_team(
    run_id: UUID,
    request: Request,
    body: TeamCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> TeamResponse:
    """Create a team in an active run.

    Open to every signed-in role, guests included. Refused when the run is not
    active, when the caller is already in a team of the run, when the run's
    team limit is reached, or when the name is taken. The new team has no
    members.

    Audit event: team.created
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state != RunState.ACTIVE:
        error_code = run_not_active_error_code(run.state)
        # Staff see the refusal live. A no-op for a reason that nobody watches.
        await notify_join_blocked(run_id, error_code)
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot create team when run is in {run.state} state. Teams can only be created in active runs.",
            error_code=error_code,
        )

    # Under a team limit the run row is locked before the team count. With room
    # for one more team, two creations must not both pass the count and both
    # insert. The lock holds to the commit, so the second one counts again after
    # the first. It comes before the account, because the run row is the first
    # lock of every path that takes it (lock order in
    # ``services/team_dissolution``).
    if run.max_teams is not None:
        await run_repo.lock_row(run_id)

    # The account next. The two checks below are read under its lock, so no
    # join of the same account can come between them and the insert.
    current_user = await _lock_acting_account(db, current_user)

    await _ensure_guest_not_departed(db, org.id, current_user, run_id)

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    if await team_member_repo.team_id_by_user_and_run(user_id=current_user.id, run_id=run_id) is not None:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="User is already in a team for this run. Cannot create another team.",
            error_code="ALREADY_IN_OTHER_TEAM",
        )

    # Counted under the run row lock taken above.
    team_repo = TeamRepository(db, organization_id=org.id)
    if run.max_teams is not None:
        current_team_count = await team_repo.count_by_run(run_id)
        if current_team_count >= run.max_teams:
            raise CodedHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Maximum team limit reached ({run.max_teams} teams). Cannot create more teams for this run.",
                error_code="TEAM_LIMIT_REACHED",
            )

    await ensure_team_name_free(db, organization_id=org.id, run_id=run_id, name=body.name)

    share_code = await team_repo.generate_unique_share_code(run_id)

    try:
        team = await team_repo.create(
            organization_id=org.id,
            run_id=run_id,
            name=body.name,
            color=body.color,
            share_code=share_code,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if is_team_name_conflict(exc):
            raise team_name_taken_error() from exc
        raise
    await db.refresh(team)

    record_audit_event(
        event="team.created",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "team", "id": str(team.id), "name": body.name, "run_id": str(run_id)},
        organization_id=org.id,
    )

    return TeamResponse(
        id=team.id,
        organization_id=team.organization_id,
        run_id=team.run_id,
        name=team.name,
        color=team.color,
        member_count=0,
        coordinator_chat_open=team.coordinator_chat_opened_at is not None,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.get(
    "/",
    response_model=TeamListResponse,
    responses={
        200: {"description": "List of teams in run"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def list_teams(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> TeamListResponse:
    """List the teams of a run with their member counts.

    Open to every signed-in role. Staff from coordinator up also get the
    member names of each team. The share code goes to staff and to the
    caller's own team only.
    """
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    team_repo = TeamRepository(db, organization_id=org.id)
    teams = await team_repo.list_by_run(run_id)

    # One query for the counts of all teams, not one per team.
    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    team_ids = [team.id for team in teams]
    member_counts = await team_member_repo.count_by_teams(team_ids)

    # The caller's own team may see its share code.
    current_member = await team_member_repo.get_by_user_and_run(user_id=current_user.id, run_id=run_id)
    user_team_id = current_member.team_id if current_member else None
    is_coordinator_plus = RoleLevel[current_user.role.name] >= RoleLevel.COORDINATOR

    # The run page names the members of each team in its row. Only staff from
    # coordinator up get the names, the role that already reads them in the
    # member dialog (GET /teams/{id}). A participant or guest gets the counts
    # only, so no names of other teams leave the server.
    members_by_team = await team_member_repo.list_by_teams(team_ids) if is_coordinator_plus else {}

    team_responses = [
        TeamResponse(
            id=team.id,
            organization_id=team.organization_id,
            run_id=team.run_id,
            name=team.name,
            color=team.color,
            member_count=member_counts.get(team.id, 0),
            members=(
                [
                    TeamMemberResponse(
                        id=m.id,
                        display_name=m.display_name,
                        is_guest=m.is_guest,
                        joined_at=m.joined_at,
                    )
                    for m in members_by_team.get(team.id, [])
                ]
                if is_coordinator_plus
                else None
            ),
            share_code=(team.share_code if (is_coordinator_plus or team.id == user_team_id) else None),
            coordinator_chat_open=team.coordinator_chat_opened_at is not None,
            created_at=team.created_at,
            updated_at=team.updated_at,
        )
        for team in teams
    ]

    return TeamListResponse(
        items=team_responses,
        total=len(team_responses),
        skip=0,
        limit=len(team_responses),
    )


@router.get(
    "/{team_id}",
    response_model=TeamResponse,
    responses={
        200: {"description": "Team details with members"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Team not found or not in this run", "model": ErrorResponse},
    },
)
async def get_team(
    run_id: UUID,
    team_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> TeamResponse:
    """Get one team of the run with its member count.

    Open to every signed-in role. A member of the team and staff from
    coordinator up also get the member list and the share code.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)

    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    members = await team_member_repo.list_by_team(team_id)
    member_count = len(members)

    is_member_of_this_team = any(m.user_id == current_user.id for m in members)

    # Only a caller who may see the share code gets the member list. Others get
    # the count.
    members_payload: list[TeamMemberResponse] | None = None
    if _can_see_share_code(current_user, is_member_of_this_team):
        members_payload = [
            TeamMemberResponse(
                id=m.id,
                display_name=m.display_name,
                is_guest=m.is_guest,
                joined_at=m.joined_at,
            )
            for m in sorted(members, key=lambda mm: mm.joined_at)
        ]

    return TeamResponse(
        id=team.id,
        organization_id=team.organization_id,
        run_id=team.run_id,
        name=team.name,
        color=team.color,
        member_count=member_count,
        members=members_payload,
        share_code=(team.share_code if _can_see_share_code(current_user, is_member_of_this_team) else None),
        coordinator_chat_open=team.coordinator_chat_opened_at is not None,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.post(
    "/by-code",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Successfully joined team via code"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Team-Code unbekannt", "model": ErrorResponse},
        422: {"description": "Run not active, team full, or already in a team"},
        429: {"description": "Too many failed attempts"},
    },
)
async def join_team_by_code(
    run_id: UUID,
    body: JoinByCodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("participant")),
) -> None:
    """Join a team of the run by its share code, as a registered participant.

    The run must be active and the team must have a free place. A caller who
    is already in a team of the run is refused. Failed codes are rate limited
    per client and run, as in the guest join.

    Audit event: team.joined
    """
    client_ip = client_address(request)

    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.state != RunState.ACTIVE:
        error_code = run_not_active_error_code(run.state)
        # Staff see the refusal live.
        await notify_join_blocked(run_id, error_code)
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot join team when run is in {run.state} state.",
            error_code=error_code,
        )

    # One admission per client and run, as in the guest join. The limiter check,
    # the code lookup and the record of a failure or the reset are one step, so
    # no other join of this client and run comes between them. A blocked client
    # gets 429 for a correct code too, until the block ends. Only a code that
    # matches no team of the run uses up the budget.
    team_repo = TeamRepository(db, organization_id=org.id)
    async with team_code_rate_limit.admission(client_ip, run_id) as admission:
        if admission.blocked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed team-code attempts. Please wait 1 minute.",
                headers={"Retry-After": "60"},
            )
        team = await team_repo.get_by_share_code(run_id, body.team_code)
        if team is None:
            if not admission.record_failure():
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed team-code attempts. Please wait 1 minute.",
                    headers={"Retry-After": "60"},
                )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team-Code unbekannt.")
        # The code matched, so the budget is reset here, inside the admission,
        # and not after the checks below: a correct code that then ends in
        # TEAM_FULL or ALREADY_IN_OTHER_TEAM did match too. The lookup is a
        # plain read, so the in-memory lock is never held while the request
        # waits for a row. The admission closes before the first row lock
        # below (lock inventory in ``services/team_dissolution``).
        admission.reset()

    # The account row, then the team row, and the team is read again under its
    # lock, as in the guest join. With one free place, two joins must not both
    # count below the limit and both insert. A team that dissolved while this
    # request waited is an unknown code. The account comes first in the lock
    # order: an account erased or deactivated since the authentication writes
    # nothing, and the name written below is the one read under that lock.
    account = await _lock_acting_account(db, current_user)
    locked_team = await team_repo.lock_live_row(team.id)
    if locked_team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team-Code unbekannt.")
    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    max_members_per_team = run.settings.get("max_members_per_team", 10)
    if locked_team.member_count >= max_members_per_team:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Team ist voll.",
            error_code="TEAM_FULL",
        )

    # Read under the account lock and the team lock.
    if await team_member_repo.team_id_by_user_and_run(user_id=account.id, run_id=run_id) is not None:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Du bist bereits in einem Team für diesen Run.",
            error_code="ALREADY_IN_OTHER_TEAM",
        )

    member_display_name = account.display_name or account.email or "Anonymous"
    await team_member_repo.create(
        organization_id=org.id,
        team_id=team.id,
        user_id=account.id,
        display_name=member_display_name,
        is_guest=False,
    )
    await db.commit()
    await notify_team_members_changed(run_id, team.id)

    record_audit_event(
        event="team.joined",
        actor=actor_from_user(account, client_ip),
        target={"entity": "team", "id": str(team.id), "name": team.name, "run_id": str(run_id)},
        organization_id=org.id,
    )


@router.post(
    "/{team_id}/join",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Successfully joined team"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Team not found or not in this run", "model": ErrorResponse},
        422: {
            "description": "Run not in active state or user already in another team",
            "model": ErrorResponse,
        },
    },
)
async def join_team(
    run_id: UUID,
    team_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> None:
    """Join a team by its id. A test aid: the route answers 404 unless ALLOW_TEAM_JOIN_BY_ID is on.

    With the test aid on, the rules of the join by code hold: the run must be
    active, the team must have a free place, the caller must not be in another
    team of the run, and a guest who left the run may not come back.

    Audit event: team.joined
    """
    # This route joins by team id alone, without the team code. It is a test
    # aid and not a product path, so it answers 404 for every role unless the
    # test aid ALLOW_TEAM_JOIN_BY_ID is on.
    if not settings.auth.allow_team_join_by_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    client_ip = client_address(request)

    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state != RunState.ACTIVE:
        error_code = run_not_active_error_code(run.state)
        # Staff see the refusal live.
        await notify_join_blocked(run_id, error_code)
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot join team when run is in {run.state} state. Teams can only be joined in active runs.",
            error_code=error_code,
        )

    await _ensure_guest_not_departed(db, org.id, current_user, run_id)

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)

    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)

    # With the test aid on, the route keeps the limit of the joins by code. The
    # account row comes first, then the team row, held to the commit, so the
    # count is safe against concurrent joins. The team is read again under its
    # lock: a team that dissolved meanwhile does not exist. The membership is
    # read under both locks, then the departure once more. A removal writes
    # both in one commit, so a guest whose member row went since the check
    # above has the departure visible here.
    account = await _lock_acting_account(db, current_user)
    locked_team = await team_repo.lock_live_row(team.id)
    if locked_team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found in this run")
    if await team_member_repo.team_id_by_user_and_run(user_id=account.id, run_id=run_id) is not None:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="User is already in a team for this run",
            error_code="ALREADY_IN_OTHER_TEAM",
        )
    await _ensure_guest_not_departed(db, org.id, account, run_id)
    max_members_per_team = run.settings.get("max_members_per_team", 10)
    if locked_team.member_count >= max_members_per_team:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Team ist voll.",
            error_code="TEAM_FULL",
        )

    member_display_name = account.display_name or account.email or "Anonymous"
    is_guest_user = account.role.value == "guest"
    await team_member_repo.create(
        organization_id=org.id,
        team_id=team_id,
        user_id=account.id,
        display_name=member_display_name,
        is_guest=is_guest_user,
    )
    await db.commit()
    await notify_team_members_changed(run_id, team_id)

    record_audit_event(
        event="team.joined",
        actor=actor_from_user(account, client_ip),
        target={"entity": "team", "id": str(team_id), "name": team.name, "run_id": str(run_id)},
        organization_id=org.id,
    )


def _record_team_dissolved(
    *,
    actor: dict[str, Any],
    team: Team,
    run_id: UUID,
    org_id: UUID,
    cause: DissolutionCause,
    result: DissolutionResult,
) -> None:
    """Audit event ``team.dissolved``: the team, the run, the cause and the numbers it held."""
    record_audit_event(
        event="team.dissolved",
        actor=actor,
        target={
            "entity": "team",
            "id": str(team.id),
            "name": team.name,
            "run_id": str(run_id),
            **result.audit_fields(cause),
        },
        organization_id=org_id,
    )


async def _close_team_chat(team_id: UUID, user_id: UUID) -> None:
    """Close the account's open socket to the team's chat, and only that one.

    The WebSocket checks membership only when it connects, so a socket opened
    before stays open until it is closed here. 4003 tells the client not to
    reconnect, because a reconnect would be refused.
    """
    await ws_manager.close_shared(f"chat:{team_id}", f"user:{user_id}", code=4003, reason="Left the team")


async def _leave_team_once(
    db: AsyncSession, org: Organization, current_user: User, run_id: UUID, team_id: UUID, confirm_dissolution: bool
) -> tuple[Team, User, DissolutionResult | None]:
    """One attempt of ``leave_team``: every read, the delete and the commit, in one transaction."""
    run_repo = RunRepository(db, organization_id=org.id)
    # The run row is the first lock of every act that can dissolve a team,
    # before the account, the team and the member rows. A dissolution rewrites
    # the stored results of the run, and the publish builds and writes them
    # under the same lock. Held to the commit.
    await run_repo.lock_row(run_id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)

    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    # The account row, then the team row, before any member row is touched, as
    # every path that removes or counts members does. A removal that dissolved
    # the team while this request waited makes it a team that is gone. The
    # member row is read for the first time under both locks.
    account = await _lock_acting_account(db, current_user)
    if not await team_repo.lock_row(team_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    member_record = await team_member_repo.get_by_user_and_team(
        user_id=account.id,
        team_id=team_id,
    )

    if member_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of this team",
        )

    # The last member to leave dissolves the team: only when the client
    # confirmed that. Decided here, under the locks and before the first write.
    await require_dissolution_confirmed(db, team, confirmed=confirm_dissolution)

    await db.delete(member_record)

    # The member row is gone after this, so the departure is the only record
    # that keeps a guest from joining the run again (create_team, join_team,
    # guest_join).
    if account.role == UserRole.GUEST:
        departure_repo = RunDepartureRepository(db, organization_id=org.id)
        await departure_repo.record(user_id=account.id, run_id=run_id)

    # The last member to leave takes the team with them, in this transaction.
    dissolution = await dissolve_team_if_empty(db, team)

    await db.commit()
    return team, account, dissolution


@router.post(
    "/{team_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Successfully left team"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {
            "description": "Team not found or not in this run, or user not a member of the team",
            "model": ErrorResponse,
        },
        409: {
            "description": (
                "Leaving would dissolve the team and `confirm_dissolution` was not sent "
                "(TEAM_WOULD_DISSOLVE, with `member_count`, `answer_count`, `result_published` and `run_ended`; "
                "nothing changed), or the team is being changed by other requests (TEAM_BUSY)"
            ),
            "model": TeamConflictResponse,
        },
    },
)
async def leave_team(
    run_id: UUID,
    team_id: UUID,
    request: Request,
    body: TeamLeaveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> None:
    """Leave a team.

    When the caller is the last member, leaving dissolves the team, and the
    body must carry `confirm_dissolution: true`. Without it nothing changes,
    and the answer is 409 `TEAM_WOULD_DISSOLVE` with `member_count`,
    `answer_count`, `result_published` and `run_ended` as they are now. A
    member who leaves others behind needs no body. A guest who leaves may not
    join the run again.

    Audit events: team.left, and team.dissolved when the team went with it
    """
    client_ip = client_address(request)
    confirm_dissolution = body is not None and body.confirm_dissolution

    team, account, dissolution = await _with_lock_retry(
        request,
        db,
        org,
        current_user,
        RoleLevel.GUEST,
        lambda: _leave_team_once(db, org, current_user, run_id, team_id, confirm_dissolution),
    )
    await notify_team_members_changed(run_id, team_id)
    await _close_team_chat(team_id, account.id)

    actor = actor_from_user(account, client_ip)
    record_audit_event(
        event="team.left",
        actor=actor,
        target={"entity": "team", "id": str(team_id), "name": team.name, "run_id": str(run_id)},
        organization_id=org.id,
    )
    if dissolution is not None:
        _record_team_dissolved(
            actor=actor,
            team=team,
            run_id=run_id,
            org_id=org.id,
            cause=DissolutionCause.LAST_MEMBER_LEFT,
            result=dissolution,
        )
        await dispatch_team_dissolved({"run_id": run_id, "team_id": team_id})


async def _remove_team_member_once(
    db: AsyncSession, org: Organization, run_id: UUID, team_id: UUID, member_id: UUID, confirm_dissolution: bool
) -> tuple[Team, str, UUID | None, DissolutionResult | None]:
    """One attempt of ``remove_team_member``: every read, the delete and the commit, in one transaction."""
    run_repo = RunRepository(db, organization_id=org.id)
    # The run row first, before the team row: see ``_leave_team_once``.
    await run_repo.lock_row(run_id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)
    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    # Lock the team row before any member row is touched, so two removals at
    # once cannot both see one member left.
    if not await team_repo.lock_row(team_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    member_record = await team_member_repo.get_by_id(member_id)
    if member_record is None or member_record.team_id != team_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    # Removing the last member dissolves the team: only when the client
    # confirmed that. Decided here, under the lock and before the first write.
    await require_dissolution_confirmed(db, team, confirmed=confirm_dissolution)

    member_display_name = member_record.display_name
    member_user_id = member_record.user_id

    await db.delete(member_record)

    # A removed guest gets a departure, as in ``_leave_team_once``.
    if member_record.is_guest and member_user_id is not None:
        departure_repo = RunDepartureRepository(db, organization_id=org.id)
        await departure_repo.record(user_id=member_user_id, run_id=run_id)

    # Removing the last member dissolves the team in the same transaction.
    dissolution = await dissolve_team_if_empty(db, team)

    await db.commit()
    return team, member_display_name, member_user_id, dissolution


@router.delete(
    "/{team_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Member removed from team"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Requires coordinator role or higher", "model": ErrorResponse},
        404: {
            "description": "Run, team, or member not found (or member not in this team)",
            "model": ErrorResponse,
        },
        409: {
            "description": (
                "The removal would dissolve the team and `confirm_dissolution` was not sent "
                "(TEAM_WOULD_DISSOLVE, with `member_count`, `answer_count`, `result_published` and `run_ended`; "
                "nothing changed), or the team is being changed by other requests (TEAM_BUSY)"
            ),
            "model": TeamConflictResponse,
        },
    },
)
async def remove_team_member(
    run_id: UUID,
    team_id: UUID,
    member_id: UUID,
    request: Request,
    confirm_dissolution: bool = Query(
        False,
        description=(
            "True when the client confirmed that this removal dissolves the team. Without it the removal "
            "of the last member answers 409 TEAM_WOULD_DISSOLVE. Ignored when other members stay."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> None:
    """Remove a member from a team. Staff from coordinator up only.

    `member_id` is the id of the membership from `TeamResponse.members`, not an
    account id, so guests and registered members are removed the same way. A
    removed guest may not join the run again with the same account.

    Removing the last member dissolves the team and needs the query parameter
    `confirm_dissolution=true`. Without it nothing changes, and the answer is
    409 `TEAM_WOULD_DISSOLVE` with `member_count`, `answer_count`,
    `result_published` and `run_ended` as they are now. A removal that leaves
    other members ignores the parameter.

    Audit events: team.member_removed, and team.dissolved when the team went
    with it
    """
    client_ip = client_address(request)

    team, member_display_name, member_user_id, dissolution = await _with_lock_retry(
        request,
        db,
        org,
        current_user,
        RoleLevel.COORDINATOR,
        lambda: _remove_team_member_once(db, org, run_id, team_id, member_id, confirm_dissolution),
    )
    await notify_team_members_changed(run_id, team_id)
    if member_user_id is not None:
        await _close_team_chat(team_id, member_user_id)

    actor = actor_from_user(current_user, client_ip)
    if dissolution is not None:
        _record_team_dissolved(
            actor=actor,
            team=team,
            run_id=run_id,
            org_id=org.id,
            cause=DissolutionCause.LAST_MEMBER_REMOVED,
            result=dissolution,
        )
        await dispatch_team_dissolved({"run_id": run_id, "team_id": team_id})
    record_audit_event(
        event="team.member_removed",
        actor=actor,
        target={
            "entity": "team_member",
            "id": str(member_id),
            "name": member_display_name,
            "team_id": str(team_id),
            "team_name": team.name,
            "run_id": str(run_id),
        },
        organization_id=org.id,
    )


async def _move_team_member_once(
    db: AsyncSession, org: Organization, run_id: UUID, team_id: UUID, member_id: UUID, body: TeamMemberMoveRequest
) -> tuple[Team, Team, str, UUID | None, DissolutionResult | None]:
    """One attempt of ``move_team_member``: every read, the re-pointing and the commit, in one transaction."""
    run_repo = RunRepository(db, organization_id=org.id)
    # The run row first, before the team rows: see ``_leave_team_once``.
    await run_repo.lock_row(run_id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state not in (RunState.ACTIVE, RunState.PAUSED):
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Cannot move a member while the run is in {run.state} state. "
                "Members can only be moved while the run is active or paused."
            ),
            error_code=run_not_active_error_code(run.state),
        )

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)
    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    member_record = await team_member_repo.get_by_id(member_id)
    if member_record is None or member_record.team_id != team_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    if body.target_team_id == team_id:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The member is already a member of this team.",
            error_code="SAME_TEAM",
        )

    target_team = await team_repo.get_by_id(body.target_team_id)
    if target_team is None or target_team.run_id != run_id:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Target team not found in this run.",
            error_code="TARGET_NOT_IN_RUN",
        )

    # Both team rows are locked in ascending id order, whichever is the source,
    # so a move from A to B and a move from B to A cannot deadlock. Then the
    # member row is locked, so two requests cannot move the same member at
    # once. All three locks are held to the commit. Both teams are read again
    # under their locks: either may have dissolved since the lookup above, and
    # then the move must not happen.
    locked_teams = await team_repo.lock_rows((team.id, target_team.id))
    source_state = locked_teams.get(team.id)
    target_state = locked_teams.get(target_team.id)
    if source_state is None or source_state.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )
    if target_state is None or target_state.is_deleted:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Target team not found in this run.",
            error_code="TARGET_NOT_IN_RUN",
        )
    # The member row is read again as it is now: the object loaded before the
    # locks is not used below, neither its team nor its name.
    locked_member = (
        await db.execute(
            select(TeamMember)
            .where(TeamMember.id == member_id, TeamMember.organization_id == org.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked_member is None or locked_member.team_id != team_id:
        # A concurrent move already re-pointed (or removed) this member.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    # The limit of the joins. The target's count is read under its lock, so a
    # second move into the same team cannot pass this check before the commit.
    max_members_per_team = run.settings.get("max_members_per_team", 10)
    if target_state.member_count >= max_members_per_team:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Target team is full ({max_members_per_team} members).",
            error_code="TEAM_FULL",
        )

    # Moving the last member out dissolves the source team: only when the
    # client confirmed that. Decided here, after every other check, under the
    # locks and before the first write.
    await require_dissolution_confirmed(db, team, confirmed=body.confirm_dissolution)

    member_display_name = locked_member.display_name
    member_user_id = locked_member.user_id

    # The answers keep their team, so they stay with the old team.
    locked_member.team_id = target_team.id

    # Moving the last member out dissolves the source team. Its row is locked
    # above.
    dissolution = await dissolve_team_if_empty(db, team)
    await db.commit()
    return team, target_team, member_display_name, member_user_id, dissolution


@router.post(
    "/{team_id}/members/{member_id}/move",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Member moved to the target team"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Requires coordinator role or higher", "model": ErrorResponse},
        404: {
            "description": "Run, team, or member not found (or member not in this team)",
            "model": ErrorResponse,
        },
        422: {
            "description": (
                "Run not active or paused (RUN_NOT_STARTED / RUN_ENDED), target team not in "
                "this run (TARGET_NOT_IN_RUN), target is the same team (SAME_TEAM), or the "
                "target team is full (TEAM_FULL)"
            ),
            "model": ErrorResponse,
        },
        409: {
            "description": (
                "The move would dissolve the team and `confirm_dissolution` was not sent "
                "(TEAM_WOULD_DISSOLVE, with `member_count`, `answer_count`, `result_published` and `run_ended`; "
                "nothing changed), or the team is being changed by other requests (TEAM_BUSY)"
            ),
            "model": TeamConflictResponse,
        },
    },
)
async def move_team_member(
    run_id: UUID,
    team_id: UUID,
    member_id: UUID,
    request: Request,
    body: TeamMemberMoveRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> None:
    """Move a member to another team of the same run. Staff from coordinator up only.

    `member_id` is the id of the membership and `target_team_id` the receiving
    team. The run must be active or paused, and the target must be another
    team of this run with a free place. A move is not a departure: a moved
    guest stays in the run. The member's answers stay with the old team.

    When the move takes out the last member, the old team dissolves, and the
    body needs `confirm_dissolution: true`. Without it nothing changes, and the
    answer is 409 `TEAM_WOULD_DISSOLVE` with `member_count`, `answer_count`,
    `result_published` and `run_ended` of the old team as they are now. A move
    that leaves other members ignores the field.

    Audit events: `team.member_moved`, and `team.dissolved` when the old team
    went with its last member. The WebSocket event `team_member_moved` goes to
    the chat channels of both teams and to the run channel, so open clients
    follow without a reload.
    """
    client_ip = client_address(request)

    team, target_team, member_display_name, member_user_id, dissolution = await _with_lock_retry(
        request,
        db,
        org,
        current_user,
        RoleLevel.COORDINATOR,
        lambda: _move_team_member_once(db, org, run_id, team_id, member_id, body),
    )

    # The old team's chat is no longer the member's. Closed after the commit and
    # outside the attempt, so a retried attempt closes nothing.
    if member_user_id is not None:
        await _close_team_chat(team_id, member_user_id)

    actor = actor_from_user(current_user, client_ip)
    if dissolution is not None:
        _record_team_dissolved(
            actor=actor,
            team=team,
            run_id=run_id,
            org_id=org.id,
            cause=DissolutionCause.LAST_MEMBER_MOVED,
            result=dissolution,
        )
    record_audit_event(
        event="team.member_moved",
        actor=actor,
        target={
            "entity": "team_member",
            "id": str(member_id),
            "name": member_display_name,
            "from_team_id": str(team_id),
            "from_team_name": team.name,
            "to_team_id": str(target_team.id),
            "to_team_name": target_team.name,
            "run_id": str(run_id),
        },
        organization_id=org.id,
    )
    log.info(
        "team.member_moved",
        team_id=str(team_id),
        target_team_id=str(target_team.id),
        run_id=str(run_id),
        org_id=str(org.id),
        actor_user_id=str(current_user.id),
        actor_ip=client_ip,
    )

    # The run channel lets the moved member's app switch chat, progress and
    # leaderboard to the new team without a reload. The chat channels update
    # the member lists of both teams.
    move_event = {
        "type": "team_member_moved",
        "data": {
            "run_id": str(run_id),
            "member_id": str(member_id),
            "user_id": str(member_user_id) if member_user_id else None,
            "display_name": member_display_name,
            "from_team_id": str(team_id),
            "to_team_id": str(target_team.id),
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
    for channel in (f"chat:{team_id}", f"chat:{target_team.id}", f"run_state:{run_id}"):
        await ws_manager.broadcast(channel=channel, message=move_event)
    # After the move event: the moved member's app re-points to the new team
    # on that one, and then reads the dissolution as one of another team.
    if dissolution is not None:
        await dispatch_team_dissolved({"run_id": run_id, "team_id": team_id})


async def _remove_team_once(
    db: AsyncSession, org: Organization, run_id: UUID, team_id: UUID, confirm_dissolution: bool
) -> tuple[Team, DissolutionResult]:
    """One attempt of ``remove_team``: every read, the dissolution and the commit, in one transaction."""
    run_repo = RunRepository(db, organization_id=org.id)
    # The run row first, before the team row: see ``_leave_team_once``.
    await run_repo.lock_row(run_id)
    if await run_repo.get_by_id(run_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)
    if team is None or team.run_id != run_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found in this run")

    # Lock first, then decide: a concurrent removal that dissolved the team
    # first leaves nothing to do, and the member rows are read under the lock,
    # so a member who joined meanwhile goes with the team too.
    if not await team_repo.lock_row(team_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found in this run")
    # This act always dissolves, so it always needs the confirmation.
    await require_dissolution_confirmed(db, team, confirmed=confirm_dissolution, removes_team=True)
    dissolution = await dissolve_team(db, team)
    await db.commit()
    return team, dissolution


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Team removed with all its members"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Requires coordinator role or higher", "model": ErrorResponse},
        404: {"description": "Run or team not found (or team gone, or in another run)", "model": ErrorResponse},
        409: {
            "description": (
                "The removal of a team dissolves it and `confirm_dissolution` was not sent "
                "(TEAM_WOULD_DISSOLVE, with `member_count`, `answer_count`, `result_published` and `run_ended`; "
                "nothing changed), or the team is being changed by other requests (TEAM_BUSY)"
            ),
            "model": TeamConflictResponse,
        },
    },
)
async def remove_team(
    run_id: UUID,
    team_id: UUID,
    request: Request,
    confirm_dissolution: bool = Query(
        False,
        description=(
            "True when the client confirmed the dissolution. This call always dissolves the team, so "
            "without it the answer is always 409 TEAM_WOULD_DISSOLVE."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> None:
    """Remove a team with all its members. Staff from coordinator up only.

    Works in every state of the run. Each member is removed as by the removal
    of a member, so a guest among them may not join the run again. The team is
    dissolved: its answers and scores stay stored and leave the ranking.

    The call always dissolves the team, so it always needs the query parameter
    `confirm_dissolution=true`. Without it nothing changes, and the answer is
    409 `TEAM_WOULD_DISSOLVE` with `member_count`, `answer_count`,
    `result_published` and `run_ended` as they are now.

    Audit event: team.dissolved, with the cause removed_by_staff
    """
    client_ip = client_address(request)

    team, dissolution = await _with_lock_retry(
        request,
        db,
        org,
        current_user,
        RoleLevel.COORDINATOR,
        lambda: _remove_team_once(db, org, run_id, team_id, confirm_dissolution),
    )

    await notify_team_members_changed(run_id, team_id)
    for member_user_id in dissolution.member_user_ids:
        await _close_team_chat(team_id, member_user_id)

    _record_team_dissolved(
        actor=actor_from_user(current_user, client_ip),
        team=team,
        run_id=run_id,
        org_id=org.id,
        cause=DissolutionCause.REMOVED_BY_STAFF,
        result=dissolution,
    )
    await dispatch_team_dissolved({"run_id": run_id, "team_id": team_id})


@router.patch(
    "/{team_id}",
    response_model=TeamResponse,
    responses={
        200: {"description": "Team name committed"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Caller is neither team creator nor coordinator/org-admin", "model": ErrorResponse},
        404: {"description": "Team not found in this run (or cross-tenant)", "model": ErrorResponse},
        409: {
            "description": (
                "The run has ended, so the team name is frozen, or the name is already taken "
                "in this run (error_code TEAM_NAME_TAKEN)"
            ),
            "model": ErrorResponse,
        },
        422: {"description": "Validation error", "model": ErrorResponse},
    },
)
async def rename_team(
    run_id: UUID,
    team_id: UUID,
    request: Request,
    body: TeamRenameRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> TeamResponse:
    """Set or change the name of a team.

    Allowed for the team's creator, who is the member that joined first, and
    for staff from coordinator up. A team of another organization answers 404,
    not 403. The name is frozen once the run has ended. The current name sent
    again changes nothing and broadcasts nothing.

    A real change broadcasts ``team.name_updated`` on the channel
    ``run_state:{run_id}``, so members who wait for the name go on at once.
    """
    client_ip = client_address(request)

    # Scoped to the caller's organization: a run of another one is a 404.
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)
    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    # The team row is locked and read again before the creator is looked up and
    # the name is written. A team that dissolved since the lookup does not
    # exist, and is not renamed.
    if not await team_repo.lock_row(team_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )
    await db.refresh(team)

    is_coordinator_plus = RoleLevel[current_user.role.name] >= RoleLevel.COORDINATOR
    if not is_coordinator_plus:
        # The creator is the member who joined first. The team row does not
        # record who created it.
        team_member_repo = TeamMemberRepository(db, organization_id=org.id)
        members = await team_member_repo.list_by_team(team_id)
        if not members:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the team creator or a coordinator may rename this team.",
            )
        creator = min(members, key=lambda m: m.joined_at)
        if creator.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the team creator or a coordinator may rename this team.",
            )

    # The name is frozen once the run has ended, because it is then part of the
    # stored result. It is not frozen while the run is active: guests can join
    # an active run only, so the creator could otherwise never name the team.
    if run.state == RunState.ENDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The team name is frozen because the run has ended.",
        )

    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Team name cannot be empty.",
        )

    old_name = team.name
    if old_name == new_name:
        # The same name: no change and no broadcast.
        member_count = await TeamMemberRepository(db, organization_id=org.id).count_by_team(team_id)
        return TeamResponse(
            id=team.id,
            organization_id=team.organization_id,
            run_id=team.run_id,
            name=team.name,
            color=team.color,
            member_count=member_count,
            share_code=team.share_code,
            coordinator_chat_open=team.coordinator_chat_opened_at is not None,
            created_at=team.created_at,
            updated_at=team.updated_at,
        )

    # The team itself does not count: "Foo" to "foo" is a new spelling of its
    # own name, not a collision.
    await ensure_team_name_free(
        db,
        organization_id=org.id,
        run_id=run_id,
        name=new_name,
        exclude_team_id=team_id,
    )

    team.name = new_name
    team.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if is_team_name_conflict(exc):
            raise team_name_taken_error() from exc
        raise
    await db.refresh(team)

    record_audit_event(
        event="team.renamed",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "team",
            "id": str(team_id),
            "old_name": old_name,
            "new_name": new_name,
            "run_id": str(run_id),
        },
        organization_id=org.id,
    )
    log.info(
        "team.renamed",
        team_id=str(team_id),
        run_id=str(run_id),
        org_id=str(org.id),
        actor_user_id=str(current_user.id),
        was_pending=old_name is None,
    )

    # The other members wait on a screen until the team has a name, and this
    # moves them on. The message has the ``{type, data, timestamp}`` form of
    # ``pg_notify.py``, which the handlers of the frontend's ``useWebSocket``
    # expect.
    await ws_manager.broadcast(
        channel=f"run_state:{run_id}",
        message={
            "type": "team.name_updated",
            "data": {
                "run_id": str(run_id),
                "team_id": str(team_id),
                "name": new_name,
            },
            "timestamp": team.updated_at.isoformat(),
        },
    )

    member_count = await TeamMemberRepository(db, organization_id=org.id).count_by_team(team_id)
    return TeamResponse(
        id=team.id,
        organization_id=team.organization_id,
        run_id=team.run_id,
        name=team.name,
        color=team.color,
        member_count=member_count,
        share_code=team.share_code,
        coordinator_chat_open=team.coordinator_chat_opened_at is not None,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.post(
    "/{team_id}/advance-stage",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Stage advanced; WS event broadcast"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Caller is neither team creator nor coordinator/org-admin", "model": ErrorResponse},
        404: {"description": "Team not found in this run (or cross-tenant)", "model": ErrorResponse},
    },
)
async def advance_team_stage(
    run_id: UUID,
    team_id: UUID,
    request: Request,
    body: TeamStageAdvanceRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> None:
    """Tell the other members that the creator moved to the next step of the team setup.

    Broadcasts ``team.stage_changed`` on the run channel, so members who
    already joined can follow on their wait screen before the team has a name.
    Nothing is stored. Allowed for the team's creator and for staff from
    coordinator up. A team of another organization answers 404.
    """
    client_ip = client_address(request)

    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    team_repo = TeamRepository(db, organization_id=org.id)
    team = await team_repo.get_by_id(team_id)
    if team is None or team.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found in this run",
        )

    is_coordinator_plus = RoleLevel[current_user.role.name] >= RoleLevel.COORDINATOR
    if not is_coordinator_plus:
        team_member_repo = TeamMemberRepository(db, organization_id=org.id)
        members = await team_member_repo.list_by_team(team_id)
        if not members:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the team creator or a coordinator may advance the team stage.",
            )
        creator = min(members, key=lambda m: m.joined_at)
        if creator.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the team creator or a coordinator may advance the team stage.",
            )

    log.info(
        "team.stage_changed",
        team_id=str(team_id),
        run_id=str(run_id),
        org_id=str(org.id),
        actor_user_id=str(current_user.id),
        actor_ip=client_ip,
        stage=body.stage,
    )

    await ws_manager.broadcast(
        channel=f"run_state:{run_id}",
        message={
            "type": "team.stage_changed",
            "data": {
                "run_id": str(run_id),
                "team_id": str(team_id),
                "stage": body.stage,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )

    return None
