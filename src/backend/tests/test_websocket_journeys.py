"""
WebSocket journeys: end-to-end workflows over the run and team sockets.

- Coordinator: feed events and run state changes reach the run socket
- Participant: chat, feed and state changes arrive, a submission pushes nothing
- Multi-team: broadcast reach and chat isolation across three teams
- Edge cases: reconnect, no replay of history, paused and ended runs
- Cross-tenant: a socket never receives another organization's events
- Targeted feed events and the dissolution of a team
"""

import asyncio
from uuid import UUID

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from tests.conftest import await_ws_subscribed
from tests.journey_helpers import setup_journey_environment

# ============================================================================
# Coordinator Real-Time Journey
# ============================================================================


@pytest.mark.asyncio
async def test_coordinator_gets_every_feed_event_and_state_change_on_the_run_socket(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A coordinator gets every feed event type and every run state change, from start to end, on the run socket."""
    env = await setup_journey_environment(db_session, client, org_slug="wj1-coordinator-realtime")
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Coordinator Real-Time Journey Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    token = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    # The socket connects before the run starts, so it sees configured → active.
    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        await await_ws_subscribed(ws)

        activate_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/start",
            headers=env.auth_headers["coordinator"],
        )
        assert activate_resp.status_code == 200

        # Give NOTIFY time to propagate through PgNotifyListener
        await asyncio.sleep(0.1)

        state_msg = await ws.receive_json(timeout=5.0)
        assert state_msg["type"] == "run_state_change"
        assert state_msg["data"]["run_id"] == str(run_id)
        assert state_msg["data"]["previous_state"] == "configured"
        assert state_msg["data"]["new_state"] == "active"
        assert "changed_at" in state_msg["data"]
        assert "timestamp" in state_msg

        # Post ANNOUNCEMENT → receive on WS
        announcement_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {
                    "de": "Willkommen zur Rallye!",
                    "en": "Welcome to the rallye!",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201
        announcement_data = announcement_resp.json()

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        feed_msg = await ws.receive_json(timeout=5.0)
        assert feed_msg["type"] == "feed_event"
        assert feed_msg["data"]["id"] == announcement_data["id"]
        assert feed_msg["data"]["event_type"] == "announcement"
        assert feed_msg["data"]["content"]["de"] == "Willkommen zur Rallye!"
        assert feed_msg["data"]["content"]["en"] == "Welcome to the rallye!"
        assert feed_msg["data"]["author_id"] == str(coordinator.id)
        assert "timestamp" in feed_msg

        # Post COUNTDOWN with target_time → receive on WS
        countdown_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "countdown",
                "content": {
                    "target_time": "2026-07-15T12:00:00Z",
                    "de": "Mittagspause endet um 12:00",
                    "en": "Lunch break ends at 12:00",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert countdown_resp.status_code == 201
        countdown_data = countdown_resp.json()

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        countdown_msg = await ws.receive_json(timeout=5.0)
        assert countdown_msg["type"] == "feed_event"
        assert countdown_msg["data"]["id"] == countdown_data["id"]
        assert countdown_msg["data"]["event_type"] == "countdown"
        assert countdown_msg["data"]["content"]["target_time"] == "2026-07-15T12:00:00Z"
        assert countdown_msg["data"]["content"]["de"] == "Mittagspause endet um 12:00"
        assert countdown_msg["data"]["content"]["en"] == "Lunch break ends at 12:00"
        assert countdown_msg["data"]["author_id"] == str(coordinator.id)
        assert "timestamp" in countdown_msg

        # Post REMINDER → receive on WS
        reminder_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "reminder",
                "content": {
                    "de": "Vergesst nicht, genug Wasser mitzubringen!",
                    "en": "Don't forget to bring enough water!",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert reminder_resp.status_code == 201
        reminder_data = reminder_resp.json()

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        reminder_msg = await ws.receive_json(timeout=5.0)
        assert reminder_msg["type"] == "feed_event"
        assert reminder_msg["data"]["id"] == reminder_data["id"]
        assert reminder_msg["data"]["event_type"] == "reminder"
        assert reminder_msg["data"]["content"]["de"] == "Vergesst nicht, genug Wasser mitzubringen!"
        assert reminder_msg["data"]["content"]["en"] == "Don't forget to bring enough water!"
        assert reminder_msg["data"]["author_id"] == str(coordinator.id)
        assert "timestamp" in reminder_msg

        # Post ALERT → receive on WS
        alert_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "alert",
                "content": {
                    "de": "ACHTUNG: Gewitter-Warnung! Bitte Schutz suchen!",
                    "en": "WARNING: Thunderstorm alert! Please seek shelter!",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert alert_resp.status_code == 201
        alert_data = alert_resp.json()

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        alert_msg = await ws.receive_json(timeout=5.0)
        assert alert_msg["type"] == "feed_event"
        assert alert_msg["data"]["id"] == alert_data["id"]
        assert alert_msg["data"]["event_type"] == "alert"
        assert alert_msg["data"]["content"]["de"] == "ACHTUNG: Gewitter-Warnung! Bitte Schutz suchen!"
        assert alert_msg["data"]["content"]["en"] == "WARNING: Thunderstorm alert! Please seek shelter!"
        assert alert_msg["data"]["author_id"] == str(coordinator.id)
        assert "timestamp" in alert_msg

        # Pause run → receive run_state_change
        pause_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/pause",
            headers=env.auth_headers["coordinator"],
        )
        assert pause_resp.status_code == 200

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        pause_msg = await ws.receive_json(timeout=5.0)
        assert pause_msg["type"] == "run_state_change"
        assert pause_msg["data"]["run_id"] == str(run_id)
        assert pause_msg["data"]["previous_state"] == "active"
        assert pause_msg["data"]["new_state"] == "paused"
        assert "changed_at" in pause_msg["data"]
        assert "timestamp" in pause_msg

        # Resume run → receive run_state_change
        resume_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/resume",
            headers=env.auth_headers["coordinator"],
        )
        assert resume_resp.status_code == 200

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        resume_msg = await ws.receive_json(timeout=5.0)
        assert resume_msg["type"] == "run_state_change"
        assert resume_msg["data"]["run_id"] == str(run_id)
        assert resume_msg["data"]["previous_state"] == "paused"
        assert resume_msg["data"]["new_state"] == "active"
        assert "changed_at" in resume_msg["data"]
        assert "timestamp" in resume_msg

        # End run → receive run_state_change
        end_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/end",
            headers=env.auth_headers["coordinator"],
        )
        assert end_resp.status_code == 200

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        end_msg = await ws.receive_json(timeout=5.0)
        assert end_msg["type"] == "run_state_change"
        assert end_msg["data"]["run_id"] == str(run_id)
        assert end_msg["data"]["previous_state"] == "active"
        assert end_msg["data"]["new_state"] == "ended"
        assert "changed_at" in end_msg["data"]
        assert "timestamp" in end_msg


# ============================================================================
# Participant Real-Time Journey
# ============================================================================


@pytest.mark.asyncio
async def test_participant_gets_team_chat_feed_and_state_changes_on_their_sockets(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A participant gets team chat on the chat socket and feed and state changes on the run socket. A submission pushes nothing."""
    env = await setup_journey_environment(db_session, client, org_slug="wj2-participant-realtime")
    participant1 = env.users["participant1"]
    participant2 = env.users["participant2"]
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Participant Real-Time Journey Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    team_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={
            "name": "Team Phoenix",
            "color_hex": "#FF6B35",
        },
        headers=env.auth_headers["participant1"],
    )
    assert team_resp.status_code == 201
    team_id = UUID(team_resp.json()["id"])

    join1_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join1_resp.status_code == 204

    join2_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant2"],
    )
    assert join2_resp.status_code == 204

    p1_token = create_access_token(
        user_id=participant1.id,
        role=participant1.role.value,
        organization_id=participant1.organization_id,
    )

    async with (
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={p1_token}", ws_client) as run_ws,
        aconnect_ws(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={p1_token}",
            ws_client,
        ) as chat_ws,
    ):
        await await_ws_subscribed(run_ws)
        await await_ws_subscribed(chat_ws)

        # Participant2 sends chat → participant1 receives on chat WS
        chat_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
            json={
                "content": "Hey team! Ready for the challenge?",
            },
            headers=env.auth_headers["participant2"],
        )
        assert chat_resp.status_code == 201
        chat_data = chat_resp.json()

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        chat_msg = await chat_ws.receive_json(timeout=5.0)
        assert chat_msg["type"] == "chat_message"
        assert chat_msg["data"]["id"] == chat_data["id"]
        assert chat_msg["data"]["team_id"] == str(team_id)
        assert chat_msg["data"]["content"] == "Hey team! Ready for the challenge?"
        assert chat_msg["data"]["author_id"] == str(participant2.id)
        assert "timestamp" in chat_msg

        # Coordinator posts announcement → participant1 receives on feed WS
        announcement_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {
                    "de": "Alle Teams: Treffpunkt um 14:00 am Rathaus!",
                    "en": "All teams: Meeting at 2pm at city hall!",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201
        announcement_data = announcement_resp.json()

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        feed_msg = await run_ws.receive_json(timeout=5.0)
        assert feed_msg["type"] == "feed_event"
        assert feed_msg["data"]["id"] == announcement_data["id"]
        assert feed_msg["data"]["event_type"] == "announcement"
        assert feed_msg["data"]["content"]["de"] == "Alle Teams: Treffpunkt um 14:00 am Rathaus!"
        assert feed_msg["data"]["content"]["en"] == "All teams: Meeting at 2pm at city hall!"
        assert feed_msg["data"]["author_id"] == str(coordinator.id)
        assert "timestamp" in feed_msg

        # Coordinator pauses run → participant1 receives state change on feed WS
        pause_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/pause",
            headers=env.auth_headers["coordinator"],
        )
        assert pause_resp.status_code == 200

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        state_msg = await run_ws.receive_json(timeout=5.0)
        assert state_msg["type"] == "run_state_change"
        assert state_msg["data"]["run_id"] == str(run_id)
        assert state_msg["data"]["previous_state"] == "active"
        assert state_msg["data"]["new_state"] == "paused"
        assert "changed_at" in state_msg["data"]
        assert "timestamp" in state_msg

        # Verify submission creation does NOT trigger WS message
        # First, resume the run (submissions can only be made when active)
        resume_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/resume",
            headers=env.auth_headers["coordinator"],
        )
        assert resume_resp.status_code == 200

        await asyncio.sleep(0.1)
        resume_state_msg = await run_ws.receive_json(timeout=5.0)
        assert resume_state_msg["type"] == "run_state_change"
        assert resume_state_msg["data"]["new_state"] == "active"

        stations_resp = await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
            headers=env.auth_headers["participant1"],
        )
        assert stations_resp.status_code == 200
        stations_data = stations_resp.json()
        assert len(stations_data["items"]) > 0
        station_id = UUID(stations_data["items"][0]["id"])

        challenges_resp = await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
            headers=env.auth_headers["participant1"],
        )
        assert challenges_resp.status_code == 200
        challenges_data = challenges_resp.json()
        assert len(challenges_data["items"]) > 0
        challenge_id = UUID(challenges_data["items"][0]["id"])

        submission_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
            json={
                "run_challenge_id": str(challenge_id),
                "content": {"text": "This is a test submission"},
            },
            headers=env.auth_headers["participant1"],
        )
        assert submission_resp.status_code == 201

        # Give time for any potential (incorrect) NOTIFY
        await asyncio.sleep(0.2)

        try:
            unexpected_run_msg = await run_ws.receive_json(timeout=0.5)
            pytest.fail(f"Unexpected message on run WS after submission: {unexpected_run_msg}")
        except TimeoutError:
            pass

        try:
            unexpected_chat_msg = await chat_ws.receive_json(timeout=0.5)
            pytest.fail(f"Unexpected message on chat WS after submission: {unexpected_chat_msg}")
        except TimeoutError:
            pass


# ============================================================================
# Multi-Team Real-Time Simulation
# ============================================================================


@pytest.mark.asyncio
async def test_three_teams_get_the_same_feed_events_and_their_own_chat(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """Three teams get the same feed events and state changes, team chat stays with its team, and the order of arrival is chronological."""
    env = await setup_journey_environment(db_session, client, org_slug="wj3-multi-team-realtime")

    # We need at least one participant per team to connect to their chat WS
    from app.models.user import UserRole
    from tests.factories import create_test_user

    participant3 = await create_test_user(
        db_session,
        email="participant3@wj3.test",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    participant4 = await create_test_user(
        db_session,
        email="participant4@wj3.test",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    participant5 = await create_test_user(
        db_session,
        email="participant5@wj3.test",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    await db_session.commit()

    from tests.factories import get_auth_headers

    p3_headers = get_auth_headers(participant3)
    p4_headers = get_auth_headers(participant4)
    p5_headers = get_auth_headers(participant5)

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Multi-Team Real-Time Simulation Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    # Team A: participant1, participant2
    team_a_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team Alpha", "color_hex": "#FF0000"},
        headers=env.auth_headers["participant1"],
    )
    assert team_a_resp.status_code == 201
    team_a_id = UUID(team_a_resp.json()["id"])

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/join",
        headers=env.auth_headers["participant1"],
    )
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/join",
        headers=env.auth_headers["participant2"],
    )

    # Team B: participant3, participant4
    team_b_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team Bravo", "color_hex": "#00FF00"},
        headers=p3_headers,
    )
    assert team_b_resp.status_code == 201
    team_b_id = UUID(team_b_resp.json()["id"])

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/join",
        headers=p3_headers,
    )
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/join",
        headers=p4_headers,
    )

    # Team C: participant5
    team_c_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team Charlie", "color_hex": "#0000FF"},
        headers=p5_headers,
    )
    assert team_c_resp.status_code == 201
    team_c_id = UUID(team_c_resp.json()["id"])

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_c_id}/join",
        headers=p5_headers,
    )

    p1_token = create_access_token(
        user_id=env.users["participant1"].id,
        role=env.users["participant1"].role.value,
        organization_id=env.users["participant1"].organization_id,
    )
    p3_token = create_access_token(
        user_id=participant3.id,
        role=participant3.role.value,
        organization_id=participant3.organization_id,
    )
    p5_token = create_access_token(
        user_id=participant5.id,
        role=participant5.role.value,
        organization_id=participant5.organization_id,
    )

    # Track messages received by each team for chronological verification
    team_a_messages = []
    team_b_messages = []
    team_c_messages = []

    async with (
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={p1_token}", ws_client) as team_a_run_ws,
        aconnect_ws(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/ws?token={p1_token}",
            ws_client,
        ) as team_a_chat_ws,
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={p3_token}", ws_client) as team_b_run_ws,
        aconnect_ws(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/ws?token={p3_token}",
            ws_client,
        ) as team_b_chat_ws,
        aconnect_ws(
            f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={p5_token}",
            ws_client,
        ) as team_c_run_ws,
        aconnect_ws(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_c_id}/ws?token={p5_token}",
            ws_client,
        ) as team_c_chat_ws,
    ):
        await await_ws_subscribed(team_a_run_ws)
        await await_ws_subscribed(team_a_chat_ws)
        await await_ws_subscribed(team_b_run_ws)
        await await_ws_subscribed(team_b_chat_ws)
        await await_ws_subscribed(team_c_run_ws)
        await await_ws_subscribed(team_c_chat_ws)

        # Coordinator sends announcement → all 3 receive
        announcement_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {
                    "de": "Willkommen alle Teams!",
                    "en": "Welcome all teams!",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201
        announcement_data = announcement_resp.json()

        await asyncio.sleep(0.1)

        msg_a = await team_a_run_ws.receive_json(timeout=5.0)
        team_a_messages.append(msg_a)
        assert msg_a["type"] == "feed_event"
        assert msg_a["data"]["id"] == announcement_data["id"]
        assert msg_a["data"]["event_type"] == "announcement"

        msg_b = await team_b_run_ws.receive_json(timeout=5.0)
        team_b_messages.append(msg_b)
        assert msg_b["type"] == "feed_event"
        assert msg_b["data"]["id"] == announcement_data["id"]
        assert msg_b["data"]["event_type"] == "announcement"

        msg_c = await team_c_run_ws.receive_json(timeout=5.0)
        team_c_messages.append(msg_c)
        assert msg_c["type"] == "feed_event"
        assert msg_c["data"]["id"] == announcement_data["id"]
        assert msg_c["data"]["event_type"] == "announcement"

        # Team A sends chat → only Team A receives
        chat_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/chat/",
            json={"content": "Team A checking in!"},
            headers=env.auth_headers["participant1"],
        )
        assert chat_resp.status_code == 201
        chat_data = chat_resp.json()

        await asyncio.sleep(0.1)

        chat_msg_a = await team_a_chat_ws.receive_json(timeout=5.0)
        team_a_messages.append(chat_msg_a)
        assert chat_msg_a["type"] == "chat_message"
        assert chat_msg_a["data"]["id"] == chat_data["id"]
        assert chat_msg_a["data"]["content"] == "Team A checking in!"

        # Team B and C should NOT receive this chat
        try:
            unexpected_b = await team_b_chat_ws.receive_json(timeout=0.5)
            pytest.fail(f"Team B received Team A's chat (unexpected): {unexpected_b}")
        except TimeoutError:
            pass  # Expected: no message

        try:
            unexpected_c = await team_c_chat_ws.receive_json(timeout=0.5)
            pytest.fail(f"Team C received Team A's chat (unexpected): {unexpected_c}")
        except TimeoutError:
            pass  # Expected: no message

        # Coordinator sends countdown → all 3 receive
        countdown_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "countdown",
                "content": {
                    "target_time": "2026-07-15T15:00:00Z",
                    "de": "Nur noch 1 Stunde!",
                    "en": "Only 1 hour left!",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert countdown_resp.status_code == 201
        countdown_data = countdown_resp.json()

        await asyncio.sleep(0.1)

        countdown_a = await team_a_run_ws.receive_json(timeout=5.0)
        team_a_messages.append(countdown_a)
        assert countdown_a["type"] == "feed_event"
        assert countdown_a["data"]["id"] == countdown_data["id"]
        assert countdown_a["data"]["event_type"] == "countdown"

        countdown_b = await team_b_run_ws.receive_json(timeout=5.0)
        team_b_messages.append(countdown_b)
        assert countdown_b["type"] == "feed_event"
        assert countdown_b["data"]["id"] == countdown_data["id"]
        assert countdown_b["data"]["event_type"] == "countdown"

        countdown_c = await team_c_run_ws.receive_json(timeout=5.0)
        team_c_messages.append(countdown_c)
        assert countdown_c["type"] == "feed_event"
        assert countdown_c["data"]["id"] == countdown_data["id"]
        assert countdown_c["data"]["event_type"] == "countdown"

        # Run ended → all 3 receive state change
        end_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/end",
            headers=env.auth_headers["coordinator"],
        )
        assert end_resp.status_code == 200

        await asyncio.sleep(0.1)

        end_a = await team_a_run_ws.receive_json(timeout=5.0)
        team_a_messages.append(end_a)
        assert end_a["type"] == "run_state_change"
        assert end_a["data"]["run_id"] == str(run_id)
        assert end_a["data"]["new_state"] == "ended"

        end_b = await team_b_run_ws.receive_json(timeout=5.0)
        team_b_messages.append(end_b)
        assert end_b["type"] == "run_state_change"
        assert end_b["data"]["run_id"] == str(run_id)
        assert end_b["data"]["new_state"] == "ended"

        end_c = await team_c_run_ws.receive_json(timeout=5.0)
        team_c_messages.append(end_c)
        assert end_c["type"] == "run_state_change"
        assert end_c["data"]["run_id"] == str(run_id)
        assert end_c["data"]["new_state"] == "ended"

    # Team A should have received: announcement, chat, countdown, ended (4 messages)
    # Teams B and C should have received: announcement, countdown, ended (3 messages each)
    assert len(team_a_messages) == 4
    assert len(team_b_messages) == 3
    assert len(team_c_messages) == 3

    def verify_chronological(messages):
        timestamps = [msg["timestamp"] for msg in messages]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1], f"Messages out of order: {timestamps}"

    verify_chronological(team_a_messages)
    verify_chronological(team_b_messages)
    verify_chronological(team_c_messages)

    assert team_a_messages[0]["type"] == "feed_event"  # announcement
    assert team_a_messages[1]["type"] == "chat_message"  # Team A chat
    assert team_a_messages[2]["type"] == "feed_event"  # countdown
    assert team_a_messages[3]["type"] == "run_state_change"  # ended

    assert team_b_messages[0]["type"] == "feed_event"  # announcement
    assert team_b_messages[1]["type"] == "feed_event"  # countdown
    assert team_b_messages[2]["type"] == "run_state_change"  # ended

    assert team_c_messages[0]["type"] == "feed_event"  # announcement
    assert team_c_messages[1]["type"] == "feed_event"  # countdown
    assert team_c_messages[2]["type"] == "run_state_change"  # ended


# ============================================================================
# Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_new_connection_gets_only_feed_events_created_after_it_connects(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A new connection gets only the feed events created after it connects, no replay of earlier ones."""
    env = await setup_journey_environment(db_session, client, org_slug="wj4-post-connection-only")
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Edge Case Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    historical_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        json={
            "event_type": "announcement",
            "content": {"de": "Historische Nachricht", "en": "Historical message"},
        },
        headers=env.auth_headers["coordinator"],
    )
    assert historical_resp.status_code == 201
    historical_id = historical_resp.json()["id"]

    token = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        # Wait until the server has subscribed this socket to the run channels,
        # so the silence below means no replay, not a missed subscription
        await await_ws_subscribed(ws)

        # Wait to see whether a historical event arrives. It must not.
        await asyncio.sleep(0.3)

        try:
            unexpected_msg = await ws.receive_json(timeout=0.5)
            pytest.fail(f"Received historical event (unexpected): {unexpected_msg}")
        except TimeoutError:
            pass  # Expected: no historical events

        new_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {"de": "Neue Nachricht", "en": "New message"},
            },
            headers=env.auth_headers["coordinator"],
        )
        assert new_resp.status_code == 201
        new_id = new_resp.json()["id"]

        await asyncio.sleep(0.1)

        new_msg = await ws.receive_json(timeout=5.0)
        assert new_msg["type"] == "feed_event"
        assert new_msg["data"]["id"] == new_id
        assert new_msg["data"]["content"]["de"] == "Neue Nachricht"
        assert new_msg["data"]["id"] != historical_id  # NOT the historical event


@pytest.mark.asyncio
async def test_disconnect_before_a_broadcast_does_not_block_other_clients(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A client that disconnects before a broadcast does not keep the other clients from receiving it."""
    env = await setup_journey_environment(db_session, client, org_slug="wj4-disconnect-broadcast")
    p1 = env.users["participant1"]
    p2 = env.users["participant2"]

    from app.models.user import UserRole
    from tests.factories import create_test_user

    p3 = await create_test_user(
        db_session,
        email="p3@wj4-disconnect.test",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    await db_session.commit()

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Disconnect Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    p1_token = create_access_token(user_id=p1.id, role=p1.role.value, organization_id=p1.organization_id)
    p2_token = create_access_token(user_id=p2.id, role=p2.role.value, organization_id=p2.organization_id)
    p3_token = create_access_token(user_id=p3.id, role=p3.role.value, organization_id=p3.organization_id)

    # Connect all 3 participants, then disconnect ws2 before broadcasting
    async with (
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={p1_token}", ws_client) as ws1,
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={p3_token}", ws_client) as ws3,
    ):
        await await_ws_subscribed(ws1)
        await await_ws_subscribed(ws3)

        # Connect ws2 and immediately disconnect by exiting its context
        # (calling close() inside the context causes httpx_ws EndOfStream on cleanup)
        try:
            async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={p2_token}", ws_client) as ws2:
                # Subscribe first, so the disconnect cleans up a live subscription
                await await_ws_subscribed(ws2)
        except Exception:
            pass

        # Give the server time to process the disconnect.
        await asyncio.sleep(0.2)

        announcement_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {"de": "Test", "en": "Test"},
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201
        announcement_id = announcement_resp.json()["id"]

        await asyncio.sleep(0.1)

        msg1 = await ws1.receive_json(timeout=5.0)
        assert msg1["type"] == "feed_event"
        assert msg1["data"]["id"] == announcement_id

        msg3 = await ws3.receive_json(timeout=5.0)
        assert msg3["type"] == "feed_event"
        assert msg3["data"]["id"] == announcement_id


@pytest.mark.asyncio
async def test_reconnected_client_receives_events_posted_after_the_reconnect(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A client that reconnects to the run receives the events posted after the reconnect."""
    env = await setup_journey_environment(db_session, client, org_slug="wj4-reconnect")
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Reconnect Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    token = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        await await_ws_subscribed(ws)

        resp1 = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {"de": "Erste Nachricht", "en": "First message"},
            },
            headers=env.auth_headers["coordinator"],
        )
        assert resp1.status_code == 201

        await asyncio.sleep(0.1)

        msg1 = await ws.receive_json(timeout=5.0)
        assert msg1["type"] == "feed_event"
        assert msg1["data"]["content"]["de"] == "Erste Nachricht"

        await ws.close()

    # Give the server time to clean up the closed socket.
    await asyncio.sleep(0.2)

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        await await_ws_subscribed(ws)

        resp2 = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {"de": "Zweite Nachricht", "en": "Second message"},
            },
            headers=env.auth_headers["coordinator"],
        )
        assert resp2.status_code == 201

        await asyncio.sleep(0.1)

        msg2 = await ws.receive_json(timeout=5.0)
        assert msg2["type"] == "feed_event"
        assert msg2["data"]["content"]["de"] == "Zweite Nachricht"


@pytest.mark.asyncio
async def test_paused_run_socket_receives_announcements(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """The feed is not blocked while the run is paused: an announcement still reaches the run socket."""
    env = await setup_journey_environment(db_session, client, org_slug="wj4-paused-announcements")
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Paused Announcements Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    token = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        await await_ws_subscribed(ws)

        pause_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/pause",
            headers=env.auth_headers["coordinator"],
        )
        assert pause_resp.status_code == 200

        await asyncio.sleep(0.1)

        pause_msg = await ws.receive_json(timeout=5.0)
        assert pause_msg["type"] == "run_state_change"
        assert pause_msg["data"]["new_state"] == "paused"

        announcement_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {
                    "de": "Pause verlängert um 30 Minuten",
                    "en": "Break extended by 30 minutes",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201
        announcement_id = announcement_resp.json()["id"]

        await asyncio.sleep(0.1)

        announcement_msg = await ws.receive_json(timeout=5.0)
        assert announcement_msg["type"] == "feed_event"
        assert announcement_msg["data"]["id"] == announcement_id
        assert announcement_msg["data"]["event_type"] == "announcement"
        assert announcement_msg["data"]["content"]["de"] == "Pause verlängert um 30 Minuten"


@pytest.mark.asyncio
async def test_ended_run_rejects_feed_events_and_its_socket_stays_quiet(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """An ended run rejects new feed events with 422, and its socket receives no further broadcast."""
    env = await setup_journey_environment(db_session, client, org_slug="wj4-ended-stable")
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Ended Run Stable WS",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    token = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        await await_ws_subscribed(ws)

        # Post announcement while active (should broadcast)
        active_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {"de": "Aktiv", "en": "Active"},
            },
            headers=env.auth_headers["coordinator"],
        )
        assert active_resp.status_code == 201

        await asyncio.sleep(0.1)

        active_msg = await ws.receive_json(timeout=5.0)
        assert active_msg["type"] == "feed_event"
        assert active_msg["data"]["event_type"] == "announcement"

        end_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/end",
            headers=env.auth_headers["coordinator"],
        )
        assert end_resp.status_code == 200

        await asyncio.sleep(0.1)

        end_msg = await ws.receive_json(timeout=5.0)
        assert end_msg["type"] == "run_state_change"
        assert end_msg["data"]["new_state"] == "ended"

        # Drain the auto-broadcasts of the run end (results_available, feedback_prompt).
        # They belong to the normal end-of-run flow and are not unexpected post-end events.
        post_end_auto_types = {"results_available", "feedback_prompt"}
        for _ in range(len(post_end_auto_types)):
            try:
                auto_msg = await ws.receive_json(timeout=1.0)
            except TimeoutError:
                break
            assert auto_msg["type"] == "feed_event", auto_msg
            assert auto_msg["data"]["event_type"] in post_end_auto_types, auto_msg

        # Posting an announcement to an ended run is rejected
        ended_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {
                    "de": "Nach Ende",
                    "en": "After end",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert ended_resp.status_code == 422
        assert "ended" in ended_resp.json()["detail"].lower()

        await asyncio.sleep(0.2)

        try:
            unexpected_msg = await ws.receive_json(timeout=0.5)
            pytest.fail(f"Received event on ended run (unexpected): {unexpected_msg}")
        except TimeoutError:
            pass  # Expected: no events after run ended


# ============================================================================
# Cross-Tenant WebSocket Isolation
# ============================================================================


@pytest.mark.asyncio
async def test_run_socket_accepts_a_user_of_the_runs_own_organization(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A user of the run's own organization can connect to the run socket. This is the baseline of the rejection test."""
    from tests.journey_helpers import setup_journey_environment

    env = await setup_journey_environment(db_session, client, org_slug="wj5-org-a")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Org A Cross-Tenant Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    token = create_access_token(
        user_id=env.users["participant1"].id,
        role=env.users["participant1"].role.value,
        organization_id=env.users["participant1"].organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        await ws.send_text("ping")
        pong = await ws.receive_text(timeout=5.0)
        assert pong == "pong"


@pytest.mark.asyncio
async def test_run_socket_refuses_a_user_of_another_organization(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A user of another organization is refused at the run socket by the WebSocket authentication."""
    from tests.journey_helpers import setup_journey_environment

    env_a = await setup_journey_environment(db_session, client, org_slug="wj5-org-a-reject")

    env_b = await setup_journey_environment(db_session, client, org_slug="wj5-org-b-reject")

    create_resp = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        json={
            "name": "Org A Cross-Tenant Test Run",
            "rallye_id": str(env_a.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env_a.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_id}/start",
        headers=env_a.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    org_b_token = create_access_token(
        user_id=env_b.users["participant1"].id,
        role=env_b.users["participant1"].role.value,
        organization_id=env_b.users["participant1"].organization_id,
    )

    try:
        async with aconnect_ws(f"/api/orgs/{env_a.slug}/runs/{run_id}/ws?token={org_b_token}", ws_client) as _:
            pytest.fail("Org B user connected to Org A run (cross-tenant access violation)")
    except Exception:
        # httpx_ws raises on a non-101 response or an immediate close.
        # The exact exception type depends on the implementation, so any exception counts as a refusal.
        # ws_authenticate closes with WS_1008_POLICY_VIOLATION.
        pass


@pytest.mark.asyncio
async def test_announcement_does_not_reach_the_subscriber_of_another_organization(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """An announcement of one organization's run reaches that run's subscriber and not the subscriber of another organization's run."""
    from tests.journey_helpers import setup_journey_environment

    env_a = await setup_journey_environment(db_session, client, org_slug="wj5-org-a-leakage")

    create_a_resp = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        json={
            "name": "Org A Run",
            "rallye_id": str(env_a.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env_a.auth_headers["coordinator"],
    )
    assert create_a_resp.status_code == 201
    run_a_id = UUID(create_a_resp.json()["id"])

    activate_a_resp = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/start",
        headers=env_a.auth_headers["coordinator"],
    )
    assert activate_a_resp.status_code == 200

    env_b = await setup_journey_environment(db_session, client, org_slug="wj5-org-b-leakage")

    create_b_resp = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        json={
            "name": "Org B Run",
            "rallye_id": str(env_b.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env_b.auth_headers["coordinator"],
    )
    assert create_b_resp.status_code == 201
    run_b_id = UUID(create_b_resp.json()["id"])

    activate_b_resp = await client.post(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/start",
        headers=env_b.auth_headers["coordinator"],
    )
    assert activate_b_resp.status_code == 200

    token_a = create_access_token(
        user_id=env_a.users["participant1"].id,
        role=env_a.users["participant1"].role.value,
        organization_id=env_a.users["participant1"].organization_id,
    )

    token_b = create_access_token(
        user_id=env_b.users["participant1"].id,
        role=env_b.users["participant1"].role.value,
        organization_id=env_b.users["participant1"].organization_id,
    )

    async with (
        aconnect_ws(f"/api/orgs/{env_a.slug}/runs/{run_a_id}/ws?token={token_a}", ws_client) as ws_a,
        aconnect_ws(f"/api/orgs/{env_b.slug}/runs/{run_b_id}/ws?token={token_b}", ws_client) as ws_b,
    ):
        # Wait until the server has subscribed both sockets to their channels,
        # so the silence on ws_b below means real isolation, not a missed subscription
        await await_ws_subscribed(ws_a)
        await await_ws_subscribed(ws_b)

        announcement_resp = await client.post(
            f"/api/orgs/{env_a.slug}/runs/{run_a_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {
                    "de": "Org A Ankündigung",
                    "en": "Org A Announcement",
                },
            },
            headers=env_a.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201
        announcement_id = announcement_resp.json()["id"]

        # Give NOTIFY time to propagate
        await asyncio.sleep(0.1)

        msg_a = await ws_a.receive_json(timeout=5.0)
        assert msg_a["type"] == "feed_event"
        assert msg_a["data"]["id"] == announcement_id
        assert msg_a["data"]["event_type"] == "announcement"
        assert msg_a["data"]["content"]["de"] == "Org A Ankündigung"

        # Org B subscriber should NOT receive the announcement (different run_id channel)
        try:
            unexpected_b = await ws_b.receive_json(timeout=0.5)
            pytest.fail(f"Org B received Org A announcement (data leakage!): {unexpected_b}")
        except TimeoutError:
            pass  # Expected: no cross-tenant data leakage


# ============================================================================
# An event for one team reaches that team and the staff, nobody else
# ============================================================================


@pytest.mark.asyncio
async def test_targeted_feed_event_reaches_only_its_team_and_the_staff(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """
    An alarm for Team Alpha, and Team Alpha's help request, are pushed to Team
    Alpha's member and to the coordinator. Team Bravo's member gets neither,
    and still gets the broadcast that follows.
    """
    from app.models.user import UserRole
    from tests.factories import create_test_user, get_auth_headers

    env = await setup_journey_environment(db_session, client, org_slug="f106-targeted-feed")
    coordinator = env.users["coordinator"]
    alpha = env.users["participant1"]
    bravo = await create_test_user(db_session, email="bravo@f106.test", role=UserRole.PARTICIPANT, organization=env.org)
    await db_session.commit()
    bravo_headers = get_auth_headers(bravo)

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={"name": "Targeted feed run", "rallye_id": str(env.rallye_id)},
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]
    start_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/start", headers=env.auth_headers["coordinator"])
    assert start_resp.status_code == 200

    team_ids = {}
    for name, headers in (("Team Alpha", env.auth_headers["participant1"]), ("Team Bravo", bravo_headers)):
        team_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/", json={"name": name}, headers=headers
        )
        assert team_resp.status_code == 201
        team_ids[name] = team_resp.json()["id"]
        await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_ids[name]}/join", headers=headers)

    def token_of(user) -> str:
        return create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    url = f"/api/orgs/{env.slug}/runs/{run_id}/ws?token="
    async with (
        aconnect_ws(url + token_of(coordinator), ws_client) as staff_ws,
        aconnect_ws(url + token_of(alpha), ws_client) as alpha_ws,
        aconnect_ws(url + token_of(bravo), ws_client) as bravo_ws,
    ):
        for ws in (staff_ws, alpha_ws, bravo_ws):
            await await_ws_subscribed(ws)

        # An alarm for Team Alpha only
        alarm_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "alert",
                "content": {"text": "Alarm nur für Team Alpha"},
                "target_team_id": team_ids["Team Alpha"],
            },
            headers=env.auth_headers["coordinator"],
        )
        assert alarm_resp.status_code == 201
        alarm_id = alarm_resp.json()["id"]

        # Team Alpha asks for help: a system line about Team Alpha
        help_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_ids['Team Alpha']}/chat/request-coordinator",
            headers=env.auth_headers["participant1"],
        )
        assert help_resp.status_code == 200

        # A broadcast for everyone
        broadcast_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={"event_type": "announcement", "content": {"text": "An alle"}},
            headers=env.auth_headers["coordinator"],
        )
        assert broadcast_resp.status_code == 201
        broadcast_id = broadcast_resp.json()["id"]

        async def feed_events_until_broadcast(ws) -> list[dict]:
            seen = []
            while True:
                message = await ws.receive_json(timeout=5.0)
                if message["type"] != "feed_event":
                    continue
                seen.append(message["data"])
                if message["data"]["id"] == broadcast_id:
                    return seen

        for ws in (staff_ws, alpha_ws):
            events = await feed_events_until_broadcast(ws)
            assert [e["id"] for e in events][0] == alarm_id
            assert [e["event_type"] for e in events] == ["alert", "system", "announcement"]
            assert events[0]["target_team_id"] == team_ids["Team Alpha"]
            assert events[1]["target_team_id"] == team_ids["Team Alpha"]
            assert events[1]["content"]["message_key"] == "help_request"

        # Team Bravo's first feed event is the broadcast: the two before it never came.
        assert [e["id"] for e in await feed_events_until_broadcast(bravo_ws)] == [broadcast_id]


# ============================================================================
# A team dissolves while its run is open in other browsers
# ============================================================================


@pytest.mark.asyncio
async def test_last_member_leaves_and_the_open_pages_follow(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """
    The last member leaving dissolves the team, and the pages that are open follow.

    The open chat socket closes with a code the app does not reconnect on. The coordinator and the
    other team get one `team_dissolved` with the run and the team by id and without a team name.
    The scoreboard and the team list no longer hold the team, and a new chat connection is refused.
    """
    import json

    from httpx_ws import WebSocketDisconnect

    env = await setup_journey_environment(db_session, client, org_slug="wj6-team-dissolved")
    coordinator, alpha, bravo = env.users["coordinator"], env.users["participant1"], env.users["participant2"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={"name": "Dissolution journey run", "rallye_id": str(env.rallye_id)},
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]
    start_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/start", headers=env.auth_headers["coordinator"])
    assert start_resp.status_code == 200

    team_ids = {}
    for name, role in (("Team Alpha", "participant1"), ("Team Bravo", "participant2")):
        team_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/", json={"name": name}, headers=env.auth_headers[role]
        )
        assert team_resp.status_code == 201
        team_ids[name] = team_resp.json()["id"]
        join_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_ids[name]}/join", headers=env.auth_headers[role]
        )
        assert join_resp.status_code == 204
    alpha_id = team_ids["Team Alpha"]

    def token_of(user) -> str:
        return create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    run_url = f"/api/orgs/{env.slug}/runs/{run_id}/ws?token="
    chat_url = f"/api/orgs/{env.slug}/runs/{run_id}/teams/{alpha_id}/ws?token={token_of(alpha)}"
    async with (
        aconnect_ws(run_url + token_of(coordinator), ws_client) as staff_ws,
        aconnect_ws(run_url + token_of(bravo), ws_client) as bravo_ws,
        aconnect_ws(chat_url, ws_client) as alpha_chat_ws,
    ):
        for ws in (staff_ws, bravo_ws, alpha_chat_ws):
            await await_ws_subscribed(ws)

        leave_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{alpha_id}/leave",
            headers=env.auth_headers["participant1"],
            json={"confirm_dissolution": True},
        )
        assert leave_resp.status_code == 204

        # The chat tab is closed for good
        with pytest.raises(WebSocketDisconnect) as closed:
            await alpha_chat_ws.receive_text(timeout=5.0)
        assert closed.value.code == 4003

        # One event for everyone else on the run, the team by id only
        for ws in (staff_ws, bravo_ws):
            await ws.send_text("ping")
            events = []
            while (text := await ws.receive_text(timeout=5.0)) != "pong":
                events.append(json.loads(text))
            dissolved = [event for event in events if event["type"] == "team_dissolved"]
            assert len(dissolved) == 1
            assert dissolved[0]["data"] == {"run_id": run_id, "team_id": alpha_id}
            assert "Team Alpha" not in json.dumps(dissolved[0])

    # What the open pages load on the event no longer holds the team
    scoreboard_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard", headers=env.auth_headers["coordinator"]
    )
    assert scoreboard_resp.status_code == 200
    assert alpha_id not in json.dumps(scoreboard_resp.json())
    teams_resp = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/teams/", headers=env.auth_headers["coordinator"])
    assert [team["name"] for team in teams_resp.json()["items"]] == ["Team Bravo"]

    # A reconnect to the chat of the dissolved team is refused
    with pytest.raises(WebSocketDisconnect):
        async with aconnect_ws(chat_url, ws_client) as refused_ws:
            await refused_ws.receive_text(timeout=5.0)
