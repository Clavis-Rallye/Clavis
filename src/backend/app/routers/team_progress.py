"""Per-team progress endpoint.

Aggregates submissions and scores per team for a single run so the
RunDashboard team-overview tab can show progress columns.

Mounted at GET /api/orgs/{slug}/runs/{run_id}/team-progress.

Returns a payload shaped like ScoreboardResponse, but with extra
fields:
  - stations_completed / stations_total
  - challenges_submitted / challenges_total
  - challenges_scored
  - total_points / total_max_points
  - member_count
  - requested_help (a coordinator-help request is pending: the assist
    window is open and no coordinator has written into it yet)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.database import get_db
from app.models.organization import Organization
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.repositories.run import RunRepository
from app.routers.schemas import (
    ErrorResponse,
    TeamProgressEntry,
    TeamProgressResponse,
)

router = APIRouter()


@router.get(
    "/{run_id}/team-progress",
    response_model=TeamProgressResponse,
    responses={
        200: {"description": "Per-team progress aggregates"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def get_team_progress(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> TeamProgressResponse:
    """Return the progress of every team of a run.

    Open to coordinators and above. Participants read the scoreboard instead. For
    each team the response gives the member count, the stations completed and the
    challenges submitted and scored, with the totals of the run, the points
    against the maximum, and whether the team waits for a coordinator. A station
    counts as completed when the team has submitted every challenge in it. Teams
    are sorted by name. A team without a name yet comes last. A run without teams
    returns an empty list. An unknown run answers 404.
    """
    run_repo = RunRepository(db, organization_id=org.id)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    # Run-wide totals, the same for every team: stations and challenges in the
    # frozen run snapshots.
    stations_total = (
        await db.execute(select(func.count(RunStation.id)).where(RunStation.run_id == run_id))
    ).scalar_one()
    challenges_total = (
        await db.execute(
            select(func.count(RunChallenge.id))
            .join(RunStation, RunChallenge.run_station_id == RunStation.id)
            .where(RunStation.run_id == run_id)
        )
    ).scalar_one()

    teams_rows = (
        await db.execute(
            select(
                Team.id,
                Team.name,
                Team.coordinator_chat_opened_at,
                Team.coordinator_joined_notified_at,
                func.count(TeamMember.id).label("member_count"),
            )
            .outerjoin(TeamMember, TeamMember.team_id == Team.id)
            .where(Team.run_id == run_id, Team.is_deleted == False)  # noqa: E712
            .group_by(
                Team.id,
                Team.name,
                Team.coordinator_chat_opened_at,
                Team.coordinator_joined_notified_at,
            )
            # Name-pending teams (NULL) sort to the end, so they do not bubble to the
            # top of the dashboard before the creator commits.
            .order_by(Team.name.asc().nulls_last())
        )
    ).all()

    if not teams_rows:
        return TeamProgressResponse(run_id=run_id, teams=[])

    team_ids = [row.id for row in teams_rows]

    # Submissions per team: distinct run_challenge_id per team.
    sub_rows = (
        await db.execute(
            select(
                Submission.team_id,
                func.count(func.distinct(Submission.run_challenge_id)).label("submitted"),
            )
            .where(Submission.team_id.in_(team_ids))
            .group_by(Submission.team_id)
        )
    ).all()
    submitted_by_team: dict[UUID, int] = {row.team_id: row.submitted for row in sub_rows}

    score_rows = (
        await db.execute(
            select(
                Submission.team_id,
                func.coalesce(func.sum(Score.points), 0).label("points"),
                func.coalesce(func.sum(Score.max_points), 0).label("max_points"),
                func.count(Score.id).label("scored"),
            )
            .join(Submission, Score.submission_id == Submission.id)
            .where(Submission.team_id.in_(team_ids))
            .group_by(Submission.team_id)
        )
    ).all()
    scores_by_team: dict[UUID, dict[str, int]] = {
        row.team_id: {
            "points": int(row.points or 0),
            "max_points": int(row.max_points or 0),
            "scored": int(row.scored or 0),
        }
        for row in score_rows
    }

    # ----------------------------------------------------------------
    # Stations completed per team — a station is "completed" when every
    # challenge in it has at least one submission from that team.
    # We compute this in Python: for each team, walk station→challenges
    # and check the submission set against {challenge.id}.
    # ----------------------------------------------------------------
    challenges_by_station: dict[UUID, list[UUID]] = {}
    rs_rows = (
        await db.execute(
            select(RunChallenge.id, RunChallenge.run_station_id)
            .join(RunStation, RunChallenge.run_station_id == RunStation.id)
            .where(RunStation.run_id == run_id)
        )
    ).all()
    for ch_id, st_id in rs_rows:
        challenges_by_station.setdefault(st_id, []).append(ch_id)

    submitted_ids_by_team: dict[UUID, set[UUID]] = {}
    sub_id_rows = (
        await db.execute(
            select(Submission.team_id, Submission.run_challenge_id).where(Submission.team_id.in_(team_ids))
        )
    ).all()
    for tid, ch_id in sub_id_rows:
        submitted_ids_by_team.setdefault(tid, set()).add(ch_id)

    def _stations_completed(team_id: UUID) -> int:
        sub_set = submitted_ids_by_team.get(team_id, set())
        n = 0
        for chs in challenges_by_station.values():
            if chs and all(c in sub_set for c in chs):
                n += 1
        return n

    entries = [
        TeamProgressEntry(
            team_id=row.id,
            team_name=row.name,
            member_count=int(row.member_count or 0),
            stations_completed=_stations_completed(row.id),
            stations_total=int(stations_total or 0),
            challenges_submitted=int(submitted_by_team.get(row.id, 0)),
            challenges_total=int(challenges_total or 0),
            challenges_scored=int(scores_by_team.get(row.id, {}).get("scored", 0)),
            total_points=int(scores_by_team.get(row.id, {}).get("points", 0)),
            total_max_points=int(scores_by_team.get(row.id, {}).get("max_points", 0)),
            requested_help=(row.coordinator_chat_opened_at is not None and row.coordinator_joined_notified_at is None),
        )
        for row in teams_rows
    ]

    return TeamProgressResponse(run_id=run_id, teams=entries)
