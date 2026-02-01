"""
Run CRUD endpoints.

Provides full CRUD operations for runs with RBAC enforcement,
organization scoping via URL slug, audit logging, and pagination.

All endpoints require authentication and enforce role-based access control:
- List/Get: coordinator+ can view runs, participant+ can view single run
- Create: coordinator+ can create runs
- Update: coordinator+ can update runs (only in configured/scheduled state)
- Delete: coordinator+ can soft-delete runs (only in configured/scheduled state)

All modification operations emit audit events for security tracking.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import RoleLevel, require_role
from app.client_address import client_address
from app.config import settings
from app.database import get_db
from app.models.organization import Organization
from app.models.run import RunState
from app.models.user import User
from app.repositories.rallye import RallyeRepository
from app.repositories.run import RunRepository
from app.repositories.run_challenge import RunChallengeRepository
from app.repositories.run_result_link import RunResultLinkRepository, compute_slug
from app.repositories.run_station import RunStationRepository
from app.repositories.score import ScoreRepository
from app.repositories.submission import SubmissionRepository
from app.repositories.team_member import TeamMemberRepository
from app.routers.schemas import (
    ErrorResponse,
    ResultLinkPublishResponse,
    ResultLinkStatusResponse,
    RunChallengeListResponse,
    RunChallengeResponse,
    RunCreateRequest,
    RunListResponse,
    RunParticipantOverviewResponse,
    RunResponse,
    RunScheduleRequest,
    RunStationListResponse,
    RunStationOverviewItem,
    RunStationResponse,
    RunUpdateRequest,
)
from app.services.audit import actor_from_user, record_audit_event
from app.services.join_code import generate_unique_join_code
from app.services.notify import notify_run_state_change
from app.services.result_snapshot import rank_entries
from app.services.run_start import AUTO_START_BLOCKED, begin_run
from app.services.run_state import RunStateService
from app.services.ws_manager import manager as ws_manager
from app.storage import station_image_url

router = APIRouter()


# Scoring keys a team needs to answer and to see its points. Everything else is
# for grading only. The right quiz option (correct_option), the expected QR value
# (expected_value) and the reference answer must not reach a team's network tab,
# because the creator form promises "Teilnehmende sehen dieses Feld nicht".
_PARTICIPANT_SCORING_KEYS = frozenset({"max_points", "time_bonus", "options", "auto_approve"})


def _is_staff(user: User) -> bool:
    return RoleLevel[user.role.value.upper()] >= RoleLevel.COORDINATOR


def _run_challenge_for(user: User, challenge: Any) -> RunChallengeResponse:
    """A run challenge as the caller may see it: complete for coordination, safe below."""
    item = RunChallengeResponse.model_validate(challenge)
    if _is_staff(user):
        return item
    return item.model_copy(
        update={
            "scoring_rules": {k: v for k, v in (item.scoring_rules or {}).items() if k in _PARTICIPANT_SCORING_KEYS},
            "suggested_solution": None,
        }
    )


async def _run_response(db: AsyncSession, org_id: UUID, run: Any) -> RunResponse:
    response = RunResponse.model_validate(run)
    if run.state == RunState.ENDED:
        response.grading_complete = await SubmissionRepository(db, org_id).count_unscored_by_run(run.id) == 0
    return response


def _result_link_url(slug: str) -> str:
    # A fragment, not a path segment: a path or query value lands in the request
    # line that access logs and reverse-proxy logs record on every request, while
    # a fragment never leaves the browser. The frontend reads it client-side and
    # calls POST /api/public/results/lookup with the slug in the body.
    return f"{settings.mail.public_base_url}/results#{slug}"


async def _build_results_snapshot(db: AsyncSession, org: Organization, run: Any) -> dict[str, Any]:
    """Build the frozen public projection stored on the result link.

    Ranks with ``rank_entries``, which mirrors the scoreboard's shared-rank
    computation (``routers/scores.py``), so a published result matches what
    the coordinator saw before publishing.
    """
    score_repo = ScoreRepository(db, org.id)
    scoreboard_data = await score_repo.get_scoreboard(run.id)

    # The team id stays in the stored entry, so a dissolution finds the entry
    # of a team whose name is still pending. The public answer is built from
    # ``PublicResultsEntry``, which has no such field.
    entries = rank_entries((entry["team_name"], entry["total_points"], entry["team_id"]) for entry in scoreboard_data)

    language = next(iter(run.content_languages), None) or next(iter(org.languages), None) or "de"

    return {
        "run_name": run.name,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        # No organization name: the page does not show it, and the public answer
        # carries nothing that the page does not show.
        "language": language,
        "entries": entries,
    }


@router.get(
    "/",
    response_model=RunListResponse,
    responses={
        200: {"description": "List of runs"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def list_runs(
    _request: Request,
    skip: int = Query(0, ge=0, description="Number of items to skip (offset)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum items to return"),
    state: RunState | None = Query(None, description="Filter by run state"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> RunListResponse:
    """List the runs of the organization, with paging and an optional state filter.

    Open to coordinators, creators and organization admins. The response holds
    the page of runs, the total count and the `skip` and `limit` of the page.
    The `state` filter takes `configured`, `scheduled`, `active`, `paused` or
    `ended`. For an ended run, `grading_complete` tells whether every
    submission is scored.
    """
    run_repo = RunRepository(db, organization_id=org.id)

    if state is not None:
        runs = await run_repo.list_by_state(state, skip=skip, limit=limit)
        total = await run_repo.count_by_state(state)
    else:
        runs = await run_repo.list(skip=skip, limit=limit)
        total = await run_repo.count()

    return RunListResponse(
        items=[await _run_response(db, org.id, r) for r in runs],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{run_id}",
    response_model=RunResponse,
    responses={
        200: {"description": "Run details"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def get_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("participant")),
) -> RunResponse:
    """Return one run of the organization.

    Open to participants, coordinators, creators and organization admins. A
    run that does not exist, that was deleted, or that belongs to another
    organization gives 404. For an ended run, `grading_complete` tells
    whether every submission is scored.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    return await _run_response(db, org.id, run)


@router.post(
    "/",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Run created successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Organization or rallye not found", "model": ErrorResponse},
        422: {"description": "Validation error", "model": ErrorResponse},
    },
)
async def create_run(
    request: Request,
    body: RunCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RunResponse:
    """Create a run from a rallye of the organization.

    Open to coordinators, creators and organization admins. An unknown
    `rallye_id` gives 404. The run gets a join code of 6 characters that no
    other run of the organization uses. It starts in the state `scheduled`
    when the body has `scheduled_for`, otherwise in `configured`. The content
    of the rallye is copied when the run starts, not now.

    Emits the audit event `run.created`.
    """
    client_ip = client_address(request)

    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(body.rallye_id)

    if rallye is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rallye with ID {body.rallye_id} not found in organization",
        )

    try:
        join_code = await generate_unique_join_code(db, org.id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e

    run_repo = RunRepository(db, organization_id=org.id)
    try:
        run = await run_repo.create(
            organization_id=org.id,
            rallye_id=body.rallye_id,
            name=body.name,
            # A start time makes the run "Geplant" at once.
            state=RunState.SCHEDULED if body.scheduled_for is not None else RunState.CONFIGURED,
            join_code=join_code,
            scheduled_for=body.scheduled_for,
            settings=body.settings or {},
            max_teams=body.max_teams,
            content_languages=body.content_languages or ["de", "en"],
        )
        await db.commit()
        await db.refresh(run)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Run creation failed: {str(e)}",
        ) from e

    record_audit_event(
        event="run.created",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "run", "id": str(run.id), "join_code": join_code},
        organization_id=org.id,
    )

    return RunResponse.model_validate(run)


@router.put(
    "/{run_id}",
    response_model=RunResponse,
    responses={
        200: {"description": "Run updated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Invalid state for update", "model": ErrorResponse},
    },
)
async def update_run(
    run_id: UUID,
    request: Request,
    body: RunUpdateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RunResponse:
    """Change the name, start time, settings or team limit of a run.

    Open to coordinators, creators and organization admins. Only the fields
    in the body change. An omitted field stays as it is. An explicit `null`
    clears `scheduled_for` or `max_teams`, and `name` and `settings` refuse
    `null`.

    Only a run in the state `configured` or `scheduled` can change. Any other
    state gives 422. A new `scheduled_for` moves the run to `scheduled`, and
    clearing it moves the run back to `configured`. A change of the start
    time also clears a recorded problem of the automatic start. A request that changes
    nothing answers with the run and writes nothing.

    Emits the audit event `run.updated` with the changed fields.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state not in [RunState.CONFIGURED, RunState.SCHEDULED]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot update run in {run.state} state. Updates only allowed in configured or scheduled state.",
        )

    # Only the fields the client sent count. The repository writes every keyword it
    # gets, so an omitted name would become NULL. An explicit null clears the
    # start time or the team limit.
    sent = body.model_fields_set
    if "name" in sent and body.name is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The name must not be empty.",
        )
    if "settings" in sent and body.settings is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="settings must be an object.",
        )

    updates: dict[str, Any] = {}
    changes: dict[str, dict[str, Any]] = {}
    if "name" in sent and body.name != run.name:
        updates["name"] = body.name
        changes["name"] = {"from": run.name, "to": body.name}
    if "scheduled_for" in sent and body.scheduled_for != run.scheduled_for:
        updates["scheduled_for"] = body.scheduled_for
        changes["scheduled_for"] = {
            "from": run.scheduled_for.isoformat() if run.scheduled_for else None,
            "to": body.scheduled_for.isoformat() if body.scheduled_for else None,
        }
    if "settings" in sent and body.settings != run.settings:
        updates["settings"] = body.settings
        changes["settings"] = {"from": run.settings, "to": body.settings}
    if "max_teams" in sent and body.max_teams != run.max_teams:
        updates["max_teams"] = body.max_teams
        changes["max_teams"] = {"from": run.max_teams, "to": body.max_teams}

    # Nothing changed: return the current state without a write.
    if not changes:
        return RunResponse.model_validate(run)

    # The start time decides between "Konfiguriert" and "Geplant", like the wizard.
    # A new time also drops an earlier automatic-start problem, which the next
    # check re-evaluates.
    if "scheduled_for" in updates:
        target = RunState.SCHEDULED if updates["scheduled_for"] is not None else RunState.CONFIGURED
        if run.state != target:
            previous_state = run.state.value
            RunStateService.transition(run, target)
            changes["state"] = {"from": previous_state, "to": target.value}
        base_settings = updates.get("settings", run.settings) or {}
        if AUTO_START_BLOCKED in base_settings:
            updates["settings"] = {k: v for k, v in base_settings.items() if k != AUTO_START_BLOCKED}

    try:
        updated_run = await run_repo.update(entity_id=run_id, **updates)
        await db.commit()
        await db.refresh(updated_run)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Run update failed: {str(e)}",
        ) from e

    record_audit_event(
        event="run.updated",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "run", "id": str(run_id)},
        changes=changes,
        organization_id=org.id,
    )

    return RunResponse.model_validate(updated_run)


@router.delete(
    "/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Run deleted successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Invalid state for deletion", "model": ErrorResponse},
    },
)
async def delete_run(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> None:
    """Delete a run that has not started.

    Open to coordinators, creators and organization admins. Only a run in the
    state `configured` or `scheduled` can be deleted. Any other state gives
    422. The run is marked as deleted and no longer appears in the API. Its
    row stays in the database.

    Emits the audit event `run.deleted`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state not in [RunState.CONFIGURED, RunState.SCHEDULED]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot delete run in {run.state} state. Deletion only allowed in configured or scheduled state.",
        )

    deleted = await run_repo.soft_delete(run_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found or already deleted",
        )

    await db.commit()

    record_audit_event(
        event="run.deleted",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "run", "id": str(run_id), "name": run.name, "join_code": run.join_code},
        organization_id=org.id,
    )


# ============================================================================
# State Transition Endpoints
# ============================================================================


@router.post(
    "/{run_id}/start",
    response_model=RunResponse,
    responses={
        200: {"description": "Run started successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Invalid state transition or snapshot creation failed", "model": ErrorResponse},
    },
)
async def start_run(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RunResponse:
    """Start a run now.

    Open to coordinators, creators and organization admins. The route copies
    the stations and challenges of the rallye into the run as frozen
    snapshots, sets the run to `active` and records `started_at`. A recorded
    problem of the automatic start is cleared. Only a run in the state
    `configured` or `scheduled` can start. The route answers 422 for every other
    state, a `paused` run included (use resume for it), and also when the
    snapshot fails. In both cases nothing is written and the run keeps its state.

    The state change is announced to the clients of the run.

    Emits the audit event `run.started`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    # The run's row is locked before the run is read, and the lock holds until the
    # snapshot and the new state are committed together. A second start, the
    # automatic start (it skips a held row) and a change of the run's state wait for
    # it, then read the run as this start left it. Without the lock two starts could
    # both read `configured` and both build the snapshot. Lock order:
    # RunRepository.lock_row.
    await run_repo.lock_row(run_id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # Snapshot, then transition: the same path as the automatic start at the
    # planned time (services/run_start.py). The state read under the lock is
    # checked first, so a refused state never writes a snapshot. Start accepts
    # only `configured` and `scheduled`: a paused run is resumed, not started.
    try:
        RunStateService.check_start(run.state)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        ) from e
    try:
        previous_state = await begin_run(db, run, org.id)
    except ValueError as e:
        # No explicit rollback: get_db rolls back on the raised exception. An extra
        # one here would expire the caller's objects in the shared test session
        # (test_co5_invalid_state_transitions).
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Failed to create run snapshot: {str(e)}",
        ) from e

    try:
        await db.commit()
        await db.refresh(run)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start run: {str(e)}",
        ) from e

    await notify_run_state_change(db, run, previous_state)
    await db.commit()  # Commit the NOTIFY transaction

    record_audit_event(
        event="run.started",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "run", "id": str(run_id), "name": run.name, "new_state": RunState.ACTIVE.value},
        organization_id=org.id,
    )

    return RunResponse.model_validate(run)


@router.post(
    "/{run_id}/schedule",
    response_model=RunResponse,
    responses={
        200: {"description": "Run scheduled successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Invalid state transition", "model": ErrorResponse},
    },
)
async def schedule_run(
    run_id: UUID,
    request: Request,
    body: RunScheduleRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RunResponse:
    """Set the start time of a run and move it to `scheduled`.

    Open to coordinators, creators and organization admins. Only a run in the
    state `configured` can be scheduled (422 otherwise). To change the start
    time of a run that is already `scheduled`, update the run.

    Emits the audit event `run.scheduled`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    run.scheduled_for = body.scheduled_for

    try:
        RunStateService.transition(run, RunState.SCHEDULED)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        ) from e

    try:
        await db.commit()
        await db.refresh(run)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to schedule run: {str(e)}",
        ) from e

    record_audit_event(
        event="run.scheduled",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "run",
            "id": str(run_id),
            "name": run.name,
            "new_state": RunState.SCHEDULED.value,
            "scheduled_for": body.scheduled_for.isoformat(),
        },
        organization_id=org.id,
    )

    return RunResponse.model_validate(run)


@router.post(
    "/{run_id}/pause",
    response_model=RunResponse,
    responses={
        200: {"description": "Run paused successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Invalid state transition", "model": ErrorResponse},
    },
)
async def pause_run(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RunResponse:
    """Pause an active run.

    Open to coordinators, creators and organization admins. The route sets the
    run to `paused` and records `paused_at`. Only a run in the state `active`
    can be paused (422 otherwise). The state change is announced to the
    clients of the run.

    Emits the audit event `run.paused`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    previous_state = run.state.value

    try:
        RunStateService.transition(run, RunState.PAUSED)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        ) from e

    try:
        await db.commit()
        await db.refresh(run)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to pause run: {str(e)}",
        ) from e

    await notify_run_state_change(db, run, previous_state)
    await db.commit()  # Commit the NOTIFY transaction

    record_audit_event(
        event="run.paused",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "run", "id": str(run_id), "name": run.name, "new_state": RunState.PAUSED.value},
        organization_id=org.id,
    )

    return RunResponse.model_validate(run)


@router.post(
    "/{run_id}/resume",
    response_model=RunResponse,
    responses={
        200: {"description": "Run resumed successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Invalid state transition", "model": ErrorResponse},
    },
)
async def resume_run(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RunResponse:
    """Resume a paused run.

    Open to coordinators, creators and organization admins. The route sets the
    run to `active` and clears `paused_at`. Only a run in the state `paused`
    can resume (422 otherwise). A `configured` or `scheduled` run never
    started and has no snapshot: it is started, not resumed, and keeps its
    state. The state change is announced to the clients of the run.

    Emits the audit event `run.resumed`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    previous_state = run.state.value

    try:
        RunStateService.resume(run)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        ) from e

    try:
        await db.commit()
        await db.refresh(run)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resume run: {str(e)}",
        ) from e

    await notify_run_state_change(db, run, previous_state)
    await db.commit()  # Commit the NOTIFY transaction

    record_audit_event(
        event="run.resumed",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "run", "id": str(run_id), "name": run.name, "new_state": RunState.ACTIVE.value},
        organization_id=org.id,
    )

    return RunResponse.model_validate(run)


@router.post(
    "/{run_id}/end",
    response_model=RunResponse,
    responses={
        200: {"description": "Run ended successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Invalid state transition", "model": ErrorResponse},
    },
)
async def end_run(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RunResponse:
    """End a run.

    Open to coordinators, creators and organization admins. The route sets the
    run to `ended` and records `ended_at`. Only a run in the state `active` or
    `paused` can end (422 otherwise). An ended run cannot start again or be
    updated.

    Ending adds two notices to the feed of the run, for every team: that the
    results are available and a prompt for feedback. While submissions are
    still unscored, the first notice says that the standing is interim. The
    state change and the notices are announced to the clients of the run.
    Guest accounts stay after the run ends.

    Emits the audit event `run.ended`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    # The run's row is locked before the run and its grading state are read, and
    # the lock holds until the end and its notices are committed together. The
    # publish and the withdrawal take the same lock, so a publish only ever sees a
    # run that has not ended yet or one that has ended with its notices in the
    # feed, never the gap between the two. Without it, a delayed end could put the
    # interim notice back beside a published result. Lock order:
    # RunRepository.lock_row.
    await run_repo.lock_row(run_id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    previous_state = run.state.value

    try:
        RunStateService.transition(run, RunState.ENDED)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        ) from e

    # The results and feedback-prompt feed events let the participant client render
    # the post-run sequence without a hard-coded redirect. Both are broadcast (no
    # target_team_id), so every team sees them in the run's feed.
    from app.models.feed_event import FeedEventType
    from app.repositories.feed_event import INTERIM_RESULT_NOTICE, FeedEventRepository
    from app.services.notify import notify_feed_event

    feed_repo = FeedEventRepository(db, org.id)
    # While an answer is still ungraded the leaderboard is an interim one, so the
    # line must not promise results.
    grading_open = await SubmissionRepository(db, org.id).count_unscored_by_run(run.id) > 0
    results_event = await feed_repo.create(
        organization_id=org.id,
        run_id=run.id,
        event_type=FeedEventType.RESULTS_AVAILABLE,
        content=(
            dict(INTERIM_RESULT_NOTICE)
            if grading_open
            else {
                "de": "Eure Ergebnisse sind verfügbar!",
                "en": "Your team's results are available!",
            }
        ),
        author_id=None,
    )
    feedback_event = await feed_repo.create(
        organization_id=org.id,
        run_id=run.id,
        event_type=FeedEventType.FEEDBACK_PROMPT,
        content={
            "de": "Erzählt uns, wie es war!",
            "en": "Tell us how it went!",
        },
        author_id=None,
    )

    # The end and its notices go in one transaction. The commit releases the run lock.
    try:
        await db.commit()
        await db.refresh(run)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to end run: {str(e)}",
        ) from e

    await notify_run_state_change(db, run, previous_state)
    await notify_feed_event(db, results_event)
    await notify_feed_event(db, feedback_event)
    await db.commit()  # Commit the NOTIFY transaction

    # Guest accounts deliberately outlive the run. Ending it emits RESULTS_AVAILABLE
    # and FEEDBACK_PROMPT, which invite the team to look at its result and answer
    # the feedback. Deleting the accounts here would log the guests out at the
    # login screen and make both invitations unanswerable. Scoring also usually
    # finishes after the run does, so the final standing only becomes visible
    # later. The LK_A retention class owns this deletion and applies the
    # organisation's configured window.

    record_audit_event(
        event="run.ended",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "run",
            "id": str(run_id),
            "name": run.name,
            "new_state": RunState.ENDED.value,
        },
        organization_id=org.id,
    )

    return RunResponse.model_validate(run)


# ============================================================================
# Snapshot Read Endpoints
# ============================================================================


@router.get(
    "/{run_id}/stations/",
    response_model=RunStationListResponse,
    responses={
        200: {"description": "List of run stations (frozen snapshots)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Run is not in an active, paused, or ended state", "model": ErrorResponse},
    },
)
async def list_run_stations(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("guest")),
) -> RunStationListResponse:
    """List the stations of a run, in route order.

    Open to every signed-in role, guests included. The stations are the frozen
    copy that the run made when it started. They do not follow later changes
    to the rallye. A run that has not started has no copy and gives 422.
    """
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state not in [RunState.ACTIVE, RunState.PAUSED, RunState.ENDED]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot read stations for run in {run.state} state. Snapshots only available when run is active, paused, or ended.",
        )

    run_station_repo = RunStationRepository(db, organization_id=org.id)
    stations = await run_station_repo.list_by_run(run_id)

    items: list[RunStationResponse] = []
    for s in stations:
        item = RunStationResponse.model_validate(s)
        # The frozen title image is streamed same-origin by /api/files for any
        # member of the organization (storage.station_image_url).
        if s.title_image_key:
            item.title_image_url = station_image_url(s.title_image_key)
        items.append(item)

    return RunStationListResponse(
        items=items,
        total=len(stations),
        skip=0,
        limit=len(stations),
    )


@router.get(
    "/{run_id}/stations/{station_id}/challenges/",
    response_model=RunChallengeListResponse,
    responses={
        200: {"description": "List of run challenges for station (frozen snapshots)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Run or station not found", "model": ErrorResponse},
        422: {"description": "Run is not in an active, paused, or ended state", "model": ErrorResponse},
    },
)
async def list_run_challenges(
    run_id: UUID,
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> RunChallengeListResponse:
    """List the challenges of one station of a run, in order.

    Open to every signed-in role, guests included. The challenges are the
    frozen copy that the run made when it started. They do not follow later
    changes to the rallye. A run that has not started gives 422. A station
    that is not in this run gives 404.

    Coordinators, creators and organization admins see the complete scoring
    rules and the suggested solution. Every other role gets the rules that
    a team needs to answer and to see its points, and no suggested solution.
    The right quiz option and the expected QR value are never sent to them.
    """
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state not in [RunState.ACTIVE, RunState.PAUSED, RunState.ENDED]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot read challenges for run in {run.state} state. Snapshots only available when run is active, paused, or ended.",
        )

    run_station_repo = RunStationRepository(db, organization_id=org.id)
    station = await run_station_repo.get_by_id(station_id)
    if station is None or station.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found in this run",
        )

    run_challenge_repo = RunChallengeRepository(db, organization_id=org.id)
    challenges = await run_challenge_repo.list_by_run_station(station_id)

    return RunChallengeListResponse(
        items=[_run_challenge_for(current_user, c) for c in challenges],
        total=len(challenges),
        skip=0,
        limit=len(challenges),
    )


@router.get(
    "/{run_id}/participant-overview",
    response_model=RunParticipantOverviewResponse,
    responses={
        200: {"description": "Stations with their challenges and the caller's answered challenges"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Run is not in an active, paused, or ended state", "model": ErrorResponse},
    },
)
async def get_participant_overview(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> RunParticipantOverviewResponse:
    """Return the stations of a run with their challenges and the challenges the caller's team answered.

    Open to every signed-in role, guests included. One answer replaces a request
    for the stations, one for the challenges of each station and one for the
    submissions. The stations come in route order, each with its challenges.
    The challenges are as limited as in the per-station list.

    `answered_challenge_ids` holds the challenges for which the caller's own
    team has a submission, and is empty for a caller without a team.
    `is_team_member` tells which case applies. A run that has not started
    gives 422.
    """
    run = await RunRepository(db, organization_id=org.id).get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.state not in [RunState.ACTIVE, RunState.PAUSED, RunState.ENDED]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot read stations for run in {run.state} state. Snapshots only available when run is active, paused, or ended.",
        )

    stations = await RunStationRepository(db, organization_id=org.id).list_by_run(run_id)
    challenge_repo = RunChallengeRepository(db, organization_id=org.id)
    by_station: dict[UUID, list[Any]] = {}
    for challenge in await challenge_repo.list_by_run(run_id):
        by_station.setdefault(challenge.run_station_id, []).append(challenge)

    items: list[RunStationOverviewItem] = []
    for station in stations:
        base = RunStationResponse.model_validate(station)
        if station.title_image_key:
            base.title_image_url = station_image_url(station.title_image_key)
        challenges = sorted(by_station.get(station.id, []), key=lambda c: c.sort_order)
        items.append(
            RunStationOverviewItem(
                **base.model_dump(),
                challenges=[_run_challenge_for(current_user, c) for c in challenges],
            )
        )

    answered: list[UUID] = []
    membership = await TeamMemberRepository(db, organization_id=org.id).get_by_user_and_run(
        user_id=current_user.id, run_id=run_id
    )
    if membership is not None:
        submissions = await SubmissionRepository(db, organization_id=org.id).list_by_team(
            membership.team_id, skip=0, limit=10_000
        )
        answered = sorted({s.run_challenge_id for s in submissions}, key=str)

    return RunParticipantOverviewResponse(
        stations=items, answered_challenge_ids=answered, is_team_member=membership is not None
    )


@router.post(
    "/{run_id}/results/publish",
    response_model=ResultLinkPublishResponse,
    responses={
        200: {"description": "Result link published (or re-published)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        409: {"description": "Run is not ended, or grading is not complete", "model": ErrorResponse},
    },
)
async def publish_run_results(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> ResultLinkPublishResponse:
    """Publish the final standing of a run as a public link, or publish it again.

    Open to coordinators, creators and organization admins. The run must have
    ended and every submission must be scored, or the route answers 409.

    The link shows a frozen copy of the standing. Publishing again replaces the
    copy, keeps the link address and restarts the expiry. Publishing after a
    revoke gives a new address, and the revoked one stays dead.

    The route adds a notice to the feed of the run that the result is
    published, in place of earlier result notices. It also removes the interim
    notice that the run end added, for good. Open pages read the feed again.

    Emits the audit event `run.results_published`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)
    # The run's row is locked before the run, its grading state, its active link
    # and the scoreboard are read, and the lock holds until the link, its snapshot
    # and the notice are committed together. A second publish, a withdrawal, the
    # end of the run and every act that dissolves a team take the same lock and
    # wait here, so each of them sees the feed, the link and the teams as the one
    # before left them. It is the first row lock of this transaction: see the lock
    # inventory in ``services/team_dissolution.py`` and RunRepository.lock_row.
    await run_repo.lock_row(run_id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state != RunState.ENDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Results can only be published once the run has ended.",
        )

    unscored = await SubmissionRepository(db, org.id).count_unscored_by_run(run.id)
    if unscored != 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Results can only be published once grading is complete.",
        )

    snapshot = await _build_results_snapshot(db, org, run)

    link_repo = RunResultLinkRepository(db)
    link, slug = await link_repo.publish(
        run_id=run.id,
        published_by=current_user.id,
        snapshot=snapshot,
        expiry_days=settings.retention.result_link_days,
    )

    # The teams learn of the published result in their feed, at every publish and
    # re-publish. The link itself lives in the "Rangliste" tab, which asks for it
    # while it is live, so a withdrawn link disappears there and the feed never
    # carries a dead one.
    from app.models.feed_event import FeedEventType
    from app.repositories.feed_event import FeedEventRepository
    from app.services.notify import notify_feed_event

    feed_repo = FeedEventRepository(db, org.id)
    await feed_repo.delete_result_link_notices(run.id)
    # The interim run-end notice ("grading is still under way") is false from here
    # on and leaves for good: a withdrawal does not bring it back.
    await feed_repo.delete_interim_result_notices(run.id)
    published_event = await feed_repo.create(
        organization_id=org.id,
        run_id=run.id,
        event_type=FeedEventType.RESULTS_AVAILABLE,
        content={
            "de": "Das Endergebnis ist veröffentlicht. Den Link findet ihr in der Rangliste.",
            "en": "The final result is published. You find the link in the leaderboard.",
            "result_link": True,
        },
        author_id=None,
    )
    # Link and notice in one transaction: a failure leaves neither. The commit
    # releases the run lock.
    await db.commit()

    record_audit_event(
        event="run.results_published",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "run_result_link", "id": str(link.id), "run_id": str(run_id)},
        organization_id=org.id,
    )

    await notify_feed_event(db, published_event)
    await db.commit()
    # Notices were removed, not only added: an open page reads its feed again.
    await ws_manager.broadcast_feed_changed(run.id)

    return ResultLinkPublishResponse(
        url=_result_link_url(slug),
        slug=slug,
        published_at=link.published_at,
        expires_at=link.expires_at,
    )


@router.delete(
    "/{run_id}/results/publish",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Result link revoked"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found, or no published result link", "model": ErrorResponse},
    },
)
async def revoke_run_results(
    run_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> None:
    """Revoke the public result link of a run.

    Open to coordinators, creators and organization admins. A run without a
    published link gives 404. The public link answers 410 from then on. The
    link row stays, marked as revoked, until the retention sweep deletes it.
    The notice that the result is published leaves the feed of the run, and
    open pages read the feed again.

    Emits the audit event `run.results_revoked`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)
    # The same lock as the publish, taken before the active link is read and held
    # until the revoked link and the removed notice are committed: a publish either
    # completes before this reads the link or starts after it has committed. Lock
    # order: RunRepository.lock_row.
    await run_repo.lock_row(run_id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    link_repo = RunResultLinkRepository(db)
    link = await link_repo.get_active_by_run_id(run_id)
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published result link for this run",
        )

    await link_repo.revoke(link)
    # The "result is published" notice goes with the link.
    from app.repositories.feed_event import FeedEventRepository

    await FeedEventRepository(db, org.id).delete_result_link_notices(run_id)
    await db.commit()

    record_audit_event(
        event="run.results_revoked",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "run_result_link", "id": str(link.id), "run_id": str(run_id)},
        organization_id=org.id,
    )

    # The withdrawal adds nothing to the feed, so no feed event tells an open page
    # that the notice is gone. This does.
    await ws_manager.broadcast_feed_changed(run_id)


@router.get(
    "/{run_id}/results/my-link",
    response_model=ResultLinkStatusResponse,
    responses={
        200: {"description": "The live public result link for a member of the run, or unpublished"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Not a member of a team in this run", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def get_my_run_results_link(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> ResultLinkStatusResponse:
    """Return the public result link of a run to the members of its teams.

    Open to every signed-in role, guests included, but only to a member of a
    team in this run (403 otherwise). The response has the status `published`
    with the link and its expiry while the link is live: published, not
    revoked and not expired. In every other case it has the status
    `unpublished`.
    """
    run = await RunRepository(db, organization_id=org.id).get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    membership = await TeamMemberRepository(db, organization_id=org.id).get_by_user_and_run(
        user_id=current_user.id, run_id=run_id
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of a team in this run")

    active = await RunResultLinkRepository(db).get_active_by_run_id(run_id)
    if active is None or (active.expires_at is not None and active.expires_at <= datetime.now(UTC)):
        return ResultLinkStatusResponse(status="unpublished")
    return ResultLinkStatusResponse(
        status="published",
        url=_result_link_url(compute_slug(active.id)),
        published_at=active.published_at,
        expires_at=active.expires_at,
    )


@router.get(
    "/{run_id}/results/link",
    response_model=ResultLinkStatusResponse,
    responses={
        200: {"description": "Current status of the run's result link"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def get_run_results_link(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> ResultLinkStatusResponse:
    """Return the status of the public result link of a run.

    Open to coordinators, creators and organization admins. The status is one
    of three values. `unpublished` means that no link was ever published for
    the run. `published` comes with the link and its expiry. `revoked` means
    that the link was revoked and not published again since.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    link_repo = RunResultLinkRepository(db)
    active = await link_repo.get_active_by_run_id(run_id)
    if active is not None:
        slug = compute_slug(active.id)
        return ResultLinkStatusResponse(
            status="published",
            url=_result_link_url(slug),
            published_at=active.published_at,
            expires_at=active.expires_at,
        )

    latest = await link_repo.get_latest_by_run_id(run_id)
    if latest is not None:
        return ResultLinkStatusResponse(status="revoked")

    return ResultLinkStatusResponse(status="unpublished")
