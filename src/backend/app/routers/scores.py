"""
Score endpoints (nested under runs).

Provides score management operations within a run context with RBAC enforcement,
organization scoping via URL slug, audit logging, and validation against challenge rules.

All endpoints require authentication and enforce role-based access control:
- Create score: coordinator+ can score submissions (validate points <= max_points, submission exists in run)
- Update score: coordinator+ can update points and/or comment
- Re-open (delete) score: coordinator+ can remove a score so the team can replace its submission
- List scores: coordinator+ can view all scores for a run

All endpoints are nested under runs: /api/orgs/{slug}/runs/{run_id}/scores/
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.client_address import client_address
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.repositories.run import RunRepository
from app.repositories.run_challenge import RunChallengeRepository
from app.repositories.score import ScoreRepository
from app.repositories.submission import SubmissionRepository
from app.routers.schemas import (
    ErrorResponse,
    ScoreboardEntry,
    ScoreboardResponse,
    ScoreCreateRequest,
    ScoreListResponse,
    ScoreResponse,
    ScoreUpdateRequest,
)
from app.services.audit import actor_from_user, record_audit_event

router = APIRouter()


@router.post(
    "/",
    response_model=ScoreResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Score created successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found or submission not found", "model": ErrorResponse},
        422: {
            "description": "Submission not in this run, submission already scored, or points exceed max_points",
            "model": ErrorResponse,
        },
    },
)
async def create_score(
    run_id: UUID,
    request: Request,
    body: ScoreCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> ScoreResponse:
    """Score a submission of a run.

    Open to coordinators and above. The route answers 422 when the submission is
    not in this run, when it has a score already, when the scoring rules of the
    challenge have no `max_points`, or when the points exceed that `max_points`.
    The score takes that `max_points` value. A submission of a dissolved team
    answers 404. It emits the audit event `score.created`.
    """
    client_ip = client_address(request)

    # The shared lock on the run row (``RunRepository.share_row``), before anything
    # is read. It is the first row lock of this path. The publish holds the other
    # lock of that row from before its count of the ungraded answers until its
    # result is committed, so this grading is committed before the publish
    # counts, or it starts after the publish has stored its standing: the publish
    # never decides "grading complete" on one state and builds the standing on
    # another. The end of the run reads its notice the same way. Gradings and
    # answers share the lock and do not wait for each other. A run that is gone
    # falls through to the 404 below.
    run_repo = RunRepository(db, organization_id=org.id)
    await run_repo.share_row(run_id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    submission_repo = SubmissionRepository(db, organization_id=org.id)
    submission = await submission_repo.get_by_id(body.submission_id)

    if submission is None or submission.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Submission not found in this run",
        )

    # An answer of a dissolved team is not graded.
    if await submission_repo.get_active_by_id(body.submission_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Submission not found",
        )

    # One score per submission.
    score_repo = ScoreRepository(db, organization_id=org.id)
    existing_score = await score_repo.get_by_submission(body.submission_id)

    if existing_score is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Submission has already been scored",
        )

    run_challenge_repo = RunChallengeRepository(db, organization_id=org.id)
    run_challenge = await run_challenge_repo.get_by_id(submission.run_challenge_id)

    if run_challenge is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge not found for this submission",
        )

    scoring_rules = run_challenge.scoring_rules or {}
    max_points = scoring_rules.get("max_points")

    if max_points is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Challenge scoring_rules does not contain max_points",
        )

    if body.points > max_points:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Points ({body.points}) cannot exceed max_points ({max_points})",
        )

    score = await score_repo.create(
        organization_id=org.id,
        submission_id=body.submission_id,
        points=body.points,
        max_points=max_points,
        comment=body.comment,
        scored_by=current_user.id,
    )
    await db.commit()
    await db.refresh(score)

    record_audit_event(
        event="score.created",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "score",
            "id": str(score.id),
            "submission_id": str(body.submission_id),
            "points": body.points,
            "max_points": max_points,
            "run_id": str(run_id),
        },
        organization_id=org.id,
    )

    return ScoreResponse(
        id=score.id,
        organization_id=score.organization_id,
        submission_id=score.submission_id,
        points=score.points,
        max_points=score.max_points,
        comment=score.comment,
        scored_by=score.scored_by,
        scored_at=score.scored_at,
        created_at=score.created_at,
        updated_at=score.updated_at,
    )


@router.put(
    "/{score_id}",
    response_model=ScoreResponse,
    responses={
        200: {"description": "Score updated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Score not found or not in this run", "model": ErrorResponse},
        422: {"description": "Points exceed max_points", "model": ErrorResponse},
    },
)
async def update_score(
    run_id: UUID,
    score_id: UUID,
    request: Request,
    body: ScoreUpdateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> ScoreResponse:
    """Change the points or the comment of a score.

    Open to coordinators and above. Fields that the request leaves out stay as
    they are. The points must not exceed the `max_points` of the score, otherwise
    the route answers 422. A score that is not in this run answers 404. It emits
    the audit event `score.updated`.
    """
    client_ip = client_address(request)

    # The shared lock on the run row, the first row lock of this path: see
    # ``create_score``.
    run_repo = RunRepository(db, organization_id=org.id)
    await run_repo.share_row(run_id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # A score belongs to a run through its submission.
    score_repo = ScoreRepository(db, organization_id=org.id)
    score = await score_repo.get_by_id(score_id)

    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Score not found",
        )

    submission_repo = SubmissionRepository(db, organization_id=org.id)
    submission = await submission_repo.get_active_by_id(score.submission_id)

    if submission is None or submission.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Score not found in this run",
        )

    if body.points is not None and body.points > score.max_points:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Points ({body.points}) cannot exceed max_points ({score.max_points})",
        )

    update_data: dict[str, int | str | None] = {}
    if body.points is not None:
        update_data["points"] = body.points
    if body.comment is not None:
        update_data["comment"] = body.comment

    if update_data:
        updated_score = await score_repo.update(score_id, **update_data)
        # Score must exist since we already verified it above
        assert updated_score is not None, "Score should exist"
        await db.commit()
        await db.refresh(updated_score)
        score = updated_score

    record_audit_event(
        event="score.updated",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "score",
            "id": str(score.id),
            "submission_id": str(score.submission_id),
            "updated_fields": list(update_data.keys()),
            "run_id": str(run_id),
        },
        organization_id=org.id,
    )

    return ScoreResponse(
        id=score.id,
        organization_id=score.organization_id,
        submission_id=score.submission_id,
        points=score.points,
        max_points=score.max_points,
        comment=score.comment,
        scored_by=score.scored_by,
        scored_at=score.scored_at,
        created_at=score.created_at,
        updated_at=score.updated_at,
    )


@router.delete(
    "/{score_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Score removed — submission re-opened for replacement"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found or score not found in this run", "model": ErrorResponse},
    },
)
async def reopen_score(
    run_id: UUID,
    score_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> None:
    """Re-open a graded submission by deleting its score.

    Open to coordinators and above. The team can then replace its submission for
    the challenge. The scoreboard counts the remaining scores. A score that is not
    in this run answers 404. It emits the audit event `score.reopened`.
    """
    client_ip = client_address(request)

    # The shared lock on the run row, the first row lock of this path: see
    # ``create_score``.
    run_repo = RunRepository(db, organization_id=org.id)
    await run_repo.share_row(run_id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # A score belongs to a run through its submission.
    score_repo = ScoreRepository(db, organization_id=org.id)
    score = await score_repo.get_by_id(score_id)

    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Score not found",
        )

    submission_repo = SubmissionRepository(db, organization_id=org.id)
    submission = await submission_repo.get_active_by_id(score.submission_id)

    if submission is None or submission.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Score not found in this run",
        )

    # Capture audit payload before the row is removed
    removed_points = score.points
    submission_id = score.submission_id
    team_id = submission.team_id

    deleted = await score_repo.delete(score_id)
    # Score must exist since we already verified it above
    assert deleted, "Score should exist"
    await db.commit()

    record_audit_event(
        event="score.reopened",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "score",
            "id": str(score_id),
            "submission_id": str(submission_id),
            "team_id": str(team_id),
            "run_id": str(run_id),
            "removed_points": removed_points,
        },
        organization_id=org.id,
    )


@router.get(
    "/",
    response_model=ScoreListResponse,
    responses={
        200: {"description": "List of scores in run"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def list_scores(
    run_id: UUID,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> ScoreListResponse:
    """List the scores of a run, the most recent first, one page at a time.

    Open to coordinators and above.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # Get scores via single JOIN query (no N+1)
    score_repo = ScoreRepository(db, organization_id=org.id)
    scores = await score_repo.list_by_run(run_id, skip=skip, limit=limit)
    total = await score_repo.count_by_run(run_id)

    score_responses = [
        ScoreResponse(
            id=score.id,
            organization_id=score.organization_id,
            submission_id=score.submission_id,
            points=score.points,
            max_points=score.max_points,
            comment=score.comment,
            scored_by=score.scored_by,
            scored_at=score.scored_at,
            created_at=score.created_at,
            updated_at=score.updated_at,
        )
        for score in scores
    ]

    return ScoreListResponse(
        items=score_responses,
        total=total,
    )


@router.get(
    "/scoreboard",
    response_model=ScoreboardResponse,
    responses={
        200: {"description": "Scoreboard for the run"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def get_scoreboard(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("guest")),
) -> ScoreboardResponse:
    """Return the teams of a run ranked by their total points.

    Open to every signed-in role, guests included. Teams with equal points share
    a rank and are listed by team name, then by team id. A team that was dissolved
    is not listed. `is_final` is true when the run has ended and every submission
    is scored. Only then a team with a rank of 1 and more than 0 points is marked
    as the winner.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    score_repo = ScoreRepository(db, organization_id=org.id)
    scoreboard_data = await score_repo.get_scoreboard(run_id)
    submission_repo = SubmissionRepository(db, organization_id=org.id)
    submission_count = await submission_repo.count_by_run(run_id)
    unscored_count = await submission_repo.count_unscored_by_run(run_id)
    is_final = run.state.value == "ended" and unscored_count == 0

    entries = []
    previous_points = None
    rank = 0
    for idx, entry in enumerate(scoreboard_data):
        if entry["total_points"] != previous_points:
            rank = idx + 1
            previous_points = entry["total_points"]
        entries.append(
            ScoreboardEntry(
                team_id=UUID(entry["team_id"]),
                team_name=entry["team_name"],
                total_points=entry["total_points"],
                total_max_points=entry["total_max_points"],
                scored_count=entry["scored_count"],
                last_scored_at=entry["last_scored_at"],
                rank=rank,
                # Nobody wins with 0 points.
                is_winner=is_final and rank == 1 and entry["total_points"] > 0,
            )
        )

    return ScoreboardResponse(
        run_id=run_id,
        entries=entries,
        is_final=is_final,
        scored_count=submission_count - unscored_count,
        submission_count=submission_count,
    )
