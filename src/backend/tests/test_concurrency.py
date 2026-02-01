"""Concurrency tests: race conditions and concurrent operations.

- The max_teams limit holds under concurrent creation. The team-creation paths lock the run's
  row and the join path locks the team's row before counting, with the lock held until the
  request's commit.
- A database UNIQUE constraint on (team_id, run_challenge_id) allows one submission per team
  and challenge.
- Operations that do not conflict run concurrently without deadlock or constraint violation.
- The publish, the end of a run, the grading, the replacement of an answer and the automatic
  score agree on one state through the lock on the run row.

These tests use `concurrent_client` instead of `client`. asyncio.gather() with the standard
`client` causes SQLAlchemy "concurrent operations not permitted" errors, because the regular
client shares a single db_session across all requests. The `concurrent_client` creates a new
session per request.

"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.challenge import Challenge, ChallengeType
from app.models.feed_event import FeedEvent, FeedEventType
from app.models.run import RunState
from app.models.run_result_link import RunResultLink
from app.models.submission import Submission
from app.models.user import UserRole
from app.repositories.run import RunRepository
from app.repositories.submission import SubmissionRepository
from app.routers import runs as runs_router
from app.routers import submissions as submissions_router
from tests.factories import create_test_user
from tests.journey_helpers import JourneyEnv, activate_run, create_team_and_join, setup_journey_environment

# ============================================================================
# Race Condition Tests
# ============================================================================


@pytest.mark.asyncio
async def test_race_condition_max_teams_exactly_one_team_created(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """Two concurrent team creations with room for exactly one more team: exactly one succeeds.

    The run's row is locked before count_by_run, so the requests serialize. The loser gets
    TEAM_LIMIT_REACHED, and the run ends at its maximum. Both creation paths are covered:
    the coordinator's teams endpoint and the guest join in mode='create'.

    """
    env = await setup_journey_environment(db_session, concurrent_client, org_slug="race-max-teams")
    await db_session.commit()
    coord_headers = env.auth_headers["coordinator"]

    async def make_run(name: str) -> tuple[UUID, str]:
        """An activated run at max_teams=2, with its id and join code."""
        create_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/",
            json={
                "name": name,
                "rallye_id": str(env.rallye_id),
                "start_time": "2026-07-15T10:00:00Z",
                "end_time": "2026-07-15T16:00:00Z",
                "max_teams": 2,
            },
            headers=coord_headers,
        )
        assert create_resp.status_code == 201
        run_id = UUID(create_resp.json()["id"])
        activate_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/start",
            headers=coord_headers,
        )
        assert activate_resp.status_code == 200
        return run_id, create_resp.json()["join_code"]

    async def assert_run_at_maximum(run_id: UUID, expected: int) -> None:
        teams_resp = await concurrent_client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
            headers=coord_headers,
        )
        assert teams_resp.status_code == 200
        final_team_count = len(teams_resp.json()["items"])
        assert final_team_count == expected, f"Run must end at its maximum of {expected}, has {final_team_count}"

    # --- Path 1: coordinator creation (POST /teams/) ----------------------

    run_id, _ = await make_run("Max Teams Race Run")

    # First team via the teams endpoint (now at 1/2 teams)
    team1_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team 1", "color": "#FF0000"},
        headers=coord_headers,
    )
    assert team1_resp.status_code == 201

    async def create_team(team_name: str):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
            json={"name": team_name, "color": "#00FF00"},
            headers=coord_headers,
        )

    results = await asyncio.gather(
        create_team("Team 2"),
        create_team("Team 3"),
        return_exceptions=False,
    )
    status_codes = [r.status_code for r in results]

    success_count = sum(1 for code in status_codes if code == 201)
    refused = [r for r in results if r.status_code == 422]
    assert success_count == 1, f"Exactly one concurrent creation may succeed, got {status_codes}"
    assert len(refused) == 1, f"The other creation must be refused with 422, got {status_codes}"
    assert refused[0].json()["error_code"] == "TEAM_LIMIT_REACHED"

    await assert_run_at_maximum(run_id, expected=2)

    # --- Path 2: join creation (guest join, mode='create') ----------------

    run_id, join_code = await make_run("Max Teams Guest Race Run")

    # First team via a guest join create (now at 1/2 teams)
    first = await concurrent_client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Gast Team 1"},
    )
    assert first.status_code == 201, first.text

    # Fresh guests for the race: drop the creator's cookie, otherwise the join
    # idempotency would recognize the creator and return their team.
    concurrent_client.cookies.clear()

    async def guest_create(team_name: str):
        return await concurrent_client.post(
            f"/api/join/{join_code}",
            json={"mode": "create", "display_name": "Gast", "team_name": team_name},
        )

    results = await asyncio.gather(
        guest_create("Gast Team 2"),
        guest_create("Gast Team 3"),
        return_exceptions=False,
    )
    status_codes = [r.status_code for r in results]

    success_count = sum(1 for code in status_codes if code == 201)
    refused = [r for r in results if r.status_code == 422]
    assert success_count == 1, f"Exactly one concurrent guest creation may succeed, got {status_codes}"
    assert len(refused) == 1, f"The other guest creation must be refused with 422, got {status_codes}"
    assert refused[0].json()["error_code"] == "TEAM_LIMIT_REACHED"

    await assert_run_at_maximum(run_id, expected=2)


@pytest.mark.asyncio
async def test_race_condition_duplicate_submission_unique_constraint_enforced(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """The database unique constraint prevents duplicate submissions under concurrent load.

    Unlike max_teams, submissions have a database-level UNIQUE constraint on
    (team_id, run_challenge_id). Two members of one team submit to the same challenge
    concurrently. Exactly one gets 201, and the other gets 422 (duplicate submission)
    or 500 (unique constraint violation).

    """
    env = await setup_journey_environment(db_session, concurrent_client, org_slug="race-duplicate-sub")
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Duplicate Submission Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    team_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Test Team", "color": "#FF0000"},
        headers=env.auth_headers["participant1"],
    )
    assert team_resp.status_code == 201
    team_id = UUID(team_resp.json()["id"])

    join1_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join1_resp.status_code == 204

    join2_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant2"],
    )
    assert join2_resp.status_code == 204

    stations_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["participant1"],
    )
    assert stations_resp.status_code == 200
    stations = stations_resp.json()["items"]
    assert len(stations) > 0

    first_station_id = UUID(stations[0]["id"])
    challenges_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{first_station_id}/challenges/",
        headers=env.auth_headers["participant1"],
    )
    assert challenges_resp.status_code == 200
    challenges = challenges_resp.json()["items"]
    assert len(challenges) > 0

    challenge_id = UUID(challenges[0]["id"])

    # Both participants concurrently submit to the same challenge
    async def submit_challenge(headers: dict):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
            json={
                "run_challenge_id": str(challenge_id),
                "content": {"text": "Concurrent submission test"},
            },
            headers=headers,
        )

    results = await asyncio.gather(
        submit_challenge(env.auth_headers["participant1"]),
        submit_challenge(env.auth_headers["participant2"]),
        return_exceptions=False,
    )

    status_codes = [r.status_code for r in results]

    success_count = sum(1 for code in status_codes if code == 201)
    failure_count = sum(1 for code in status_codes if code in [422, 500])

    # Unique constraint MUST prevent duplicates: exactly one succeeds
    assert success_count == 1, (
        f"Expected exactly 1 successful submission, got {success_count} (status codes: {status_codes})"
    )
    assert failure_count == 1, (
        f"Expected exactly 1 failed submission, got {failure_count} (status codes: {status_codes})"
    )

    submissions_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=env.auth_headers["coordinator"],
    )
    assert submissions_resp.status_code == 200
    submission_count = len(submissions_resp.json()["items"])
    assert submission_count == 1, f"Database should have exactly 1 submission, found {submission_count}"


@pytest.mark.asyncio
async def test_race_condition_max_teams_boundary_zero_slots(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """With max_teams already reached, two concurrent creations are both refused.

    The run's row is locked before count_by_run, so both see the full count even when they
    interleave. Both get 422 TEAM_LIMIT_REACHED, and the final count stays 1.

    """
    env = await setup_journey_environment(db_session, concurrent_client, org_slug="race-zero-slots")
    await db_session.commit()
    coord_headers = env.auth_headers["coordinator"]

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Zero Slots Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
            "max_teams": 1,
        },
        headers=coord_headers,
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=coord_headers,
    )
    assert activate_resp.status_code == 200

    # Create first team (now at 1/1 teams, FULL)
    team1_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Only Team", "color": "#FF0000"},
        headers=coord_headers,
    )
    assert team1_resp.status_code == 201

    # Concurrently attempt to create two more teams (0 slots left)
    async def create_team(team_name: str):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
            json={"name": team_name, "color": "#00FF00"},
            headers=coord_headers,
        )

    results = await asyncio.gather(
        create_team("Team 2"),
        create_team("Team 3"),
        return_exceptions=False,
    )

    status_codes = [r.status_code for r in results]

    # Both must fail with the coded limit error — the run-row lock serializes
    # them and each re-counts a full run.
    assert status_codes == [422, 422], f"Both creations must be refused, got {status_codes}"
    assert results[0].json()["error_code"] == "TEAM_LIMIT_REACHED"
    assert results[1].json()["error_code"] == "TEAM_LIMIT_REACHED"

    teams_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_resp.status_code == 200
    final_team_count = len(teams_resp.json()["items"])
    assert final_team_count == 1, f"Run must stay at its maximum of 1 team, has {final_team_count}"


@pytest.mark.asyncio
async def test_race_condition_submission_different_challenges_both_succeed(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """Concurrent submissions to different challenges both succeed.

    This is not a race condition. It checks that the unique constraint is scoped to
    (team_id, run_challenge_id) and does not block all concurrent submissions.

    """
    env = await setup_journey_environment(
        db_session,
        concurrent_client,
        org_slug="race-diff-challenges",
        num_stations=2,  # At least 2 stations for 2 different challenges
        challenges_per_station=1,
        challenge_type_override=ChallengeType.TEXT,  # Use TEXT for all to simplify submission content
    )
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Different Challenges Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    team_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Test Team", "color": "#FF0000"},
        headers=env.auth_headers["participant1"],
    )
    assert team_resp.status_code == 201
    team_id = UUID(team_resp.json()["id"])

    join1_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join1_resp.status_code == 204

    join2_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant2"],
    )
    assert join2_resp.status_code == 204

    stations_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["participant1"],
    )
    assert stations_resp.status_code == 200
    stations = stations_resp.json()["items"]
    assert len(stations) >= 2

    station1_id = UUID(stations[0]["id"])
    challenges1_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station1_id}/challenges/",
        headers=env.auth_headers["participant1"],
    )
    assert challenges1_resp.status_code == 200
    challenges1 = challenges1_resp.json()["items"]
    assert len(challenges1) > 0
    challenge1_id = UUID(challenges1[0]["id"])

    station2_id = UUID(stations[1]["id"])
    challenges2_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station2_id}/challenges/",
        headers=env.auth_headers["participant1"],
    )
    assert challenges2_resp.status_code == 200
    challenges2 = challenges2_resp.json()["items"]
    assert len(challenges2) > 0
    challenge2_id = UUID(challenges2[0]["id"])

    assert challenge1_id != challenge2_id

    # Both participants concurrently submit to DIFFERENT challenges
    async def submit_challenge(headers: dict, challenge_id: UUID):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
            json={
                "run_challenge_id": str(challenge_id),
                "content": {"text": "Concurrent submission to different challenge"},
            },
            headers=headers,
        )

    results = await asyncio.gather(
        submit_challenge(env.auth_headers["participant1"], challenge1_id),
        submit_challenge(env.auth_headers["participant2"], challenge2_id),
        return_exceptions=False,
    )

    status_codes = [r.status_code for r in results]

    assert status_codes == [201, 201], f"Expected both 201, got {status_codes}"

    submissions_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=env.auth_headers["coordinator"],
    )
    assert submissions_resp.status_code == 200
    submission_count = len(submissions_resp.json()["items"])
    assert submission_count == 2, f"Expected 2 submissions for different challenges, found {submission_count}"


# ============================================================================
# Concurrent Operations Tests
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_submissions_5_teams_5_challenges_all_succeed(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """5 teams submitting to 5 different challenges concurrently all succeed.

    Each answers 201, and the database holds exactly 5 submissions.

    """
    env = await setup_journey_environment(
        db_session,
        concurrent_client,
        org_slug="concurrent-5teams",
        num_stations=5,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.TEXT,  # Use TEXT for all to simplify submission content
    )

    # The setup has participant1 and participant2. Add participants 3 to 7, of which three are used.
    from tests.factories import get_auth_headers

    participants = []
    for i in range(3, 8):
        p = await create_test_user(
            db_session,
            email=f"participant{i}@concurrent-5teams.test",
            password="ParticipantPass123!",
            display_name=f"Participant {i}",
            role=UserRole.PARTICIPANT,
            organization=env.org,
        )
        participants.append(p)
    # Commit so concurrent_client's per-request sessions can see the data
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "5 Teams Concurrent Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    stations_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["coordinator"],
    )
    assert stations_resp.status_code == 200
    stations = stations_resp.json()["items"]
    assert len(stations) >= 5

    challenge_ids = []
    for station in stations[:5]:
        station_id = UUID(station["id"])
        challenges_resp = await concurrent_client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
            headers=env.auth_headers["coordinator"],
        )
        assert challenges_resp.status_code == 200
        challenges = challenges_resp.json()["items"]
        assert len(challenges) > 0
        challenge_ids.append(UUID(challenges[0]["id"]))

    team_ids = []
    all_participants = [
        (env.auth_headers["participant1"], "Team 1"),
        (env.auth_headers["participant2"], "Team 2"),
        (get_auth_headers(participants[0]), "Team 3"),
        (get_auth_headers(participants[1]), "Team 4"),
        (get_auth_headers(participants[2]), "Team 5"),
    ]

    for headers, team_name in all_participants:
        team_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
            json={"name": team_name, "color": "#FF0000"},
            headers=headers,
        )
        assert team_resp.status_code == 201
        team_id = UUID(team_resp.json()["id"])
        team_ids.append(team_id)

        join_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
            headers=headers,
        )
        assert join_resp.status_code == 204

    # Concurrently submit from all 5 teams to different challenges
    async def submit_challenge(headers: dict, challenge_id: UUID):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
            json={
                "run_challenge_id": str(challenge_id),
                "content": {"text": "Concurrent submission from team"},
            },
            headers=headers,
        )

    results = await asyncio.gather(
        submit_challenge(all_participants[0][0], challenge_ids[0]),
        submit_challenge(all_participants[1][0], challenge_ids[1]),
        submit_challenge(all_participants[2][0], challenge_ids[2]),
        submit_challenge(all_participants[3][0], challenge_ids[3]),
        submit_challenge(all_participants[4][0], challenge_ids[4]),
        return_exceptions=False,
    )

    status_codes = [r.status_code for r in results]

    assert status_codes == [201, 201, 201, 201, 201], f"Expected all 201, got {status_codes}"

    submissions_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=env.auth_headers["coordinator"],
    )
    assert submissions_resp.status_code == 200
    submission_count = len(submissions_resp.json()["items"])
    assert submission_count == 5, f"Expected 5 submissions, found {submission_count}"


@pytest.mark.asyncio
async def test_concurrent_team_joins_multiple_users_all_succeed(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """5 users joining 5 different teams concurrently all succeed: 204 each, and every team ends with exactly 1 member."""
    env = await setup_journey_environment(
        db_session,
        concurrent_client,
        org_slug="concurrent-joins",
    )

    # The setup has participant1 and participant2. Add participants 3 to 7, of which three are used.
    from tests.factories import get_auth_headers

    participants = []
    for i in range(3, 8):
        p = await create_test_user(
            db_session,
            email=f"participant{i}@concurrent-joins.test",
            password="ParticipantPass123!",
            display_name=f"Participant {i}",
            role=UserRole.PARTICIPANT,
            organization=env.org,
        )
        participants.append(p)
    # Commit so concurrent_client's per-request sessions can see the data
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Concurrent Joins Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    team_ids = []
    for i in range(1, 6):
        team_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
            json={"name": f"Team {i}", "color": "#FF0000"},
            headers=env.auth_headers["coordinator"],
        )
        assert team_resp.status_code == 201
        team_ids.append(UUID(team_resp.json()["id"]))

    all_participants = [
        env.auth_headers["participant1"],
        env.auth_headers["participant2"],
        get_auth_headers(participants[0]),
        get_auth_headers(participants[1]),
        get_auth_headers(participants[2]),
    ]

    # Concurrently join different teams
    async def join_team(headers: dict, team_id: UUID):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
            headers=headers,
        )

    results = await asyncio.gather(
        join_team(all_participants[0], team_ids[0]),
        join_team(all_participants[1], team_ids[1]),
        join_team(all_participants[2], team_ids[2]),
        join_team(all_participants[3], team_ids[3]),
        join_team(all_participants[4], team_ids[4]),
        return_exceptions=False,
    )

    status_codes = [r.status_code for r in results]

    assert status_codes == [204, 204, 204, 204, 204], f"Expected all 204, got {status_codes}"

    teams_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=env.auth_headers["coordinator"],
    )
    assert teams_resp.status_code == 200
    teams = teams_resp.json()["items"]
    assert len(teams) == 5

    for team in teams:
        assert team["member_count"] == 1, f"Team {team['name']} should have 1 member, has {team['member_count']}"


@pytest.mark.asyncio
async def test_concurrent_scoring_3_submissions_all_succeed(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """Scoring 3 submissions concurrently all succeed: 201 each, and 3 scores are stored."""
    env = await setup_journey_environment(
        db_session,
        concurrent_client,
        org_slug="concurrent-scoring",
    )

    # The setup has participant1 and participant2.
    from tests.factories import get_auth_headers

    participant3 = await create_test_user(
        db_session,
        email="participant3@concurrent-scoring.test",
        password="ParticipantPass123!",
        display_name="Participant 3",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    # Commit so concurrent_client's per-request sessions can see the data
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Concurrent Scoring Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    stations_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["coordinator"],
    )
    assert stations_resp.status_code == 200
    stations = stations_resp.json()["items"]
    assert len(stations) > 0
    station_id = UUID(stations[0]["id"])

    challenges_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
        headers=env.auth_headers["coordinator"],
    )
    assert challenges_resp.status_code == 200
    challenges = challenges_resp.json()["items"]
    assert len(challenges) > 0
    challenge_id = UUID(challenges[0]["id"])

    all_participants = [
        (env.auth_headers["participant1"], "Team 1"),
        (env.auth_headers["participant2"], "Team 2"),
        (get_auth_headers(participant3), "Team 3"),
    ]

    submission_ids = []
    for headers, team_name in all_participants:
        team_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
            json={"name": team_name, "color": "#FF0000"},
            headers=headers,
        )
        assert team_resp.status_code == 201
        team_id = UUID(team_resp.json()["id"])

        join_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
            headers=headers,
        )
        assert join_resp.status_code == 204

        submit_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
            json={
                "run_challenge_id": str(challenge_id),
                "content": {"text": "Submission for scoring test"},
            },
            headers=headers,
        )
        assert submit_resp.status_code == 201
        submission_ids.append(UUID(submit_resp.json()["id"]))

    assert len(submission_ids) == 3

    # Concurrently score all 3 submissions
    async def score_submission(submission_id: UUID, points: int):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
            json={
                "submission_id": str(submission_id),
                "points": points,
            },
            headers=env.auth_headers["coordinator"],
        )

    # max_points is 10
    results = await asyncio.gather(
        score_submission(submission_ids[0], 5),
        score_submission(submission_ids[1], 8),
        score_submission(submission_ids[2], 10),
        return_exceptions=False,
    )

    status_codes = [r.status_code for r in results]

    assert status_codes == [201, 201, 201], f"Expected all 201, got {status_codes}"

    scores_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=env.auth_headers["coordinator"],
    )
    assert scores_resp.status_code == 200
    score_count = len(scores_resp.json()["items"])
    assert score_count == 3, f"Expected 3 scores, found {score_count}"


@pytest.mark.asyncio
async def test_concurrent_operations_mixed_team_join_and_submission(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """Concurrent team joins and submissions do not interfere with each other.

    Two new users join two existing teams while two members submit to different challenges.
    The joins answer 204 and the submissions 201. Each team ends with 2 members, and the
    database with 2 submissions.

    """
    env = await setup_journey_environment(
        db_session,
        concurrent_client,
        org_slug="concurrent-mixed",
        num_stations=2,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.TEXT,  # Use TEXT for all to simplify submission content
    )

    from tests.factories import get_auth_headers

    participant3 = await create_test_user(
        db_session,
        email="participant3@concurrent-mixed.test",
        password="ParticipantPass123!",
        display_name="Participant 3",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    participant4 = await create_test_user(
        db_session,
        email="participant4@concurrent-mixed.test",
        password="ParticipantPass123!",
        display_name="Participant 4",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    # Commit so concurrent_client's per-request sessions can see the data
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Mixed Concurrent Operations Test",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    stations_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["coordinator"],
    )
    assert stations_resp.status_code == 200
    stations = stations_resp.json()["items"]
    assert len(stations) >= 2

    station1_id = UUID(stations[0]["id"])
    challenges1_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station1_id}/challenges/",
        headers=env.auth_headers["coordinator"],
    )
    assert challenges1_resp.status_code == 200
    challenge1_id = UUID(challenges1_resp.json()["items"][0]["id"])

    station2_id = UUID(stations[1]["id"])
    challenges2_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station2_id}/challenges/",
        headers=env.auth_headers["coordinator"],
    )
    assert challenges2_resp.status_code == 200
    challenge2_id = UUID(challenges2_resp.json()["items"][0]["id"])

    team1_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team 1", "color": "#FF0000"},
        headers=env.auth_headers["participant1"],
    )
    assert team1_resp.status_code == 201
    team1_id = UUID(team1_resp.json()["id"])

    join1_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team1_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join1_resp.status_code == 204

    team2_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team 2", "color": "#00FF00"},
        headers=env.auth_headers["participant2"],
    )
    assert team2_resp.status_code == 201
    team2_id = UUID(team2_resp.json()["id"])

    join2_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team2_id}/join",
        headers=env.auth_headers["participant2"],
    )
    assert join2_resp.status_code == 204

    # Concurrently:
    # - participant3 joins team1
    # - participant4 joins team2
    # - participant1 submits to challenge1
    # - participant2 submits to challenge2

    async def join_team(headers: dict, team_id: UUID):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
            headers=headers,
        )

    async def submit_challenge(headers: dict, challenge_id: UUID):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
            json={
                "run_challenge_id": str(challenge_id),
                "content": {"text": "Mixed concurrent operation submission"},
            },
            headers=headers,
        )

    results = await asyncio.gather(
        join_team(get_auth_headers(participant3), team1_id),
        join_team(get_auth_headers(participant4), team2_id),
        submit_challenge(env.auth_headers["participant1"], challenge1_id),
        submit_challenge(env.auth_headers["participant2"], challenge2_id),
        return_exceptions=False,
    )

    status_codes = [r.status_code for r in results]

    assert status_codes == [204, 204, 201, 201], f"Expected [204, 204, 201, 201], got {status_codes}"

    teams_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=env.auth_headers["coordinator"],
    )
    assert teams_resp.status_code == 200
    teams = teams_resp.json()["items"]

    team1 = next(t for t in teams if t["id"] == str(team1_id))
    team2 = next(t for t in teams if t["id"] == str(team2_id))

    assert team1["member_count"] == 2, f"Team 1 should have 2 members, has {team1['member_count']}"
    assert team2["member_count"] == 2, f"Team 2 should have 2 members, has {team2['member_count']}"

    submissions_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=env.auth_headers["coordinator"],
    )
    assert submissions_resp.status_code == 200
    submission_count = len(submissions_resp.json()["items"])
    assert submission_count == 2, f"Expected 2 submissions, found {submission_count}"


@pytest.mark.asyncio
async def test_duplicate_submission_race_answers_422_not_500(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loser of a genuine submit race gets the same 422 as anyone submitting twice, not a 500.

    Racing two real requests, as the test above does, reaches the constraint only when the
    timing happens to line up. Here the race window is produced deterministically by making
    the pre-check miss the existing row, which is what happens when two teammates tap submit
    in the same instant.

    """
    env = await setup_journey_environment(db_session, concurrent_client, org_slug="race-dup-422")
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Duplicate Submission 422 Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    team_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Race Team", "color": "#00FF00"},
        headers=env.auth_headers["participant1"],
    )
    assert team_resp.status_code == 201
    team_id = UUID(team_resp.json()["id"])

    for who in ("participant1", "participant2"):
        join_resp = await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
            headers=env.auth_headers[who],
        )
        assert join_resp.status_code == 204

    stations_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["participant1"],
    )
    station_id = UUID(stations_resp.json()["items"][0]["id"])
    challenges_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
        headers=env.auth_headers["participant1"],
    )
    challenge_id = UUID(challenges_resp.json()["items"][0]["id"])

    async def submit(who: str):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
            json={"run_challenge_id": str(challenge_id), "content": {"text": "Race answer"}},
            headers=env.auth_headers[who],
        )

    first = await submit("participant1")
    assert first.status_code == 201

    # Blind the pre-check, leaving the database constraint as the only guard —
    # the state both requests are in during a real race.
    async def _sees_nothing(self, **kwargs):  # noqa: ANN001, ANN202, ARG001
        return None

    monkeypatch.setattr(SubmissionRepository, "get_by_team_and_challenge", _sees_nothing)

    second = await submit("participant2")
    assert second.status_code == 422, f"Race loser must get the ordinary duplicate response, got {second.status_code}"
    assert second.json()["error_code"] == "TEAM_ALREADY_SUBMITTED"


# ============================================================================
# The publish, the end of a run and the grading agree on one state
# ============================================================================
#
# An answer and a grading take a shared lock on the run row
# (``RunRepository.share_row``). The publish and the end of the run take the
# exclusive one (``RunRepository.lock_row``). The tests hold one side with an
# event and read the wait of the other side from the database: no sleep
# decides an order.


def _locks_the_run_row(statement: Any) -> bool:
    """Matches a ``SELECT ... FOR ...`` on the run row, shared or not."""
    return getattr(statement, "_for_update_arg", None) is not None and "FROM runs" in str(statement)


class _HoldAfter:
    """Holds one session right after its first statement that ``matches`` until ``release`` is set.

    ``pid`` is the database backend of the held session: who a waiting
    statement must be blocked by.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, matches: Callable[[Any], bool] = _locks_the_run_row) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.pid = 0
        self._armed = True
        original = AsyncSession.execute
        hook = self

        async def execute(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
            result = await original(session, statement, *args, **kwargs)
            if hook._armed and matches(statement):
                hook._armed = False
                hook.pid = int((await original(session, text("SELECT pg_backend_pid()"))).scalar_one())
                hook.reached.set()
                await hook.release.wait()
            return result

        monkeypatch.setattr(AsyncSession, "execute", execute)

    async def wait_reached(self) -> None:
        await asyncio.wait_for(self.reached.wait(), timeout=10)


class _InFlight:
    """The requests a test started and the holds it set: all are let go and awaited when the test ends.

    A test that fails while a request is held would otherwise leave a
    transaction open with a row lock, and the teardown of the database would
    wait for it.
    """

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[Any]] = []
        self.releases: list[asyncio.Event] = []

    def start(self, request: Any) -> "asyncio.Task[Any]":
        task = asyncio.ensure_future(request)
        self.tasks.append(task)
        return task

    def hold(self, monkeypatch: pytest.MonkeyPatch) -> _HoldAfter:
        hook = _HoldAfter(monkeypatch)
        self.releases.append(hook.release)
        return hook


@pytest_asyncio.fixture
async def in_flight() -> AsyncGenerator[_InFlight, None]:
    held = _InFlight()
    yield held
    for release in held.releases:
        release.set()
    await asyncio.gather(*held.tasks, return_exceptions=True)


_WAITS_FOR = text(
    """
    SELECT (SELECT min(l.relation::regclass::text) FROM pg_locks l
             WHERE l.pid = a.pid AND l.locktype = 'tuple') AS waits_in
      FROM pg_stat_activity a
     WHERE a.wait_event_type = 'Lock' AND :holder = ANY(pg_blocking_pids(a.pid))
    """
)


async def _waits_for(test_engine: AsyncEngine, task: "asyncio.Task[Any]", holder: int) -> list[str | None]:
    """The tables in which a backend waits for a row that backend ``holder`` has.

    Returns as soon as there is such a wait, or empty when ``task`` ended
    without one. A statement that waits inside the database can set no event,
    so the state of the database is the signal.
    """

    async def watch() -> list[str | None]:
        while not task.done():
            async with test_engine.connect() as connection:
                tables = [row.waits_in for row in (await connection.execute(_WAITS_FOR, {"holder": holder})).all()]
            if tables:
                return tables
            await asyncio.sleep(0.01)
        return []

    return await asyncio.wait_for(watch(), timeout=10)


async def _assert_waits_at_the_run_row(test_engine: AsyncEngine, task: "asyncio.Task[Any]", holder: int) -> None:
    waits = await _waits_for(test_engine, task, holder)
    assert not task.done(), "the request ended without waiting for the run row"
    assert waits == ["runs"], waits


async def _run_with_two_teams(db_session: AsyncSession, client: AsyncClient, slug: str) -> tuple[JourneyEnv, str, str]:
    """An active run with one text challenge and two teams of one member each: (env, run id, challenge id)."""
    env = await setup_journey_environment(db_session, client, org_slug=slug, num_stations=1, challenges_per_station=1)
    await db_session.commit()
    staff = env.auth_headers["coordinator"]
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/", headers=staff, json={"rallye_id": str(env.rallye_id), "name": "Shared Lock Run"}
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    await activate_run(client, env.slug, run_id, staff)
    stations = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/stations/", headers=staff)
    station_id = stations.json()["items"][0]["id"]
    challenges = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/", headers=staff
    )
    challenge_id = challenges.json()["items"][0]["id"]
    await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant1"], "Team One", "#FF0000")
    await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant2"], "Team Two", "#00FF00")
    return env, run_id, challenge_id


def _answer(client: AsyncClient, env: JourneyEnv, run_id: str, challenge_id: str, who: str) -> Any:
    return client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=env.auth_headers[who],
        json={"run_challenge_id": challenge_id, "content": {"text": f"Answer of {who}"}},
    )


def _grade(client: AsyncClient, env: JourneyEnv, run_id: str, submission_id: str) -> Any:
    return client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=env.auth_headers["coordinator"],
        json={"submission_id": submission_id, "points": 10, "comment": "Good"},
    )


async def _result_notices(test_engine: AsyncEngine, run_id: str) -> list[dict[str, Any]]:
    """The contents of the run's result notices, oldest first."""
    async with AsyncSession(test_engine) as session:
        rows = await session.execute(
            select(FeedEvent.content)
            .where(FeedEvent.run_id == UUID(run_id), FeedEvent.event_type == FeedEventType.RESULTS_AVAILABLE)
            .order_by(FeedEvent.created_at)
        )
        return [dict(content) for content in rows.scalars()]


@pytest.mark.asyncio
async def test_an_answer_that_waits_for_the_end_of_the_run_is_refused_as_a_late_answer(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    in_flight: "_InFlight",
) -> None:
    """The end holds the run row, an answer starts, the end commits, and the answer is refused."""
    env, run_id, challenge_id = await _run_with_two_teams(db_session, concurrent_client, "shared-end-first")
    staff = env.auth_headers["coordinator"]

    hook = in_flight.hold(monkeypatch)
    end = in_flight.start(concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=staff))
    await hook.wait_reached()
    answer = in_flight.start(_answer(concurrent_client, env, run_id, challenge_id, "participant1"))
    await _assert_waits_at_the_run_row(test_engine, answer, hook.pid)
    hook.release.set()

    assert (await end).status_code == 200
    refused = await answer
    assert refused.status_code == 422, refused.text
    # A late submission gets the same answer from a request that starts after the end.
    late = await _answer(concurrent_client, env, run_id, challenge_id, "participant2")
    assert late.status_code == 422
    assert refused.json()["error_code"] == late.json()["error_code"] == "RUN_ENDED"
    assert refused.json()["detail"] == late.json()["detail"]
    listed = await concurrent_client.get(f"/api/orgs/{env.slug}/runs/{run_id}/submissions/", headers=staff)
    assert listed.json()["items"] == []
    # No answer, so the end saw a complete grading.
    assert [notice.get("interim") for notice in await _result_notices(test_engine, run_id)] == [None]


@pytest.mark.asyncio
async def test_the_end_of_the_run_waits_for_an_answer_in_flight_and_counts_it(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    in_flight: "_InFlight",
) -> None:
    """An answer holds the shared lock, the end waits, the answer commits, and the end counts it."""
    env, run_id, challenge_id = await _run_with_two_teams(db_session, concurrent_client, "shared-answer-first")
    staff = env.auth_headers["coordinator"]

    hook = in_flight.hold(monkeypatch)
    answer = in_flight.start(_answer(concurrent_client, env, run_id, challenge_id, "participant1"))
    await hook.wait_reached()
    end = in_flight.start(concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=staff))
    await _assert_waits_at_the_run_row(test_engine, end, hook.pid)
    hook.release.set()

    assert (await answer).status_code == 201
    assert (await end).status_code == 200
    # The answer is stored and ungraded, and the end read it: the notice is the interim one.
    assert [notice.get("interim") for notice in await _result_notices(test_engine, run_id)] == [True]


@pytest.mark.asyncio
async def test_two_answers_of_different_teams_do_not_wait_for_each_other(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    in_flight: "_InFlight",
) -> None:
    """One answer holds the shared lock of the run, uncommitted, and the answer of another team goes through."""
    env, run_id, challenge_id = await _run_with_two_teams(db_session, concurrent_client, "shared-two-answers")

    hook = in_flight.hold(monkeypatch)
    held = in_flight.start(_answer(concurrent_client, env, run_id, challenge_id, "participant1"))
    await hook.wait_reached()
    second = await asyncio.wait_for(_answer(concurrent_client, env, run_id, challenge_id, "participant2"), timeout=10)
    assert second.status_code == 201, second.text
    assert not held.done()
    hook.release.set()
    assert (await held).status_code == 201


@pytest.mark.asyncio
async def test_answers_and_gradings_go_through_beside_a_held_shared_lock(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
) -> None:
    """A transaction holds the shared lock of the run while two answers and two gradings commit."""
    env, run_id, challenge_id = await _run_with_two_teams(db_session, concurrent_client, "shared-held")

    async with AsyncSession(test_engine) as holder:
        assert await RunRepository(holder, organization_id=env.org.id).share_row(UUID(run_id)) == RunState.ACTIVE
        answers = await asyncio.wait_for(
            asyncio.gather(
                _answer(concurrent_client, env, run_id, challenge_id, "participant1"),
                _answer(concurrent_client, env, run_id, challenge_id, "participant2"),
            ),
            timeout=10,
        )
        assert [response.status_code for response in answers] == [201, 201]
        gradings = await asyncio.wait_for(
            asyncio.gather(*(_grade(concurrent_client, env, run_id, response.json()["id"]) for response in answers)),
            timeout=10,
        )
        assert [response.status_code for response in gradings] == [201, 201]
        await holder.rollback()


@pytest.mark.asyncio
async def test_a_grading_taken_back_beside_a_publish_waits_for_its_commit(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    in_flight: "_InFlight",
) -> None:
    """A grading taken back beside a publish waits for the publish to commit.

    The publish has counted "grading complete" and holds its lock. Without the shared lock the
    delete could commit between the count and the standing, and the publish would store a
    result without the points it had just counted as graded.

    """
    env, run_id, challenge_id = await _run_with_two_teams(db_session, concurrent_client, "shared-publish")
    staff = env.auth_headers["coordinator"]
    answer = await _answer(concurrent_client, env, run_id, challenge_id, "participant1")
    score = await _grade(concurrent_client, env, run_id, answer.json()["id"])
    assert score.status_code == 201
    assert (await concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=staff)).status_code == 200

    # Hold the publish between its count and its standing.
    reached, release = asyncio.Event(), asyncio.Event()
    in_flight.releases.append(release)
    pid = 0
    build = runs_router._build_results_snapshot

    async def held_build(db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        nonlocal pid
        pid = int((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        reached.set()
        await release.wait()
        return await build(db, *args, **kwargs)

    monkeypatch.setattr(runs_router, "_build_results_snapshot", held_build)
    publish = in_flight.start(
        concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=staff)
    )
    await asyncio.wait_for(reached.wait(), timeout=10)
    taken_back = in_flight.start(
        concurrent_client.delete(f"/api/orgs/{env.slug}/runs/{run_id}/scores/{score.json()['id']}", headers=staff)
    )
    await _assert_waits_at_the_run_row(test_engine, taken_back, pid)
    release.set()

    assert (await publish).status_code == 200
    assert (await taken_back).status_code == 204

    # The standing is the state the publish counted: the grading is in it.
    async with AsyncSession(test_engine) as session:
        snapshot = (
            await session.execute(select(RunResultLink.snapshot).where(RunResultLink.run_id == UUID(run_id)))
        ).scalar_one()
    assert [entry["points"] for entry in snapshot["entries"]] == [10, 0]
    # What the product does with a grading taken back after a publish, unchanged
    # by the lock: the link stays published with the standing it stored, and the
    # grading is open again, so a new publish is refused until it is complete.
    link = await concurrent_client.get(f"/api/orgs/{env.slug}/runs/{run_id}/results/link", headers=staff)
    assert link.json()["status"] == "published"
    again = await concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=staff)
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_the_notice_of_the_end_agrees_with_a_grading_in_flight_and_follows_a_later_one_as_before(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    in_flight: "_InFlight",
) -> None:
    """The notice of the end is written from the grading state at its commit.

    Two answers, one graded. The second grading holds the shared lock, the end waits for it
    and then reads a complete grading: no interim notice. In a second run the last grading
    comes after the end: the interim notice stays in the feed and the scoreboard turns final.
    Only a publish takes the interim notice away.

    """
    env, run_id, challenge_id = await _run_with_two_teams(db_session, concurrent_client, "shared-notice")
    staff = env.auth_headers["coordinator"]
    first = await _answer(concurrent_client, env, run_id, challenge_id, "participant1")
    second = await _answer(concurrent_client, env, run_id, challenge_id, "participant2")
    assert (await _grade(concurrent_client, env, run_id, first.json()["id"])).status_code == 201

    hook = in_flight.hold(monkeypatch)
    grading = in_flight.start(_grade(concurrent_client, env, run_id, second.json()["id"]))
    await hook.wait_reached()
    end = in_flight.start(concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=staff))
    await _assert_waits_at_the_run_row(test_engine, end, hook.pid)
    hook.release.set()
    assert (await grading).status_code == 201
    assert (await end).status_code == 200
    assert [notice.get("interim") for notice in await _result_notices(test_engine, run_id)] == [None]

    # A grading that completes the grading after the end.
    created = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/", headers=staff, json={"rallye_id": str(env.rallye_id), "name": "Graded Late"}
    )
    late_run = created.json()["id"]
    await activate_run(concurrent_client, env.slug, late_run, staff)
    stations = await concurrent_client.get(f"/api/orgs/{env.slug}/runs/{late_run}/stations/", headers=staff)
    challenges = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{late_run}/stations/{stations.json()['items'][0]['id']}/challenges/", headers=staff
    )
    late_challenge = challenges.json()["items"][0]["id"]
    await create_team_and_join(
        concurrent_client, env.slug, late_run, env.auth_headers["participant1"], "Team Late", "#0000FF"
    )
    answer = await _answer(concurrent_client, env, late_run, late_challenge, "participant1")
    assert (await concurrent_client.post(f"/api/orgs/{env.slug}/runs/{late_run}/end", headers=staff)).status_code == 200
    assert [notice.get("interim") for notice in await _result_notices(test_engine, late_run)] == [True]

    assert (await _grade(concurrent_client, env, late_run, answer.json()["id"])).status_code == 201
    assert [notice.get("interim") for notice in await _result_notices(test_engine, late_run)] == [True]
    board = await concurrent_client.get(f"/api/orgs/{env.slug}/runs/{late_run}/scores/scoreboard", headers=staff)
    assert board.json()["is_final"] is True

    published = await concurrent_client.post(f"/api/orgs/{env.slug}/runs/{late_run}/results/publish", headers=staff)
    assert published.status_code == 200
    notices = await _result_notices(test_engine, late_run)
    assert [notice.get("interim") for notice in notices] == [None]
    assert notices[0].get("result_link") is True


# ============================================================================
# The replacement of an answer and the automatic score against the
# end of a run
# ============================================================================
#
# A replacement is an answer too: ``replace_submission`` takes the shared lock
# on the run row before it writes, as ``create_submission`` does.


def _replace(client: AsyncClient, env: JourneyEnv, run_id: str, submission_id: str, who: str) -> Any:
    return client.put(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/{submission_id}",
        headers=env.auth_headers[who],
        json={"content": {"text": f"Replacement of {who}"}},
    )


async def _stored_answer(test_engine: AsyncEngine, submission_id: str) -> tuple[dict[str, Any], Any]:
    """The content and the time of an answer as the database has them."""
    async with AsyncSession(test_engine) as session:
        row = (
            await session.execute(
                select(Submission.content, Submission.submitted_at).where(Submission.id == UUID(submission_id))
            )
        ).one()
        return dict(row.content), row.submitted_at


@pytest.mark.asyncio
async def test_a_replacement_that_waits_for_the_end_of_the_run_is_refused_as_a_late_answer(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    in_flight: "_InFlight",
) -> None:
    """The end holds the run row, a replacement starts, the end commits, and the replacement is refused.

    The replacement is sent by the member of the team that answered, for an
    answer without a score: all its checks pass, the state it read is
    ``active``, and only the state under the shared lock refuses it.

    """
    env, run_id, challenge_id = await _run_with_two_teams(db_session, concurrent_client, "replace-end-first")
    staff = env.auth_headers["coordinator"]
    first = await _answer(concurrent_client, env, run_id, challenge_id, "participant1")
    assert first.status_code == 201, first.text
    submission_id = first.json()["id"]
    before = await _stored_answer(test_engine, submission_id)

    hook = in_flight.hold(monkeypatch)
    end = in_flight.start(concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=staff))
    await hook.wait_reached()
    replacement = in_flight.start(_replace(concurrent_client, env, run_id, submission_id, "participant1"))
    await _assert_waits_at_the_run_row(test_engine, replacement, hook.pid)
    hook.release.set()

    assert (await end).status_code == 200
    refused = await replacement
    assert refused.status_code == 422, refused.text
    # A late replacement gets the same answer from a request that starts after the end.
    late = await _replace(concurrent_client, env, run_id, submission_id, "participant1")
    assert late.status_code == 422
    assert refused.json()["error_code"] == late.json()["error_code"] == "RUN_ENDED"
    assert refused.json()["detail"] == late.json()["detail"]
    # Neither refusal wrote: the content and the time of the answer are the ones from before the end.
    assert before[0] == {"text": "Answer of participant1"}
    assert await _stored_answer(test_engine, submission_id) == before
    # The end counted the answer, ungraded as it was.
    assert [notice.get("interim") for notice in await _result_notices(test_engine, run_id)] == [True]


@pytest.mark.asyncio
async def test_the_end_of_the_run_waits_for_a_replacement_in_flight_and_sees_it_stored(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    in_flight: "_InFlight",
) -> None:
    """A replacement has written and not committed, the end waits, then reads the replacement.

    The replacement is held after its update of the answer row, inside its
    transaction. The end reads its counts in a transaction of its own: what
    that transaction finds in the answer row at that moment is recorded.

    """
    env, run_id, challenge_id = await _run_with_two_teams(db_session, concurrent_client, "replace-first")
    staff = env.auth_headers["coordinator"]
    first = await _answer(concurrent_client, env, run_id, challenge_id, "participant1")
    assert first.status_code == 201, first.text
    submission_id = first.json()["id"]
    before = await _stored_answer(test_engine, submission_id)

    # Hold the replacement between its update and its commit.
    reached, release = asyncio.Event(), asyncio.Event()
    in_flight.releases.append(release)
    pid = 0
    update_content = SubmissionRepository.update_content

    async def held_update(repo: SubmissionRepository, *args: Any, **kwargs: Any) -> Any:
        nonlocal pid
        updated = await update_content(repo, *args, **kwargs)
        pid = int((await repo.session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        reached.set()
        await release.wait()
        return updated

    monkeypatch.setattr(SubmissionRepository, "update_content", held_update)

    # What the end finds in the answer row when it reads its counts.
    seen_by_the_end: list[dict[str, Any]] = []
    count_unscored = SubmissionRepository.count_unscored_by_run

    async def recording_count(repo: SubmissionRepository, counted_run: UUID) -> int:
        content = await repo.session.execute(select(Submission.content).where(Submission.id == UUID(submission_id)))
        seen_by_the_end.append(dict(content.scalar_one()))
        return await count_unscored(repo, counted_run)

    monkeypatch.setattr(SubmissionRepository, "count_unscored_by_run", recording_count)

    replacement = in_flight.start(_replace(concurrent_client, env, run_id, submission_id, "participant1"))
    await asyncio.wait_for(reached.wait(), timeout=10)
    # Uncommitted: every other transaction still reads the first answer.
    assert await _stored_answer(test_engine, submission_id) == before
    end = in_flight.start(concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=staff))
    await _assert_waits_at_the_run_row(test_engine, end, pid)
    assert seen_by_the_end == []
    release.set()

    replaced = await replacement
    assert replaced.status_code == 200, replaced.text
    assert (await end).status_code == 200
    # The end read its counts once, and the answer it found was the replacement.
    assert seen_by_the_end == [{"text": "Replacement of participant1"}]
    content, submitted_at = await _stored_answer(test_engine, submission_id)
    assert content == {"text": "Replacement of participant1"}
    assert submitted_at > before[1]
    assert [notice.get("interim") for notice in await _result_notices(test_engine, run_id)] == [True]


# The automatic score of a quiz or a QR answer is a grading: it takes the
# shared lock on the run row in ``_try_auto_score_and_notify``, in a
# transaction of its own after the commit of the answer.


async def _ungraded_answers(test_engine: AsyncEngine, env: JourneyEnv, run_id: str) -> int:
    """The count of ungraded answers of the run that the end and the publish read, from a session of its own."""
    async with AsyncSession(test_engine) as session:
        return await _COUNT_UNSCORED(SubmissionRepository(session, env.org.id), UUID(run_id))


# The count itself: the tests below put a recording one in its place.
_COUNT_UNSCORED = SubmissionRepository.count_unscored_by_run


@pytest.mark.asyncio
async def test_an_automatic_score_waits_for_the_end_of_the_run_and_the_notice_agrees_with_the_grading_at_its_commit(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    in_flight: "_InFlight",
) -> None:
    """The automatic score of a stored answer waits for an end of the run that holds its lock.

    A quiz answer on ``auto_approve`` is committed and the request is held
    before its automatic score. The end of the run starts and is held after
    its ``lock_row``. The score then asks for the shared lock and waits in
    "runs" for the backend of the end. The end counts one ungraded answer and
    writes the interim notice: the grading was not complete at its commit.
    Then the score commits, and the interim notice stays, as it does after a
    grading by hand that completes after the end.

    What this test does NOT prove: the answer and its automatic score are two
    transactions, so the shared lock does not keep them together. An end of
    the run that comes between them counts one ungraded answer and writes the
    interim notice, as it does here.

    """
    env = await setup_journey_environment(
        db_session,
        concurrent_client,
        org_slug="auto-score-end",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.QUIZ,
    )
    await db_session.execute(
        update(Challenge)
        .where(Challenge.id == env.challenge_ids[0][0])
        .values(
            scoring_rules={
                "max_points": 5,
                "options": ["Lech", "Wertach"],
                "correct_option": "Lech",
                "auto_approve": True,
            }
        )
    )
    await db_session.commit()
    staff = env.auth_headers["coordinator"]
    created = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/", headers=staff, json={"rallye_id": str(env.rallye_id), "name": "Auto Score Run"}
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    await activate_run(concurrent_client, env.slug, run_id, staff)
    stations = await concurrent_client.get(f"/api/orgs/{env.slug}/runs/{run_id}/stations/", headers=staff)
    challenges = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{stations.json()['items'][0]['id']}/challenges/", headers=staff
    )
    challenge = challenges.json()["items"][0]
    assert challenge["scoring_rules"]["auto_approve"] is True
    await create_team_and_join(
        concurrent_client, env.slug, run_id, env.auth_headers["participant1"], "Team Quiz", "#FF0000"
    )

    # Hold the request between the commit of its answer and its automatic score.
    answered, score_now = asyncio.Event(), asyncio.Event()
    in_flight.releases.append(score_now)
    auto_score = submissions_router._try_auto_score_and_notify

    async def held_auto_score(*args: Any, **kwargs: Any) -> bool:
        answered.set()
        await score_now.wait()
        return await auto_score(*args, **kwargs)

    monkeypatch.setattr(submissions_router, "_try_auto_score_and_notify", held_auto_score)

    # What the end counts, read by the end inside its transaction.
    counted_by_the_end: list[int] = []

    async def recording_count(repo: SubmissionRepository, counted_run: UUID) -> int:
        counted = await _COUNT_UNSCORED(repo, counted_run)
        counted_by_the_end.append(counted)
        return counted

    monkeypatch.setattr(SubmissionRepository, "count_unscored_by_run", recording_count)

    answer = in_flight.start(
        concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
            headers=env.auth_headers["participant1"],
            json={"run_challenge_id": challenge["id"], "content": {"selected_option": "Lech"}},
        )
    )
    await asyncio.wait_for(answered.wait(), timeout=10)
    # The answer is committed and has no score yet.
    assert await _ungraded_answers(test_engine, env, run_id) == 1

    # The hold of the end is set only now: the answer has taken its own shared lock already.
    hook = in_flight.hold(monkeypatch)
    end = in_flight.start(concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=staff))
    await hook.wait_reached()
    score_now.set()
    await _assert_waits_at_the_run_row(test_engine, answer, hook.pid)
    # The score waits before it writes, and the end has not read its count yet.
    assert await _ungraded_answers(test_engine, env, run_id) == 1
    assert counted_by_the_end == []
    hook.release.set()

    assert (await end).status_code == 200
    # The end read its count once: the answer, ungraded. Its notice is the interim one, and there is no results notice.
    assert counted_by_the_end == [1]
    assert [notice.get("interim") for notice in await _result_notices(test_engine, run_id)] == [True]

    scored = await answer
    assert scored.status_code == 201, scored.text
    assert scored.json()["is_scored"] is True
    assert (scored.json()["points"], scored.json()["max_points"]) == (5, 5)
    assert await _ungraded_answers(test_engine, env, run_id) == 0
    # The grading is complete after the end: the interim notice stays in the
    # feed and the scoreboard turns final, as after a grading by hand.
    assert [notice.get("interim") for notice in await _result_notices(test_engine, run_id)] == [True]
    board = await concurrent_client.get(f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard", headers=staff)
    assert board.json()["is_final"] is True
