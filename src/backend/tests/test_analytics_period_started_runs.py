"""The period view counts only runs that started.

A run that was configured or planned and never started is no run of the
period: it is in no tile, no chart bucket, no points, no task row, no team row
and no export. A run that started counts, whether it is still active or ended
without a team.

One seeded set serves the tests of the runs that did not start. All of March 2026 is the range:

- "Lauf gestartet" started on the 10th and ended three hours later. Team
  "Gestartet" (one guest) joined ten minutes after the start and answered the
  task "Echte Aufgabe" thirty minutes later, for 7 of 10.
- "Lauf konfiguriert" was created on the 11th and never started.
- "Lauf geplant" was created on the 11th, planned for the 12th and never
  started.

Each of the two runs without a start holds a team with one registered person
and a scored answer (9 of 10). Such an answer does not arise in the product.
It is here so that every number of the view would move if the run counted.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember

# Midday UTC so the Europe/Berlin bucketing date equals the calendar date.
D10 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
D11 = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
D12 = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
MARCH = {"date_from": "2026-03-01", "date_to": "2026-03-31", "bucket": "day"}
URL = "/api/orgs/org-a/analytics/timeseries"
EXPORT_URL = "/api/orgs/org-a/analytics/timeseries/export"


async def _run(
    db: AsyncSession,
    org,
    name: str,
    state: RunState,
    *,
    created_at: datetime,
    scheduled_for: datetime | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> Run:
    run = Run(
        organization_id=org.id,
        name=name,
        state=state,
        join_code=uuid.uuid4().hex[:6].upper(),
        created_at=created_at,
        scheduled_for=scheduled_for,
        started_at=started_at,
        ended_at=ended_at,
    )
    db.add(run)
    await db.flush()
    return run


async def _team_with_answer(
    db: AsyncSession, org, run: Run, *, team: str, station: str, task: str, guest: bool, points: int, at: datetime
) -> None:
    """One team with one person and one scored answer to the one task of the run."""
    row = Team(
        organization_id=org.id,
        run_id=run.id,
        share_code=uuid.uuid4().hex[:8].upper(),
        name=team,
        created_at=at - timedelta(minutes=30),
    )
    db.add(row)
    await db.flush()
    db.add(TeamMember(organization_id=org.id, team_id=row.id, display_name="P", is_guest=guest))
    await db.flush()
    run_station = RunStation(organization_id=org.id, run_id=run.id, title={"de": station}, description={}, sort_order=0)
    db.add(run_station)
    await db.flush()
    run_task = RunChallenge(
        organization_id=org.id,
        run_id=run.id,
        run_station_id=run_station.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": task},
        instructions={"de": "x"},
        scoring_rules={"max_points": 10},
        sort_order=0,
    )
    db.add(run_task)
    await db.flush()
    submission = Submission(
        organization_id=org.id,
        run_id=run.id,
        team_id=row.id,
        run_challenge_id=run_task.id,
        content={"text": "a"},
        submitted_at=at,
    )
    db.add(submission)
    await db.flush()
    db.add(Score(organization_id=org.id, submission_id=submission.id, points=points, max_points=10))
    await db.flush()


async def _unstarted_runs(db: AsyncSession, org) -> None:
    configured = await _run(db, org, "Lauf konfiguriert", RunState.CONFIGURED, created_at=D11)
    await _team_with_answer(
        db,
        org,
        configured,
        team="Konfiguriert",
        station="Station konfiguriert",
        task="Aufgabe konfiguriert",
        guest=False,
        points=9,
        at=D11,
    )
    planned = await _run(db, org, "Lauf geplant", RunState.SCHEDULED, created_at=D11, scheduled_for=D12)
    await _team_with_answer(
        db,
        org,
        planned,
        team="Geplant",
        station="Station geplant",
        task="Aufgabe geplant",
        guest=False,
        points=9,
        at=D12,
    )


async def _seed(db: AsyncSession, org) -> None:
    started = await _run(
        db,
        org,
        "Lauf gestartet",
        RunState.ENDED,
        created_at=D10 - timedelta(days=1),
        started_at=D10,
        ended_at=D10 + timedelta(hours=3),
    )
    await _team_with_answer(
        db,
        org,
        started,
        team="Gestartet",
        station="Echte Station",
        task="Echte Aufgabe",
        guest=True,
        points=7,
        at=D10 + timedelta(minutes=40),
    )
    await _unstarted_runs(db, org)


def _csv_lines(text: str) -> list[str]:
    return text.strip("\r\n").split("\r\n")


# ============================================================================
# A configured run and a planned run are in no number of the period view
# ============================================================================


@pytest.mark.asyncio
async def test_run_count_leaves_out_a_configured_and_a_planned_run(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    """The count of runs is 1, not 3: the two runs that never started do not count."""
    await _seed(db_session, org_a)
    body = (await client_org_a.get(URL, params=MARCH)).json()
    assert body["totals"]["run_count"] == 1


@pytest.mark.asyncio
async def test_tiles_leave_out_a_configured_and_a_planned_run(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    await _seed(db_session, org_a)
    totals = (await client_org_a.get(URL, params=MARCH)).json()["totals"]
    # Only the team of the started run and its one guest.
    assert (totals["team_count"], totals["participant_count"]) == (1, 1)
    assert (totals["guest_count"], totals["registered_count"]) == (1, 0)
    assert (totals["submission_count"], totals["scored_count"], totals["unscored_count"]) == (1, 1, 0)


@pytest.mark.asyncio
async def test_chart_leaves_out_a_configured_and_a_planned_run(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    await _seed(db_session, org_a)
    by_day = {b["bucket_start"][:10]: b for b in (await client_org_a.get(URL, params=MARCH)).json()["buckets"]}
    assert by_day["2026-03-10"]["run_count"] == 1
    # The day of the creation of both runs and the planned day of one of them.
    for day in ("2026-03-11", "2026-03-12"):
        bucket = by_day[day]
        assert (bucket["run_count"], bucket["team_count"], bucket["participant_count"]) == (0, 0, 0)
        assert (bucket["guest_count"], bucket["registered_count"]) == (0, 0)
        assert (bucket["submission_count"], bucket["scored_count"], bucket["unscored_count"]) == (0, 0, 0)
    assert sum(b["run_count"] for b in by_day.values()) == 1


@pytest.mark.asyncio
async def test_points_leave_out_a_configured_and_a_planned_run(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    await _seed(db_session, org_a)
    body = (await client_org_a.get(URL, params=MARCH)).json()
    totals = body["totals"]
    # 7 of 10 of the one team. With the two other runs it would be 25 of 30.
    assert (totals["total_points"], totals["avg_points_per_team"]) == (7, 7.0)
    assert (totals["avg_points_per_answer"], totals["points_percent"]) == (7.0, 70.0)
    # The team joined at 12:10, after the start of the run, and answered at 12:40.
    assert (totals["teams_measured"], totals["median_duration_seconds"]) == (1, 1800)
    by_day = {b["bucket_start"][:10]: b for b in body["buckets"]}
    for day in ("2026-03-11", "2026-03-12"):
        bucket = by_day[day]
        assert (bucket["total_points"], bucket["avg_points_per_team"], bucket["points_percent"]) == (0, None, None)
        assert (bucket["teams_measured"], bucket["median_duration_seconds"]) == (0, None)


@pytest.mark.asyncio
async def test_task_table_leaves_out_a_configured_and_a_planned_run(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    await _seed(db_session, org_a)
    tasks = (await client_org_a.get(URL, params=MARCH)).json()["tasks"]
    assert [(t["station_title"], t["title"]) for t in tasks] == [({"de": "Echte Station"}, {"de": "Echte Aufgabe"})]
    assert (tasks[0]["run_count"], tasks[0]["submissions"], tasks[0]["scored"]) == (1, 1, 1)


@pytest.mark.asyncio
async def test_team_table_leaves_out_a_configured_and_a_planned_run(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    await _seed(db_session, org_a)
    teams = (await client_org_a.get(URL, params=MARCH)).json()["teams"]
    assert [(t["run_name"], t["team_name"], t["points"]) for t in teams] == [("Lauf gestartet", "Gestartet", 7)]
    # The date of the row is the start of its run.
    assert teams[0]["run_date"].startswith("2026-03-10T12:00")


@pytest.mark.asyncio
async def test_exports_leave_out_a_configured_and_a_planned_run(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    await _seed(db_session, org_a)

    metrics = _csv_lines((await client_org_a.get(EXPORT_URL, params=MARCH)).text)
    assert "10.03.2026;1;1;1;0;1;1;1;0" in metrics
    assert "11.03.2026;0;0;0;0;0;0;0;0" in metrics
    assert "12.03.2026;0;0;0;0;0;0;0;0" in metrics

    points = _csv_lines((await client_org_a.get(EXPORT_URL, params={**MARCH, "table": "points"})).text)
    assert "10.03.2026;1;1;7;7;7;70;1;30" in points
    assert "11.03.2026;0;0;0;;;;0;" in points
    assert "12.03.2026;0;0;0;;;;0;" in points

    tasks = _csv_lines((await client_org_a.get(EXPORT_URL, params={**MARCH, "table": "tasks"})).text)
    assert len(tasks) == 1 + 1
    assert tasks[1].startswith("Echte Station;Echte Aufgabe;")
    assert "konfiguriert" not in "".join(tasks) and "geplant" not in "".join(tasks)

    teams = _csv_lines((await client_org_a.get(EXPORT_URL, params={**MARCH, "table": "teams"})).text)
    assert len(teams) == 1 + 1
    assert teams[1] == "Lauf gestartet;10.03.2026;Gestartet;7;1;1;30"


@pytest.mark.asyncio
async def test_a_range_with_only_runs_without_a_start_is_empty(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    """No run of the period: the view says so instead of drawing a row of zeros."""
    await _unstarted_runs(db_session, org_a)
    body = (await client_org_a.get(URL, params=MARCH)).json()
    assert body["totals"]["run_count"] == 0
    assert (body["buckets"], body["tasks"], body["teams"]) == ([], [], [])
    assert len(_csv_lines((await client_org_a.get(EXPORT_URL, params=MARCH)).text)) == 1


# ============================================================================
# A run that started counts
# ============================================================================


@pytest.mark.asyncio
async def test_an_active_run_counts_in_the_bucket_of_its_start(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    # Created in February, planned for the 9th, started on the 10th, not ended.
    run = await _run(
        db_session,
        org_a,
        "Lauf aktiv",
        RunState.ACTIVE,
        created_at=datetime(2026, 2, 20, 12, 0, tzinfo=UTC),
        scheduled_for=D10 - timedelta(days=1),
        started_at=D10,
    )
    await _team_with_answer(
        db_session,
        org_a,
        run,
        team="Aktiv",
        station="Station",
        task="Aufgabe",
        guest=True,
        points=7,
        at=D10 + timedelta(minutes=40),
    )
    body = (await client_org_a.get(URL, params=MARCH)).json()
    assert (body["totals"]["run_count"], body["totals"]["team_count"], body["totals"]["total_points"]) == (1, 1, 7)
    with_runs = {b["bucket_start"][:10]: b["run_count"] for b in body["buckets"] if b["run_count"]}
    assert with_runs == {"2026-03-10": 1}
    assert [t["team_name"] for t in body["teams"]] == ["Aktiv"]


@pytest.mark.asyncio
async def test_a_run_that_started_and_ended_without_a_team_counts(
    client_org_a: AsyncClient, db_session: AsyncSession, org_a
):
    await _run(
        db_session,
        org_a,
        "Lauf ohne Team",
        RunState.ENDED,
        created_at=D10 - timedelta(days=1),
        started_at=D10,
        ended_at=D10 + timedelta(hours=3),
    )
    body = (await client_org_a.get(URL, params=MARCH)).json()
    totals = body["totals"]
    assert (totals["run_count"], totals["team_count"], totals["participant_count"]) == (1, 0, 0)
    with_runs = {b["bucket_start"][:10]: b["run_count"] for b in body["buckets"] if b["run_count"]}
    assert with_runs == {"2026-03-10": 1}
    assert body["teams"] == []
    assert "10.03.2026;1;0;0;0;0;0;0;0" in _csv_lines((await client_org_a.get(EXPORT_URL, params=MARCH)).text)
