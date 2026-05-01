"""Current-user endpoints scoped to /api/me/*.

/api/me/run-history for the participant Verlauf page, and /api/me/export for
GDPR Art. 20 data portability.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_role
from app.client_address import client_address
from app.database import get_db
from app.models.run import Run
from app.models.run_challenge import RunChallenge
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.routers.schemas import (
    DataExportAccount,
    DataExportParticipation,
    DataExportResponse,
    DataExportScore,
    DataExportSubmission,
    RunHistoryEntry,
    RunHistoryResponse,
)
from app.services.audit import actor_from_user, record_audit_event

router = APIRouter()


@router.get("/run-history", response_model=RunHistoryResponse)
async def get_my_run_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("guest")),
) -> RunHistoryResponse:
    """List the runs in which the caller was a team member, newest run first.

    Open to every signed-in role, guests included. A guest sees only the run of
    the guest account. Each entry holds the run, the team and the points of the
    team. The placement and the number of teams are set only when the run has
    ended and every answer of the run is graded. Teams with equal points share a
    place, and a removed team takes no place.
    """
    rows: Sequence[Any] = (
        await db.execute(
            select(
                TeamMember.team_id,
                Team.name.label("team_name"),
                Team.run_id,
                Run.name.label("run_name"),
                Run.state,
                Run.started_at,
                Run.ended_at,
                Run.created_at,
            )
            .join(Team, TeamMember.team_id == Team.id)
            .join(Run, Team.run_id == Run.id)
            .where(TeamMember.user_id == current_user.id)
            .order_by(desc(Run.started_at), desc(Run.created_at))
        )
    ).all()

    if not rows:
        return RunHistoryResponse(items=[])

    team_ids = [row.team_id for row in rows]
    run_ids = [row.run_id for row in rows]

    score_rows = (
        await db.execute(
            select(
                Submission.team_id,
                func.coalesce(func.sum(Score.points), 0).label("points"),
            )
            .join(Submission, Score.submission_id == Submission.id)
            .where(Submission.team_id.in_(team_ids))
            .group_by(Submission.team_id)
        )
    ).all()
    points_by_team: dict[UUID, int] = {row.team_id: int(row.points or 0) for row in score_rows}

    # Points per team of each run, for the placement and total_teams. It is the
    # scoreboard's join of submission, score and team, grouped by team.
    sb_rows = (
        await db.execute(
            select(
                Team.run_id,
                # The team's own id, not the id on its submissions: a team that sent nothing
                # has no submission row, so the id would be NULL and the team would get no
                # placement, shown as "?" in the history.
                Team.id.label("team_id"),
                func.coalesce(func.sum(Score.points), 0).label("points"),
            )
            .join(Submission, Submission.team_id == Team.id, isouter=True)
            .join(Score, Score.submission_id == Submission.id, isouter=True)
            .where(Team.run_id.in_(run_ids))
            # As on the scoreboard: a removed team takes no place.
            .where(Team.is_deleted == False)  # noqa: E712
            .group_by(Team.run_id, Team.id)
        )
    ).all()

    # A place is final only when the run has ended and every answer is graded,
    # the rule of the scoreboard's `is_final`. Before that the history shows
    # the run's state, not "Platz 1 von 2" for a run still in play.
    unscored_rows = (
        await db.execute(
            select(Submission.run_id, func.count(Submission.id))
            .outerjoin(Score, Score.submission_id == Submission.id)
            .join(Team, Team.id == Submission.team_id)
            .where(Submission.run_id.in_(run_ids))
            .where(Score.id.is_(None))
            .where(Team.is_deleted == False)  # noqa: E712
            .group_by(Submission.run_id)
        )
    ).all()
    runs_with_open_grading = {run_id for run_id, count in unscored_rows if count}

    # Index by run: sort teams desc by points → 1-indexed placement
    placements: dict[tuple[UUID, UUID], tuple[int | None, int]] = {}
    teams_per_run: dict[UUID, int] = {}
    grouped: dict[UUID, list[tuple[UUID, int]]] = {}
    for run_id, team_id, pts in sb_rows:
        if team_id is None:
            continue
        grouped.setdefault(run_id, []).append((team_id, int(pts or 0)))
    for run_id, lst in grouped.items():
        lst.sort(key=lambda x: -x[1])
        teams_per_run[run_id] = len(lst)
        # Teams with the same points share a place, as on the scoreboard:
        # 30, 5, 5 reads 1, 2, 2. Ranking by index alone would give the second of two
        # equal teams a worse place than the first.
        rank = 0
        previous_points: int | None = None
        for idx, (team_id, pts) in enumerate(lst):
            if pts != previous_points:
                rank = idx + 1
                previous_points = pts
            placements[(run_id, team_id)] = (rank, len(lst))

    items = []
    for row in rows:
        state = row.state.value if hasattr(row.state, "value") else row.state
        is_final = state == "ended" and row.run_id not in runs_with_open_grading
        place, total = placements.get((row.run_id, row.team_id), (None, None)) if is_final else (None, None)
        items.append(
            RunHistoryEntry(
                run_id=row.run_id,
                run_name=row.run_name,
                rallye_title={},
                team_id=row.team_id,
                team_name=row.team_name,
                started_at=row.started_at,
                ended_at=row.ended_at,
                state=row.state,
                placement=place,
                total_teams=total,
                total_points=points_by_team.get(row.team_id, 0),
            )
        )

    return RunHistoryResponse(items=items)


@router.get("/export", response_model=DataExportResponse)
async def export_my_data(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("participant")),
) -> DataExportResponse:
    """Download the caller's own data as a JSON file, as GDPR Article 20 requires.

    Open to registered users. Guests have no persistent account and get 403. The
    file holds the account fields, the team memberships with their runs, and the
    submissions of the caller's teams with their scores. It does not embed media
    files. The content of a submission refers to them by storage reference. The
    route emits the audit event `data_export_requested`.
    """
    # Participation history — every team membership → its run.
    part_rows: Sequence[Any] = (
        await db.execute(
            select(
                TeamMember.team_id,
                TeamMember.joined_at,
                Team.name.label("team_name"),
                Team.run_id,
                Run.name.label("run_name"),
                Run.state,
                Run.started_at,
                Run.ended_at,
            )
            .join(Team, TeamMember.team_id == Team.id)
            .join(Run, Team.run_id == Run.id)
            .where(TeamMember.user_id == current_user.id)
            .order_by(desc(Run.started_at), desc(Run.created_at))
        )
    ).all()

    team_ids = [row.team_id for row in part_rows]

    # Submissions by the caller's teams (+ challenge type and score, if any).
    sub_rows: Sequence[Any] = []
    if team_ids:
        sub_rows = (
            await db.execute(
                select(
                    Submission.id,
                    Submission.run_id,
                    Submission.run_challenge_id,
                    RunChallenge.challenge_type,
                    Submission.submitted_at,
                    Submission.content,
                    Score.points,
                    Score.max_points,
                )
                .join(RunChallenge, Submission.run_challenge_id == RunChallenge.id)
                .join(Score, Score.submission_id == Submission.id, isouter=True)
                .where(Submission.team_id.in_(team_ids))
                .order_by(desc(Submission.submitted_at))
            )
        ).all()

    account = DataExportAccount(
        user_id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        role=current_user.role.value,
        organization_id=current_user.organization_id,
        created_at=current_user.created_at,
        last_login=current_user.last_login,
    )
    participations = [
        DataExportParticipation(
            run_id=row.run_id,
            run_name=row.run_name,
            run_state=row.state.value,
            team_id=row.team_id,
            team_name=row.team_name,
            joined_at=row.joined_at,
            run_started_at=row.started_at,
            run_ended_at=row.ended_at,
        )
        for row in part_rows
    ]
    submissions = [
        DataExportSubmission(
            submission_id=row.id,
            run_id=row.run_id,
            run_challenge_id=row.run_challenge_id,
            challenge_type=row.challenge_type.value,
            submitted_at=row.submitted_at,
            content=row.content or {},
            score=(DataExportScore(points=row.points, max_points=row.max_points) if row.points is not None else None),
        )
        for row in sub_rows
    ]

    record_audit_event(
        "data_export_requested",
        actor=actor_from_user(current_user, client_address(request)),
        organization_id=current_user.organization_id,
        exported_participations=len(participations),
        exported_submissions=len(submissions),
    )

    response.headers["Content-Disposition"] = f'attachment; filename="clavis-data-export-{current_user.id}.json"'
    return DataExportResponse(
        generated_at=datetime.now(UTC),
        account=account,
        participations=participations,
        submissions=submissions,
    )
