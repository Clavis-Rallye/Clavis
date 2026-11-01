"""
Run comparison: two to six runs side by side.

Read-only, coordinator and above, mounted at /api/orgs/{slug}/analytics/compare.

Privacy boundary, as in analytics.py: team level only. No response field holds
a person's name, an e-mail address or a user id.

Query count: constant for any number of runs and teams. Runs, stations and
tasks, members and the answers are each read once for all chosen runs.

How rows match over runs. A run works on a frozen copy of its stations and
tasks. Two copies stand in one row when they come from the same library
station or task. A copy without a library source is matched by its position,
and only within one rallye. A run that does not hold a row has no cell in it.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import Row, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.database import get_db
from app.models.organization import Organization
from app.models.run import Run
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.routers.analytics import (
    _CSV_TYPE_LABELS,
    _csv_language,
    _display_title,
    _spreadsheet_csv,
    minutes,
    working_time_of,
)
from app.routers.schemas import (
    AnalyticsCompareResponse,
    AnalyticsCompareRun,
    AnalyticsCompareStation,
    AnalyticsCompareStationCell,
    AnalyticsCompareTask,
    AnalyticsCompareTaskCell,
    ErrorResponse,
)
from app.services.analytics_time import fetch_run_times, median_or_none, spread

compare_router = APIRouter()

# The state names of the run list (runs:list.filter in the frontend).
_STATE_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "configured": "Konfiguriert",
        "scheduled": "Geplant",
        "active": "Aktiv",
        "paused": "Pausiert",
        "ended": "Beendet",
    },
    "en": {
        "configured": "Configured",
        "scheduled": "Scheduled",
        "active": "Active",
        "paused": "Paused",
        "ended": "Ended",
    },
}

MIN_COMPARE_RUNS = 2
MAX_COMPARE_RUNS = 6


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _unique_run_ids(run_ids: list[UUID]) -> list[UUID]:
    ordered = list(dict.fromkeys(run_ids))
    if not MIN_COMPARE_RUNS <= len(ordered) <= MAX_COMPARE_RUNS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Choose {MIN_COMPARE_RUNS} to {MAX_COMPARE_RUNS} different runs to compare.",
        )
    return ordered


async def build_comparison(db: AsyncSession, org_id: UUID, run_ids: list[UUID]) -> AnalyticsCompareResponse:
    """The comparison document for the given runs, in the order of ``run_ids``."""
    run_rows = (
        await db.execute(
            select(
                Run.id,
                Run.rallye_id,
                Run.name,
                Run.state,
                Run.started_at,
                Run.ended_at,
                Run.scheduled_for,
                Run.created_at,
            ).where(
                Run.id.in_(run_ids),
                Run.organization_id == org_id,
                Run.is_deleted == False,  # noqa: E712
            )
        )
    ).all()
    runs_by_id = {row.id: row for row in run_rows}
    if len(runs_by_id) != len(run_ids):
        # A run of another organization reads like a run that does not exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    member_rows = (
        await db.execute(
            select(
                Team.run_id.label("run_id"),
                func.count(func.distinct(Team.id)).label("team_count"),
                func.count(TeamMember.id).label("member_count"),
                func.count(case((TeamMember.is_guest.is_(True), TeamMember.id))).label("guest_count"),
            )
            .select_from(Team)
            .outerjoin(TeamMember, TeamMember.team_id == Team.id)
            .where(Team.organization_id == org_id, Team.run_id.in_(run_ids), Team.is_deleted == False)  # noqa: E712
            .group_by(Team.run_id)
        )
    ).all()
    members_by_run = {row.run_id: row for row in member_rows}

    task_rows = (
        await db.execute(
            select(
                RunChallenge.id,
                RunChallenge.run_id,
                RunChallenge.source_challenge_id,
                RunChallenge.title,
                RunChallenge.challenge_type,
                RunChallenge.scoring_rules,
                RunChallenge.sort_order,
                RunStation.id.label("station_id"),
                RunStation.run_id.label("station_run_id"),
                RunStation.source_station_id,
                RunStation.title.label("station_title"),
                RunStation.sort_order.label("station_sort_order"),
            )
            .select_from(RunStation)
            .outerjoin(RunChallenge, RunChallenge.run_station_id == RunStation.id)
            .where(RunStation.organization_id == org_id, RunStation.run_id.in_(run_ids))
            .order_by(RunStation.run_id, RunStation.sort_order, RunChallenge.sort_order)
        )
    ).all()
    times = await fetch_run_times(db, org_id, run_ids)

    stations: dict[str, AnalyticsCompareStation] = {}
    tasks: dict[str, AnalyticsCompareTask] = {}
    per_run_counts: dict[UUID, dict[str, int]] = {
        run_id: {"stations": 0, "tasks": 0, "achievable": 0} for run_id in run_ids
    }

    # Walk the runs in the order of the request, so the rows follow the first run.
    rows_by_run: dict[UUID, list[Row[Any]]] = {run_id: [] for run_id in run_ids}
    for row in task_rows:
        rows_by_run[row.station_run_id].append(row)

    for run_id in run_ids:
        run_times = times[run_id]
        team_count = len(run_times.teams)
        # A copy without a library source matches by position, within its rallye only.
        position_scope = str(runs_by_id[run_id].rallye_id or run_id)
        seen_stations: set[UUID] = set()
        station_answered: dict[str, set[UUID]] = {}
        used_task_keys: set[str] = set()
        for row in rows_by_run[run_id]:
            station_key = (
                str(row.source_station_id)
                if row.source_station_id is not None
                else f"{position_scope}:position:{row.station_sort_order}"
            )
            if row.station_id not in seen_stations:
                seen_stations.add(row.station_id)
                per_run_counts[run_id]["stations"] += 1
                stations.setdefault(
                    station_key, AnalyticsCompareStation(key=station_key, title=row.station_title, cells={})
                )
                station_answered[station_key] = set()
            if row.id is None:  # a station without a task
                continue

            task_key = f"{station_key}/" + (
                str(row.source_challenge_id) if row.source_challenge_id is not None else f"position:{row.sort_order}"
            )
            if task_key in used_task_keys:  # the same library task twice at one station
                task_key = f"{task_key}#{row.sort_order}"
            used_task_keys.add(task_key)

            answered = run_times.answered_by.get(row.id, set())
            station_answered[station_key] |= answered
            scores = run_times.scores.get(row.id, [])
            time = spread(run_times.steps.get(row.id, []))
            max_points = row.scoring_rules.get("max_points") if row.scoring_rules else None
            max_points = max_points if isinstance(max_points, int) else None
            per_run_counts[run_id]["tasks"] += 1
            per_run_counts[run_id]["achievable"] += max_points or 0

            task = tasks.setdefault(
                task_key,
                AnalyticsCompareTask(
                    key=task_key,
                    station_key=station_key,
                    station_title=row.station_title,
                    title=row.title,
                    type=row.challenge_type,
                    cells={},
                ),
            )
            task.cells[str(run_id)] = AnalyticsCompareTaskCell(
                team_count=team_count,
                submissions=len(answered),
                percent=_percent(len(answered), team_count),
                scored=len(scores),
                avg_points=round(sum(scores) / len(scores), 2) if scores else None,
                max_points=max_points,
                time_team_count=time.count,
                time_median_seconds=time.median,
            )

        for station_key, answered in station_answered.items():
            stations[station_key].cells[str(run_id)] = AnalyticsCompareStationCell(
                team_count=team_count,
                teams_attempted=len(answered),
                percent=_percent(len(answered), team_count),
            )

    runs: list[AnalyticsCompareRun] = []
    for run_id in run_ids:
        row = runs_by_id[run_id]
        run_times = times[run_id]
        counts = per_run_counts[run_id]
        members = members_by_run.get(run_id)
        team_count = len(run_times.teams)
        participant_count = int(members.member_count) if members is not None else 0
        guest_count = int(members.guest_count) if members is not None else 0
        team_points = [float(t.points) for t in run_times.teams.values()]
        total_points = int(sum(team_points))
        avg_points = round(total_points / team_count, 2) if team_count else None
        median_points = median_or_none(team_points)
        submission_count = sum(t.answers for t in run_times.teams.values())
        run_date: datetime | None = row.started_at or row.scheduled_for or row.created_at
        runs.append(
            AnalyticsCompareRun(
                run_id=run_id,
                name=row.name,
                state=row.state.value,
                run_date=run_date,
                started_at=row.started_at,
                ended_at=row.ended_at,
                station_count=counts["stations"],
                task_count=counts["tasks"],
                team_count=team_count,
                participant_count=participant_count,
                guest_count=guest_count,
                registered_count=participant_count - guest_count,
                submission_count=submission_count,
                scored_count=sum(t.scored for t in run_times.teams.values()),
                completion_percent=_percent(submission_count, team_count * counts["tasks"]),
                teams_finished=(
                    sum(1 for t in run_times.teams.values() if t.answers >= counts["tasks"]) if counts["tasks"] else 0
                ),
                total_points=total_points,
                achievable_points=counts["achievable"],
                avg_points_per_team=avg_points,
                median_points_per_team=round(median_points, 2) if median_points is not None else None,
                points_percent=(
                    round(100 * avg_points / counts["achievable"], 1)
                    if avg_points is not None and counts["achievable"]
                    else None
                ),
                working_time=working_time_of(run_times.durations()),
            )
        )

    return AnalyticsCompareResponse(runs=runs, stations=list(stations.values()), tasks=list(tasks.values()))


@compare_router.get(
    "/compare",
    response_model=AnalyticsCompareResponse,
    responses={
        200: {"description": "The chosen runs side by side"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (requires coordinator+ role)", "model": ErrorResponse},
        404: {"description": "A run was not found in this organization", "model": ErrorResponse},
        422: {"description": "Fewer than two or more than six runs", "model": ErrorResponse},
    },
)
async def get_analytics_comparison(
    run_ids: list[UUID] = Query(..., description="Two to six runs, in the order they are shown"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> AnalyticsCompareResponse:
    """Compare two to six runs side by side on participation, points, working time and completion.

    Open to coordinators and above. The response holds team-level figures only,
    with no name, e-mail address or user id of a person. Repeated run ids count
    once. Fewer than two or more than six different runs answer 422, an unknown
    run 404. Stations and tasks of different runs share a row when they come from
    the same library entry. A run without that station or task has no cell in the row.
    """
    return await build_comparison(db, org.id, _unique_run_ids(run_ids))


@compare_router.get(
    "/compare/export",
    responses={
        200: {"description": "CSV of one comparison table", "content": {"text/csv": {}}},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (requires coordinator+ role)", "model": ErrorResponse},
        404: {"description": "A run was not found in this organization", "model": ErrorResponse},
        422: {"description": "Invalid table, or fewer than two or more than six runs", "model": ErrorResponse},
    },
)
async def export_analytics_comparison(
    request: Request,
    run_ids: list[UUID] = Query(...),
    table: Literal["runs", "stations", "tasks"] = Query("runs", description="Which table to export"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> Response:
    """Download one table of the run comparison as a CSV file.

    Open to coordinators and above. The `table` parameter picks the table:
    `runs` has one row per run, `stations` one row per run and station, `tasks`
    one row per run and task. Headers, decimals and dates follow the language of
    the request. The same rules for the chosen runs apply as for the comparison
    itself.
    """
    comparison = await build_comparison(db, org.id, _unique_run_ids(run_ids))
    lang = _csv_language(request)
    run_names = {str(run.run_id): run.name for run in comparison.runs}
    columns: list[str]
    rows: list[list[object]]

    if table == "runs":
        columns = [
            "run_name",
            "run_date",
            "run_state",
            "station_count",
            "task_count",
            "team_count",
            "participant_count",
            "registered_count",
            "guest_count",
            "submission_count",
            "scored_count",
            "completion_percent",
            "teams_finished",
            "total_points",
            "achievable_points",
            "avg_points_per_team",
            "median_points_per_team",
            "points_percent",
            "teams_measured",
            "median_duration_minutes",
            "min_duration_minutes",
            "max_duration_minutes",
        ]
        rows = [
            [
                run.name,
                run.run_date.date() if run.run_date is not None else "",
                _STATE_LABELS.get(lang, _STATE_LABELS["de"]).get(run.state, run.state),
                run.station_count,
                run.task_count,
                run.team_count,
                run.participant_count,
                run.registered_count,
                run.guest_count,
                run.submission_count,
                run.scored_count,
                run.completion_percent if run.completion_percent is not None else "",
                run.teams_finished,
                run.total_points,
                run.achievable_points,
                run.avg_points_per_team if run.avg_points_per_team is not None else "",
                run.median_points_per_team if run.median_points_per_team is not None else "",
                run.points_percent if run.points_percent is not None else "",
                run.working_time.teams_measured,
                minutes(run.working_time.median_seconds),
                minutes(run.working_time.min_seconds),
                minutes(run.working_time.max_seconds),
            ]
            for run in comparison.runs
        ]
    elif table == "stations":
        columns = ["station", "run_name", "team_count", "teams_attempted", "answered_percent"]
        rows = [
            [
                _display_title(station.title),
                run_names[run_id],
                cell.team_count,
                cell.teams_attempted,
                cell.percent if cell.percent is not None else "",
            ]
            for station in comparison.stations
            for run_id, cell in station.cells.items()
        ]
    else:
        types = _CSV_TYPE_LABELS.get(lang, _CSV_TYPE_LABELS["de"])
        columns = [
            "station",
            "challenge_title",
            "type",
            "run_name",
            "team_count",
            "submissions",
            "answered_percent",
            "scored",
            "avg_points",
            "max_points",
            "time_team_count",
            "time_median_minutes",
        ]
        rows = [
            [
                _display_title(task.station_title),
                _display_title(task.title),
                types.get(task.type.value, task.type.value),
                run_names[run_id],
                cell.team_count,
                cell.submissions,
                cell.percent if cell.percent is not None else "",
                cell.scored,
                float(cell.avg_points) if cell.avg_points is not None else "",
                cell.max_points if cell.max_points is not None else "",
                cell.time_team_count,
                minutes(cell.time_median_seconds),
            ]
            for task in comparison.tasks
            for run_id, cell in task.cells.items()
        ]

    return Response(
        content=_spreadsheet_csv(columns, rows, lang),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=analytics-compare-{table}-{org.slug}.csv"},
    )
