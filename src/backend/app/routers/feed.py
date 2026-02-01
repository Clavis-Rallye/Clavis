"""
Feed event endpoints.

Provides endpoints for listing feed events and creating coordinator announcements.

All endpoints require authentication and enforce role-based access control:
- GET /: guest+ can view feed events for the run; who reads what is the rule
  in list_feed_events
- POST /: coordinator+ can create announcements

All modification operations emit audit events for security tracking.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.client_address import client_address
from app.database import get_db
from app.middleware.rate_limit import limiter
from app.models.feed_event import FeedEventType
from app.models.organization import Organization
from app.models.user import User
from app.repositories.feed_event import FeedEventRepository
from app.repositories.run import RunRepository
from app.routers.schemas import (
    ErrorResponse,
    FeedEventCreateRequest,
    FeedEventListResponse,
    FeedEventResponse,
)
from app.services.audit import actor_from_user, record_audit_event
from app.services.notify import notify_feed_event

router = APIRouter()


@router.get(
    "/",
    response_model=FeedEventListResponse,
    responses={
        200: {"description": "List of feed events"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied, or a guest without a team in this run", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def list_feed_events(
    run_id: UUID,
    _request: Request,
    skip: int = Query(0, ge=0, description="Number of items to skip (offset)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum items to return"),
    latest: bool = Query(
        False,
        description="Return the most recent events instead of the first ones (still oldest-first)",
    ),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> FeedEventListResponse:
    """List the feed events of a run, oldest first, one page at a time.

    Open to every signed-in role, guests included. What a caller reads depends
    on the role and on the team:
    - Coordinators and above read every event of the run.
    - A member of a team reads the broadcasts and the events for that team.
    - A registered participant without a team in the run reads the broadcasts
      only. This includes a former member, who sees nothing of the team that
      was theirs. A participant who is about to join a team needs this read
      before the team exists.
    - A guest without a team in the run gets 403. A guest account belongs to
      one run and is created together with its team membership, so such a
      guest left the team, was removed from it, or belongs to another run.

    The run's WebSocket follows the same rule.

    Returns 404 when the run is not found in the organization.
    """
    run_repo = RunRepository(db, org.id)
    run = await run_repo.get_by_id(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # Resolve the viewer's team membership for this run so target_team_id
    # filtering is honoured. Coordinators+ pass None and see everything.
    viewer_team_id: UUID | None = None
    is_participant = current_user.role.value in ("guest", "participant")
    if is_participant:
        from app.models.team import Team
        from app.models.team_member import TeamMember

        result = await db.execute(
            select(Team.id)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id == current_user.id, Team.run_id == run_id)
        )
        viewer_team_id = result.scalar_one_or_none()

        # A guest reads a run's feed only as a member of a team in it
        if current_user.role.value == "guest" and viewer_team_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guests read the feed of a run only as a member of a team in it.",
            )

    feed_repo = FeedEventRepository(db, org.id)
    events = await feed_repo.list_by_run(
        run_id=run_id,
        skip=skip,
        limit=limit,
        viewer_team_id=viewer_team_id,
        latest=latest,
        # Without a team in this run a participant reads broadcasts only.
        broadcast_only=is_participant and viewer_team_id is None,
    )

    total = await feed_repo.count_by_run(run_id=run_id)

    return FeedEventListResponse(
        items=events,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/",
    response_model=FeedEventResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Feed event created"},
        400: {"description": "Invalid request", "model": ErrorResponse},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
@limiter.limit("10/minute")
async def create_announcement(
    run_id: UUID,
    request: Request,
    body: FeedEventCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> FeedEventResponse:
    """Create a feed event in a run and push it to the run's feed.

    Open to coordinators and above. The event type must be one of
    announcement, countdown, reminder, alert or team_update. The run itself
    creates the system, milestone, results_available and feedback_prompt
    events, so a request for one of these types is refused with 400. An unknown
    type answers 422.

    The content is a JSON object, typically one text per language, for
    example `{"de": "...", "en": "..."}`. A countdown must carry a
    `target_time` key with an ISO 8601 datetime string, otherwise the route
    answers 422.

    Without `target_team_id` the event reaches every reader of the feed. With
    it, only that team and the staff receive the event. A dissolved team answers
    404. A run that has ended refuses new events with 422. The route emits the
    audit event `feed_event.announcement_created` and is limited to 10 requests
    per minute.
    """
    run_repo = RunRepository(db, org.id)
    run = await run_repo.get_by_id(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # An entry aimed at a dissolved team is refused in every run state, before the
    # state and content checks below answer.
    if body.target_team_id is not None:
        from app.models.team import Team

        dissolved = await db.execute(
            select(Team.id).where(
                Team.id == body.target_team_id,
                Team.organization_id == org.id,
                Team.is_deleted == True,  # noqa: E712
            )
        )
        if dissolved.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found",
            )

    if run.state == "ended":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cannot create feed events for ended run. Run state is terminal.",
        )

    coordinator_creatable_types = {
        FeedEventType.ANNOUNCEMENT,
        FeedEventType.COUNTDOWN,
        FeedEventType.REMINDER,
        FeedEventType.ALERT,
        FeedEventType.TEAM_UPDATE,
    }

    # The run state machine creates these events itself. A coordinator cannot.
    system_only_types = {
        FeedEventType.SYSTEM,
        FeedEventType.MILESTONE,
        FeedEventType.RESULTS_AVAILABLE,
        FeedEventType.FEEDBACK_PROMPT,
    }

    if body.event_type in system_only_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Event type '{body.event_type}' cannot be created manually. System and milestone events are auto-generated.",
        )

    if body.event_type not in coordinator_creatable_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid event type '{body.event_type}'. Must be one of: {', '.join(t.value for t in coordinator_creatable_types)}",
        )

    if body.event_type == FeedEventType.COUNTDOWN:
        if "target_time" not in body.content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="COUNTDOWN event requires 'target_time' key in content",
            )

        target_time = body.content.get("target_time")
        if not isinstance(target_time, str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="'target_time' must be a string (ISO 8601 datetime)",
            )

        try:
            datetime.fromisoformat(target_time.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="'target_time' must be a valid ISO 8601 datetime string",
            ) from None

    feed_repo = FeedEventRepository(db, org.id)
    event = await feed_repo.create(
        run_id=run_id,
        event_type=body.event_type,
        content=body.content,
        author_id=current_user.id,
        target_team_id=body.target_team_id,
    )
    await db.commit()
    await db.refresh(event)

    await notify_feed_event(db, event)
    await db.commit()  # Commit the NOTIFY transaction

    client_ip = client_address(request)
    record_audit_event(
        event="feed_event.announcement_created",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "feed_event",
            "id": str(event.id),
            "run_id": str(run_id),
            "event_type": body.event_type.value,
        },
        changes={"content_preview": str(body.content)[:100]},
        organization_id=org.id,
    )

    return FeedEventResponse.model_validate(event)
