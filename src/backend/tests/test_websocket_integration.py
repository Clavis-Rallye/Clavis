"""
WebSocket integration tests.

- Connection: authentication, authorization and the close codes of a refusal
- Broadcasts of feed events, chat messages and run state changes
- Isolation between organizations, teams and runs
- Sockets close when the password of their account changes
- Admission to the run's socket without a team
"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from app.models.user import UserRole
from tests.conftest import await_ws_subscribed
from tests.factories import create_test_user, create_test_user_with_org
from tests.journey_helpers import JourneyEnv, create_team_and_join, leave_as_last_member, setup_journey_environment

# ============================================================================
# Connection Tests
# ============================================================================


@pytest.mark.asyncio
async def test_ws_run_feed_valid_token_connects(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A participant with a valid token connects to the run feed and gets a pong for a ping."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-valid-token")

    participant = env.users["participant1"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "WebSocket Test Run",
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
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        await ws.send_text("ping")
        response = await ws.receive_text()
        assert response == "pong"


@pytest.mark.asyncio
async def test_ws_run_feed_missing_token_rejected(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A connection without a token is closed with 1008 and "Missing authentication token"."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-missing-token")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "WebSocket Test Run",
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

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert "Missing authentication token" in exc_info.value.reason


@pytest.mark.asyncio
async def test_ws_run_feed_expired_token_rejected(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A connection with an expired token is closed with 1008 and "Invalid token"."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-expired-token")

    participant = env.users["participant1"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "WebSocket Test Run",
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

    # Create an expired token (expires_delta = -1 minute = already expired)
    expired_token = create_access_token(
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
        expires_delta=timedelta(minutes=-1),
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={expired_token}", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert "Invalid token" in exc_info.value.reason


@pytest.mark.asyncio
async def test_ws_run_feed_wrong_org_rejected(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A user of another organization is closed with 1008 and "Access denied"."""
    env_a = await setup_journey_environment(db_session, client, org_slug="ws-org-a")
    user_b, org_b = await create_test_user_with_org(
        db_session,
        email="user@org-b.test",
        role=UserRole.PARTICIPANT,
        org_slug="ws-org-b",
    )

    create_resp = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        json={
            "name": "Org A Run",
            "rallye_id": str(env_a.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env_a.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id_a = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_id_a}/start",
        headers=env_a.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    token_b = create_access_token(
        user_id=user_b.id,
        role=user_b.role.value,
        organization_id=user_b.organization_id,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env_a.slug}/runs/{run_id_a}/ws?token={token_b}", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert "Access denied" in exc_info.value.reason


@pytest.mark.asyncio
async def test_ws_run_feed_nonexistent_run_rejected(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A connection to a run that does not exist is closed with 1008 and "Run not found"."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-nonexistent-run")
    participant = env.users["participant1"]

    token = create_access_token(
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
    )

    fake_run_id = uuid4()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{fake_run_id}/ws?token={token}", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert "Run not found" in exc_info.value.reason


@pytest.mark.asyncio
async def test_ws_run_feed_inactive_user_rejected(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """An inactive user is closed with 1008 and "User account disabled"."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-inactive-user")

    inactive_user = await create_test_user(
        db_session,
        email="inactive@ws-inactive-user.test",
        role=UserRole.PARTICIPANT,
        organization=env.org,
        is_active=False,
    )

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "WebSocket Test Run",
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
        user_id=inactive_user.id,
        role=inactive_user.role.value,
        organization_id=inactive_user.organization_id,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert "User account disabled" in exc_info.value.reason


@pytest.mark.asyncio
async def test_ws_team_chat_non_member_rejected(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A participant who is not a member of the team is refused the team chat with 1008."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-team-non-member")
    participant2 = env.users["participant2"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Team Chat Test Run",
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
            "name": "Team Alpha",
            "color": "#FF0000",
        },
        headers=env.auth_headers["participant1"],
    )
    assert team_resp.status_code == 201
    team_id = UUID(team_resp.json()["id"])

    join_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join_resp.status_code == 204

    # Create token for participant2 (NOT a member of the team)
    token_p2 = create_access_token(
        user_id=participant2.id,
        role=participant2.role.value,
        organization_id=participant2.organization_id,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={token_p2}", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert "team member" in exc_info.value.reason


@pytest.mark.asyncio
async def test_ws_team_chat_coordinator_accepted(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A coordinator is refused the team chat with 4003 until the team requests assistance, and accepted after it."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-team-coordinator")
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Team Chat Test Run",
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
            "name": "Team Beta",
            "color": "#00FF00",
        },
        headers=env.auth_headers["participant1"],
    )
    assert team_resp.status_code == 201
    team_id = UUID(team_resp.json()["id"])

    join_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join_resp.status_code == 204

    # Create token for coordinator (NOT a member, but has coordinator+ role)
    token_coord = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    # Without an assistance request the coordinator is rejected. The window
    # is closed (never opened), so the close code is the non-recoverable 4003
    # and not the generic 1008, because reconnecting would not help.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={token_coord}", ws_client):
            pass
    assert exc_info.value.code == 4003

    # Team requests coordinator assistance, so the assist window opens
    request_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=env.auth_headers["participant1"],
    )
    assert request_resp.status_code == 200

    async with aconnect_ws(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={token_coord}", ws_client
    ) as ws:
        await ws.send_text("ping")
        response = await ws.receive_text()
        assert response == "pong"


@pytest.mark.asyncio
async def test_ws_team_chat_coordinator_reconnect_after_leave_gets_non_recoverable_code(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A coordinator socket is refused with the non-recoverable 4003, not 1008, after leave-coordinator closed a window that was open."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-team-coord-reconnect-closed")
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Reconnect Closed Window Test Run",
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
        json={"name": "Team Reconnect", "color": "#0000FF"},
        headers=env.auth_headers["participant1"],
    )
    assert team_resp.status_code == 201
    team_id = UUID(team_resp.json()["id"])

    join_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join_resp.status_code == 204

    request_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=env.auth_headers["participant1"],
    )
    assert request_resp.status_code == 200

    token_coord = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    # Window open: coordinator connects fine.
    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={token_coord}", ws_client):
        pass

    leave_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/leave-coordinator",
        headers=env.auth_headers["coordinator"],
    )
    assert leave_resp.status_code == 200

    # Window closed again: a fresh connection is rejected with 4003.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={token_coord}", ws_client):
            pass
    assert exc_info.value.code == 4003


# ============================================================================
# Broadcast Tests
# ============================================================================


@pytest.mark.asyncio
async def test_ws_feed_announcement_broadcast(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A feed announcement created over HTTP is broadcast to the run's WebSocket subscribers."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-feed-broadcast")
    participant = env.users["participant1"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Feed Broadcast Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    # Connect to WebSocket BEFORE activating run
    token = create_access_token(
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        # Wait until the server has subscribed this socket to the run channels
        await await_ws_subscribed(ws)

        activate_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/start",
            headers=env.auth_headers["coordinator"],
        )
        assert activate_resp.status_code == 200

        state_msg = await ws.receive_json(timeout=2.0)
        assert state_msg["type"] == "run_state_change"
        assert state_msg["data"]["new_state"] == "active"

        announcement_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {
                    "de": "Wichtige Nachricht!",
                    "en": "Important message!",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201
        announcement_data = announcement_resp.json()

        message = await ws.receive_json(timeout=2.0)
        assert message is not None, "Did not receive feed_event message"

        assert message["type"] == "feed_event"
        assert "data" in message
        assert "timestamp" in message

        data = message["data"]
        assert data["id"] == announcement_data["id"]
        assert data["run_id"] == str(run_id)
        assert data["event_type"] == "announcement"
        assert data["content"]["de"] == "Wichtige Nachricht!"
        assert data["content"]["en"] == "Important message!"
        assert data["author_id"] is not None


@pytest.mark.asyncio
async def test_ws_feed_countdown_broadcast(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A countdown feed event is broadcast with its target_time in the content."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-countdown-broadcast")
    participant = env.users["participant1"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Countdown Broadcast Test Run",
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
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        # Wait until the server has subscribed this socket to the run channels
        await await_ws_subscribed(ws)

        countdown_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "countdown",
                "content": {
                    "target_time": "2026-07-15T15:00:00Z",
                    "de": "Pause endet",
                    "en": "Break ends",
                },
            },
            headers=env.auth_headers["coordinator"],
        )
        assert countdown_resp.status_code == 201
        countdown_data = countdown_resp.json()

        message = await ws.receive_json(timeout=5.0)

        assert message["type"] == "feed_event"
        assert "data" in message
        assert "timestamp" in message

        data = message["data"]
        assert data["id"] == countdown_data["id"]
        assert data["event_type"] == "countdown"
        assert data["content"]["target_time"] == "2026-07-15T15:00:00Z"
        assert data["content"]["de"] == "Pause endet"
        assert data["content"]["en"] == "Break ends"


@pytest.mark.asyncio
async def test_ws_chat_message_broadcast(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A chat message sent over HTTP is broadcast to the other team member's WebSocket."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-chat-broadcast")
    participant1 = env.users["participant1"]
    participant2 = env.users["participant2"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Chat Broadcast Test Run",
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
            "name": "Chat Team",
            "color": "#FF0000",
        },
        headers=env.auth_headers["participant1"],
    )
    assert team_resp.status_code == 201
    team_id = UUID(team_resp.json()["id"])

    join_resp1 = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join_resp1.status_code == 204

    join_resp2 = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant2"],
    )
    assert join_resp2.status_code == 204

    token_p2 = create_access_token(
        user_id=participant2.id,
        role=participant2.role.value,
        organization_id=participant2.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={token_p2}", ws_client) as ws:
        # Wait until the server has subscribed this socket to the team channel
        await await_ws_subscribed(ws)

        chat_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
            json={
                "content": "Hello team!",
            },
            headers=env.auth_headers["participant1"],
        )
        assert chat_resp.status_code == 201
        chat_data = chat_resp.json()

        message = await ws.receive_json(timeout=5.0)

        assert message["type"] == "chat_message"
        assert "data" in message
        assert "timestamp" in message

        data = message["data"]
        assert data["id"] == chat_data["id"]
        assert data["team_id"] == str(team_id)
        assert data["content"] == "Hello team!"
        assert data["author_id"] == str(participant1.id)
        assert data["is_system_message"] is False


@pytest.mark.asyncio
async def test_ws_run_state_activate_broadcast(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """Starting a run broadcasts run_state_change with the new state "active"."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-activate-broadcast")
    participant = env.users["participant1"]

    # Create a run (but DON'T start it yet)
    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Activate Broadcast Test Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_id = UUID(create_resp.json()["id"])

    token = create_access_token(
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
    )

    # The participant connects while the run is still configured.
    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        # Wait until the server has subscribed this socket to the run channels
        await await_ws_subscribed(ws)

        activate_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/start",
            headers=env.auth_headers["coordinator"],
        )
        assert activate_resp.status_code == 200

        message = await ws.receive_json(timeout=5.0)

        assert message["type"] == "run_state_change"
        assert "data" in message
        assert "timestamp" in message

        data = message["data"]
        assert data["run_id"] == str(run_id)
        assert data["previous_state"] == "configured"
        assert data["new_state"] == "active"
        assert "changed_at" in data


@pytest.mark.asyncio
async def test_ws_run_state_pause_broadcast(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """Pausing a run broadcasts run_state_change with the new state "paused"."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-pause-broadcast")
    participant = env.users["participant1"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Pause Broadcast Test Run",
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
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        # Wait until the server has subscribed this socket to the run channels
        await await_ws_subscribed(ws)

        pause_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/pause",
            headers=env.auth_headers["coordinator"],
        )
        assert pause_resp.status_code == 200

        message = await ws.receive_json(timeout=5.0)

        assert message["type"] == "run_state_change"
        assert "data" in message
        assert "timestamp" in message

        data = message["data"]
        assert data["run_id"] == str(run_id)
        assert data["previous_state"] == "active"
        assert data["new_state"] == "paused"
        assert "changed_at" in data


@pytest.mark.asyncio
async def test_ws_run_state_end_broadcast(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """Ending a run broadcasts run_state_change with the new state "ended"."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-end-broadcast")
    participant = env.users["participant1"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "End Broadcast Test Run",
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
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        # Wait until the server has subscribed this socket to the run channels
        await await_ws_subscribed(ws)

        end_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/end",
            headers=env.auth_headers["coordinator"],
        )
        assert end_resp.status_code == 200

        message = await ws.receive_json(timeout=5.0)

        assert message["type"] == "run_state_change"
        assert "data" in message
        assert "timestamp" in message

        data = message["data"]
        assert data["run_id"] == str(run_id)
        assert data["previous_state"] == "active"
        assert data["new_state"] == "ended"
        assert "changed_at" in data


# ============================================================================
# Isolation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_ws_chat_team_isolation(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A chat message of Team A does not reach a subscriber of Team B."""
    import asyncio

    env = await setup_journey_environment(db_session, client, org_slug="ws-chat-isolation")
    participant2 = env.users["participant2"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Chat Isolation Test Run",
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

    team_a_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team Alpha", "color": "#FF0000"},
        headers=env.auth_headers["participant1"],
    )
    assert team_a_resp.status_code == 201
    team_a_id = UUID(team_a_resp.json()["id"])

    join_a_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join_a_resp.status_code == 204

    team_b_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team Beta", "color": "#00FF00"},
        headers=env.auth_headers["participant2"],
    )
    assert team_b_resp.status_code == 201
    team_b_id = UUID(team_b_resp.json()["id"])

    join_b_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/join",
        headers=env.auth_headers["participant2"],
    )
    assert join_b_resp.status_code == 204

    token_b = create_access_token(
        user_id=participant2.id,
        role=participant2.role.value,
        organization_id=participant2.organization_id,
    )

    async with aconnect_ws(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/ws?token={token_b}", ws_client
    ) as ws_team_b:
        # Wait until the server has subscribed this socket to Team B's channel,
        # so the silence below means real isolation, not a missed subscription
        await await_ws_subscribed(ws_team_b)

        chat_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/chat/",
            json={"content": "Message for Team A only"},
            headers=env.auth_headers["participant1"],
        )
        assert chat_resp.status_code == 201

        # Team B subscriber should NOT receive anything
        # Wait a bit to ensure message doesn't arrive
        with pytest.raises(asyncio.TimeoutError):
            await ws_team_b.receive_json(timeout=1.0)


@pytest.mark.asyncio
async def test_ws_feed_run_isolation(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A feed announcement of one active run does not reach a subscriber of another active run."""
    import asyncio

    env = await setup_journey_environment(db_session, client, org_slug="ws-feed-isolation")
    participant = env.users["participant1"]

    create_resp_1 = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Run 1",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp_1.status_code == 201
    run_1_id = UUID(create_resp_1.json()["id"])

    activate_resp_1 = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_1_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp_1.status_code == 200

    create_resp_2 = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Run 2",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T11:00:00Z",
            "end_time": "2026-07-15T17:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert create_resp_2.status_code == 201
    run_2_id = UUID(create_resp_2.json()["id"])

    activate_resp_2 = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_2_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert activate_resp_2.status_code == 200

    token = create_access_token(
        user_id=participant.id,
        role=participant.role.value,
        organization_id=participant.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_2_id}/ws?token={token}", ws_client) as ws_run_2:
        # Wait until the server has subscribed this socket to Run 2's channels,
        # so the silence below means real isolation, not a missed subscription
        await await_ws_subscribed(ws_run_2)

        announcement_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_1_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {"de": "Nachricht für Run 1", "en": "Message for Run 1"},
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201

        # Run 2 subscriber should NOT receive anything from Run 1
        with pytest.raises(asyncio.TimeoutError):
            await ws_run_2.receive_json(timeout=1.0)


@pytest.mark.asyncio
async def test_ws_cross_tenant_rejected(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A user of another organization is closed with 1008 and "Access denied"."""
    env_a = await setup_journey_environment(db_session, client, org_slug="ws-cross-tenant-a")
    user_b, org_b = await create_test_user_with_org(
        db_session,
        email="user@cross-tenant-b.test",
        role=UserRole.PARTICIPANT,
        org_slug="ws-cross-tenant-b",
    )

    create_resp = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        json={
            "name": "Org A Run",
            "rallye_id": str(env_a.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env_a.auth_headers["coordinator"],
    )
    assert create_resp.status_code == 201
    run_a_id = UUID(create_resp.json()["id"])

    activate_resp = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/start",
        headers=env_a.auth_headers["coordinator"],
    )
    assert activate_resp.status_code == 200

    token_b = create_access_token(
        user_id=user_b.id,
        role=user_b.role.value,
        organization_id=user_b.organization_id,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env_a.slug}/runs/{run_a_id}/ws?token={token_b}", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert "Access denied" in exc_info.value.reason


@pytest.mark.asyncio
async def test_ws_disconnect_no_crash_other_connections(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A subscriber that disconnected does not stop broadcasts to the remaining subscriber."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-disconnect-isolation")
    participant1 = env.users["participant1"]
    participant2 = env.users["participant2"]

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

    token_p1 = create_access_token(
        user_id=participant1.id,
        role=participant1.role.value,
        organization_id=participant1.organization_id,
    )
    token_p2 = create_access_token(
        user_id=participant2.id,
        role=participant2.role.value,
        organization_id=participant2.organization_id,
    )

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token_p1}", ws_client) as ws_p1:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token_p2}", ws_client) as ws_p2:
            # Wait until the server has subscribed both sockets to the run channels
            await await_ws_subscribed(ws_p1)
            await await_ws_subscribed(ws_p2)

            announcement_resp_1 = await client.post(
                f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
                json={
                    "event_type": "announcement",
                    "content": {"de": "Erste Nachricht", "en": "First message"},
                },
                headers=env.auth_headers["coordinator"],
            )
            assert announcement_resp_1.status_code == 201

            msg_p1_1 = await ws_p1.receive_json(timeout=2.0)
            assert msg_p1_1["type"] == "feed_event"
            msg_p2_1 = await ws_p2.receive_json(timeout=2.0)
            assert msg_p2_1["type"] == "feed_event"

        # ws_p2 disconnected (exited context manager)

        # Send another announcement. Only ws_p1 should receive it.
        announcement_resp_2 = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {"de": "Zweite Nachricht", "en": "Second message"},
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp_2.status_code == 201

        msg_p1_2 = await ws_p1.receive_json(timeout=2.0)
        assert msg_p1_2["type"] == "feed_event"
        assert msg_p1_2["data"]["content"]["de"] == "Zweite Nachricht"


@pytest.mark.asyncio
async def test_ws_broadcast_multiple_subscribers_same_channel(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """One broadcast reaches every subscriber of the run's feed channel."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-multi-subscriber")
    participant1 = env.users["participant1"]
    participant2 = env.users["participant2"]
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Multi-Subscriber Test Run",
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

    token_p1 = create_access_token(
        user_id=participant1.id,
        role=participant1.role.value,
        organization_id=participant1.organization_id,
    )
    token_p2 = create_access_token(
        user_id=participant2.id,
        role=participant2.role.value,
        organization_id=participant2.organization_id,
    )
    token_coord = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    async with (
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token_p1}", ws_client) as ws_p1,
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token_p2}", ws_client) as ws_p2,
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token_coord}", ws_client) as ws_coord,
    ):
        # Wait until the server has subscribed all three sockets to the run channels
        await await_ws_subscribed(ws_p1)
        await await_ws_subscribed(ws_p2)
        await await_ws_subscribed(ws_coord)

        announcement_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
            json={
                "event_type": "announcement",
                "content": {"de": "Broadcast an alle", "en": "Broadcast to all"},
            },
            headers=env.auth_headers["coordinator"],
        )
        assert announcement_resp.status_code == 201
        announcement_id = announcement_resp.json()["id"]

        msg_p1 = await ws_p1.receive_json(timeout=2.0)
        msg_p2 = await ws_p2.receive_json(timeout=2.0)
        msg_coord = await ws_coord.receive_json(timeout=2.0)

        assert msg_p1["type"] == "feed_event"
        assert msg_p1["data"]["id"] == announcement_id
        assert msg_p2["type"] == "feed_event"
        assert msg_p2["data"]["id"] == announcement_id
        assert msg_coord["type"] == "feed_event"
        assert msg_coord["data"]["id"] == announcement_id


@pytest.mark.asyncio
async def test_ws_team_chat_coordinator_isolation(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A coordinator with an open assist window receives the chat of Team A but not of Team B."""
    import asyncio

    env = await setup_journey_environment(db_session, client, org_slug="ws-coord-chat-isolation")
    coordinator = env.users["coordinator"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Coordinator Chat Isolation Run",
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

    team_a_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team Alpha", "color": "#FF0000"},
        headers=env.auth_headers["participant1"],
    )
    assert team_a_resp.status_code == 201
    team_a_id = UUID(team_a_resp.json()["id"])

    join_a_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/join",
        headers=env.auth_headers["participant1"],
    )
    assert join_a_resp.status_code == 204

    team_b_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        json={"name": "Team Beta", "color": "#00FF00"},
        headers=env.auth_headers["participant2"],
    )
    assert team_b_resp.status_code == 201
    team_b_id = UUID(team_b_resp.json()["id"])

    join_b_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/join",
        headers=env.auth_headers["participant2"],
    )
    assert join_b_resp.status_code == 204

    # Team A requests coordinator assistance. This opens the assist window so the
    # coordinator may subscribe to Team A's chat.
    request_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/chat/request-coordinator",
        headers=env.auth_headers["participant1"],
    )
    assert request_resp.status_code == 200

    token_coord = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role.value,
        organization_id=coordinator.organization_id,
    )

    async with aconnect_ws(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/ws?token={token_coord}", ws_client
    ) as ws_coord_team_a:
        # Wait until the server has subscribed this socket to Team A's channel
        await await_ws_subscribed(ws_coord_team_a)

        chat_a_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_a_id}/chat/",
            json={"content": "Message to Team A"},
            headers=env.auth_headers["participant1"],
        )
        assert chat_a_resp.status_code == 201

        msg_a = await ws_coord_team_a.receive_json(timeout=2.0)
        assert msg_a["type"] == "chat_message"
        assert msg_a["data"]["content"] == "Message to Team A"

        # Send chat to Team B — coordinator should NOT receive (subscribed to Team A only)
        chat_b_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_b_id}/chat/",
            json={"content": "Message to Team B"},
            headers=env.auth_headers["participant2"],
        )
        assert chat_b_resp.status_code == 201

        with pytest.raises(asyncio.TimeoutError):
            await ws_coord_team_a.receive_json(timeout=1.0)


# ============================================================================
# Session Revocation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_ws_password_change_closes_sockets_of_that_account_only(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    """A password change closes the sockets of that account with 4000 "session revoked" and leaves the socket of another account open."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-password-change")
    participant1 = env.users["participant1"]
    participant2 = env.users["participant2"]

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Password Change Close Test Run",
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

    token_p1 = create_access_token(
        user_id=participant1.id,
        role=participant1.role.value,
        organization_id=participant1.organization_id,
    )
    token_p2 = create_access_token(
        user_id=participant2.id,
        role=participant2.role.value,
        organization_id=participant2.organization_id,
    )

    async with (
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token_p1}", ws_client) as ws_p1,
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token_p2}", ws_client) as ws_p2,
    ):
        # Wait until the server has subscribed both sockets to their channels
        await await_ws_subscribed(ws_p1)
        await await_ws_subscribed(ws_p2)

        # Participant1 changes their password (journey_helpers creates the
        # account with this password)
        change_resp = await client.post(
            "/api/auth/password-change",
            json={"old_password": "ParticipantPass123!", "new_password": "NewPassword456!"},
            headers=env.auth_headers["participant1"],
        )
        assert change_resp.status_code == 200

        with pytest.raises(WebSocketDisconnect) as p1_exc_info:
            await ws_p1.receive_text(timeout=5.0)
        assert p1_exc_info.value.code == 4000
        assert p1_exc_info.value.reason == "session revoked"

        # Participant2's socket, an account whose password did not change,
        # stays open
        await ws_p2.send_text("ping")
        assert await ws_p2.receive_text(timeout=5.0) == "pong"


# ============================================================================
# Admission to the run's socket without a team
#
# The rule (auth/ws_dependencies.py::ws_refusal): a registered participant
# without a team in the run is admitted and gets the broadcasts only, and a
# guest without a team in the run is not admitted.
# ============================================================================


async def _started_run(client: AsyncClient, env: JourneyEnv) -> tuple[UUID, str]:
    """An active run: its id and its join code."""
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={"name": "Started Run", "rallye_id": str(env.rallye_id)},
        headers=env.auth_headers["coordinator"],
    )
    assert created.status_code == 201, created.text
    run_id = UUID(created.json()["id"])
    started = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/start", headers=env.auth_headers["coordinator"])
    assert started.status_code == 200, started.text
    return run_id, created.json()["join_code"]


async def _guest_with_new_team(client: AsyncClient, join_code: str) -> tuple[str, str]:
    """A guest joins with a new team: the team's id and the guest's token."""
    joined = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Gaeste"},
    )
    assert joined.status_code == 201, joined.text
    # The join answers with the session cookie; the tests authenticate by header
    client.cookies.clear()
    return joined.json()["team_id"], joined.json()["token"]


@pytest.mark.asyncio
async def test_ws_run_feed_guest_who_left_the_team_is_not_admitted(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-guest-left")
    run_id, join_code = await _started_run(client, env)
    team_id, guest_token = await _guest_with_new_team(client, join_code)
    url = f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={guest_token}"

    # As a member of a team in the run the guest is admitted
    async with aconnect_ws(url, ws_client) as ws:
        await await_ws_subscribed(ws)

    left = await leave_as_last_member(client, env.slug, run_id, team_id, {"Authorization": f"Bearer {guest_token}"})
    assert left.status_code == 204, left.text

    # 4003 is non-recoverable for the client: a guest who left may not rejoin
    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(url, ws_client):
            pass
    assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_run_feed_guest_is_not_admitted_to_a_run_it_has_no_team_in(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-guest-other-run")
    _own_run_id, join_code = await _started_run(client, env)
    other_run_id, _ = await _started_run(client, env)
    _team_id, guest_token = await _guest_with_new_team(client, join_code)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{other_run_id}/ws?token={guest_token}", ws_client):
            pass
    assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_run_feed_registered_former_member_gets_broadcasts_and_nothing_of_the_team(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-former-member")
    run_id, _ = await _started_run(client, env)
    former, stayer = env.users["participant1"], env.users["participant2"]
    team = await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant1"], "Alpha", "#FF0000")
    joined = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}/join", headers=env.auth_headers["participant2"]
    )
    assert joined.status_code == 204, joined.text
    left = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}/leave", headers=env.auth_headers["participant1"]
    )
    assert left.status_code == 204, left.text

    def url(user) -> str:  # noqa: ANN001
        token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)
        return f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}"

    async def post(text: str, team_id: str | None) -> None:
        body: dict[str, object] = {"event_type": "announcement", "content": {"de": text}}
        if team_id is not None:
            body["target_team_id"] = team_id
        posted = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=env.auth_headers["coordinator"], json=body
        )
        assert posted.status_code == 201, posted.text

    # A registered account without a team is admitted to the run's socket
    async with aconnect_ws(url(former), ws_client) as ws_former, aconnect_ws(url(stayer), ws_client) as ws_stayer:
        await await_ws_subscribed(ws_former)
        await await_ws_subscribed(ws_stayer)

        # An event for the team that was theirs reaches the member only; the
        # broadcast after it reaches both. The former member's next feed
        # event is therefore the broadcast.
        await post("Nur fuer Alpha", team["id"])
        targeted = await ws_stayer.receive_json(timeout=5.0)
        assert targeted["data"]["content"]["de"] == "Nur fuer Alpha"

        await post("An alle", None)
        for ws in (ws_former, ws_stayer):
            message = await ws.receive_json(timeout=5.0)
            assert message["type"] == "feed_event"
            assert message["data"]["content"]["de"] == "An alle"
