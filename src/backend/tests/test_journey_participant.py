"""
Registered participant journeys through the API.

The golden path runs from joining a run to feedback after it ends. The other tests cover the rules
around it: joining and leaving a team, one team per participant, one submission per challenge,
submissions only in an active run, chat isolation between teams, the permission boundaries of the
role, and access to single submissions.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from tests.journey_helpers import (
    activate_run,
    setup_journey_environment,
    submit_challenge,
)

# =============================================================================
# Scenario: Complete Participant Experience (Happy Path)
# =============================================================================


@pytest.mark.asyncio
async def test_participant_goes_from_joining_a_run_to_feedback_after_the_end(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A participant goes from joining a run through all challenges to feedback after the run ends."""
    # Only TEXT challenges, because PHOTO verification requires the S3 store
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="pa1-happy-org",
        challenge_type_override=ChallengeType.TEXT,
    )

    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Happy Path Test Run"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    run_id = run_data["id"]
    assert run_data["state"] == "configured"

    # Starting the run creates the frozen snapshots
    start_response = await activate_run(client, env.slug, run_id, coord_headers)
    assert start_response["state"] == "active"
    assert start_response["started_at"] is not None

    # The participant views the run
    participant_headers = env.auth_headers["participant1"]
    get_run_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}",
        headers=participant_headers,
    )
    assert get_run_response.status_code == 200
    assert get_run_response.json()["state"] == "active"

    # The participant creates a team
    create_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_headers,
        json={"name": "Die Entdecker", "color": "#FF5733"},
    )
    assert create_team_response.status_code == 201
    team_data = create_team_response.json()
    team_id = team_data["id"]
    assert team_data["name"] == "Die Entdecker"
    assert team_data["color"] == "#FF5733"
    assert team_data["member_count"] == 0  # Not yet joined

    # The participant joins the team
    join_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    # The member count increases
    get_team_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        headers=participant_headers,
    )
    assert get_team_response.status_code == 200
    assert get_team_response.json()["member_count"] == 1

    # The participant views the stations of the snapshot
    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=participant_headers,
    )
    assert stations_response.status_code == 200
    stations_data = stations_response.json()
    assert len(stations_data["items"]) == 3
    # The station order is preserved
    for idx, station in enumerate(stations_data["items"]):
        assert station["sort_order"] == idx

    # The participant views the challenges of each station
    all_run_challenges: list[dict[str, Any]] = []

    for station in stations_data["items"]:
        challenges_response = await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station['id']}/challenges/",
            headers=participant_headers,
        )
        assert challenges_response.status_code == 200
        challenges_data = challenges_response.json()
        assert len(challenges_data["items"]) == 2  # 2 challenges per station

        station_challenges = sorted(challenges_data["items"], key=lambda c: c["sort_order"])

        all_run_challenges.extend(station_challenges)

        # The scoring rules are visible, because the participant needs max_points
        for challenge in station_challenges:
            assert "scoring_rules" in challenge
            assert "max_points" in challenge["scoring_rules"]

    # Submit all 6 challenges across 3 stations, all TEXT because of challenge_type_override
    submission_answers = [
        "Das Rathaus wurde 1620 erbaut",
        "Beschreibung des Rathausplatzes",
        "STATION2-SECRET",
        "Option 2",
        "Ein wunderschöner Ort",
        "Jakob Fugger gründete die Fuggerei 1521",
    ]

    for i, challenge in enumerate(all_run_challenges):
        assert challenge["challenge_type"] == "text"  # All TEXT due to override
        submission = await submit_challenge(
            client,
            env.slug,
            run_id,
            participant_headers,
            challenge["id"],
            {"text": submission_answers[i]},
        )
        assert submission["content"]["text"] == submission_answers[i]
        assert submission["submitted_at"] is not None

    # The participant sees the submissions of the own team
    submissions_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
    )
    assert submissions_response.status_code == 200
    submissions_data = submissions_response.json()
    assert submissions_data["total"] == 6  # All 6 challenges submitted
    assert len(submissions_data["items"]) == 6

    # The scoreboard is readable before anything is scored
    scoreboard_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=participant_headers,
    )
    assert scoreboard_response.status_code == 200
    # Its content is not asserted: it is empty or shows the team with 0 points.

    # The participant views the feed
    feed_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=participant_headers,
    )
    assert feed_response.status_code == 200
    feed_data = feed_response.json()
    assert "items" in feed_data

    # The participant sends a chat message to the team
    chat_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
        json={"content": "Leute, Station 2 ist links vom Dom!"},
    )
    assert chat_response.status_code == 201
    chat_message = chat_response.json()
    assert chat_message["content"] == "Leute, Station 2 ist links vom Dom!"

    # The participant reads the chat
    get_chat_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
    )
    assert get_chat_response.status_code == 200
    chat_data = get_chat_response.json()
    assert len(chat_data["items"]) >= 1
    our_message = next(m for m in chat_data["items"] if m["content"] == "Leute, Station 2 ist links vom Dom!")
    assert our_message is not None

    # The coordinator ends the run
    end_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=coord_headers,
    )
    assert end_response.status_code == 200
    end_data = end_response.json()
    assert end_data["state"] == "ended"
    assert end_data["ended_at"] is not None

    # The participant submits feedback after the run ended
    feedback_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=participant_headers,
        json={
            "rating": 4,
            "comment": "Tolles Erlebnis, aber Station 3 war schwer zu finden",
        },
    )
    assert feedback_response.status_code == 201
    feedback_data = feedback_response.json()
    assert feedback_data["rating"] == 4
    assert feedback_data["comment"] == "Tolles Erlebnis, aber Station 3 war schwer zu finden"
    # Feedback is anonymous: no user_id in the response
    assert "user_id" not in feedback_data or feedback_data["user_id"] is None

    # The scoreboard is still readable after the end
    final_scoreboard_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=participant_headers,
    )
    assert final_scoreboard_response.status_code == 200


# =============================================================================
# Scenario: Join Existing Team
# =============================================================================


@pytest.mark.asyncio
async def test_join_existing_team(client: AsyncClient, db_session: AsyncSession) -> None:
    """A second participant joins a team that another participant created, and both read its chat."""
    env = await setup_journey_environment(db_session, client, org_slug="pa2-join-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Join Team Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    await activate_run(client, env.slug, run_id, coord_headers)

    # Participant A creates a team and joins it
    participant_a_headers = env.auth_headers["participant1"]
    create_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_a_headers,
        json={"name": "Team Sonnenschein", "color": "#FFAA00"},
    )
    assert create_team_response.status_code == 201
    team_data = create_team_response.json()
    team_id = team_data["id"]
    assert team_data["member_count"] == 0

    join_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_a_headers,
    )
    assert join_a_response.status_code == 204

    # The member count is 1
    get_team_after_a = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        headers=participant_a_headers,
    )
    assert get_team_after_a.status_code == 200
    assert get_team_after_a.json()["member_count"] == 1

    # Participant B joins the team
    participant_b_headers = env.auth_headers["participant2"]
    join_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_b_headers,
    )
    assert join_response.status_code == 204

    # The member count is 2
    get_team_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        headers=participant_a_headers,
    )
    assert get_team_response.status_code == 200
    team_data_updated = get_team_response.json()
    assert team_data_updated["member_count"] == 2

    # Participant B posts a chat message
    chat_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_b_headers,
        json={"content": "Hallo Team! Ich bin dabei!"},
    )
    assert chat_response.status_code == 201

    # Both can read the message
    for headers in [participant_a_headers, participant_b_headers]:
        get_chat_response = await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
            headers=headers,
        )
        assert get_chat_response.status_code == 200
        chat_data = get_chat_response.json()
        messages = [m["content"] for m in chat_data["items"]]
        assert "Hallo Team! Ich bin dabei!" in messages


# =============================================================================
# Scenario: Leave Team
# =============================================================================


@pytest.mark.asyncio
async def test_leave_team(client: AsyncClient, db_session: AsyncSession) -> None:
    """A participant who leaves a team is no longer counted as a member and can no longer chat in it."""
    env = await setup_journey_environment(db_session, client, org_slug="pa3-leave-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Leave Team Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    await activate_run(client, env.slug, run_id, coord_headers)

    # Participant A creates a team and joins it
    participant_a_headers = env.auth_headers["participant1"]
    create_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_a_headers,
        json={"name": "Team Alpha", "color": "#0000FF"},
    )
    assert create_team_response.status_code == 201
    team_data = create_team_response.json()
    team_id = team_data["id"]
    assert team_data["member_count"] == 0

    join_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_a_headers,
    )
    assert join_a_response.status_code == 204

    # Participant B joins the team
    participant_b_headers = env.auth_headers["participant2"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_b_headers,
    )

    # The member count is 2
    get_team_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        headers=participant_a_headers,
    )
    assert get_team_response.status_code == 200
    assert get_team_response.json()["member_count"] == 2

    # Participant B leaves the team
    leave_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/leave",
        headers=participant_b_headers,
    )
    assert leave_response.status_code == 204

    # The member count is 1
    get_team_response_after = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        headers=participant_a_headers,
    )
    assert get_team_response_after.status_code == 200
    assert get_team_response_after.json()["member_count"] == 1

    # Participant B can no longer send chat messages to the team (403 or 404)
    chat_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_b_headers,
        json={"content": "This should fail"},
    )
    assert chat_response.status_code in [403, 404]


# =============================================================================
# Scenario: Cannot Join Multiple Teams
# =============================================================================


@pytest.mark.asyncio
async def test_cannot_join_multiple_teams(client: AsyncClient, db_session: AsyncSession) -> None:
    """A participant who is in a team can neither create nor join another team in the same run (422)."""
    env = await setup_journey_environment(db_session, client, org_slug="pa4-multi-team-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Multi Team Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    await activate_run(client, env.slug, run_id, coord_headers)

    # Participant A creates Team A and joins it
    participant_a_headers = env.auth_headers["participant1"]
    create_team_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_a_headers,
        json={"name": "Team A", "color": "#FF0000"},
    )
    assert create_team_a_response.status_code == 201
    team_a_id = create_team_a_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/join",
        headers=participant_a_headers,
    )

    # Participant A is already in Team A, so creating Team B fails
    create_team_b_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_a_headers,
        json={"name": "Team B", "color": "#00FF00"},
    )
    assert create_team_b_response.status_code == 422

    # Participant B creates Team C
    participant_b_headers = env.auth_headers["participant2"]
    create_team_c_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_b_headers,
        json={"name": "Team C", "color": "#0000FF"},
    )
    assert create_team_c_response.status_code == 201
    team_c_id = create_team_c_response.json()["id"]

    # Participant A is already in Team A, so joining Team C fails
    join_team_c_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_c_id}/join",
        headers=participant_a_headers,
    )
    assert join_team_c_response.status_code == 422


# =============================================================================
# Scenario: One Submission Per Challenge
# =============================================================================


@pytest.mark.asyncio
async def test_one_submission_per_challenge(client: AsyncClient, db_session: AsyncSession) -> None:
    """A second submission of a team for the same challenge is refused with 422 and leaves the first unchanged."""
    env = await setup_journey_environment(db_session, client, org_slug="pa5-one-sub-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "One Submission Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    await activate_run(client, env.slug, run_id, coord_headers)

    participant_headers = env.auth_headers["participant1"]
    create_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_headers,
        json={"name": "Team Solo", "color": "#FF00FF"},
    )
    assert create_team_response.status_code == 201
    team_id = create_team_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )

    # The first challenge is of TEXT type, at station 0
    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=participant_headers,
    )
    assert stations_response.status_code == 200
    station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
        headers=participant_headers,
    )
    assert challenges_response.status_code == 200
    text_challenge_id = challenges_response.json()["items"][0]["id"]

    # The first submission succeeds
    first_submission = await submit_challenge(
        client,
        env.slug,
        run_id,
        participant_headers,
        text_challenge_id,
        {"text": "Original answer"},
    )
    assert first_submission["content"]["text"] == "Original answer"
    submission_id = first_submission["id"]

    # The second submission for the same challenge is refused
    second_submission_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": text_challenge_id,
            "content": {"text": "Second answer"},
        },
    )
    assert second_submission_response.status_code == 422

    # The original submission is unchanged
    get_submission_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/{submission_id}",
        headers=participant_headers,
    )
    assert get_submission_response.status_code == 200
    submission_data = get_submission_response.json()
    assert submission_data["content"]["text"] == "Original answer"


# =============================================================================
# Scenario: Cannot Submit When Run Not Active
# =============================================================================


@pytest.mark.asyncio
async def test_cannot_submit_when_run_not_active(client: AsyncClient, db_session: AsyncSession) -> None:
    """A submission is accepted while the run is active and refused while it is paused or ended."""
    env = await setup_journey_environment(db_session, client, org_slug="pa6-state-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "State Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    # Start the run first, to get challenge ids and create the team
    await activate_run(client, env.slug, run_id, coord_headers)

    participant_headers = env.auth_headers["participant1"]
    create_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_headers,
        json={"name": "Team State", "color": "#00FFFF"},
    )
    assert create_team_response.status_code == 201
    team_id = create_team_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )

    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=participant_headers,
    )
    station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
        headers=participant_headers,
    )
    challenge_id = challenges_response.json()["items"][0]["id"]

    # A submission while the run is active succeeds
    active_submission = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": challenge_id,
            "content": {"text": "Active submission"},
        },
    )
    assert active_submission.status_code == 201

    challenge_id_2 = challenges_response.json()["items"][1]["id"]

    # A submission while the run is paused is refused
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/pause",
        headers=coord_headers,
    )

    paused_submission = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": challenge_id_2,
            "content": {"text": "Paused submission"},
        },
    )
    assert paused_submission.status_code in [400, 403, 422]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/resume",
        headers=coord_headers,
    )

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": challenge_id_2,
            "content": {"text": "Resumed submission"},
        },
    )

    station_2_id = stations_response.json()["items"][1]["id"]
    challenges_2_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_2_id}/challenges/",
        headers=participant_headers,
    )
    challenge_id_3 = challenges_2_response.json()["items"][0]["id"]

    # A submission after the run ended is refused
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=coord_headers,
    )

    ended_submission = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": challenge_id_3,
            "content": {"text": "Ended submission"},
        },
    )
    assert ended_submission.status_code in [400, 403, 422]


# =============================================================================
# Scenario: Participant Cannot Access Other Teams' Chat
# =============================================================================


@pytest.mark.asyncio
async def test_cannot_access_other_teams_chat(client: AsyncClient, db_session: AsyncSession) -> None:
    """A participant can neither read nor write the chat of another team."""
    env = await setup_journey_environment(db_session, client, org_slug="pa7-chat-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Chat Isolation Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    await activate_run(client, env.slug, run_id, coord_headers)

    participant_a_headers = env.auth_headers["participant1"]
    create_team_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_a_headers,
        json={"name": "Team A", "color": "#FF0000"},
    )
    assert create_team_a_response.status_code == 201
    team_a_id = create_team_a_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/join",
        headers=participant_a_headers,
    )

    participant_b_headers = env.auth_headers["participant2"]
    create_team_b_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_b_headers,
        json={"name": "Team B", "color": "#00FF00"},
    )
    assert create_team_b_response.status_code == 201
    team_b_id = create_team_b_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/join",
        headers=participant_b_headers,
    )

    # Participant B posts a message to Team B
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/chat/",
        headers=participant_b_headers,
        json={"content": "Team B secret message"},
    )

    # Participant A cannot read the chat of Team B
    get_chat_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/chat/",
        headers=participant_a_headers,
    )
    assert get_chat_response.status_code in [403, 404]

    # Participant A cannot post to the chat of Team B
    post_chat_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/chat/",
        headers=participant_a_headers,
        json={"content": "Trying to infiltrate Team B"},
    )
    assert post_chat_response.status_code in [403, 404]


# =============================================================================
# Scenario: Participant Permission Boundaries
# =============================================================================


@pytest.mark.asyncio
async def test_participant_permission_boundaries(client: AsyncClient, db_session: AsyncSession) -> None:
    """A participant gets 403 on coordinator and admin actions, and can use the endpoints of a participant."""
    env = await setup_journey_environment(db_session, client, org_slug="pa8-perms-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Permissions Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    await activate_run(client, env.slug, run_id, coord_headers)

    participant_headers = env.auth_headers["participant1"]

    create_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_headers,
        json={"name": "Team Participant", "color": "#AABBCC"},
    )
    team_id = create_team_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )

    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=participant_headers,
    )
    station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
        headers=participant_headers,
    )
    challenge_id = challenges_response.json()["items"][0]["id"]

    # The submission is the target of the score attempt
    submit_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": challenge_id,
            "content": {"text": "Test answer"},
        },
    )
    submission_id = submit_response.json()["id"]

    # Forbidden: the participant tries coordinator and admin actions

    # Create a run
    create_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=participant_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Unauthorized Run"},
    )
    assert create_run_response.status_code == 403

    # A second run, still configured, for the start attempt
    create_test_run = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Test Run 2"},
    )
    test_run_id = create_test_run.json()["id"]

    # Start a run
    start_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{test_run_id}/start",
        headers=participant_headers,
    )
    assert start_run_response.status_code == 403

    # Pause a run
    pause_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/pause",
        headers=participant_headers,
    )
    assert pause_run_response.status_code == 403

    # End a run
    end_run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=participant_headers,
    )
    assert end_run_response.status_code == 403

    # Score a submission
    score_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=participant_headers,
        json={
            "submission_id": submission_id,
            "points": 5,
            "comment": "Unauthorized score",
        },
    )
    assert score_response.status_code == 403

    # Post a feed announcement
    feed_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=participant_headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Unauthorized announcement", "en": "Unauthorized"},
        },
    )
    assert feed_response.status_code == 403

    # List the feedback
    feedback_list_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=participant_headers,
    )
    assert feedback_list_response.status_code == 403

    # Read the feedback summary
    feedback_summary_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/summary",
        headers=participant_headers,
    )
    assert feedback_summary_response.status_code == 403

    # Update a run
    update_run_response = await client.put(
        f"/api/orgs/{env.slug}/runs/{run_id}",
        headers=participant_headers,
        json={"name": "Unauthorized Update"},
    )
    assert update_run_response.status_code == 403

    # Allowed: run, teams, stations, challenges, scoreboard, feed, and the chat of the own team
    get_run_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}",
        headers=participant_headers,
    )
    assert get_run_response.status_code == 200

    get_teams_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_headers,
    )
    assert get_teams_response.status_code == 200

    assert stations_response.status_code == 200

    assert challenges_response.status_code == 200

    scoreboard_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=participant_headers,
    )
    assert scoreboard_response.status_code == 200

    get_feed_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=participant_headers,
    )
    assert get_feed_response.status_code == 200

    # The submission above was created with 201
    assert submit_response.status_code == 201

    chat_post_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
        json={"content": "Test chat message"},
    )
    assert chat_post_response.status_code == 201

    chat_get_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
    )
    assert chat_get_response.status_code == 200


# =============================================================================
# Scenario: View Individual Submission Access Control
# =============================================================================


@pytest.mark.asyncio
async def test_view_individual_submission_access_control(client: AsyncClient, db_session: AsyncSession) -> None:
    """A participant can read the submission of the own team but not that of another team, and a coordinator can read both."""
    env = await setup_journey_environment(db_session, client, org_slug="pa9-sub-access-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Submission Access Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    await activate_run(client, env.slug, run_id, coord_headers)

    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coord_headers,
    )
    station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
        headers=coord_headers,
    )
    challenge_id = challenges_response.json()["items"][0]["id"]

    # Participant A creates Team A and submits
    participant_a_headers = env.auth_headers["participant1"]
    create_team_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_a_headers,
        json={"name": "Team A", "color": "#FF0000"},
    )
    team_a_id = create_team_a_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/join",
        headers=participant_a_headers,
    )

    submit_a_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_a_headers,
        json={
            "run_challenge_id": challenge_id,
            "content": {"text": "Team A answer"},
        },
    )
    submission_a_id = submit_a_response.json()["id"]

    # Participant B creates Team B and submits
    participant_b_headers = env.auth_headers["participant2"]
    create_team_b_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=participant_b_headers,
        json={"name": "Team B", "color": "#00FF00"},
    )
    team_b_id = create_team_b_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/join",
        headers=participant_b_headers,
    )

    submit_b_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_b_headers,
        json={
            "run_challenge_id": challenge_id,
            "content": {"text": "Team B answer"},
        },
    )
    submission_b_id = submit_b_response.json()["id"]

    # Participant A can read the submission of the own team
    get_own_submission = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/{submission_a_id}",
        headers=participant_a_headers,
    )
    assert get_own_submission.status_code == 200
    assert get_own_submission.json()["content"]["text"] == "Team A answer"

    # Participant A cannot read the submission of Team B (403 or 404)
    get_other_submission = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/{submission_b_id}",
        headers=participant_a_headers,
    )
    assert get_other_submission.status_code in [403, 404]

    # The coordinator can read both submissions
    coord_view_a = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/{submission_a_id}",
        headers=coord_headers,
    )
    assert coord_view_a.status_code == 200

    coord_view_b = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/{submission_b_id}",
        headers=coord_headers,
    )
    assert coord_view_b.status_code == 200
