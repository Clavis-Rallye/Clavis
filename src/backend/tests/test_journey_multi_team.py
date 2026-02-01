"""
Journeys of several teams in one run.

The full journey of three competing teams comes first. The other tests cover the rules between
teams: the cap on teams, team creation only in an active run, and submissions of several teams
to the same challenge.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from tests.factories import create_test_user, get_auth_headers
from tests.journey_helpers import (
    activate_run,
    setup_journey_environment,
    submit_challenge,
)

# =============================================================================
# Scenario: The Full Orchestra
# =============================================================================


@pytest.mark.asyncio
async def test_three_teams_compete_at_the_same_time(client: AsyncClient, db_session: AsyncSession) -> None:
    """Three teams complete different shares of a run, and the ranking, chat isolation, feed and anonymous feedback hold for all of them."""
    # 3 stations with 2 challenges each. All TEXT, because PHOTO verification requires the S3 store.
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="stadt-augsburg",
        num_stations=3,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )

    # The environment has two participants, but this test needs three
    alice = await create_test_user(
        db_session,
        email="alice@augsburg.test",
        password="AlicePass123!",
        display_name="Alice",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    alice_headers = get_auth_headers(alice)

    bob = await create_test_user(
        db_session,
        email="bob@augsburg.test",
        password="BobPass123!",
        display_name="Bob",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    bob_headers = get_auth_headers(bob)

    charlie = await create_test_user(
        db_session,
        email="charlie@augsburg.test",
        password="CharliePass123!",
        display_name="Charlie",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    charlie_headers = get_auth_headers(charlie)

    coord_headers = env.auth_headers["coordinator"]

    # =========================================================================
    # Coordinator creates run
    # =========================================================================

    create_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Augsburg Entdecken - Frühling 2026"},
    )
    assert create_run_response.status_code == 201
    run_data = create_run_response.json()
    run_id = run_data["id"]
    assert run_data["state"] == "configured"

    # =========================================================================
    # Coordinator starts run (snapshots created)
    # =========================================================================

    start_response = await activate_run(client, env.slug, run_id, coord_headers)
    assert start_response["state"] == "active"
    assert start_response["started_at"] is not None

    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coord_headers,
    )
    assert stations_response.status_code == 200
    run_stations = stations_response.json()["items"]
    assert len(run_stations) == 3

    run_challenges_by_station: list[list[dict[str, Any]]] = []
    for run_station in run_stations:
        challenges_response = await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_station['id']}/challenges/",
            headers=coord_headers,
        )
        assert challenges_response.status_code == 200
        challenges = challenges_response.json()["items"]
        assert len(challenges) == 2  # 2 challenges per station
        run_challenges_by_station.append(challenges)

    all_run_challenges = [ch for station_challenges in run_challenges_by_station for ch in station_challenges]
    assert len(all_run_challenges) == 6

    # All TEXT because of challenge_type_override
    expected_types = ["text"] * 6
    actual_types = [ch["challenge_type"] for ch in all_run_challenges]
    assert actual_types == expected_types

    # =========================================================================
    # Alice creates "Team Rathaus" (color #FF0000)
    # =========================================================================

    alice_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=alice_headers,
        json={"name": "Team Rathaus", "color": "#FF0000"},
    )
    assert alice_team_response.status_code == 201
    alice_team = alice_team_response.json()
    assert alice_team["name"] == "Team Rathaus"
    assert alice_team["color"] == "#FF0000"
    assert alice_team["member_count"] == 0  # Not yet joined

    # Alice joins her own team
    alice_join_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{alice_team['id']}/join",
        headers=alice_headers,
    )
    assert alice_join_response.status_code == 204

    # =========================================================================
    # Bob creates "Team Dom" (color #00FF00)
    # =========================================================================

    bob_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=bob_headers,
        json={"name": "Team Dom", "color": "#00FF00"},
    )
    assert bob_team_response.status_code == 201
    bob_team = bob_team_response.json()
    assert bob_team["name"] == "Team Dom"
    assert bob_team["member_count"] == 0

    # Bob joins his own team
    bob_join_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{bob_team['id']}/join",
        headers=bob_headers,
    )
    assert bob_join_response.status_code == 204

    # =========================================================================
    # Charlie creates "Team Fuggerei" (color #0000FF)
    # =========================================================================

    charlie_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=charlie_headers,
        json={"name": "Team Fuggerei", "color": "#0000FF"},
    )
    assert charlie_team_response.status_code == 201
    charlie_team = charlie_team_response.json()
    assert charlie_team["name"] == "Team Fuggerei"
    assert charlie_team["member_count"] == 0

    # Charlie joins his own team
    charlie_join_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{charlie_team['id']}/join",
        headers=charlie_headers,
    )
    assert charlie_join_response.status_code == 204

    # =========================================================================
    # The run has 3 teams
    # =========================================================================

    teams_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_response.status_code == 200
    teams_data = teams_response.json()
    assert teams_data["total"] == 3
    assert len(teams_data["items"]) == 3

    # =========================================================================
    # Round 1: station 1 submissions
    # =========================================================================

    # Alice submits both challenges at station 1
    alice_sub1 = await submit_challenge(
        client,
        env.slug,
        run_id,
        alice_headers,
        all_run_challenges[0]["id"],
        {"text": "1620"},
    )
    assert alice_sub1["content"]["text"] == "1620"

    alice_sub2 = await submit_challenge(
        client,
        env.slug,
        run_id,
        alice_headers,
        all_run_challenges[1]["id"],
        {"text": "Das Rathaus wurde 1620 erbaut"},
    )
    assert alice_sub2["content"]["text"] == "Das Rathaus wurde 1620 erbaut"

    # Bob submits both challenges at station 1
    bob_sub1 = await submit_challenge(
        client,
        env.slug,
        run_id,
        bob_headers,
        all_run_challenges[0]["id"],
        {"text": "Im 17. Jahrhundert"},
    )
    assert bob_sub1["content"]["text"] == "Im 17. Jahrhundert"

    bob_sub2 = await submit_challenge(
        client,
        env.slug,
        run_id,
        bob_headers,
        all_run_challenges[1]["id"],
        {"text": "Der Dom ist ein gotisches Meisterwerk"},
    )

    # Charlie submits only the first challenge at station 1
    charlie_sub1 = await submit_challenge(
        client,
        env.slug,
        run_id,
        charlie_headers,
        all_run_challenges[0]["id"],
        {"text": "1615"},
    )
    assert charlie_sub1["content"]["text"] == "1615"

    # =========================================================================
    # Round 2: station 2 submissions
    # =========================================================================

    # Alice submits both challenges at station 2
    alice_sub3 = await submit_challenge(
        client,
        env.slug,
        run_id,
        alice_headers,
        all_run_challenges[2]["id"],
        {"text": "DOM-QR-2026"},
    )

    alice_sub4 = await submit_challenge(
        client,
        env.slug,
        run_id,
        alice_headers,
        all_run_challenges[3]["id"],
        {"text": "Die Antwort ist C"},
    )

    # Bob submits challenge 3 only (skips challenge 4)
    bob_sub3 = await submit_challenge(
        client,
        env.slug,
        run_id,
        bob_headers,
        all_run_challenges[2]["id"],
        {"text": "DOM-QR-2026"},
    )

    # Charlie submits both challenges at station 2
    charlie_sub2 = await submit_challenge(
        client,
        env.slug,
        run_id,
        charlie_headers,
        all_run_challenges[2]["id"],
        {"text": "DOM-QR-2026"},
    )

    charlie_sub3 = await submit_challenge(
        client,
        env.slug,
        run_id,
        charlie_headers,
        all_run_challenges[3]["id"],
        {"text": "Antwort A"},
    )

    # =========================================================================
    # Round 3: station 3 submissions
    # =========================================================================

    # Alice submits both challenges at station 3 (completes ALL 6 challenges)
    alice_sub5 = await submit_challenge(
        client,
        env.slug,
        run_id,
        alice_headers,
        all_run_challenges[4]["id"],
        {"text": "Wunderschöne Architektur der Fuggerei"},
    )

    alice_sub6 = await submit_challenge(
        client,
        env.slug,
        run_id,
        alice_headers,
        all_run_challenges[5]["id"],
        {"text": "Jakob Fugger gründete die Fuggerei 1521"},
    )

    # Bob submits challenge 5 only (completes 4/6 total)
    bob_sub4 = await submit_challenge(
        client,
        env.slug,
        run_id,
        bob_headers,
        all_run_challenges[4]["id"],
        {"text": "Historisch bedeutsam"},
    )

    # Charlie skips Station 3 entirely (completes 3/6 total)

    # =========================================================================
    # Coordinator scores all submissions
    # =========================================================================

    # Team Rathaus (Alice): 6 submissions
    # max_points sequence: [10, 20, 15, 5, 25, 10] (from setup_journey_environment)
    # All challenges are TEXT, but max_points still vary by position
    # Award: 10/10 + 18/20 + 15/15 + 5/5 + 22/25 + 9/10 = 79/85

    alice_scores = [
        {"submission_id": alice_sub1["id"], "points": 10, "comment": "Perfekt!"},  # max 10
        {"submission_id": alice_sub2["id"], "points": 18, "comment": "Sehr gut"},  # max 20
        {"submission_id": alice_sub3["id"], "points": 15, "comment": "Perfekt!"},  # max 15
        {"submission_id": alice_sub4["id"], "points": 5, "comment": "Alle richtig"},  # max 5
        {"submission_id": alice_sub5["id"], "points": 22, "comment": "Kreativ und gut"},  # max 25
        {"submission_id": alice_sub6["id"], "points": 9, "comment": "Fast perfekt"},  # max 10
    ]

    for score_data in alice_scores:
        score_response = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
            headers=coord_headers,
            json=score_data,
        )
        assert score_response.status_code == 201

    # Team Dom (Bob): 4 submissions (skipped challenge 4 at station 2, skipped challenge 6 at station 3)
    # Submitted challenges 1, 2, 3, 5 with max_points: 10, 20, 15, 25 = max 70
    # Award: 7/10 + 15/20 + 12/15 + 20/25 = 54/70

    bob_scores = [
        {"submission_id": bob_sub1["id"], "points": 7, "comment": "Fast richtig"},  # max 10
        {"submission_id": bob_sub2["id"], "points": 15, "comment": "Gut"},  # max 20
        {"submission_id": bob_sub3["id"], "points": 12, "comment": "Gut gemacht"},  # max 15
        {"submission_id": bob_sub4["id"], "points": 20, "comment": "Sehr kreativ"},  # max 25
    ]

    for score_data in bob_scores:
        score_response = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
            headers=coord_headers,
            json=score_data,
        )
        assert score_response.status_code == 201

    # Team Fuggerei (Charlie): 3 submissions (skipped challenge 2 at station 1, skipped station 3)
    # Submitted challenges 1, 3, 4 with max_points: 10, 15, 5 = max 30
    # Award: 4/10 + 12/15 + 4/5 = 20/30

    charlie_scores = [
        {"submission_id": charlie_sub1["id"], "points": 4, "comment": "Nicht ganz korrekt"},  # max 10
        {"submission_id": charlie_sub2["id"], "points": 12, "comment": "Sehr gut"},  # max 15
        {"submission_id": charlie_sub3["id"], "points": 4, "comment": "Fast perfekt"},  # max 5
    ]

    for score_data in charlie_scores:
        score_response = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
            headers=coord_headers,
            json=score_data,
        )
        assert score_response.status_code == 201

    # =========================================================================
    # Scoreboard ranking
    # =========================================================================

    scoreboard_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=coord_headers,
    )
    assert scoreboard_response.status_code == 200
    scoreboard = scoreboard_response.json()

    entries = scoreboard["entries"]
    assert len(entries) == 3

    # Ranking: Team Rathaus (79) > Team Dom (54) > Team Fuggerei (20)
    assert entries[0]["rank"] == 1
    assert entries[0]["team_name"] == "Team Rathaus"
    assert entries[0]["total_points"] == 79
    assert entries[0]["total_max_points"] == 85
    assert entries[0]["scored_count"] == 6

    assert entries[1]["rank"] == 2
    assert entries[1]["team_name"] == "Team Dom"
    assert entries[1]["total_points"] == 54
    assert entries[1]["total_max_points"] == 70
    assert entries[1]["scored_count"] == 4

    assert entries[2]["rank"] == 3
    assert entries[2]["team_name"] == "Team Fuggerei"
    assert entries[2]["total_points"] == 20
    assert entries[2]["total_max_points"] == 30
    assert entries[2]["scored_count"] == 3

    # =========================================================================
    # Coordinator posts announcement mid-run
    # =========================================================================

    announcement_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=coord_headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Halbzeit! Noch 30 Minuten!", "en": "Half-time! 30 minutes remaining!"},
        },
    )
    assert announcement_response.status_code == 201

    # =========================================================================
    # All 3 participants can read the announcement
    # =========================================================================

    for participant_headers in [alice_headers, bob_headers, charlie_headers]:
        feed_response = await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            headers=participant_headers,
        )
        assert feed_response.status_code == 200
        feed_items = feed_response.json()["items"]
        announcements = [item for item in feed_items if item["event_type"] == "announcement"]
        assert len(announcements) >= 1
        assert announcements[0]["content"]["de"] == "Halbzeit! Noch 30 Minuten!"

    # =========================================================================
    # Chat isolation between teams
    # =========================================================================

    # Alice posts in Team Rathaus chat
    alice_chat_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{alice_team['id']}/chat/",
        headers=alice_headers,
        json={"content": "Station 2 ist am Dom!"},
    )
    assert alice_chat_response.status_code == 201

    # Alice can read her own team's chat
    alice_read_chat_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{alice_team['id']}/chat/",
        headers=alice_headers,
    )
    assert alice_read_chat_response.status_code == 200
    alice_chat_messages = alice_read_chat_response.json()["items"]
    assert len(alice_chat_messages) >= 1
    assert alice_chat_messages[0]["content"] == "Station 2 ist am Dom!"

    # Bob CANNOT read Team Rathaus chat (403 or 404)
    bob_read_alice_chat_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{alice_team['id']}/chat/",
        headers=bob_headers,
    )
    assert bob_read_alice_chat_response.status_code in [403, 404]

    # Bob posts in Team Dom chat
    bob_chat_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{bob_team['id']}/chat/",
        headers=bob_headers,
        json={"content": "Weiter geht's!"},
    )
    assert bob_chat_response.status_code == 201

    # Alice CANNOT read Team Dom chat
    alice_read_bob_chat_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{bob_team['id']}/chat/",
        headers=alice_headers,
    )
    assert alice_read_bob_chat_response.status_code in [403, 404]

    # =========================================================================
    # Coordinator ends the run
    # =========================================================================

    end_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=coord_headers,
    )
    assert end_response.status_code == 200
    assert end_response.json()["state"] == "ended"
    assert end_response.json()["ended_at"] is not None

    # =========================================================================
    # All teams can still read the scoreboard after the end
    # =========================================================================

    for participant_headers in [alice_headers, bob_headers, charlie_headers]:
        scoreboard_response = await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
            headers=participant_headers,
        )
        assert scoreboard_response.status_code == 200

    # =========================================================================
    # All 3 participants submit feedback
    # =========================================================================

    # Alice submits feedback with rating 5
    alice_feedback_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=alice_headers,
        json={"rating": 5, "comment": "Fantastisch!"},
    )
    assert alice_feedback_response.status_code == 201
    # Feedback is anonymous: no user_id in the response
    alice_feedback = alice_feedback_response.json()
    assert "user_id" not in alice_feedback or alice_feedback.get("user_id") is None

    # Bob submits feedback with rating 3
    bob_feedback_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=bob_headers,
        json={"rating": 3, "comment": "Gut, aber kurz"},
    )
    assert bob_feedback_response.status_code == 201

    # Charlie submits feedback with rating 4, no comment
    charlie_feedback_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=charlie_headers,
        json={"rating": 4},
    )
    assert charlie_feedback_response.status_code == 201

    # =========================================================================
    # Coordinator reviews feedback list (anonymous, no user_id)
    # =========================================================================

    feedback_list_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=coord_headers,
    )
    assert feedback_list_response.status_code == 200
    feedback_items = feedback_list_response.json()["items"]
    assert len(feedback_items) == 3

    # All feedback is anonymous
    for feedback in feedback_items:
        assert "user_id" not in feedback or feedback.get("user_id") is None

    # =========================================================================
    # Coordinator views feedback summary
    # =========================================================================

    feedback_summary_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/summary",
        headers=coord_headers,
    )
    assert feedback_summary_response.status_code == 200
    summary = feedback_summary_response.json()
    assert summary["total_count"] == 3
    # Average rating: (5 + 3 + 4) / 3 = 4.0
    assert summary["average_rating"] == 4.0
    # One rating each of 3, 4 and 5
    # Note: JSON serialization converts int keys to strings
    assert summary["distribution"] == {"3": 1, "4": 1, "5": 1}


# =============================================================================
# Scenario: Max Teams Enforcement
# =============================================================================


@pytest.mark.asyncio
async def test_max_teams_enforcement(client: AsyncClient, db_session: AsyncSession) -> None:
    """With max_teams=2 a third team is refused with 422, but the third participant can still join an existing team."""
    env = await setup_journey_environment(
        db_session, client, org_slug="max-teams-org", num_stations=2, challenges_per_station=1
    )

    participant_a = await create_test_user(
        db_session,
        email="participant_a@test.com",
        password="Pass123!",
        display_name="Participant A",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    participant_a_headers = get_auth_headers(participant_a)

    participant_b = await create_test_user(
        db_session,
        email="participant_b@test.com",
        password="Pass123!",
        display_name="Participant B",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    participant_b_headers = get_auth_headers(participant_b)

    participant_c = await create_test_user(
        db_session,
        email="participant_c@test.com",
        password="Pass123!",
        display_name="Participant C",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    participant_c_headers = get_auth_headers(participant_c)

    coord_headers = env.auth_headers["coordinator"]

    create_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Max Teams Test Run", "max_teams": 2},
    )
    assert create_run_response.status_code == 201
    run_data = create_run_response.json()
    run_id = run_data["id"]

    start_response = await activate_run(client, env.slug, run_id, coord_headers)
    assert start_response["state"] == "active"

    # Participant A creates the first team (1/2)
    team_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_a_headers,
        json={"name": "Team Alpha", "color": "#FF0000"},
    )
    assert team_a_response.status_code == 201
    team_a = team_a_response.json()

    join_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a['id']}/join",
        headers=participant_a_headers,
    )
    assert join_a_response.status_code == 204

    # Participant B creates the second team (2/2)
    team_b_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_b_headers,
        json={"name": "Team Beta", "color": "#00FF00"},
    )
    assert team_b_response.status_code == 201
    team_b = team_b_response.json()

    join_b_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b['id']}/join",
        headers=participant_b_headers,
    )
    assert join_b_response.status_code == 204

    # Two teams exist
    teams_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_response.status_code == 200
    assert teams_response.json()["total"] == 2

    # Participant C cannot create a third team
    team_c_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_c_headers,
        json={"name": "Team Gamma", "color": "#0000FF"},
    )
    assert team_c_response.status_code == 422
    error_detail = team_c_response.json()["detail"]
    assert "maximum" in error_detail.lower() or "max_teams" in error_detail.lower()

    # Participant C can still join an existing team
    join_c_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a['id']}/join",
        headers=participant_c_headers,
    )
    assert join_c_response.status_code == 204

    # Team A now has 2 members
    team_a_detail_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a['id']}",
        headers=coord_headers,
    )
    assert team_a_detail_response.status_code == 200
    assert team_a_detail_response.json()["member_count"] == 2


@pytest.mark.asyncio
async def test_max_teams_unlimited(client: AsyncClient, db_session: AsyncSession) -> None:
    """A run without max_teams allows any number of teams."""
    env = await setup_journey_environment(
        db_session, client, org_slug="unlimited-teams-org", num_stations=2, challenges_per_station=1
    )

    participants = []
    for i in range(5):
        participant = await create_test_user(
            db_session,
            email=f"participant{i}@test.com",
            password="Pass123!",
            display_name=f"Participant {i}",
            role=env.users["participant1"].role,
            organization=env.org,
        )
        participants.append(participant)

    coord_headers = env.auth_headers["coordinator"]

    create_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Unlimited Teams Test Run"},
        # Without max_teams the run is unlimited
    )
    assert create_run_response.status_code == 201
    run_data = create_run_response.json()
    run_id = run_data["id"]

    start_response = await activate_run(client, env.slug, run_id, coord_headers)
    assert start_response["state"] == "active"

    for i, participant in enumerate(participants):
        participant_headers = get_auth_headers(participant)
        team_response = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
            headers=participant_headers,
            json={"name": f"Team {i + 1}", "color": f"#{i}{i}{i}{i}{i}{i}"},
        )
        assert team_response.status_code == 201
        team_id = team_response.json()["id"]

        join_response = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
            headers=participant_headers,
        )
        assert join_response.status_code == 204

    teams_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_response.status_code == 200
    assert teams_response.json()["total"] == 5


# =============================================================================
# Scenario: Teams Can Only Be Created in Active Run
# =============================================================================


@pytest.mark.asyncio
async def test_teams_only_created_in_active_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """A team can be created while the run is active, but not while it is configured, paused or ended."""
    env = await setup_journey_environment(
        db_session, client, org_slug="active-only-org", num_stations=2, challenges_per_station=1
    )

    # One participant per attempt
    participants = []
    for i in range(5):
        participant = await create_test_user(
            db_session,
            email=f"participant{i}@test.com",
            password="Pass123!",
            display_name=f"Participant {i}",
            role=env.users["participant1"].role,
            organization=env.org,
        )
        participants.append(participant)

    coord_headers = env.auth_headers["coordinator"]

    create_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "State Transition Test Run"},
    )
    assert create_run_response.status_code == 201
    run_data = create_run_response.json()
    run_id = run_data["id"]
    assert run_data["state"] == "configured"

    # =========================================================================
    # A configured run refuses a new team
    # =========================================================================

    participant_0_headers = get_auth_headers(participants[0])
    team_response_configured = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_0_headers,
        json={"name": "Team Configured", "color": "#FF0000"},
    )
    assert team_response_configured.status_code in [422, 400]

    # =========================================================================
    # An active run accepts a new team
    # =========================================================================

    start_response = await activate_run(client, env.slug, run_id, coord_headers)
    assert start_response["state"] == "active"

    participant_1_headers = get_auth_headers(participants[1])
    team_response_active_1 = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_1_headers,
        json={"name": "Team Active 1", "color": "#00FF00"},
    )
    assert team_response_active_1.status_code == 201
    team_1_id = team_response_active_1.json()["id"]

    join_1_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_1_id}/join",
        headers=participant_1_headers,
    )
    assert join_1_response.status_code == 204

    # =========================================================================
    # A paused run refuses a new team
    # =========================================================================

    pause_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/pause",
        headers=coord_headers,
    )
    assert pause_response.status_code == 200
    assert pause_response.json()["state"] == "paused"

    participant_2_headers = get_auth_headers(participants[2])
    team_response_paused = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_2_headers,
        json={"name": "Team Paused", "color": "#0000FF"},
    )
    assert team_response_paused.status_code in [422, 400]

    # =========================================================================
    # A resumed run accepts a new team again
    # =========================================================================

    resume_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/resume",
        headers=coord_headers,
    )
    assert resume_response.status_code == 200
    assert resume_response.json()["state"] == "active"

    participant_3_headers = get_auth_headers(participants[3])
    team_response_active_2 = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_3_headers,
        json={"name": "Team Active 2", "color": "#FFFF00"},
    )
    assert team_response_active_2.status_code == 201
    team_2_id = team_response_active_2.json()["id"]

    join_2_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_2_id}/join",
        headers=participant_3_headers,
    )
    assert join_2_response.status_code == 204

    # =========================================================================
    # An ended run refuses a new team
    # =========================================================================

    end_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=coord_headers,
    )
    assert end_response.status_code == 200
    assert end_response.json()["state"] == "ended"

    participant_4_headers = get_auth_headers(participants[4])
    team_response_ended = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_4_headers,
        json={"name": "Team Ended", "color": "#FF00FF"},
    )
    assert team_response_ended.status_code in [422, 400]

    # Only the two teams created while the run was active exist
    teams_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_response.status_code == 200
    assert teams_response.json()["total"] == 2


# =============================================================================
# Scenario: Multiple Teams Submit to Same Challenge
# =============================================================================


@pytest.mark.asyncio
async def test_multiple_teams_submit_same_challenge(client: AsyncClient, db_session: AsyncSession) -> None:
    """Different teams can submit to the same challenge, but a team cannot submit to it twice."""
    env = await setup_journey_environment(
        db_session, client, org_slug="multi-submit-org", num_stations=2, challenges_per_station=2
    )

    participant_a = await create_test_user(
        db_session,
        email="team_a_leader@test.com",
        password="Pass123!",
        display_name="Team A Leader",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    participant_a_headers = get_auth_headers(participant_a)

    participant_b = await create_test_user(
        db_session,
        email="team_b_leader@test.com",
        password="Pass123!",
        display_name="Team B Leader",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    participant_b_headers = get_auth_headers(participant_b)

    coord_headers = env.auth_headers["coordinator"]

    create_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Multi Submit Test Run"},
    )
    assert create_run_response.status_code == 201
    run_id = create_run_response.json()["id"]

    start_response = await activate_run(client, env.slug, run_id, coord_headers)
    assert start_response["state"] == "active"

    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coord_headers,
    )
    assert stations_response.status_code == 200
    run_stations = stations_response.json()["items"]

    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[0]['id']}/challenges/",
        headers=coord_headers,
    )
    assert challenges_response.status_code == 200
    run_challenges = challenges_response.json()["items"]
    first_challenge_id = run_challenges[0]["id"]

    team_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_a_headers,
        json={"name": "Team Alpha", "color": "#FF0000"},
    )
    assert team_a_response.status_code == 201
    team_a_id = team_a_response.json()["id"]

    join_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/join",
        headers=participant_a_headers,
    )
    assert join_a_response.status_code == 204

    team_b_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_b_headers,
        json={"name": "Team Beta", "color": "#00FF00"},
    )
    assert team_b_response.status_code == 201
    team_b_id = team_b_response.json()["id"]

    join_b_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/join",
        headers=participant_b_headers,
    )
    assert join_b_response.status_code == 204

    # =========================================================================
    # Team A submits to the first challenge
    # =========================================================================

    submission_a = await submit_challenge(
        client,
        env.slug,
        run_id,
        participant_a_headers,
        first_challenge_id,
        {"text": "Team A answer"},
    )
    assert submission_a["content"]["text"] == "Team A answer"
    submission_a_id = submission_a["id"]

    # =========================================================================
    # Team B submits to the same challenge. A different team is allowed.
    # =========================================================================

    submission_b = await submit_challenge(
        client,
        env.slug,
        run_id,
        participant_b_headers,
        first_challenge_id,
        {"text": "Team B answer"},
    )
    assert submission_b["content"]["text"] == "Team B answer"
    submission_b_id = submission_b["id"]

    # =========================================================================
    # Team A cannot submit to the same challenge again: one submission per team and challenge
    # =========================================================================

    duplicate_submission_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_a_headers,
        json={"run_challenge_id": first_challenge_id, "content": {"text": "Team A second answer"}},
    )
    assert duplicate_submission_response.status_code == 422
    assert duplicate_submission_response.json()["error_code"] == "TEAM_ALREADY_SUBMITTED"

    # =========================================================================
    # The coordinator sees both submissions
    # =========================================================================

    submissions_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=coord_headers,
    )
    assert submissions_response.status_code == 200
    all_submissions = submissions_response.json()["items"]
    assert len(all_submissions) == 2

    submission_ids = [sub["id"] for sub in all_submissions]
    assert submission_a_id in submission_ids
    assert submission_b_id in submission_ids

    team_ids = [sub["team_id"] for sub in all_submissions]
    assert team_a_id in team_ids
    assert team_b_id in team_ids


@pytest.mark.asyncio
async def test_team_can_submit_different_challenges(client: AsyncClient, db_session: AsyncSession) -> None:
    """A team can submit to several different challenges."""
    # Only TEXT challenges, because PHOTO verification requires the S3 store
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="multi-challenge-org",
        num_stations=2,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )

    participant = await create_test_user(
        db_session,
        email="multi_challenge@test.com",
        password="Pass123!",
        display_name="Multi Challenge Participant",
        role=env.users["participant1"].role,
        organization=env.org,
    )
    participant_headers = get_auth_headers(participant)

    coord_headers = env.auth_headers["coordinator"]

    create_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Multi Challenge Test Run"},
    )
    assert create_run_response.status_code == 201
    run_id = create_run_response.json()["id"]

    start_response = await activate_run(client, env.slug, run_id, coord_headers)
    assert start_response["state"] == "active"

    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coord_headers,
    )
    run_stations = stations_response.json()["items"]

    all_run_challenges = []
    for run_station in run_stations:
        challenges_response = await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_station['id']}/challenges/",
            headers=coord_headers,
        )
        all_run_challenges.extend(challenges_response.json()["items"])

    # The run has 4 challenges (2 stations with 2 each), and the test needs 3
    assert len(all_run_challenges) >= 3

    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_headers,
        json={"name": "Team Multi", "color": "#FF0000"},
    )
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    join_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    submission_ids = []
    for i in range(3):
        challenge_id = all_run_challenges[i]["id"]
        challenge_type = all_run_challenges[i]["challenge_type"]

        content: dict[str, Any]
        if challenge_type == "text":
            content = {"text": f"Answer {i + 1}"}
        elif challenge_type == "photo":
            content = {"photo_url": f"https://s3.example.com/photo{i + 1}.jpg"}
        elif challenge_type == "qr_scan":
            content = {"code": f"QR-CODE-{i + 1}"}
        elif challenge_type == "quiz":
            content = {"selected_option": 1}
        elif challenge_type == "creative":
            content = {"text": f"Creative answer {i + 1}"}
        else:
            content = {"text": f"Answer {i + 1}"}

        submission = await submit_challenge(
            client,
            env.slug,
            run_id,
            participant_headers,
            challenge_id,
            content,
        )
        submission_ids.append(submission["id"])

    submissions_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=coord_headers,
    )
    assert submissions_response.status_code == 200
    all_submissions = submissions_response.json()["items"]
    assert len(all_submissions) == 3

    fetched_ids = [sub["id"] for sub in all_submissions]
    for sub_id in submission_ids:
        assert sub_id in fetched_ids
