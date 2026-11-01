"""
Working time ("Bearbeitungsdauer") and points per team, from stored timestamps.

The app records no moment at which a team opens a station or a challenge, and
no pauses of a run. So every time measure is built from three timestamps:

- ``Run.started_at``: the start of the run.
- ``Team.created_at``: counts only for a team that joined after the start.
- ``Submission.submitted_at``: the answer that counts. Replacing an unscored
  answer moves it to the replacement.

The measures:

- Working time: from the team's start to its last answer.
- Longest gap: the longest time between two answers in a row. It shows a break
  that the app does not record.
- Time to an answer: from the team's previous answer, or its start, to the
  answer of this challenge. It contains the way to the station. Over all teams
  it is given as median and spread.

Privacy boundary: team level only, no person, no location, no device. One query
for the teams and one for the submissions serve any number of runs, so callers
keep a constant query count.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team


@dataclass
class Spread:
    """Median and spread of a set of durations in whole seconds."""

    count: int = 0
    median: int | None = None
    p25: int | None = None
    p75: int | None = None
    minimum: int | None = None
    maximum: int | None = None


def _quantile(ordered: list[float], q: float) -> float:
    """Linear interpolation between the closest ranks of an ordered list."""
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def spread(values: list[float]) -> Spread:
    """Median, quartiles, minimum and maximum. An empty list gives no value at all."""
    if not values:
        return Spread()
    ordered = sorted(values)
    return Spread(
        count=len(ordered),
        median=round(_quantile(ordered, 0.5)),
        p25=round(_quantile(ordered, 0.25)),
        p75=round(_quantile(ordered, 0.75)),
        minimum=round(ordered[0]),
        maximum=round(ordered[-1]),
    )


def median_or_none(values: list[float]) -> float | None:
    return _quantile(sorted(values), 0.5) if values else None


@dataclass
class TeamTime:
    """The time measures and the points of one team."""

    team_id: UUID
    run_id: UUID
    name: str | None = None
    started_at: datetime | None = None
    last_answer_at: datetime | None = None
    duration_seconds: int | None = None
    longest_gap_seconds: int | None = None
    answers: int = 0
    scored: int = 0
    points: int = 0
    max_points_scored: int = 0


@dataclass
class RunTimes:
    """Everything the time measures of one run need."""

    run_id: UUID
    teams: dict[UUID, TeamTime] = field(default_factory=dict)
    # run_challenge_id -> seconds from the previous answer of each team
    steps: dict[UUID, list[float]] = field(default_factory=lambda: defaultdict(list))
    # run_challenge_id -> the teams that answered, and the scores they got
    answered_by: dict[UUID, set[UUID]] = field(default_factory=lambda: defaultdict(set))
    scores: dict[UUID, list[int]] = field(default_factory=lambda: defaultdict(list))

    def durations(self) -> list[float]:
        return [float(t.duration_seconds) for t in self.teams.values() if t.duration_seconds is not None]


def team_start(
    run_started_at: datetime | None, team_created_at: datetime, first_answer_at: datetime
) -> datetime | None:
    """The start of the run, or the team's creation for a team that came later.

    A creation after the first answer happens only in imported or seeded data,
    and the run start stays. A run without a start has no clock.
    """
    if run_started_at is None:
        return None
    if run_started_at < team_created_at <= first_answer_at:
        return team_created_at
    return run_started_at


async def fetch_run_times(db: AsyncSession, org_id: UUID, run_ids: list[UUID]) -> dict[UUID, RunTimes]:
    """The time measures and points of every team of the given runs, in two queries."""
    result: dict[UUID, RunTimes] = {run_id: RunTimes(run_id=run_id) for run_id in run_ids}
    if not run_ids:
        return result

    team_rows = (
        await db.execute(
            select(Team.id, Team.run_id, Team.name, Team.created_at, Run.started_at)
            .join(Run, Run.id == Team.run_id)
            .where(
                Team.organization_id == org_id,
                Team.run_id.in_(run_ids),
                Team.is_deleted == False,  # noqa: E712
            )
        )
    ).all()
    team_meta: dict[UUID, tuple[datetime, datetime | None]] = {}
    for team_id, run_id, name, created_at, run_started_at in team_rows:
        result[run_id].teams[team_id] = TeamTime(team_id=team_id, run_id=run_id, name=name)
        team_meta[team_id] = (created_at, run_started_at)

    answer_rows = (
        await db.execute(
            select(
                Submission.run_id,
                Submission.team_id,
                Submission.run_challenge_id,
                Submission.submitted_at,
                Score.points,
                Score.max_points,
            )
            .outerjoin(Score, Score.submission_id == Submission.id)
            .where(Submission.organization_id == org_id, Submission.run_id.in_(run_ids))
            .order_by(Submission.team_id, Submission.submitted_at)
        )
    ).all()

    previous: dict[UUID, datetime] = {}
    for run_id, team_id, run_challenge_id, submitted_raw, points, max_points in answer_rows:
        team = result[run_id].teams.get(team_id)
        if team is None:  # the answers of a deleted team do not count
            continue
        submitted_at = cast(datetime, submitted_raw)
        team.answers += 1
        result[run_id].answered_by[run_challenge_id].add(team_id)
        if points is not None:
            result[run_id].scores[run_challenge_id].append(int(points))
            team.scored += 1
            team.points += int(points)
            team.max_points_scored += int(max_points or 0)

        if team_id not in previous:
            created_at, run_started_at = team_meta[team_id]
            team.started_at = team_start(run_started_at, created_at, submitted_at)
            reference = team.started_at
        else:
            reference = previous[team_id]
            gap = max(int((submitted_at - reference).total_seconds()), 0)
            team.longest_gap_seconds = max(team.longest_gap_seconds or 0, gap)

        if reference is not None:
            result[run_id].steps[run_challenge_id].append(max((submitted_at - reference).total_seconds(), 0.0))
        previous[team_id] = submitted_at
        team.last_answer_at = submitted_at
        if team.started_at is not None:
            team.duration_seconds = max(int((submitted_at - team.started_at).total_seconds()), 0)

    return result
