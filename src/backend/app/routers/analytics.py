"""
Run analytics endpoints (nested under runs).

Read-only aggregation over Team/TeamMember/RunStation/RunChallenge/Submission/
Score (+ Feedback) for a single run. It holds no writable state, and every
endpoint requires coordinator+ role.

Privacy boundary: NO response field may contain a user display name, e-mail,
or user id anywhere. The aggregation is at team level only. Team names are
permitted, because they already appear on public leaderboards.

Query-count discipline: every aggregate below is a single GROUP BY query over
the whole run, never a query per team/station/challenge. The query count is
therefore constant regardless of run size (2 teams or 50 teams).

All endpoints are nested under runs: /api/orgs/{slug}/runs/{run_id}/analytics
"""

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.database import get_db
from app.models.challenge import ChallengeType
from app.models.organization import Organization
from app.models.run import Run
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.repositories.feedback import FeedbackRepository
from app.routers.admin import _csv_safe  # reuse the formula-injection-safe serializer
from app.routers.schemas import (
    AnalyticsPeriodTask,
    AnalyticsPeriodTeam,
    AnalyticsTimeseriesBucket,
    AnalyticsTimeseriesMetrics,
    AnalyticsTimeseriesRange,
    AnalyticsTimeseriesResponse,
    AnalyticsWorkingTime,
    ErrorResponse,
    FeedbackSummaryResponse,
    RunAnalyticsChallengeStat,
    RunAnalyticsParticipation,
    RunAnalyticsResponse,
    RunAnalyticsStationFunnel,
    RunAnalyticsTeamSummary,
    RunAnalyticsTimelineBucket,
)
from app.services.analytics_time import RunTimes, Spread, fetch_run_times, median_or_none, spread
from app.services.mail.templates import normalize_language

router = APIRouter()

# Org-scoped date-range analytics, mounted separately at
# /api/orgs/{slug}/analytics. The `router` above is nested under a single run.
timeseries_router = APIRouter()

_TIMELINE_BUCKET_SECONDS = 15 * 60

# Bucketing timezone: runs are bucketed by their instance-local calendar day so a
# near-midnight run lands in the same bucket in the JSON and the CSV. German
# deployment default; a future config knob can override it.
_BUCKET_TZ = "Europe/Berlin"
_MAX_DAY_BUCKETS = 366  # guard against thousand-bar day charts over huge ranges


# ============================================================================
# Internal row shapes (shared between the JSON endpoint and the CSV export so
# neither the query nor the aggregation logic is ever duplicated)
# ============================================================================


@dataclass
class _TeamSummaryRow:
    team_id: UUID
    team_name: str | None
    member_count: int
    guest_count: int
    total_points: int
    challenges_attempted: int
    challenges_scored: int
    # Working time, filled from the stored answer times.
    started_at: datetime | None = None
    last_answer_at: datetime | None = None
    duration_seconds: int | None = None
    longest_gap_seconds: int | None = None


@dataclass
class _ChallengeStatRow:
    run_challenge_id: UUID
    run_station_id: UUID
    station_title: dict[str, str]
    title: dict[str, str]
    challenge_type: ChallengeType
    submissions: int
    scored: int
    avg_points: float | None
    max_points: int | None
    # Time to the answer over the teams, and the answers and scores of the
    # teams that are not removed. The counts above also hold the answers of a
    # removed team.
    time: Spread = field(default_factory=Spread)
    team_answers: int = 0
    team_scores: list[int] = field(default_factory=list)


def working_time_of(durations: list[float]) -> AnalyticsWorkingTime:
    """The working time block of a response, from the teams' durations in seconds."""
    s = spread(durations)
    return AnalyticsWorkingTime(
        teams_measured=s.count,
        median_seconds=s.median,
        p25_seconds=s.p25,
        p75_seconds=s.p75,
        min_seconds=s.minimum,
        max_seconds=s.maximum,
    )


def _blank(value: object) -> object:
    """No value gives an empty spreadsheet cell, not the word None."""
    return "" if value is None else value


def minutes(seconds: int | None) -> float | str:
    """Seconds as minutes with one decimal for a spreadsheet cell. No value gives an empty cell."""
    return "" if seconds is None else round(seconds / 60, 1)


def _apply_times(team_rows: list[_TeamSummaryRow], challenge_rows: list[_ChallengeStatRow], times: RunTimes) -> None:
    """Write the time measures of a run into its team and task rows."""
    for team_row in team_rows:
        team_time = times.teams.get(team_row.team_id)
        if team_time is not None:
            team_row.started_at = team_time.started_at
            team_row.last_answer_at = team_time.last_answer_at
            team_row.duration_seconds = team_time.duration_seconds
            team_row.longest_gap_seconds = team_time.longest_gap_seconds
    for challenge_row in challenge_rows:
        challenge_row.time = spread(times.steps.get(challenge_row.run_challenge_id, []))
        challenge_row.team_answers = len(times.answered_by.get(challenge_row.run_challenge_id, ()))
        challenge_row.team_scores = times.scores.get(challenge_row.run_challenge_id, [])


def _display_title(title: dict[str, str]) -> str:
    """Pick a single display string out of a multilingual {locale: text} dict for CSV cells."""
    return title.get("de") or title.get("en") or next(iter(title.values()), "")


async def _run_bounds(db: AsyncSession, org_id: UUID, run_id: UUID) -> tuple[datetime | None, datetime | None]:
    """Start and end of the run, or 404. One column-only query, like `_verify_run_exists`."""
    row = (
        await db.execute(
            select(Run.started_at, Run.ended_at).where(
                Run.id == run_id,
                Run.organization_id == org_id,
                Run.is_deleted == False,  # noqa: E712
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return row[0], row[1]


async def _verify_run_exists(db: AsyncSession, org_id: UUID, run_id: UUID) -> datetime | None:
    """
    Verify the run exists in this org and return its started_at (for timeline
    bucketing). Deliberately a raw column-only query rather than
    RunRepository.get_by_id(): Run.teams and Run.run_stations (and their own
    nested relationships) are all `lazy="selectin"`, so fetching a full Run
    ORM entity here would cascade into a dozen-plus extra eager-load queries
    per request. The count stays constant, but it is needlessly heavy for an
    existence check that only needs one column.
    """
    stmt = select(Run.started_at).where(
        Run.id == run_id,
        Run.organization_id == org_id,
        Run.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    started_at: datetime | None = row[0]
    return started_at


async def _fetch_team_summaries(db: AsyncSession, org_id: UUID, run_id: UUID) -> list[_TeamSummaryRow]:
    """
    Per-team summary: one GROUP BY for membership counts, one for submission/score
    totals. Two separate queries (not one) — joining team_members AND submissions
    in a single query would fan out the join and double-count points/submissions.
    """
    members_stmt = (
        select(
            Team.id.label("team_id"),
            Team.name.label("team_name"),
            func.count(TeamMember.id).label("member_count"),
            func.count(case((TeamMember.is_guest.is_(True), TeamMember.id))).label("guest_count"),
        )
        .select_from(Team)
        .outerjoin(TeamMember, TeamMember.team_id == Team.id)
        .where(
            Team.organization_id == org_id,
            Team.run_id == run_id,
            Team.is_deleted == False,  # noqa: E712
        )
        .group_by(Team.id, Team.name)
        .order_by(Team.created_at)
    )
    members_result = await db.execute(members_stmt)
    members_rows = members_result.all()

    scoring_stmt = (
        select(
            Team.id.label("team_id"),
            func.count(func.distinct(Submission.id)).label("challenges_attempted"),
            func.count(func.distinct(Score.id)).label("challenges_scored"),
            func.coalesce(func.sum(Score.points), 0).label("total_points"),
        )
        .select_from(Team)
        .outerjoin(Submission, Submission.team_id == Team.id)
        .outerjoin(Score, Score.submission_id == Submission.id)
        .where(
            Team.organization_id == org_id,
            Team.run_id == run_id,
            Team.is_deleted == False,  # noqa: E712
        )
        .group_by(Team.id)
    )
    scoring_result = await db.execute(scoring_stmt)
    scoring_by_team = {row.team_id: row for row in scoring_result.all()}

    rows: list[_TeamSummaryRow] = []
    for m in members_rows:
        s = scoring_by_team.get(m.team_id)
        rows.append(
            _TeamSummaryRow(
                team_id=m.team_id,
                team_name=m.team_name,
                member_count=m.member_count,
                guest_count=m.guest_count,
                total_points=int(s.total_points) if s is not None else 0,
                challenges_attempted=int(s.challenges_attempted) if s is not None else 0,
                challenges_scored=int(s.challenges_scored) if s is not None else 0,
            )
        )
    return rows


def _live_teams(run_id: UUID) -> Select[tuple[UUID]]:
    """The teams of a run that were not removed, the condition of the team table.

    The answers of a removed team count nowhere."""
    return select(Team.id).where(Team.run_id == run_id, Team.is_deleted == False)  # noqa: E712


async def _fetch_station_funnel(db: AsyncSession, org_id: UUID, run_id: UUID) -> list[RunAnalyticsStationFunnel]:
    """Per-station completion funnel: one GROUP BY over run_stations."""
    stmt = (
        select(
            RunStation.id.label("run_station_id"),
            RunStation.title.label("title"),
            func.count(func.distinct(Submission.team_id)).label("teams_attempted"),
            func.count(func.distinct(case((Score.id.isnot(None), Submission.team_id)))).label("teams_scored"),
        )
        .select_from(RunStation)
        .outerjoin(RunChallenge, RunChallenge.run_station_id == RunStation.id)
        .outerjoin(
            Submission,
            and_(Submission.run_challenge_id == RunChallenge.id, Submission.team_id.in_(_live_teams(run_id))),
        )
        .outerjoin(Score, Score.submission_id == Submission.id)
        .where(RunStation.organization_id == org_id, RunStation.run_id == run_id)
        .group_by(RunStation.id)
        .order_by(RunStation.sort_order)
    )
    result = await db.execute(stmt)
    return [
        RunAnalyticsStationFunnel(
            run_station_id=row.run_station_id,
            title=row.title,
            teams_attempted=row.teams_attempted,
            teams_scored=row.teams_scored,
        )
        for row in result.all()
    ]


async def _fetch_challenge_stats(db: AsyncSession, org_id: UUID, run_id: UUID) -> list[_ChallengeStatRow]:
    """Per-challenge stats: one GROUP BY over run_challenges (joined to its station for the title)."""
    stmt = (
        select(
            RunChallenge.id.label("run_challenge_id"),
            RunChallenge.run_station_id.label("run_station_id"),
            RunChallenge.title.label("title"),
            RunChallenge.challenge_type.label("challenge_type"),
            RunChallenge.scoring_rules.label("scoring_rules"),
            RunStation.title.label("station_title"),
            func.count(func.distinct(Submission.id)).label("submissions"),
            func.count(func.distinct(Score.id)).label("scored"),
            func.avg(Score.points).label("avg_points"),
        )
        .select_from(RunChallenge)
        .join(RunStation, RunStation.id == RunChallenge.run_station_id)
        .outerjoin(
            Submission,
            and_(Submission.run_challenge_id == RunChallenge.id, Submission.team_id.in_(_live_teams(run_id))),
        )
        .outerjoin(Score, Score.submission_id == Submission.id)
        .where(RunChallenge.organization_id == org_id, RunChallenge.run_id == run_id)
        .group_by(RunChallenge.id, RunStation.id)
        .order_by(RunStation.sort_order, RunChallenge.sort_order)
    )
    result = await db.execute(stmt)
    rows: list[_ChallengeStatRow] = []
    for row in result.all():
        max_points = row.scoring_rules.get("max_points") if row.scoring_rules else None
        rows.append(
            _ChallengeStatRow(
                run_challenge_id=row.run_challenge_id,
                run_station_id=row.run_station_id,
                station_title=row.station_title,
                title=row.title,
                challenge_type=row.challenge_type,
                submissions=row.submissions,
                scored=row.scored,
                avg_points=float(row.avg_points) if row.avg_points is not None else None,
                max_points=max_points,
            )
        )
    return rows


async def _fetch_timeline(
    db: AsyncSession, org_id: UUID, run_id: UUID, run_started_at: datetime | None
) -> list[RunAnalyticsTimelineBucket]:
    """Submissions bucketed into 15-minute windows starting at the run's started_at."""
    stmt = (
        select(Submission.submitted_at)
        .where(
            Submission.organization_id == org_id,
            Submission.run_id == run_id,
            Submission.team_id.in_(_live_teams(run_id)),
        )
        .order_by(Submission.submitted_at)
    )
    result = await db.execute(stmt)
    # Submission.submitted_at is annotated Mapped[DateTime] (the SQLAlchemy type,
    # not python's datetime) in the model, which otherwise leaves mypy thinking
    # arithmetic below is on a DateTime column type rather than a real datetime.
    timestamps = [cast(datetime, ts) for ts in result.scalars().all()]
    if not timestamps:
        return []

    origin = run_started_at or timestamps[0]
    counts: dict[int, int] = {}
    for ts in timestamps:
        offset_seconds = (ts - origin).total_seconds()
        bucket_index = max(int(offset_seconds // _TIMELINE_BUCKET_SECONDS), 0)
        counts[bucket_index] = counts.get(bucket_index, 0) + 1

    return [
        RunAnalyticsTimelineBucket(
            bucket_start=origin + timedelta(seconds=_TIMELINE_BUCKET_SECONDS * index),
            submissions=count,
        )
        for index, count in sorted(counts.items())
    ]


# ----------------------------------------------------------------------------
# Spreadsheet CSV
#
# The exports are opened in a spreadsheet by the coordination, mostly Excel on
# a German Windows. Excel reads UTF-8 only with a byte order mark, and a German
# Excel splits columns at ";" and reads "7,5" as a number. So every file starts
# with the BOM, and a German file uses ";" and the decimal comma. The headers
# and the task types are the names the screen shows, not field names and enum
# codes. The JSON export stays the machine format with its stable keys.
# ----------------------------------------------------------------------------

_CSV_HEADERS: dict[str, dict[str, str]] = {
    "de": {
        "team_name": "Team",
        "member_count": "Mitglieder",
        "guest_count": "Gäste",
        "total_points": "Punkte",
        "challenges_attempted": "Aufgaben beantwortet",
        "challenges_scored": "Aufgaben bewertet",
        "station": "Station",
        "challenge_title": "Aufgabe",
        "type": "Typ",
        "submissions": "Einreichungen",
        "scored": "Bewertet",
        "avg_points": "Punkte im Schnitt",
        "bucket_start": "Zeitraum ab",
        "run_count": "Läufe",
        "participant_count": "Teilnehmende",
        "registered_count": "Registrierte",
        "team_count": "Teams",
        "submission_count": "Einreichungen",
        "scored_count": "Bewertet",
        "unscored_count": "Unbewertet",
        # Points and working time. Times are minutes with one decimal.
        "max_points": "Max. Punkte",
        "started_at": "Beginn",
        "last_answer_at": "Letzte Antwort",
        "duration_minutes": "Bearbeitungsdauer (Min.)",
        "longest_gap_minutes": "Längster Abstand zwischen zwei Antworten (Min.)",
        "time_team_count": "Teams mit Zeit",
        "time_median_minutes": "Zeit bis zur Antwort, Median (Min.)",
        "time_p25_minutes": "Zeit bis zur Antwort, unteres Viertel (Min.)",
        "time_p75_minutes": "Zeit bis zur Antwort, oberes Viertel (Min.)",
        "time_min_minutes": "Zeit bis zur Antwort, kürzeste (Min.)",
        "time_max_minutes": "Zeit bis zur Antwort, längste (Min.)",
        "avg_points_per_team": "Punkte je Team im Schnitt",
        "avg_points_per_answer": "Punkte je bewerteter Antwort im Schnitt",
        "points_percent": "Erreichte Punkte (%)",
        "teams_measured": "Teams mit Bearbeitungsdauer",
        "median_duration_minutes": "Bearbeitungsdauer, Median (Min.)",
        "min_duration_minutes": "Bearbeitungsdauer, kürzeste (Min.)",
        "max_duration_minutes": "Bearbeitungsdauer, längste (Min.)",
        "run_name": "Lauf",
        "run_date": "Datum",
        "run_state": "Status",
        "station_count": "Stationen",
        "task_count": "Aufgaben",
        "completion_percent": "Beantwortet (%)",
        "teams_finished": "Teams mit allen Aufgaben",
        "achievable_points": "Erreichbare Punkte je Team",
        "median_points_per_team": "Punkte je Team, Median",
        "teams_attempted": "Teams mit Antwort",
        "answered_percent": "Teams mit Antwort (%)",
    },
    "en": {
        "team_name": "Team",
        "member_count": "Members",
        "guest_count": "Guests",
        "total_points": "Points",
        "challenges_attempted": "Tasks answered",
        "challenges_scored": "Tasks scored",
        "station": "Station",
        "challenge_title": "Task",
        "type": "Type",
        "submissions": "Submissions",
        "scored": "Scored",
        "avg_points": "Average points",
        "bucket_start": "Period from",
        "run_count": "Runs",
        "participant_count": "Participants",
        "registered_count": "Registered",
        "team_count": "Teams",
        "submission_count": "Submissions",
        "scored_count": "Scored",
        "unscored_count": "Unscored",
        "max_points": "Max. points",
        "started_at": "Start",
        "last_answer_at": "Last answer",
        "duration_minutes": "Working time (min)",
        "longest_gap_minutes": "Longest gap between two answers (min)",
        "time_team_count": "Teams with a time",
        "time_median_minutes": "Time to the answer, median (min)",
        "time_p25_minutes": "Time to the answer, lower quartile (min)",
        "time_p75_minutes": "Time to the answer, upper quartile (min)",
        "time_min_minutes": "Time to the answer, shortest (min)",
        "time_max_minutes": "Time to the answer, longest (min)",
        "avg_points_per_team": "Average points per team",
        "avg_points_per_answer": "Average points per scored answer",
        "points_percent": "Points reached (%)",
        "teams_measured": "Teams with a working time",
        "median_duration_minutes": "Working time, median (min)",
        "min_duration_minutes": "Working time, shortest (min)",
        "max_duration_minutes": "Working time, longest (min)",
        "run_name": "Run",
        "run_date": "Date",
        "run_state": "State",
        "station_count": "Stations",
        "task_count": "Tasks",
        "completion_percent": "Answered (%)",
        "teams_finished": "Teams with every task",
        "achievable_points": "Achievable points per team",
        "median_points_per_team": "Points per team, median",
        "teams_attempted": "Teams with an answer",
        "answered_percent": "Teams with an answer (%)",
    },
}

# The names of the task library (content:challenge.types in the frontend).
_CSV_TYPE_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "photo": "Foto",
        "text": "Text",
        "qr_scan": "QR-Code",
        "quiz": "Quiz",
        "creative": "Kreativ",
        "audio": "Audio",
        "video": "Video",
    },
    "en": {
        "photo": "Photo",
        "text": "Text",
        "qr_scan": "QR code",
        "quiz": "Quiz",
        "creative": "Creative",
        "audio": "Audio",
        "video": "Video",
    },
}


def _csv_language(request: Request) -> str:
    """The reader's language: the app's own header first, then the browser's."""
    explicit = request.headers.get("X-Clavis-Language")
    if explicit:
        return normalize_language(explicit)
    accept = request.headers.get("Accept-Language", "")
    return normalize_language(accept.split(",")[0].strip() or None)


def _local_time(value: datetime | None) -> datetime | str:
    """A stored moment in the instance's time zone for a spreadsheet cell. No value gives an empty cell."""
    return "" if value is None else value.astimezone(ZoneInfo(_BUCKET_TZ))


def _csv_cell(value: object, lang: str) -> str:
    """One formula-safe cell, with the decimal comma and the German date in a German file."""
    if isinstance(value, datetime):
        # A moment to the minute, in the reader's format.
        return value.strftime("%d.%m.%Y %H:%M") if lang == "de" else value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        # 05.01.2026 in a German file: a spreadsheet with German settings reads
        # it as a date, and so does a person. English keeps the ISO date.
        return value.strftime("%d.%m.%Y") if lang == "de" else value.isoformat()
    if isinstance(value, float):
        text = f"{round(value, 2):g}"
        if lang == "de":
            text = text.replace(".", ",")
        return _csv_safe(text)
    return _csv_safe(value)


def _spreadsheet_csv(columns: list[str], rows: list[list[object]], lang: str) -> str:
    """A CSV a spreadsheet in ``lang`` opens with the right columns and umlauts."""
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";" if lang == "de" else ",")
    headers = _CSV_HEADERS.get(lang, _CSV_HEADERS["de"])
    writer.writerow([headers.get(column, column) for column in columns])
    for row in rows:
        writer.writerow([_csv_cell(value, lang) for value in row])
    return buf.getvalue()


def _build_teams_csv(rows: list[_TeamSummaryRow], lang: str) -> str:
    """Serialize per-team summary rows to CSV (formula-injection-safe)."""
    columns = ["team_name", "member_count", "guest_count", "total_points", "challenges_attempted", "challenges_scored"]
    return _spreadsheet_csv(
        columns,
        [
            [
                r.team_name,
                r.member_count,
                r.guest_count,
                r.total_points,
                r.challenges_attempted,
                r.challenges_scored,
            ]
            for r in rows
        ],
        lang,
    )


def _build_challenges_csv(rows: list[_ChallengeStatRow], lang: str) -> str:
    """Serialize per-challenge stats rows to CSV (formula-injection-safe)."""
    columns = ["station", "challenge_title", "type", "submissions", "scored", "avg_points"]
    types = _CSV_TYPE_LABELS.get(lang, _CSV_TYPE_LABELS["de"])
    return _spreadsheet_csv(
        columns,
        [
            [
                _display_title(r.station_title),
                _display_title(r.title),
                types.get(r.challenge_type.value, r.challenge_type.value),
                r.submissions,
                r.scored,
                float(r.avg_points) if r.avg_points is not None else "",
            ]
            for r in rows
        ],
        lang,
    )


# The two tables above keep their columns. The time measures have their own
# tables, so a sheet built on an old export reads the same file.
def _build_working_time_csv(rows: list[_TeamSummaryRow], lang: str) -> str:
    """Working time per team: start, last answer, working time and longest gap, with the points."""
    columns = [
        "team_name",
        "total_points",
        "challenges_attempted",
        "started_at",
        "last_answer_at",
        "duration_minutes",
        "longest_gap_minutes",
    ]
    return _spreadsheet_csv(
        columns,
        [
            [
                r.team_name,
                r.total_points,
                r.challenges_attempted,
                _local_time(r.started_at),
                _local_time(r.last_answer_at),
                minutes(r.duration_seconds),
                minutes(r.longest_gap_seconds),
            ]
            for r in rows
        ],
        lang,
    )


def _build_task_times_csv(rows: list[_ChallengeStatRow], lang: str) -> str:
    """Points and time to the answer per task, with median and spread over the teams."""
    columns = [
        "station",
        "challenge_title",
        "type",
        "submissions",
        "scored",
        "avg_points",
        "max_points",
        "time_team_count",
        "time_median_minutes",
        "time_p25_minutes",
        "time_p75_minutes",
        "time_min_minutes",
        "time_max_minutes",
    ]
    types = _CSV_TYPE_LABELS.get(lang, _CSV_TYPE_LABELS["de"])
    return _spreadsheet_csv(
        columns,
        [
            [
                _display_title(r.station_title),
                _display_title(r.title),
                types.get(r.challenge_type.value, r.challenge_type.value),
                r.team_answers,
                len(r.team_scores),
                round(sum(r.team_scores) / len(r.team_scores), 2) if r.team_scores else "",
                r.max_points if r.max_points is not None else "",
                r.time.count,
                minutes(r.time.median),
                minutes(r.time.p25),
                minutes(r.time.p75),
                minutes(r.time.minimum),
                minutes(r.time.maximum),
            ]
            for r in rows
        ],
        lang,
    )


@router.get(
    "",
    response_model=RunAnalyticsResponse,
    responses={
        200: {"description": "Aggregated analytics document for this run"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (requires coordinator+ role)", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
    },
)
async def get_run_analytics(
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> RunAnalyticsResponse:
    """Return the analytics of one run.

    Open to coordinators, creators and organization admins. The response holds
    the participation counts, the funnel of each station, the statistics of each
    challenge, a summary of each team, the working time, a timeline of the
    submissions in 15-minute windows and the feedback summary. The feedback is
    null while the run has no feedback.

    The response names teams only. It holds no user name, e-mail address or user id.
    A run without teams gives zero counts and empty lists.
    """
    started_at, ended_at = await _run_bounds(db, org.id, run_id)

    team_rows = await _fetch_team_summaries(db, org.id, run_id)
    stations = await _fetch_station_funnel(db, org.id, run_id)
    challenge_rows = await _fetch_challenge_stats(db, org.id, run_id)
    timeline = await _fetch_timeline(db, org.id, run_id, started_at)
    times = (await fetch_run_times(db, org.id, [run_id]))[run_id]
    _apply_times(team_rows, challenge_rows, times)

    feedback_repo = FeedbackRepository(db, organization_id=org.id)
    feedback_summary = await feedback_repo.get_run_summary(run_id)
    feedback = (
        None
        if feedback_summary["count"] == 0
        else FeedbackSummaryResponse(
            average_rating=feedback_summary["average_rating"] or 0.0,
            total_count=feedback_summary["count"],
            distribution=feedback_summary["rating_distribution"],
        )
    )

    participant_count = sum(r.member_count for r in team_rows)
    guest_count = sum(r.guest_count for r in team_rows)

    return RunAnalyticsResponse(
        run_id=run_id,
        participation=RunAnalyticsParticipation(
            team_count=len(team_rows),
            participant_count=participant_count,
            guest_count=guest_count,
            registered_count=participant_count - guest_count,
        ),
        stations=stations,
        challenges=[
            RunAnalyticsChallengeStat(
                run_challenge_id=r.run_challenge_id,
                run_station_id=r.run_station_id,
                station_title=r.station_title,
                title=r.title,
                type=r.challenge_type,
                submissions=r.submissions,
                scored=r.scored,
                avg_points=r.avg_points,
                max_points=r.max_points,
                time_team_count=r.time.count,
                time_median_seconds=r.time.median,
                time_p25_seconds=r.time.p25,
                time_p75_seconds=r.time.p75,
                time_min_seconds=r.time.minimum,
                time_max_seconds=r.time.maximum,
            )
            for r in challenge_rows
        ],
        teams=[
            RunAnalyticsTeamSummary(
                team_id=r.team_id,
                team_name=r.team_name,
                member_count=r.member_count,
                guest_count=r.guest_count,
                total_points=r.total_points,
                challenges_attempted=r.challenges_attempted,
                challenges_scored=r.challenges_scored,
                started_at=r.started_at,
                last_answer_at=r.last_answer_at,
                duration_seconds=r.duration_seconds,
                longest_gap_seconds=r.longest_gap_seconds,
            )
            for r in team_rows
        ],
        timeline=timeline,
        feedback=feedback,
        started_at=started_at,
        ended_at=ended_at,
        working_time=working_time_of(times.durations()),
    )


@router.get(
    "/export",
    responses={
        200: {"description": "CSV export of the requested aggregate table", "content": {"text/csv": {}}},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (requires coordinator+ role)", "model": ErrorResponse},
        404: {"description": "Run not found", "model": ErrorResponse},
        422: {"description": "Invalid table parameter", "model": ErrorResponse},
    },
)
async def export_run_analytics(
    request: Request,
    run_id: UUID,
    table: str = Query(
        ...,
        pattern="^(teams|challenges|working_time|task_times)$",
        description="Which aggregate table to export. working_time and task_times hold the time measures",
    ),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> Response:
    """Export one analytics table of a run as a CSV file.

    Open to coordinators, creators and organization admins. The `table`
    parameter picks `teams`, `challenges`, `working_time` or `task_times`. Any
    other value gives 422. The response is an attachment of type `text/csv`. The
    language of the headers and of the number and date format comes from the
    `X-Clavis-Language` header, then from `Accept-Language`. A cell that starts
    with a formula character, such as a team named `=cmd()`, gets a leading
    quote so that a spreadsheet reads it as text.
    """
    await _verify_run_exists(db, org.id, run_id)
    lang = _csv_language(request)

    if table == "teams":
        csv_content = _build_teams_csv(await _fetch_team_summaries(db, org.id, run_id), lang)
    elif table == "challenges":
        csv_content = _build_challenges_csv(await _fetch_challenge_stats(db, org.id, run_id), lang)
    else:
        times = (await fetch_run_times(db, org.id, [run_id]))[run_id]
        if table == "working_time":
            team_rows = await _fetch_team_summaries(db, org.id, run_id)
            _apply_times(team_rows, [], times)
            csv_content = _build_working_time_csv(team_rows, lang)
        else:
            challenge_rows = await _fetch_challenge_stats(db, org.id, run_id)
            _apply_times([], challenge_rows, times)
            csv_content = _build_task_times_csv(challenge_rows, lang)

    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=run-analytics-{table}-{run_id}.csv"},
    )


# ============================================================================
# Date-Range Analytics — org-scoped timeseries
# ============================================================================


async def _fetch_timeseries(
    db: AsyncSession,
    org_id: UUID,
    date_from: date,
    date_to: date,
    bucket: str,
) -> tuple[
    list[AnalyticsTimeseriesBucket], AnalyticsTimeseriesMetrics, list[AnalyticsPeriodTask], list[AnalyticsPeriodTeam]
]:
    """Aggregate every started run whose bucketing date falls in [date_from, date_to]
    into per-date buckets. Bucketing date = coalesce(ended_at, started_at), truncated
    to ``bucket`` in the instance-local timezone: the end of a run that ended, the
    start of a run that is still active.

    A run without a start (configured or planned, never started) is no run of the
    period. It is in no bucket, no total, no task row and no team row, and so in
    no tile, chart or export either.

    Constant query count: one run->bucket map + one teams/members GROUP BY
    + one submissions GROUP BY, each grouped by run_id — never a query per run.
    Per-run rows are summed into buckets in Python (no SQL join fan-out).
    """
    bucket_local = func.timezone(_BUCKET_TZ, func.coalesce(Run.ended_at, Run.started_at))
    bucket_start = func.date_trunc(bucket, bucket_local)
    to_exclusive = date_to + timedelta(days=1)

    run_rows = (
        await db.execute(
            select(
                Run.id.label("run_id"),
                Run.name.label("run_name"),
                Run.started_at.label("run_date"),
                bucket_start.label("bucket_start"),
            ).where(
                Run.organization_id == org_id,
                Run.is_deleted == False,  # noqa: E712  (a deleted run counts nowhere)
                Run.started_at.is_not(None),  # a run that never started counts nowhere either
                bucket_local >= date_from,
                bucket_local < to_exclusive,
            )
        )
    ).all()

    empty = AnalyticsTimeseriesMetrics(
        run_count=0,
        participant_count=0,
        guest_count=0,
        registered_count=0,
        team_count=0,
        submission_count=0,
        scored_count=0,
        unscored_count=0,
    )
    if not run_rows:
        return [], empty, [], []

    bucket_by_run: dict[UUID, datetime] = {r.run_id: r.bucket_start for r in run_rows}
    run_ids = list(bucket_by_run.keys())

    team_rows = (
        await db.execute(
            select(
                Team.run_id.label("run_id"),
                func.count(func.distinct(Team.id)).label("team_count"),
                func.count(TeamMember.id).label("member_count"),
                func.count(case((TeamMember.is_guest.is_(True), TeamMember.id))).label("guest_count"),
            )
            .select_from(Team)
            .outerjoin(TeamMember, TeamMember.team_id == Team.id)
            .where(Team.run_id.in_(run_ids), Team.is_deleted == False)  # noqa: E712
            .group_by(Team.run_id)
        )
    ).all()
    teams_by_run = {r.run_id: r for r in team_rows}

    sub_rows = (
        await db.execute(
            select(
                Submission.run_id.label("run_id"),
                func.count(func.distinct(Submission.id)).label("submission_count"),
                func.count(func.distinct(Score.id)).label("scored_count"),
            )
            .select_from(Submission)
            .join(Team, Team.id == Submission.team_id)
            .outerjoin(Score, Score.submission_id == Submission.id)
            .where(Submission.run_id.in_(run_ids), Team.is_deleted == False)  # noqa: E712
            .group_by(Submission.run_id)
        )
    ).all()
    subs_by_run = {r.run_id: r for r in sub_rows}

    # Points and working time per team, two queries for all runs.
    times = await fetch_run_times(db, org_id, run_ids)
    points_acc: dict[datetime, _PointsAcc] = defaultdict(_PointsAcc)
    points_total = _PointsAcc()
    for run_id, bkt in bucket_by_run.items():
        for team_time in times[run_id].teams.values():
            for target in (points_acc[bkt], points_total):
                target.points += team_time.points
                target.scored += team_time.scored
                target.max_points_scored += team_time.max_points_scored
                if team_time.duration_seconds is not None:
                    target.durations.append(float(team_time.duration_seconds))

    def zero_metrics() -> dict[str, int]:
        return {
            "run_count": 0,
            "participant_count": 0,
            "guest_count": 0,
            "team_count": 0,
            "submission_count": 0,
            "scored_count": 0,
        }

    acc: dict[datetime, dict[str, int]] = defaultdict(zero_metrics)
    for run_id, bkt in bucket_by_run.items():
        a = acc[bkt]
        a["run_count"] += 1
        t = teams_by_run.get(run_id)
        if t is not None:
            a["team_count"] += int(t.team_count or 0)
            a["participant_count"] += int(t.member_count or 0)
            a["guest_count"] += int(t.guest_count or 0)
        s = subs_by_run.get(run_id)
        if s is not None:
            a["submission_count"] += int(s.submission_count or 0)
            a["scored_count"] += int(s.scored_count or 0)

    # A range with runs shows every bucket, empty ones as zeros. Without them
    # the chart would space the remaining weeks evenly and hide the gaps, in
    # the chart and in both exports. A range without any run stays empty, so
    # the view says so instead of drawing zeros.
    if acc:
        for start in _bucket_starts(date_from, date_to, bucket):
            acc.setdefault(start, zero_metrics())

    buckets: list[AnalyticsTimeseriesBucket] = []
    total = dict.fromkeys(
        ("run_count", "participant_count", "guest_count", "team_count", "submission_count", "scored_count"), 0
    )
    for bkt in sorted(acc.keys()):
        a = acc[bkt]
        for k in total:
            total[k] += a[k]
        buckets.append(
            AnalyticsTimeseriesBucket(
                bucket_start=bkt,
                run_count=a["run_count"],
                participant_count=a["participant_count"],
                guest_count=a["guest_count"],
                registered_count=a["participant_count"] - a["guest_count"],
                team_count=a["team_count"],
                submission_count=a["submission_count"],
                scored_count=a["scored_count"],
                unscored_count=a["submission_count"] - a["scored_count"],
                **points_acc[bkt].fields(a["team_count"]),
            )
        )

    totals = AnalyticsTimeseriesMetrics(
        run_count=total["run_count"],
        participant_count=total["participant_count"],
        guest_count=total["guest_count"],
        registered_count=total["participant_count"] - total["guest_count"],
        team_count=total["team_count"],
        submission_count=total["submission_count"],
        scored_count=total["scored_count"],
        unscored_count=total["submission_count"] - total["scored_count"],
        **points_total.fields(total["team_count"]),
    )
    tasks = await _fetch_period_tasks(db, org_id, run_ids, times)
    teams = [
        AnalyticsPeriodTeam(
            run_id=row.run_id,
            run_name=row.run_name,
            run_date=row.run_date,
            team_id=team_time.team_id,
            team_name=team_time.name,
            points=team_time.points,
            answers=team_time.answers,
            scored=team_time.scored,
            duration_seconds=team_time.duration_seconds,
        )
        for row in run_rows
        for team_time in times[row.run_id].teams.values()
    ]
    # The most points first. A tie keeps the newer run first, then the team name.
    teams.sort(key=lambda t: (t.team_name or "").casefold())
    teams.sort(key=lambda t: t.run_date or datetime.min.replace(tzinfo=UTC), reverse=True)
    teams.sort(key=lambda t: t.points, reverse=True)
    return buckets, totals, tasks, teams


@dataclass
class _PointsAcc:
    """Points and working times of the teams of one bucket, or of the whole period."""

    points: int = 0
    # Scored answers of these teams. The answers of a deleted team count
    # nowhere here, so the average matches the points.
    scored: int = 0
    max_points_scored: int = 0
    durations: list[float] = field(default_factory=list)

    def fields(self, team_count: int) -> dict[str, int | float | None]:
        median = median_or_none(self.durations)
        return {
            "total_points": self.points,
            "avg_points_per_team": round(self.points / team_count, 2) if team_count else None,
            "avg_points_per_answer": round(self.points / self.scored, 2) if self.scored else None,
            "points_percent": (
                round(100 * self.points / self.max_points_scored, 1) if self.max_points_scored else None
            ),
            "teams_measured": len(self.durations),
            "median_duration_seconds": round(median) if median is not None else None,
        }


async def _fetch_period_tasks(
    db: AsyncSession, org_id: UUID, run_ids: list[UUID], times: dict[UUID, RunTimes]
) -> list[AnalyticsPeriodTask]:
    """Points and time per task over the runs of a period.

    A run works on a frozen copy of its tasks. Two copies are the same task when
    they come from the same library task. A copy without a library task (the
    task was deleted, or the run is older than the link) is matched by its
    station title and its own title. One query for all runs.
    """
    rows = (
        await db.execute(
            select(
                RunChallenge.id,
                RunChallenge.run_id,
                RunChallenge.source_challenge_id,
                RunChallenge.title,
                RunChallenge.challenge_type,
                RunChallenge.scoring_rules,
                RunStation.title.label("station_title"),
            )
            .join(RunStation, RunStation.id == RunChallenge.run_station_id)
            .join(Run, Run.id == RunChallenge.run_id)
            .where(RunChallenge.organization_id == org_id, RunChallenge.run_id.in_(run_ids))
            .order_by(
                func.coalesce(Run.started_at, Run.created_at).desc(),
                Run.id,
                RunStation.sort_order,
                RunChallenge.sort_order,
            )
        )
    ).all()

    @dataclass
    class _Task:
        station_title: dict[str, str]
        title: dict[str, str]
        challenge_type: ChallengeType
        max_points: int | None = None
        runs: set[UUID] = field(default_factory=set)
        submissions: int = 0
        scores: list[int] = field(default_factory=list)
        steps: list[float] = field(default_factory=list)

    tasks: dict[str, _Task] = {}
    for row in rows:
        key = (
            str(row.source_challenge_id)
            if row.source_challenge_id is not None
            else f"title:{_display_title(row.station_title)}|{_display_title(row.title)}"
        )
        # The newest run comes first, so a renamed task shows its current name.
        task = tasks.setdefault(
            key, _Task(station_title=row.station_title, title=row.title, challenge_type=row.challenge_type)
        )
        run_times = times[row.run_id]
        task.runs.add(row.run_id)
        task.submissions += len(run_times.answered_by.get(row.id, ()))
        task.scores.extend(run_times.scores.get(row.id, []))
        task.steps.extend(run_times.steps.get(row.id, []))
        row_max = row.scoring_rules.get("max_points") if row.scoring_rules else None
        if isinstance(row_max, int):
            task.max_points = max(task.max_points or 0, row_max)

    result: list[AnalyticsPeriodTask] = []
    for key, task in tasks.items():
        time = spread(task.steps)
        result.append(
            AnalyticsPeriodTask(
                key=key,
                station_title=task.station_title,
                title=task.title,
                type=task.challenge_type,
                run_count=len(task.runs),
                submissions=task.submissions,
                scored=len(task.scores),
                avg_points=round(sum(task.scores) / len(task.scores), 2) if task.scores else None,
                max_points=task.max_points,
                time_team_count=time.count,
                time_median_seconds=time.median,
                time_p25_seconds=time.p25,
                time_p75_seconds=time.p75,
            )
        )
    return result


def _bucket_starts(date_from: date, date_to: date, bucket: str) -> list[datetime]:
    """Every bucket start from ``date_from`` to ``date_to``, as Postgres ``date_trunc`` names them.

    Naive midnights, like the keys the query returns (``timezone()`` drops the
    zone). A week starts on Monday, a month on its first day.
    """
    if bucket == "week":
        current = date_from - timedelta(days=date_from.weekday())
    elif bucket == "month":
        current = date_from.replace(day=1)
    else:
        current = date_from
    starts: list[datetime] = []
    while current <= date_to:
        starts.append(datetime(current.year, current.month, current.day))
        if bucket == "week":
            current += timedelta(days=7)
        elif bucket == "month":
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            current += timedelta(days=1)
    return starts


def _validate_range(date_from: date, date_to: date, bucket: str) -> None:
    if date_to < date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_to must be on or after date_from")
    if bucket == "day" and (date_to - date_from).days > _MAX_DAY_BUCKETS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Range too large for daily buckets (> {_MAX_DAY_BUCKETS} days); widen the bucket to week or month.",
        )


_METRIC_COLUMNS = (
    "run_count",
    "participant_count",
    "guest_count",
    "registered_count",
    "team_count",
    "submission_count",
    "scored_count",
    "unscored_count",
)
# The points and working time of the period have their own table, so the
# metrics table keeps its columns.
_POINTS_COLUMNS = (
    "run_count",
    "team_count",
    "total_points",
    "avg_points_per_team",
    "avg_points_per_answer",
    "points_percent",
    "teams_measured",
    "median_duration_minutes",
)


@timeseries_router.get(
    "/timeseries",
    response_model=AnalyticsTimeseriesResponse,
    responses={
        200: {"description": "Date-bucketed org analytics for the range"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (requires coordinator+ role)", "model": ErrorResponse},
        422: {"description": "Invalid range", "model": ErrorResponse},
    },
)
async def get_analytics_timeseries(
    date_from: date = Query(..., description="Inclusive range start (YYYY-MM-DD)"),
    date_to: date = Query(..., description="Inclusive range end (YYYY-MM-DD)"),
    bucket: Literal["day", "week", "month"] = Query("day", description="Bucket granularity"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> AnalyticsTimeseriesResponse:
    """Return the analytics of the organization over a date range, grouped into buckets.

    Open to coordinators, creators and organization admins. The route adds up
    every started run whose bucketing date lies between `date_from` and
    `date_to`, both included. The bucketing date of a run is its end, or its
    start while it has not ended. The buckets are `day`, `week` or `month`. A
    range that holds one run gives the participation numbers of that run. Runs
    that never started are not counted.

    The response names teams only and holds no personal data. A range with an end
    before its start gives 422, and so does a daily range whose ends are more
    than 366 days apart.
    """
    _validate_range(date_from, date_to, bucket)
    buckets, totals, tasks, teams = await _fetch_timeseries(db, org.id, date_from, date_to, bucket)
    return AnalyticsTimeseriesResponse(
        range=AnalyticsTimeseriesRange(date_from=date_from, date_to=date_to, bucket=bucket),
        totals=totals,
        buckets=buckets,
        tasks=tasks,
        teams=teams,
    )


@timeseries_router.get(
    "/timeseries/export",
    responses={
        200: {"description": "CSV of one table of the period", "content": {"text/csv": {}}},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (requires coordinator+ role)", "model": ErrorResponse},
        422: {"description": "Invalid range or table", "model": ErrorResponse},
    },
)
async def export_analytics_timeseries(
    request: Request,
    date_from: date = Query(...),
    date_to: date = Query(...),
    bucket: Literal["day", "week", "month"] = Query("day"),
    table: Literal["metrics", "points", "tasks", "teams"] = Query(
        "metrics",
        description=(
            "metrics: the counts per bucket (the default). points: points and working time per "
            "bucket. tasks and teams: one row per task or team over the whole period"
        ),
    ),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("coordinator")),
) -> Response:
    """Export one analytics table of a date range as a CSV file.

    Open to coordinators, creators and organization admins. The range and the
    buckets work as in the analytics of the range. The `table` parameter picks
    `metrics`, `points`, `tasks` or `teams`. The file names teams only and holds
    no personal data. The headers and the number and date format follow the
    `X-Clavis-Language` header, then `Accept-Language`.
    """
    _validate_range(date_from, date_to, bucket)
    buckets, _totals, tasks, teams = await _fetch_timeseries(db, org.id, date_from, date_to, bucket)
    lang = _csv_language(request)
    columns: list[str]
    rows: list[list[object]]

    if table == "metrics":
        columns = ["bucket_start", *_METRIC_COLUMNS]
        rows = [[b.bucket_start.date(), *[getattr(b, col) for col in _METRIC_COLUMNS]] for b in buckets]
    elif table == "points":
        columns = ["bucket_start", *_POINTS_COLUMNS]
        rows = [[b.bucket_start.date(), *[_blank(getattr(b, col)) for col in _POINTS_COLUMNS]] for b in buckets]
    elif table == "tasks":
        types = _CSV_TYPE_LABELS.get(lang, _CSV_TYPE_LABELS["de"])
        columns = [
            "station",
            "challenge_title",
            "type",
            "run_count",
            "submissions",
            "scored",
            "avg_points",
            "max_points",
            "time_team_count",
            "time_median_minutes",
            "time_p25_minutes",
            "time_p75_minutes",
        ]
        rows = [
            [
                _display_title(t.station_title),
                _display_title(t.title),
                types.get(t.type.value, t.type.value),
                t.run_count,
                t.submissions,
                t.scored,
                _blank(t.avg_points),
                _blank(t.max_points),
                t.time_team_count,
                minutes(t.time_median_seconds),
                minutes(t.time_p25_seconds),
                minutes(t.time_p75_seconds),
            ]
            for t in tasks
        ]
    else:
        columns = [
            "run_name",
            "run_date",
            "team_name",
            "total_points",
            "challenges_attempted",
            "challenges_scored",
            "duration_minutes",
        ]
        rows = [
            [
                t.run_name,
                t.run_date.date() if t.run_date is not None else "",
                t.team_name or "",
                t.points,
                t.answers,
                t.scored,
                minutes(t.duration_seconds),
            ]
            for t in teams
        ]

    # The period is in the name, like the JSON and PNG exports. The metrics
    # table keeps its old name.
    name = "timeseries" if table == "metrics" else f"period-{table}"
    return Response(
        content=_spreadsheet_csv(columns, rows, lang),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=analytics-{name}-{org.slug}-{date_from.isoformat()}-{date_to.isoformat()}.csv"
            )
        },
    )
