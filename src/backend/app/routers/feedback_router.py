"""
Feedback endpoints (nested under runs).

Provides anonymous feedback operations after a run ends, with RBAC enforcement
for viewing feedback (coordinator+), organization scoping via URL slug, and audit logging.

All endpoints are nested under runs: /api/orgs/{slug}/runs/{run_id}/feedback/

Endpoints:
- POST / — Submit feedback (anyone, run must be ended): create Feedback with rating and optional comment, NO user_id
- GET / — List feedback for run (coordinator+): paginated list of all feedback
- GET /summary — Feedback summary (coordinator+): average rating and distribution

IMPORTANT: Feedback is ANONYMOUS by design. There is NO user_id field. Anyone can submit
feedback after a run ends, and the submission is not linked to any user identity.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.client_address import client_address
from app.database import get_db
from app.middleware.rate_limit import limiter
from app.models.organization import Organization
from app.models.run import RunState
from app.models.user import User
from app.repositories.feedback import FeedbackRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.run import RunRepository
from app.routers.schemas import (
    ErrorResponse,
    FeedbackCreateRequest,
    FeedbackListResponse,
    FeedbackResponse,
    FeedbackSummaryResponse,
)
from app.services.audit import record_audit_event

router = APIRouter()


async def _get_org_from_slug_anonymous(
    slug: str = Path(..., description="Organization slug from URL path"),
    db: AsyncSession = Depends(get_db),
) -> Organization:
    """Resolve the organization from the URL slug, without authentication.

    Anonymous endpoints, such as the feedback submission, need the organization but
    no user. Raises 404 when the organization does not exist.
    """
    org_repo = OrganizationRepository(db)
    org = await org_repo.get_by_slug(slug)

    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return org


async def _verify_run_ended(
    db: AsyncSession,
    org_id: UUID,
    run_id: UUID,
) -> None:
    """Check that the run exists and has ended, the condition for a feedback.

    Raises 404 when the run does not exist and 400 when it has not ended.
    """
    run_repo = RunRepository(db, organization_id=org_id)
    run = await run_repo.get_by_id(run_id)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state != RunState.ENDED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Feedback can only be submitted after the run has ended. Current state: {run.state.value}",
        )


# Anonymous feedback per client address, run and hour.
FEEDBACK_LIMIT_PER_HOUR = 300


def _feedback_key(request: Request) -> str:
    """Client address and run: one sender's count per run, without storing who it was."""
    return f"{client_address(request)}:{request.path_params.get('run_id')}"


@router.post(
    "/",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Feedback submitted successfully"},
        400: {"description": "Run is not ended", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        429: {"description": "Too many feedback submissions from this address for this run"},
    },
)
# The browser keeps one feedback per device, but the endpoint is anonymous,
# so a script could send any number. The limit counts per client address and
# run, held in memory only. At a meeting point every phone shares one address,
# and 200 phones must fit, so the limit is 300. That still caps a script at
# 300 per hour and run.
@limiter.limit(f"{FEEDBACK_LIMIT_PER_HOUR}/hour", key_func=_feedback_key)
async def submit_feedback(
    run_id: UUID,
    request: Request,
    body: FeedbackCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(_get_org_from_slug_anonymous),
) -> FeedbackResponse:
    """Submit anonymous feedback, a rating and an optional comment, for an ended run.

    Open to anyone without sign-in. The feedback stores no user. Only the audit
    event `feedback.submitted` records the client address. A run that has not
    ended answers 400, an unknown organization or run 404. One client address may
    send up to 300 feedbacks per run and hour, then the route answers 429.
    """
    client_ip = client_address(request)

    await _verify_run_ended(db, org.id, run_id)

    # No user_id: the feedback is anonymous.
    feedback_repo = FeedbackRepository(db, organization_id=org.id)
    feedback = await feedback_repo.create(
        organization_id=org.id,
        run_id=run_id,
        rating=body.rating,
        comment=body.comment,
    )
    await db.commit()
    await db.refresh(feedback)

    record_audit_event(
        event="feedback.submitted",
        actor={"ip": client_ip},  # No user_id — anonymous
        target={
            "entity": "feedback",
            "id": str(feedback.id),
            "run_id": str(run_id),
            "rating": feedback.rating,
        },
        organization_id=org.id,
    )

    return FeedbackResponse(
        id=feedback.id,
        run_id=feedback.run_id,
        rating=feedback.rating,
        comment=feedback.comment,
        created_at=feedback.created_at,
    )


@router.get(
    "/",
    response_model=FeedbackListResponse,
    responses={
        200: {"description": "List of feedback entries"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (requires coordinator+ role)", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def list_feedback(
    run_id: UUID,
    skip: int = Query(0, ge=0, description="Number of feedback entries to skip (offset)"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of feedback entries to return"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> FeedbackListResponse:
    """List the feedback of a run, newest first, one page at a time.

    Open to coordinators and above. An entry holds the rating, the comment and
    the time, and nothing that names the sender. The total counts at most 10000
    entries.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    feedback_repo = FeedbackRepository(db, organization_id=org.id)
    feedback_list = await feedback_repo.list_by_run(run_id, skip=skip, limit=limit)

    # The total comes from a second read, because list_by_run applies limit and offset.
    # It reads at most 10000 entries. A count query would be cheaper.
    all_feedback = await feedback_repo.list_by_run(run_id, skip=0, limit=10000)
    total = len(all_feedback)

    return FeedbackListResponse(
        items=[
            FeedbackResponse(
                id=fb.id,
                run_id=fb.run_id,
                rating=fb.rating,
                comment=fb.comment,
                created_at=fb.created_at,
            )
            for fb in feedback_list
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/summary",
    response_model=FeedbackSummaryResponse,
    responses={
        200: {"description": "Feedback summary statistics"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (requires coordinator+ role)", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def get_feedback_summary(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> FeedbackSummaryResponse:
    """Return the average rating, the number of feedbacks and the count per rating of a run.

    Open to coordinators and above. The average is 0.0 when the run has no
    feedback. The distribution lists only the ratings that were given, so it
    is empty when there is no feedback.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    feedback_repo = FeedbackRepository(db, organization_id=org.id)
    summary = await feedback_repo.get_run_summary(run_id)

    return FeedbackSummaryResponse(
        average_rating=summary["average_rating"] if summary["average_rating"] is not None else 0.0,
        total_count=summary["count"],
        distribution=summary["rating_distribution"],
    )
