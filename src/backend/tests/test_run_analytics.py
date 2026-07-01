"""Integration tests for GET /api/orgs/{slug}/runs/{run_id}/analytics and its CSV export.

Covers:
- hand-computed aggregates for a seeded fixture run
- zero PII in the response
- 403 for the participant role (coordinator and above only)
- the empty run, with zero tiles and no NaN or division by zero
- CSV export headers, row content and formula-injection safety
- a query count that is constant regardless of the team count

Mirrors tests/test_remove_team_member.py: `setup_journey_environment` for org and users, then direct model
inserts for Run, RunStation, RunChallenge, Team, TeamMember, Submission, Score and Feedback.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.challenge import ChallengeType
from app.models.feedback import Feedback
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from tests.factories import create_test_user
from tests.journey_helpers import setup_journey_environment

# ============================================================================
# Direct-insert helpers (mirror test_remove_team_member.py's compact style)
# ============================================================================


async def _make_run(db_session: AsyncSession, org_id, *, started_at: datetime | None = None) -> Run:
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name=f"Analytics Run {uuid4().hex[:6]}",
        state=RunState.ACTIVE,
        join_code=uuid4().hex[:6].upper(),
        started_at=started_at,
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _make_station(db_session: AsyncSession, org_id, run_id, *, title: dict, sort_order: int) -> RunStation:
    station = RunStation(
        organization_id=org_id,
        run_id=run_id,
        source_station_id=None,
        title=title,
        description={},
        sort_order=sort_order,
    )
    db_session.add(station)
    await db_session.flush()
    return station


async def _make_challenge(
    db_session: AsyncSession,
    org_id,
    run_id,
    run_station_id,
    *,
    title: dict,
    challenge_type: ChallengeType = ChallengeType.TEXT,
    max_points: int,
    sort_order: int,
) -> RunChallenge:
    challenge = RunChallenge(
        organization_id=org_id,
        run_id=run_id,
        run_station_id=run_station_id,
        source_challenge_id=None,
        challenge_type=challenge_type,
        title=title,
        instructions={},
        scoring_rules={"max_points": max_points},
        sort_order=sort_order,
    )
    db_session.add(challenge)
    await db_session.flush()
    return challenge


async def _make_team(db_session: AsyncSession, org_id, run_id, *, name: str | None) -> Team:
    team = Team(
        organization_id=org_id,
        run_id=run_id,
        name=name,
        color="#123456",
        share_code=uuid4().hex[:6].upper(),
    )
    db_session.add(team)
    await db_session.flush()
    return team


async def _make_member(
    db_session: AsyncSession, org_id, team_id, *, user=None, is_guest: bool = False, display_name: str = "Member"
) -> TeamMember:
    member = TeamMember(
        organization_id=org_id,
        team_id=team_id,
        user_id=user.id if user is not None else None,
        display_name=display_name,
        is_guest=is_guest,
    )
    db_session.add(member)
    await db_session.flush()
    return member


async def _make_submission(
    db_session: AsyncSession, org_id, run_id, team_id, run_challenge_id, *, submitted_at: datetime | None = None
) -> Submission:
    kwargs: dict = {
        "organization_id": org_id,
        "run_id": run_id,
        "team_id": team_id,
        "run_challenge_id": run_challenge_id,
        "content": {"text": "answer"},
    }
    if submitted_at is not None:
        kwargs["submitted_at"] = submitted_at
    submission = Submission(**kwargs)
    db_session.add(submission)
    await db_session.flush()
    return submission


async def _make_score(db_session: AsyncSession, org_id, submission_id, *, points: int, max_points: int) -> Score:
    score = Score(organization_id=org_id, submission_id=submission_id, points=points, max_points=max_points)
    db_session.add(score)
    await db_session.flush()
    return score


# ============================================================================
# Hand-computed aggregates
# ============================================================================


@pytest.mark.asyncio
async def test_run_analytics_matches_hand_computed_aggregates(client: AsyncClient, db_session: AsyncSession):
    """The full analytics document matches hand-computed aggregates for a seeded fixture run."""
    env = await setup_journey_environment(db_session, client, org_slug="analytics-sc001")
    org_id = env.org.id

    started_at = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    run = await _make_run(db_session, org_id, started_at=started_at)

    station_a = await _make_station(
        db_session, org_id, run.id, title={"de": "Rathaus", "en": "City Hall"}, sort_order=0
    )
    station_b = await _make_station(db_session, org_id, run.id, title={"de": "Park", "en": "Park"}, sort_order=1)

    ch1 = await _make_challenge(
        db_session,
        org_id,
        run.id,
        station_a.id,
        title={"de": "Frage 1", "en": "Question 1"},
        max_points=10,
        sort_order=0,
    )
    ch2 = await _make_challenge(
        db_session,
        org_id,
        run.id,
        station_a.id,
        title={"de": "Frage 2", "en": "Question 2"},
        max_points=20,
        sort_order=1,
    )
    ch3 = await _make_challenge(
        db_session,
        org_id,
        run.id,
        station_b.id,
        title={"de": "Frage 3", "en": "Question 3"},
        max_points=15,
        sort_order=0,
    )

    team1 = await _make_team(db_session, org_id, run.id, name="Team Alpha")
    team2 = await _make_team(db_session, org_id, run.id, name="Team Beta")

    # Team 1: 2 registered + 1 guest member
    await _make_member(db_session, org_id, team1.id, user=env.users["participant1"], display_name="P1")
    await _make_member(db_session, org_id, team1.id, user=env.users["participant2"], display_name="P2")
    await _make_member(db_session, org_id, team1.id, user=None, is_guest=True, display_name="Guest 1")

    # Team 2: 1 registered member
    beta_user = await create_test_user(
        db_session, email="beta@analytics-sc001.test", organization=env.org, display_name="Beta User"
    )
    await _make_member(db_session, org_id, team2.id, user=beta_user, display_name="Beta")

    await db_session.commit()

    # Submissions (bucketed for the timeline assertion below)
    sub_t1_ch1 = await _make_submission(
        db_session, org_id, run.id, team1.id, ch1.id, submitted_at=started_at + timedelta(minutes=2)
    )
    await _make_submission(
        db_session, org_id, run.id, team2.id, ch1.id, submitted_at=started_at + timedelta(minutes=10)
    )
    await _make_submission(
        db_session, org_id, run.id, team1.id, ch2.id, submitted_at=started_at + timedelta(minutes=16)
    )
    await _make_submission(
        db_session, org_id, run.id, team1.id, ch3.id, submitted_at=started_at + timedelta(minutes=47)
    )

    # Only one submission gets scored
    await _make_score(db_session, org_id, sub_t1_ch1.id, points=8, max_points=10)

    # Feedback: ratings 4 and 5 (flush separately — batching two ORM inserts of
    # the same model in one flush trips asyncpg's insertmanyvalues sentinel
    # matching in this SQLAlchemy version)
    db_session.add(Feedback(organization_id=org_id, run_id=run.id, rating=4, comment=None))
    await db_session.flush()
    db_session.add(Feedback(organization_id=org_id, run_id=run.id, rating=5, comment="Toll!"))
    await db_session.commit()

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/analytics",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 200
    data = response.json()

    assert data["run_id"] == str(run.id)

    assert data["participation"] == {
        "team_count": 2,
        "participant_count": 4,
        "guest_count": 1,
        "registered_count": 3,
    }

    # Stations (funnel)
    stations_by_id = {s["run_station_id"]: s for s in data["stations"]}
    assert stations_by_id[str(station_a.id)]["teams_attempted"] == 2
    assert stations_by_id[str(station_a.id)]["teams_scored"] == 1
    assert stations_by_id[str(station_b.id)]["teams_attempted"] == 1
    assert stations_by_id[str(station_b.id)]["teams_scored"] == 0

    challenges_by_id = {c["run_challenge_id"]: c for c in data["challenges"]}
    c1 = challenges_by_id[str(ch1.id)]
    assert c1["submissions"] == 2
    assert c1["scored"] == 1
    assert c1["avg_points"] == 8.0
    assert c1["max_points"] == 10
    assert c1["type"] == "text"

    c2 = challenges_by_id[str(ch2.id)]
    assert c2["submissions"] == 1
    assert c2["scored"] == 0
    assert c2["avg_points"] is None
    assert c2["max_points"] == 20

    c3 = challenges_by_id[str(ch3.id)]
    assert c3["submissions"] == 1
    assert c3["scored"] == 0
    assert c3["max_points"] == 15

    teams_by_id = {t["team_id"]: t for t in data["teams"]}
    t1 = teams_by_id[str(team1.id)]
    assert t1["team_name"] == "Team Alpha"
    assert t1["member_count"] == 3
    assert t1["guest_count"] == 1
    assert t1["total_points"] == 8
    assert t1["challenges_attempted"] == 3
    assert t1["challenges_scored"] == 1

    t2 = teams_by_id[str(team2.id)]
    assert t2["team_name"] == "Team Beta"
    assert t2["member_count"] == 1
    assert t2["guest_count"] == 0
    assert t2["total_points"] == 0
    assert t2["challenges_attempted"] == 1
    assert t2["challenges_scored"] == 0

    # Timeline: 15-minute buckets from started_at
    assert data["timeline"] == [
        {"bucket_start": "2026-07-01T10:00:00Z", "submissions": 2},
        {"bucket_start": "2026-07-01T10:15:00Z", "submissions": 1},
        {"bucket_start": "2026-07-01T10:45:00Z", "submissions": 1},
    ]

    assert data["feedback"]["average_rating"] == 4.5
    assert data["feedback"]["total_count"] == 2
    assert data["feedback"]["distribution"] == {"4": 1, "5": 1}

    # No PII: no user id, name or e-mail appears anywhere in the payload
    serialized = response.text
    for user_key in ("participant1", "participant2"):
        user = env.users[user_key]
        assert str(user.id) not in serialized
        assert user.email not in serialized
        assert user.display_name not in serialized
    assert beta_user.email not in serialized
    assert str(beta_user.id) not in serialized


@pytest.mark.asyncio
async def test_run_analytics_requires_coordinator_role(client: AsyncClient, db_session: AsyncSession):
    """403 — a participant (even a team member) cannot view run analytics."""
    env = await setup_journey_environment(db_session, client, org_slug="analytics-403")
    run = await _make_run(db_session, env.org.id)
    await db_session.commit()

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/analytics",
        headers=env.auth_headers["participant1"],
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_run_analytics_empty_run_no_teams(client: AsyncClient, db_session: AsyncSession):
    """Empty run (no teams/submissions yet) renders zero tiles, never NaN or division errors."""
    env = await setup_journey_environment(db_session, client, org_slug="analytics-empty")
    run = await _make_run(db_session, env.org.id, started_at=datetime.now(UTC))
    station = await _make_station(
        db_session, env.org.id, run.id, title={"de": "Station", "en": "Station"}, sort_order=0
    )
    await _make_challenge(
        db_session, env.org.id, run.id, station.id, title={"de": "Aufgabe", "en": "Task"}, max_points=10, sort_order=0
    )
    await db_session.commit()

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/analytics",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 200
    data = response.json()

    assert data["participation"] == {
        "team_count": 0,
        "participant_count": 0,
        "guest_count": 0,
        "registered_count": 0,
    }
    assert data["teams"] == []
    assert data["timeline"] == []
    assert data["feedback"] is None
    # Station/challenge lists still render (frozen content exists) with zero counts, no NaN.
    assert len(data["stations"]) == 1
    assert data["stations"][0]["teams_attempted"] == 0
    assert data["stations"][0]["teams_scored"] == 0
    assert len(data["challenges"]) == 1
    assert data["challenges"][0]["submissions"] == 0
    assert data["challenges"][0]["avg_points"] is None


@pytest.mark.asyncio
async def test_run_analytics_not_found(client: AsyncClient, db_session: AsyncSession):
    """404 — an unknown run id in this org."""
    env = await setup_journey_environment(db_session, client, org_slug="analytics-404")
    await db_session.commit()

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{uuid4()}/analytics",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 404


# ============================================================================
# CSV export
# ============================================================================


@pytest.mark.asyncio
async def test_export_teams_csv_headers_and_row(client: AsyncClient, db_session: AsyncSession):
    """teams CSV has the expected header set and a matching data row, no PII columns."""
    env = await setup_journey_environment(db_session, client, org_slug="analytics-csv-teams")
    org_id = env.org.id
    run = await _make_run(db_session, org_id)
    team = await _make_team(db_session, org_id, run.id, name="Export Team")
    await _make_member(db_session, org_id, team.id, user=env.users["participant1"], display_name="P1")
    await db_session.commit()

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/analytics/export?table=teams",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert response.headers["content-disposition"] == f"attachment; filename=run-analytics-teams-{run.id}.csv"

    # A BOM for Excel, and for a German reader ";" with the headers the screen shows.
    lines = response.text.strip("\r\n").split("\r\n")
    assert lines[0] == "\ufeffTeam;Mitglieder;Gäste;Punkte;Aufgaben beantwortet;Aufgaben bewertet"
    assert lines[1] == "Export Team;1;0;0;0;0"

    english = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/analytics/export?table=teams",
        headers={**env.auth_headers["coordinator"], "X-Clavis-Language": "en"},
    )
    english_lines = english.text.strip("\r\n").split("\r\n")
    assert english_lines[0] == "\ufeffTeam,Members,Guests,Points,Tasks answered,Tasks scored"
    assert english_lines[1] == "Export Team,1,0,0,0,0"

    # No PII: the participant's e-mail/display name/id must never appear
    assert env.users["participant1"].email not in response.text
    assert str(env.users["participant1"].id) not in response.text


@pytest.mark.asyncio
async def test_export_challenges_csv_headers_and_row(client: AsyncClient, db_session: AsyncSession):
    """challenges CSV has the expected header set and a matching data row."""
    env = await setup_journey_environment(db_session, client, org_slug="analytics-csv-challenges")
    org_id = env.org.id
    run = await _make_run(db_session, org_id)
    station = await _make_station(db_session, org_id, run.id, title={"de": "Rathaus", "en": "City Hall"}, sort_order=0)
    challenge = await _make_challenge(
        db_session, org_id, run.id, station.id, title={"de": "Frage", "en": "Question"}, max_points=10, sort_order=0
    )
    team = await _make_team(db_session, org_id, run.id, name="Export Team")
    sub = await _make_submission(db_session, org_id, run.id, team.id, challenge.id)
    await _make_score(db_session, org_id, sub.id, points=7, max_points=10)
    await db_session.commit()

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/analytics/export?table=challenges",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f"attachment; filename=run-analytics-challenges-{run.id}.csv"

    lines = response.text.strip("\r\n").split("\r\n")
    assert lines[0] == "\ufeffStation;Aufgabe;Typ;Einreichungen;Bewertet;Punkte im Schnitt"
    # _display_title prefers "de" over "en"; the type is the library's name.
    assert lines[1] == "Rathaus;Frage;Text;1;1;7"


@pytest.mark.asyncio
async def test_export_invalid_table_param_422(client: AsyncClient, db_session: AsyncSession):
    """422 — an unsupported `table` value is rejected."""
    env = await setup_journey_environment(db_session, client, org_slug="analytics-csv-422")
    run = await _make_run(db_session, env.org.id)
    await db_session.commit()

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/analytics/export?table=bogus",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_export_csv_neutralizes_formula_injection(client: AsyncClient, db_session: AsyncSession):
    """A team named '=cmd()' must come out prefixed exactly as the audit-log export does it."""
    env = await setup_journey_environment(db_session, client, org_slug="analytics-csv-injection")
    org_id = env.org.id
    run = await _make_run(db_session, org_id)
    await _make_team(db_session, org_id, run.id, name="=cmd()")
    await db_session.commit()

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/analytics/export?table=teams",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 200
    body = response.text
    assert "'=cmd()" in body
    # The raw, unescaped formula must never appear on its own (only the escaped form)
    assert "\n=cmd()" not in body and "\r=cmd()" not in body


# ============================================================================
# Bounded query count (no per-team loop)
# ============================================================================


@pytest.mark.asyncio
async def test_analytics_query_count_is_constant_regardless_of_team_count(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine
):
    """
    The query count of the analytics endpoint does not grow with the number of teams.

    The aggregation uses bounded GROUP BY queries, not a per-team loop. A 2-team run and a 15-team run
    must issue the same number of SQL statements.
    """
    env = await setup_journey_environment(db_session, client, org_slug="analytics-qcount")
    org_id = env.org.id

    async def _seed_run_with_teams(team_count: int) -> Run:
        run = await _make_run(db_session, org_id, started_at=datetime.now(UTC))
        station = await _make_station(db_session, org_id, run.id, title={"de": "S", "en": "S"}, sort_order=0)
        challenge = await _make_challenge(
            db_session, org_id, run.id, station.id, title={"de": "C", "en": "C"}, max_points=10, sort_order=0
        )
        for i in range(team_count):
            team = await _make_team(db_session, org_id, run.id, name=f"Team {i}")
            await _make_member(db_session, org_id, team.id, user=None, is_guest=True, display_name=f"G{i}")
            sub = await _make_submission(db_session, org_id, run.id, team.id, challenge.id)
            if i % 2 == 0:
                await _make_score(db_session, org_id, sub.id, points=5, max_points=10)
        await db_session.commit()
        return run

    small_run = await _seed_run_with_teams(2)
    big_run = await _seed_run_with_teams(15)

    async def _count_queries(run_id) -> int:
        count = 0

        def _on_execute(*_args, **_kwargs) -> None:
            nonlocal count
            count += 1

        event.listen(test_engine.sync_engine, "before_cursor_execute", _on_execute)
        try:
            response = await client.get(
                f"/api/orgs/{env.slug}/runs/{run_id}/analytics",
                headers=env.auth_headers["coordinator"],
            )
            assert response.status_code == 200
        finally:
            event.remove(test_engine.sync_engine, "before_cursor_execute", _on_execute)
        return count

    small_count = await _count_queries(small_run.id)
    big_count = await _count_queries(big_run.id)

    assert small_count == big_count, (
        f"query count scaled with team count ({small_count} for 2 teams vs {big_count} for 15 teams) "
        "— aggregation must be O(1) GROUP BY queries, not a per-team loop"
    )
    # Sanity bound so a future regression that reintroduces N+1 elsewhere is still caught.
    assert small_count <= 20


def test_csv_cell_uses_the_decimal_comma_for_german_only():
    """A German Excel reads "7,5" as a number, and an English one "7.5"."""
    from app.routers.analytics import _csv_cell

    assert _csv_cell(7.5, "de") == "7,5"
    assert _csv_cell(7.5, "en") == "7.5"
    assert _csv_cell(7.0, "de") == "7"
    assert _csv_cell(3, "de") == "3"
