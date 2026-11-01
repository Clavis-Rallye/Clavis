"""Points, working time and the run comparison.

One seeded set of four runs serves every test. Times are fixed, so every
expected value below is computed by hand:

- Run A (rallye, 2026-03-10, start 10:00 UTC). Team A1 answers task 1 at
  10:20 (8 of 10) and task 2 at 10:50 (20 of 20). Team A2 joins at 10:10 and
  answers task 1 at 10:40 (4 of 10). Team A3 never answers. Team A4 is
  deleted, and its scored answer must count nowhere.
- Run B (same rallye, 2026-03-17, start 10:00). Team B1 answers task 1 at
  10:10 (10 of 10) and task 2 at 10:40 (not scored).
- Run C (no rallye, 2026-03-24, start 09:00). One station "Dom", one task
  without a library source. Team C1 answers at 10:00 (5 of 5).
- Run D (never started). One team, no answer.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import UserRole
from app.services.analytics_time import spread, team_start
from tests.factories import create_test_user, get_auth_headers
from tests.journey_helpers import JourneyEnv, setup_journey_environment

A_START = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)
B_START = datetime(2026, 3, 17, 10, 0, tzinfo=UTC)
C_START = datetime(2026, 3, 24, 9, 0, tzinfo=UTC)
MARCH = {"date_from": "2026-03-01", "date_to": "2026-03-31", "bucket": "week"}


def _min(minutes: int, start: datetime = A_START) -> datetime:
    return start + timedelta(minutes=minutes)


@dataclass
class Seed:
    env: JourneyEnv
    run_a: Run
    run_b: Run
    run_c: Run
    run_d: Run
    teams: dict[str, Team]
    lib_tasks: list[UUID]
    lib_stations: list[UUID]


async def _run(db: AsyncSession, env: JourneyEnv, name: str, *, rallye: bool, started_at: datetime | None) -> Run:
    run = Run(
        organization_id=env.org.id,
        rallye_id=env.rallye_id if rallye else None,
        name=name,
        state=RunState.ENDED if started_at else RunState.CONFIGURED,
        join_code=uuid4().hex[:6].upper(),
        started_at=started_at,
        ended_at=started_at + timedelta(hours=3) if started_at else None,
    )
    db.add(run)
    await db.flush()
    return run


async def _copy_rallye(db: AsyncSession, env: JourneyEnv, run: Run) -> list[RunChallenge]:
    """The run's frozen copy of the two library stations with one task each."""
    tasks: list[RunChallenge] = []
    for index, (station_id, challenge_ids) in enumerate(zip(env.station_ids, env.challenge_ids, strict=True)):
        station = RunStation(
            organization_id=env.org.id,
            run_id=run.id,
            source_station_id=station_id,
            title={"de": f"Station {index + 1}", "en": f"Station {index + 1}"},
            description={},
            sort_order=index,
        )
        db.add(station)
        await db.flush()
        task = RunChallenge(
            organization_id=env.org.id,
            run_id=run.id,
            run_station_id=station.id,
            source_challenge_id=challenge_ids[0],
            challenge_type=env.challenge_types[challenge_ids[0]],
            title={"de": f"Aufgabe {index + 1}", "en": f"Task {index + 1}"},
            instructions={},
            scoring_rules={"max_points": 10 if index == 0 else 20},
            sort_order=0,
        )
        db.add(task)
        await db.flush()
        tasks.append(task)
    return tasks


async def _team(
    db: AsyncSession, env: JourneyEnv, run: Run, name: str, *, created_at: datetime, guest: bool | None = None
) -> Team:
    team = Team(
        organization_id=env.org.id,
        run_id=run.id,
        name=name,
        share_code=uuid4().hex[:8].upper(),
        created_at=created_at,
    )
    db.add(team)
    await db.flush()
    if guest is not None:
        db.add(TeamMember(organization_id=env.org.id, team_id=team.id, display_name="Person X", is_guest=guest))
        await db.flush()
    return team


async def _answer(
    db: AsyncSession, env: JourneyEnv, team: Team, task: RunChallenge, at: datetime, points: int | None = None
) -> None:
    submission = Submission(
        organization_id=env.org.id,
        run_id=team.run_id,
        team_id=team.id,
        run_challenge_id=task.id,
        content={"text": "x"},
        submitted_at=at,
    )
    db.add(submission)
    await db.flush()
    if points is not None:
        max_points = int(task.scoring_rules["max_points"])
        db.add(Score(organization_id=env.org.id, submission_id=submission.id, points=points, max_points=max_points))
        await db.flush()


async def _seed(db: AsyncSession, client: AsyncClient, slug: str) -> Seed:
    env = await setup_journey_environment(db, client, org_slug=slug, num_stations=2, challenges_per_station=1)
    teams: dict[str, Team] = {}

    run_a = await _run(db, env, "Lauf A", rallye=True, started_at=A_START)
    a1, a2 = await _copy_rallye(db, env, run_a)
    teams["A1"] = await _team(db, env, run_a, "A1", created_at=_min(-30), guest=True)
    teams["A2"] = await _team(db, env, run_a, "A2", created_at=_min(10), guest=False)
    teams["A3"] = await _team(db, env, run_a, "A3", created_at=_min(-5))
    teams["A4"] = await _team(db, env, run_a, "A4", created_at=_min(-5))
    await _answer(db, env, teams["A1"], a1, _min(20), 8)
    await _answer(db, env, teams["A1"], a2, _min(50), 20)
    await _answer(db, env, teams["A2"], a1, _min(40), 4)
    await _answer(db, env, teams["A4"], a1, _min(15), 10)
    teams["A4"].is_deleted = True
    teams["A4"].deleted_at = _min(60)

    run_b = await _run(db, env, "Lauf B", rallye=True, started_at=B_START)
    b1, b2 = await _copy_rallye(db, env, run_b)
    teams["B1"] = await _team(db, env, run_b, "B1", created_at=_min(-60, B_START), guest=True)
    await _answer(db, env, teams["B1"], b1, _min(10, B_START), 10)
    await _answer(db, env, teams["B1"], b2, _min(40, B_START))

    run_c = await _run(db, env, "Lauf C", rallye=False, started_at=C_START)
    station = RunStation(organization_id=env.org.id, run_id=run_c.id, title={"de": "Dom"}, description={}, sort_order=0)
    db.add(station)
    await db.flush()
    c_task = RunChallenge(
        organization_id=env.org.id,
        run_id=run_c.id,
        run_station_id=station.id,
        challenge_type=ChallengeType.QUIZ,
        title={"de": "Frage"},
        instructions={},
        scoring_rules={"max_points": 5},
        sort_order=0,
    )
    db.add(c_task)
    await db.flush()
    teams["C1"] = await _team(db, env, run_c, "C1", created_at=_min(-10, C_START))
    await _answer(db, env, teams["C1"], c_task, _min(60, C_START), 5)

    run_d = await _run(db, env, "Lauf D", rallye=True, started_at=None)
    await _copy_rallye(db, env, run_d)
    teams["D1"] = await _team(db, env, run_d, "D1", created_at=_min(0))
    await db.commit()
    return Seed(
        env=env,
        run_a=run_a,
        run_b=run_b,
        run_c=run_c,
        run_d=run_d,
        teams=teams,
        lib_tasks=[ids[0] for ids in env.challenge_ids],
        lib_stations=list(env.station_ids),
    )


def _csv_lines(text: str) -> list[str]:
    return text.strip("\r\n").split("\r\n")


# ============================================================================
# The definitions, without a database
# ============================================================================


def test_spread_is_median_quartiles_and_range():
    assert spread([]).count == 0 and spread([]).median is None
    one = spread([90.0])
    assert (one.count, one.median, one.p25, one.p75, one.minimum, one.maximum) == (1, 90, 90, 90, 90, 90)
    four = spread([600.0, 1200.0, 1800.0, 3000.0])
    assert (four.median, four.p25, four.p75, four.minimum, four.maximum) == (1500, 1050, 2100, 600, 3000)


def test_team_start_is_the_run_start_or_a_later_creation():
    start = A_START
    assert team_start(None, _min(-5), _min(20)) is None
    # Created before the start: the run start counts.
    assert team_start(start, _min(-5), _min(20)) == start
    # Joined during the run: its own creation counts.
    assert team_start(start, _min(10), _min(20)) == _min(10)
    # A creation after the first answer cannot happen in operation: the run start stays.
    assert team_start(start, _min(30), _min(20)) == start


# ============================================================================
# One run
# ============================================================================


@pytest.mark.asyncio
async def test_run_analytics_has_working_time_per_team_and_task(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "offer-run")
    response = await client.get(
        f"/api/orgs/{seed.env.slug}/runs/{seed.run_a.id}/analytics",
        headers=seed.env.auth_headers["coordinator"],
    )
    assert response.status_code == 200
    body = response.json()

    teams = {t["team_name"]: t for t in body["teams"]}
    assert "A4" not in teams
    # A1: start 10:00, last answer 10:50, the gap between its answers is 30 minutes.
    assert (teams["A1"]["duration_seconds"], teams["A1"]["longest_gap_seconds"]) == (3000, 1800)
    # A2 joined at 10:10, so its clock starts there: 30 minutes, no gap with one answer.
    assert (teams["A2"]["duration_seconds"], teams["A2"]["longest_gap_seconds"]) == (1800, None)
    assert teams["A2"]["started_at"].startswith("2026-03-10T10:10")
    assert (teams["A3"]["duration_seconds"], teams["A3"]["last_answer_at"]) == (None, None)

    assert body["working_time"] == {
        "teams_measured": 2,
        "median_seconds": 2400,
        "p25_seconds": 2100,
        "p75_seconds": 2700,
        "min_seconds": 1800,
        "max_seconds": 3000,
    }
    assert body["started_at"].startswith("2026-03-10T10:00")

    first, second = body["challenges"]
    # Task 1: A1 after 20 minutes, A2 30 minutes after its own start. The deleted team does not count.
    assert (first["time_team_count"], first["time_median_seconds"]) == (2, 1500)
    assert (first["time_p25_seconds"], first["time_p75_seconds"]) == (1350, 1650)
    assert (first["time_min_seconds"], first["time_max_seconds"]) == (1200, 1800)
    # Task 2: A1, 30 minutes after its answer to task 1.
    assert (second["time_team_count"], second["time_median_seconds"]) == (1, 1800)


@pytest.mark.asyncio
async def test_a_run_without_a_start_has_no_working_time(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "offer-nostart")
    body = (
        await client.get(
            f"/api/orgs/{seed.env.slug}/runs/{seed.run_d.id}/analytics",
            headers=seed.env.auth_headers["coordinator"],
        )
    ).json()
    assert body["working_time"]["teams_measured"] == 0
    assert body["working_time"]["median_seconds"] is None
    assert all(c["time_team_count"] == 0 for c in body["challenges"])


@pytest.mark.asyncio
async def test_old_exports_keep_their_columns_and_new_tables_hold_the_times(
    client: AsyncClient, db_session: AsyncSession
):
    seed = await _seed(db_session, client, "offer-csv")
    base = f"/api/orgs/{seed.env.slug}/runs/{seed.run_a.id}/analytics/export"
    headers = seed.env.auth_headers["coordinator"]

    teams = _csv_lines((await client.get(base, params={"table": "teams"}, headers=headers)).text)
    assert teams[0] == "\ufeffTeam;Mitglieder;Gäste;Punkte;Aufgaben beantwortet;Aufgaben bewertet"
    tasks = _csv_lines((await client.get(base, params={"table": "challenges"}, headers=headers)).text)
    assert tasks[0] == "\ufeffStation;Aufgabe;Typ;Einreichungen;Bewertet;Punkte im Schnitt"

    working = await client.get(base, params={"table": "working_time"}, headers=headers)
    assert working.status_code == 200
    assert (
        working.headers["content-disposition"] == f"attachment; filename=run-analytics-working_time-{seed.run_a.id}.csv"
    )
    lines = _csv_lines(working.text)
    assert lines[0] == (
        "\ufeffTeam;Punkte;Aufgaben beantwortet;Beginn;Letzte Antwort;Bearbeitungsdauer (Min.);"
        "Längster Abstand zwischen zwei Antworten (Min.)"
    )
    # Local time (UTC+1 in March), minutes with the decimal comma, an empty cell for no value.
    assert "A1;28;2;10.03.2026 11:00;10.03.2026 11:50;50;30" in lines
    assert "A2;4;1;10.03.2026 11:10;10.03.2026 11:40;30;" in lines
    assert "A3;0;0;;;;" in lines
    # Team level only: no person's name.
    assert "Person X" not in working.text

    times = _csv_lines((await client.get(base, params={"table": "task_times"}, headers=headers)).text)
    assert times[0].startswith("\ufeffStation;Aufgabe;Typ;Einreichungen;Bewertet;Punkte im Schnitt;Max. Punkte;")
    assert times[1] == "Station 1;Aufgabe 1;Text;2;2;6;10;2;25;22,5;27,5;20;30"

    english = _csv_lines(
        (await client.get(base, params={"table": "working_time"}, headers={**headers, "X-Clavis-Language": "en"})).text
    )
    assert english[0].startswith("\ufeffTeam,Points,Tasks answered,Start,Last answer,Working time (min)")
    assert "A1,28,2,2026-03-10 11:00,2026-03-10 11:50,50,30" in english


# ============================================================================
# A period
# ============================================================================


@pytest.mark.asyncio
async def test_period_has_points_working_time_tasks_and_teams(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "offer-period")
    response = await client.get(
        f"/api/orgs/{seed.env.slug}/analytics/timeseries",
        params=MARCH,
        headers=seed.env.auth_headers["coordinator"],
    )
    assert response.status_code == 200
    body = response.json()

    totals = body["totals"]
    # 28 + 4 (run A, without the deleted team) + 10 (B) + 5 (C) over five teams.
    assert (totals["total_points"], totals["avg_points_per_team"]) == (47, 9.4)
    # Five scored answers. Their maximum is 10 + 20 + 10 + 10 + 5 = 55.
    assert (totals["avg_points_per_answer"], totals["points_percent"]) == (9.4, 85.5)
    # Working times 50, 30, 40 and 60 minutes.
    assert (totals["teams_measured"], totals["median_duration_seconds"]) == (4, 2700)

    by_week = {b["bucket_start"][:10]: b for b in body["buckets"]}
    assert (by_week["2026-03-09"]["total_points"], by_week["2026-03-09"]["avg_points_per_team"]) == (32, 10.67)
    assert by_week["2026-03-09"]["median_duration_seconds"] == 2400
    assert by_week["2026-03-16"]["total_points"] == 10
    # A week without a run has no points and no working time, not a zero time.
    assert (by_week["2026-03-02"]["total_points"], by_week["2026-03-02"]["median_duration_seconds"]) == (0, None)

    tasks = {t["key"]: t for t in body["tasks"]}
    task_1 = tasks[str(seed.lib_tasks[0])]
    # The same library task in runs A and B is one row.
    assert (task_1["run_count"], task_1["submissions"], task_1["scored"]) == (2, 3, 3)
    assert task_1["avg_points"] == pytest.approx(7.33)
    # 20, 30 (A) and 10 minutes (B).
    assert (task_1["time_team_count"], task_1["time_median_seconds"]) == (3, 1200)
    dom = next(t for t in body["tasks"] if t["station_title"] == {"de": "Dom"})
    assert (dom["run_count"], dom["key"]) == (1, "title:Dom|Frage")

    ranking = [(t["team_name"], t["points"]) for t in body["teams"]]
    assert ranking == [("A1", 28), ("B1", 10), ("C1", 5), ("A2", 4), ("A3", 0)]
    assert body["teams"][0]["duration_seconds"] == 3000
    assert body["teams"][0]["run_name"] == "Lauf A"


@pytest.mark.asyncio
async def test_period_export_tables(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "offer-period-csv")
    url = f"/api/orgs/{seed.env.slug}/analytics/timeseries/export"
    headers = seed.env.auth_headers["coordinator"]

    metrics = await client.get(url, params=MARCH, headers=headers)
    # Without "table", the export keeps the columns and the file name of the metrics table.
    assert _csv_lines(metrics.text)[0] == (
        "\ufeffZeitraum ab;Läufe;Teilnehmende;Gäste;Registrierte;Teams;Einreichungen;Bewertet;Unbewertet"
    )
    assert "analytics-timeseries-" in metrics.headers["content-disposition"]

    points = await client.get(url, params={**MARCH, "table": "points"}, headers=headers)
    lines = _csv_lines(points.text)
    assert lines[0].startswith("\ufeffZeitraum ab;Läufe;Teams;Punkte;Punkte je Team im Schnitt")
    assert "09.03.2026;1;3;32;10,67;10,67;80;2;40" in lines
    assert "02.03.2026;0;0;0;;;;0;" in lines
    assert "analytics-period-points-" in points.headers["content-disposition"]

    teams = _csv_lines((await client.get(url, params={**MARCH, "table": "teams"}, headers=headers)).text)
    assert teams[0] == ("\ufeffLauf;Datum;Team;Punkte;Aufgaben beantwortet;Aufgaben bewertet;Bearbeitungsdauer (Min.)")
    assert teams[1] == "Lauf A;10.03.2026;A1;28;2;2;50"

    tasks = _csv_lines((await client.get(url, params={**MARCH, "table": "tasks"}, headers=headers)).text)
    assert tasks[0].startswith("\ufeffStation;Aufgabe;Typ;Läufe;Einreichungen;Bewertet;Punkte im Schnitt")
    assert "Station 1;Aufgabe 1;Text;2;3;3;7,33;10;3;20;15;25" in tasks

    wrong = await client.get(url, params={**MARCH, "table": "people"}, headers=headers)
    assert wrong.status_code == 422


# ============================================================================
# Removed teams and deleted runs count nowhere
# ============================================================================


async def _delete_run(db: AsyncSession, run: Run) -> None:
    run.is_deleted = True
    run.deleted_at = _min(0, C_START + timedelta(days=1))
    await db.commit()


@pytest.mark.asyncio
async def test_station_funnel_leaves_out_a_removed_team(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "q2-funnel")
    body = (
        await client.get(
            f"/api/orgs/{seed.env.slug}/runs/{seed.run_a.id}/analytics",
            headers=seed.env.auth_headers["coordinator"],
        )
    ).json()
    # Station 1: A1 and A2 answered and were scored. A4 answered too, then was removed.
    funnel = [(s["teams_attempted"], s["teams_scored"]) for s in body["stations"]]
    assert funnel == [(2, 2), (1, 1)]


@pytest.mark.asyncio
async def test_task_statistics_leave_out_a_removed_team(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "q2-tasks")
    headers = seed.env.auth_headers["coordinator"]
    body = (await client.get(f"/api/orgs/{seed.env.slug}/runs/{seed.run_a.id}/analytics", headers=headers)).json()
    first, second = body["challenges"]
    # Task 1: 8 (A1) and 4 (A2). The 10 of the removed team A4 counts nowhere.
    assert (first["submissions"], first["scored"], first["avg_points"]) == (2, 2, 6.0)
    assert (second["submissions"], second["scored"], second["avg_points"]) == (1, 1, 20.0)

    # The export uses the same numbers.
    url = f"/api/orgs/{seed.env.slug}/runs/{seed.run_a.id}/analytics/export"
    lines = _csv_lines((await client.get(url, params={"table": "challenges"}, headers=headers)).text)
    assert lines[1] == "Station 1;Aufgabe 1;Text;2;2;6"


@pytest.mark.asyncio
async def test_timeline_leaves_out_a_removed_team(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "q2-timeline")
    headers = seed.env.auth_headers["coordinator"]
    body = (await client.get(f"/api/orgs/{seed.env.slug}/runs/{seed.run_a.id}/analytics", headers=headers)).json()
    # Answers of run A: A1 after 20 and 50 minutes, A2 after 40. The answer of the
    # removed team A4 after 15 minutes would fall into the window of A1's first answer.
    timeline = [(b["bucket_start"], b["submissions"]) for b in body["timeline"]]
    assert [count for _, count in timeline] == [1, 1, 1]
    assert [datetime.fromisoformat(start) for start, _ in timeline] == [_min(15), _min(30), _min(45)]
    # The timeline agrees with the task table of the same page.
    assert sum(count for _, count in timeline) == sum(c["submissions"] for c in body["challenges"])


@pytest.mark.asyncio
async def test_period_answer_counts_leave_out_a_removed_team(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "q2-period")
    headers = seed.env.auth_headers["coordinator"]
    body = (await client.get(f"/api/orgs/{seed.env.slug}/analytics/timeseries", params=MARCH, headers=headers)).json()
    # Answers: A1 2, A2 1 (run A, without A4), B1 2 (one unscored), C1 1.
    totals = body["totals"]
    assert (totals["submission_count"], totals["scored_count"], totals["unscored_count"]) == (6, 5, 1)
    by_week = {b["bucket_start"][:10]: b for b in body["buckets"]}
    assert (by_week["2026-03-09"]["submission_count"], by_week["2026-03-09"]["scored_count"]) == (3, 3)

    url = f"/api/orgs/{seed.env.slug}/analytics/timeseries/export"
    lines = _csv_lines((await client.get(url, params=MARCH, headers=headers)).text)
    # Run A: two people (one guest), three teams, three answers, all scored.
    assert "09.03.2026;1;2;1;1;3;3;3;0" in lines


@pytest.mark.asyncio
async def test_period_leaves_out_a_deleted_run(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "q5-period")
    await _delete_run(db_session, seed.run_c)
    headers = seed.env.auth_headers["coordinator"]
    body = (await client.get(f"/api/orgs/{seed.env.slug}/analytics/timeseries", params=MARCH, headers=headers)).json()

    totals = body["totals"]
    # Runs A and B: teams A1, A2, A3 and B1, people A1, A2 and B1 (two guests).
    assert (totals["run_count"], totals["team_count"]) == (2, 4)
    assert (totals["participant_count"], totals["guest_count"], totals["registered_count"]) == (3, 2, 1)
    assert (totals["submission_count"], totals["scored_count"], totals["unscored_count"]) == (5, 4, 1)
    # 28 + 4 + 10 over four teams, four scored answers with a maximum of 10 + 20 + 10 + 10.
    assert (totals["total_points"], totals["avg_points_per_team"]) == (42, 10.5)
    assert (totals["avg_points_per_answer"], totals["points_percent"]) == (10.5, 84.0)
    # Working times 50, 30 and 40 minutes.
    assert (totals["teams_measured"], totals["median_duration_seconds"]) == (3, 2400)

    by_week = {b["bucket_start"][:10]: b for b in body["buckets"]}
    week_c = by_week["2026-03-23"]
    assert (week_c["run_count"], week_c["team_count"], week_c["submission_count"], week_c["total_points"]) == (
        0,
        0,
        0,
        0,
    )

    assert all(t["station_title"] != {"de": "Dom"} for t in body["tasks"])
    ranking = [(t["team_name"], t["points"]) for t in body["teams"]]
    assert ranking == [("A1", 28), ("B1", 10), ("A2", 4), ("A3", 0)]


@pytest.mark.asyncio
async def test_period_export_leaves_out_a_deleted_run(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "q5-period-csv")
    await _delete_run(db_session, seed.run_c)
    url = f"/api/orgs/{seed.env.slug}/analytics/timeseries/export"
    headers = seed.env.auth_headers["coordinator"]

    metrics = _csv_lines((await client.get(url, params=MARCH, headers=headers)).text)
    assert "23.03.2026;0;0;0;0;0;0;0;0" in metrics
    points = _csv_lines((await client.get(url, params={**MARCH, "table": "points"}, headers=headers)).text)
    assert "23.03.2026;0;0;0;;;;0;" in points
    teams = (await client.get(url, params={**MARCH, "table": "teams"}, headers=headers)).text
    assert "Lauf C" not in teams
    tasks = (await client.get(url, params={**MARCH, "table": "tasks"}, headers=headers)).text
    assert "Dom" not in tasks

    # The single-run view does not offer a deleted run either.
    single = await client.get(f"/api/orgs/{seed.env.slug}/runs/{seed.run_c.id}/analytics", headers=headers)
    assert single.status_code == 404


# ============================================================================
# The comparison
# ============================================================================


@pytest.mark.asyncio
async def test_compare_runs_side_by_side(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "offer-compare")
    response = await client.get(
        f"/api/orgs/{seed.env.slug}/analytics/compare",
        params={"run_ids": [str(seed.run_b.id), str(seed.run_a.id), str(seed.run_c.id)]},
        headers=seed.env.auth_headers["coordinator"],
    )
    assert response.status_code == 200
    body = response.json()
    # The order of the request.
    assert [r["name"] for r in body["runs"]] == ["Lauf B", "Lauf A", "Lauf C"]

    run_a = body["runs"][1]
    assert (run_a["team_count"], run_a["participant_count"], run_a["guest_count"]) == (3, 2, 1)
    assert (run_a["station_count"], run_a["task_count"], run_a["submission_count"]) == (2, 2, 3)
    # Three answers of six possible, one team answered both tasks.
    assert (run_a["completion_percent"], run_a["teams_finished"]) == (50.0, 1)
    assert (run_a["total_points"], run_a["achievable_points"]) == (32, 30)
    assert (run_a["avg_points_per_team"], run_a["median_points_per_team"]) == (10.67, 4.0)
    assert run_a["points_percent"] == 35.6
    assert run_a["working_time"]["median_seconds"] == 2400
    assert body["runs"][0]["working_time"]["median_seconds"] == 2400
    assert body["runs"][2]["completion_percent"] == 100.0

    stations = {s["title"]["de"]: s for s in body["stations"]}
    a, b, c = str(seed.run_a.id), str(seed.run_b.id), str(seed.run_c.id)
    # The same library station in A and B is one row. Run C does not hold it.
    assert set(stations["Station 1"]["cells"]) == {a, b}
    assert stations["Station 1"]["cells"][a] == {"team_count": 3, "teams_attempted": 2, "percent": 66.7}
    assert stations["Station 2"]["cells"][a]["percent"] == 33.3
    assert set(stations["Dom"]["cells"]) == {c}

    task_1 = next(t for t in body["tasks"] if t["key"].endswith(str(seed.lib_tasks[0])))
    assert task_1["cells"][a]["submissions"] == 2
    assert task_1["cells"][a]["avg_points"] == 6.0
    assert task_1["cells"][a]["time_median_seconds"] == 1500
    assert task_1["cells"][b]["time_median_seconds"] == 600

    # Team level only: no person, no e-mail, no user id.
    text = response.text
    assert "Person X" not in text
    assert seed.env.users["participant1"].email not in text


@pytest.mark.asyncio
async def test_compare_takes_two_to_six_runs_of_the_own_organization(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "offer-compare-limits")
    url = f"/api/orgs/{seed.env.slug}/analytics/compare"
    headers = seed.env.auth_headers["coordinator"]

    one = await client.get(url, params={"run_ids": [str(seed.run_a.id)]}, headers=headers)
    assert one.status_code == 422
    # The same run twice is one run.
    twice = await client.get(url, params={"run_ids": [str(seed.run_a.id)] * 2}, headers=headers)
    assert twice.status_code == 422
    seven = await client.get(url, params={"run_ids": [str(uuid4()) for _ in range(7)]}, headers=headers)
    assert seven.status_code == 422
    unknown = await client.get(url, params={"run_ids": [str(seed.run_a.id), str(uuid4())]}, headers=headers)
    assert unknown.status_code == 404

    other = await setup_journey_environment(db_session, client, org_slug="offer-compare-other")
    foreign = await client.get(
        f"/api/orgs/{other.slug}/analytics/compare",
        params={"run_ids": [str(seed.run_a.id), str(seed.run_b.id)]},
        headers=other.auth_headers["coordinator"],
    )
    assert foreign.status_code == 404

    participant = await create_test_user(
        db_session,
        email="p-compare@example.com",
        display_name="P",
        role=UserRole.PARTICIPANT,
        organization=seed.env.org,
    )
    denied = await client.get(
        url, params={"run_ids": [str(seed.run_a.id), str(seed.run_b.id)]}, headers=get_auth_headers(participant)
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_compare_export_tables(client: AsyncClient, db_session: AsyncSession):
    seed = await _seed(db_session, client, "offer-compare-csv")
    url = f"/api/orgs/{seed.env.slug}/analytics/compare/export"
    headers = seed.env.auth_headers["coordinator"]
    params = {"run_ids": [str(seed.run_a.id), str(seed.run_b.id)]}

    runs = _csv_lines((await client.get(url, params=params, headers=headers)).text)
    assert runs[0].startswith("\ufeffLauf;Datum;Status;Stationen;Aufgaben;Teams;Teilnehmende;Registrierte;Gäste")
    assert runs[1].startswith("Lauf A;10.03.2026;Beendet;2;2;3;2;1;1;3;3;50;1;32;30;10,67;4;35,6;2;40;30;50")

    stations = _csv_lines((await client.get(url, params={**params, "table": "stations"}, headers=headers)).text)
    assert stations[0] == "\ufeffStation;Lauf;Teams;Teams mit Antwort;Teams mit Antwort (%)"
    assert "Station 1;Lauf A;3;2;66,7" in stations

    tasks = _csv_lines((await client.get(url, params={**params, "table": "tasks"}, headers=headers)).text)
    assert "Station 1;Aufgabe 1;Text;Lauf B;1;1;100;1;10;10;1;10" in tasks


@pytest.mark.asyncio
async def test_compare_query_count_does_not_grow_with_the_runs(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine
):
    seed = await _seed(db_session, client, "offer-compare-queries")
    url = f"/api/orgs/{seed.env.slug}/analytics/compare"
    headers = seed.env.auth_headers["coordinator"]

    async def _count(run_ids: list[UUID]) -> int:
        count = 0

        def _on_execute(*_args, **_kwargs) -> None:
            nonlocal count
            count += 1

        event.listen(test_engine.sync_engine, "before_cursor_execute", _on_execute)
        try:
            response = await client.get(url, params={"run_ids": [str(r) for r in run_ids]}, headers=headers)
            assert response.status_code == 200
        finally:
            event.remove(test_engine.sync_engine, "before_cursor_execute", _on_execute)
        return count

    two = await _count([seed.run_a.id, seed.run_b.id])
    four = await _count([seed.run_a.id, seed.run_b.id, seed.run_c.id, seed.run_d.id])
    assert two == four
