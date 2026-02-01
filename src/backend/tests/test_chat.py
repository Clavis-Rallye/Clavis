"""Integration tests for the team chat endpoints: listing and sending, access control for
team members and coordinators, pagination, and cross-tenant isolation.
"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers


async def _setup_run(
    client: AsyncClient,
    db_session: AsyncSession,
    org_slug: str,
    coordinator,
    org,
) -> tuple[str, str]:
    """Create a started run with one team and return (run_id, team_id).

    `coordinator` must be a coordinator or above, which creating runs and teams needs.

    """
    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_1 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_1.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    headers = get_auth_headers(coordinator)
    run_response = await client.post(
        f"/api/orgs/{org_slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    start_response = await client.post(f"/api/orgs/{org_slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    team_response = await client.post(
        f"/api/orgs/{org_slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    return run_id, team_id


@pytest.mark.asyncio
async def test_send_chat_message_as_team_member(client: AsyncClient, db_session: AsyncSession):
    """A team member sends a message, and the response names the author with role and display name."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
        json={"content": "Hello team!"},
    )

    assert response.status_code == 201
    data = response.json()

    assert data["content"] == "Hello team!"
    assert data["team_id"] == team_id
    assert data["author_id"] == str(participant.id)
    assert data["is_system_message"] is False
    assert data["author_role"] == "participant"
    # The author's display name comes from the already-loaded current_user, with no extra query.
    assert data["author_display_name"] == participant.display_name
    assert UUID(data["id"])
    assert "created_at" in data


@pytest.mark.asyncio
async def test_send_chat_message_as_coordinator_not_in_team(client: AsyncClient, db_session: AsyncSession):
    """A coordinator who is not a team member can send to the team chat only after the team requested assistance."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    coord_headers = get_auth_headers(coordinator)
    participant_headers = get_auth_headers(participant)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    # Without a request the coordinator has no access
    denied = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=coord_headers,
        json={"content": "Coordinator message"},
    )
    assert denied.status_code == 403

    # Team member joins and requests coordinator assistance
    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204
    request_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=participant_headers,
    )
    assert request_response.status_code == 200

    # Send message as coordinator (not a team member, assist window open)
    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=coord_headers,
        json={"content": "Coordinator message"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["content"] == "Coordinator message"
    assert data["author_id"] == str(coordinator.id)
    # author_role lets frontends render coordinator messages distinctly.
    assert data["author_role"] == "coordinator"


@pytest.mark.asyncio
async def test_send_chat_message_access_denied(client: AsyncClient, db_session: AsyncSession):
    """A participant who is not a team member cannot send to the team chat: 403."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    user1 = await create_test_user(db_session, email="user1@test.com", role=UserRole.PARTICIPANT, organization=org)
    user2 = await create_test_user(db_session, email="user2@test.com", role=UserRole.PARTICIPANT, organization=org)
    user1_headers = get_auth_headers(user1)
    user2_headers = get_auth_headers(user2)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=user1_headers,
    )
    assert join_response.status_code == 204

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=user2_headers,
        json={"content": "Unauthorized message"},
    )

    assert response.status_code == 403
    assert "Access denied" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_chat_messages_as_team_member(client: AsyncClient, db_session: AsyncSession):
    """A team member lists the messages in order, with total, author_role and author_display_name."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    for i in range(3):
        send_response = await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
            headers=participant_headers,
            json={"content": f"Message {i + 1}"},
        )
        assert send_response.status_code == 201

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 3
    assert len(data["items"]) == 3
    assert data["items"][0]["content"] == "Message 1"
    assert data["items"][1]["content"] == "Message 2"
    assert data["items"][2]["content"] == "Message 3"
    assert all(item["author_role"] == "participant" for item in data["items"])
    # author_display_name is resolved from the User row, so the frontend never falls back to a UUID
    # fragment.
    assert all(item["author_display_name"] == participant.display_name for item in data["items"])


@pytest.mark.asyncio
async def test_list_chat_messages_author_display_name_per_author(client: AsyncClient, db_session: AsyncSession):
    """A message carries the display name of its own author, and the system message carries none."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        display_name="Coordinator Carla",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        display_name="Participant Paul",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    coord_headers = get_auth_headers(coordinator)
    participant_headers = get_auth_headers(participant)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)
    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    send_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
        json={"content": "Hilfe bitte"},
    )
    assert send_response.status_code == 201

    # The request opens the assist window and posts a system message
    request_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=participant_headers,
    )
    assert request_response.status_code == 200

    coord_send_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=coord_headers,
        json={"content": "Wir kommen!"},
    )
    assert coord_send_response.status_code == 201

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=coord_headers,
    )
    assert response.status_code == 200
    data = response.json()
    by_content = {item["content"]: item for item in data["items"]}

    assert by_content["Hilfe bitte"]["author_display_name"] == "Participant Paul"
    assert by_content["Wir kommen!"]["author_display_name"] == "Coordinator Carla"
    # System message (coordinator_request marker) has no author -> no name
    system_item = next(item for item in data["items"] if item["is_system_message"])
    assert system_item["author_id"] is None
    assert system_item["author_display_name"] is None


@pytest.mark.asyncio
async def test_list_chat_messages_pagination(client: AsyncClient, db_session: AsyncSession):
    """skip and limit page through the messages, and total counts all of them."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    for i in range(5):
        send_response = await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
            headers=participant_headers,
            json={"content": f"Message {i + 1}"},
        )
        assert send_response.status_code == 201

    # List first 2 messages
    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/?skip=0&limit=2",
        headers=participant_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["items"][0]["content"] == "Message 1"
    assert data["items"][1]["content"] == "Message 2"

    # List next 2 messages
    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/?skip=2&limit=2",
        headers=participant_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["items"][0]["content"] == "Message 3"
    assert data["items"][1]["content"] == "Message 4"


@pytest.mark.asyncio
async def test_list_chat_messages_access_denied(client: AsyncClient, db_session: AsyncSession):
    """A participant who is not a team member cannot list the team's messages: 403."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    user1 = await create_test_user(db_session, email="user1@test.com", role=UserRole.PARTICIPANT, organization=org)
    user2 = await create_test_user(db_session, email="user2@test.com", role=UserRole.PARTICIPANT, organization=org)
    user1_headers = get_auth_headers(user1)
    user2_headers = get_auth_headers(user2)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=user1_headers,
    )
    assert join_response.status_code == 204

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=user2_headers,
    )

    assert response.status_code == 403
    assert "Access denied" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_chat_messages_as_coordinator_not_in_team(client: AsyncClient, db_session: AsyncSession):
    """A non-member coordinator reads a team chat only after the team requested assistance.

    Then the coordinator sees the full earlier history.

    """
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    coord_headers = get_auth_headers(coordinator)
    participant_headers = get_auth_headers(participant)

    # The member joins and writes before any request
    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)
    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204
    send_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
        json={"content": "Pre-request message"},
    )
    assert send_response.status_code == 201

    # Without a request the coordinator has no read access
    denied = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=coord_headers,
    )
    assert denied.status_code == 403

    # Team requests coordinator assistance
    request_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=participant_headers,
    )
    assert request_response.status_code == 200

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=coord_headers,
    )
    assert response.status_code == 200
    data = response.json()
    contents = [item["content"] for item in data["items"]]
    assert "Pre-request message" in contents


@pytest.mark.asyncio
async def test_list_chat_messages_chronological_order(client: AsyncClient, db_session: AsyncSession):
    """Messages are listed oldest first, by created_at ascending."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    for i in range(5):
        send_response = await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
            headers=participant_headers,
            json={"content": f"Message number {i + 1}"},
        )
        assert send_response.status_code == 201

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 5
    assert len(data["items"]) == 5

    assert data["items"][0]["content"] == "Message number 1"
    assert data["items"][1]["content"] == "Message number 2"
    assert data["items"][2]["content"] == "Message number 3"
    assert data["items"][3]["content"] == "Message number 4"
    assert data["items"][4]["content"] == "Message number 5"

    timestamps = [item["created_at"] for item in data["items"]]
    assert timestamps == sorted(timestamps), "Timestamps should be in ascending order (oldest first)"


@pytest.mark.asyncio
async def test_chat_cross_tenant_isolation(client: AsyncClient, db_session: AsyncSession):
    """A coordinator of org B cannot reach the chat of org A's team: 404, not 403."""
    coord_a, org_a = await create_test_user_with_org(
        db_session, email="coord-a@test.com", role=UserRole.COORDINATOR, org_slug="org-a"
    )

    coord_b, org_b = await create_test_user_with_org(
        db_session, email="coord-b@test.com", role=UserRole.COORDINATOR, org_slug="org-b"
    )
    coord_b_headers = get_auth_headers(coord_b)

    # A member seeds the message, because coordinators have no standing chat access
    run_id_a, team_id_a = await _setup_run(client, db_session, org_a.slug, coord_a, org_a)
    member_a = await create_test_user(
        db_session, email="member-a@test.com", role=UserRole.PARTICIPANT, organization=org_a
    )
    member_a_headers = get_auth_headers(member_a)
    join_response = await client.post(
        f"/api/orgs/{org_a.slug}/runs/{run_id_a}/teams/{team_id_a}/join",
        headers=member_a_headers,
    )
    assert join_response.status_code == 204

    send_response = await client.post(
        f"/api/orgs/{org_a.slug}/runs/{run_id_a}/teams/{team_id_a}/chat/",
        headers=member_a_headers,
        json={"content": "Message from org A"},
    )
    assert send_response.status_code == 201

    # The run and team belong to org A, so they do not exist in org B's context
    response = await client.get(
        f"/api/orgs/{org_b.slug}/runs/{run_id_a}/teams/{team_id_a}/chat/",
        headers=coord_b_headers,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_send_chat_message_rejected_when_run_paused(client: AsyncClient, db_session: AsyncSession):
    """Pausing a run halts participant chat: POST chat returns 422 while the run is not ACTIVE."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord-pause-chat@test.com", role=UserRole.COORDINATOR, org_slug="org-pause-chat"
    )
    participant = await create_test_user(
        db_session, email="participant-pause-chat@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)
    coordinator_headers = get_auth_headers(coordinator)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    # Sanity: chat works while the run is ACTIVE
    active_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
        json={"content": "before pause"},
    )
    assert active_response.status_code == 201

    pause_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=coordinator_headers)
    assert pause_response.status_code == 200

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
        json={"content": "while paused"},
    )
    assert response.status_code == 422
    assert "active" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_request_coordinator_rejected_when_run_paused(client: AsyncClient, db_session: AsyncSession):
    """Pausing a run halts coordinator-assistance requests: 422 while the run is not ACTIVE."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord-pause-req@test.com", role=UserRole.COORDINATOR, org_slug="org-pause-req"
    )
    participant = await create_test_user(
        db_session, email="participant-pause-req@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)
    coordinator_headers = get_auth_headers(coordinator)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )
    assert join_response.status_code == 204

    pause_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=coordinator_headers)
    assert pause_response.status_code == 200

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=participant_headers,
    )
    assert response.status_code == 422
    assert "active" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_help_request_feed_line_reaches_only_the_asking_team(client: AsyncClient, db_session: AsyncSession):
    """The help-request feed line reaches only the asking team and the Spielleitung.

    It carries the asking team's target_team_id.
    A member of another team and a participant without a team do not see it.

    """
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord-help@test.com", role=UserRole.COORDINATOR, org_slug="help-target-org"
    )
    coord_headers = get_auth_headers(coordinator)
    asker = await create_test_user(db_session, email="asker@test.com", role=UserRole.PARTICIPANT, organization=org)
    other = await create_test_user(db_session, email="other@test.com", role=UserRole.PARTICIPANT, organization=org)
    loner = await create_test_user(db_session, email="loner@test.com", role=UserRole.PARTICIPANT, organization=org)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)
    other_team = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=coord_headers, json={"name": "Team Beta"}
    )
    assert other_team.status_code == 201
    other_team_id = other_team.json()["id"]

    for user, team in ((asker, team_id), (other, other_team_id)):
        joined = await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team}/join", headers=get_auth_headers(user)
        )
        assert joined.status_code == 204

    asked = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=get_auth_headers(asker),
    )
    assert asked.status_code == 200

    async def system_lines(user) -> list[dict]:
        response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feed/", headers=get_auth_headers(user))
        assert response.status_code == 200
        return [item for item in response.json()["items"] if item["event_type"] == "system"]

    own = await system_lines(asker)
    assert len(own) == 1
    assert own[0]["target_team_id"] == team_id
    assert own[0]["content"]["message_key"] == "help_request"
    assert len(await system_lines(coordinator)) == 1
    assert await system_lines(other) == []
    assert await system_lines(loner) == []
