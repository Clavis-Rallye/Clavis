"""
Integration tests for coordinator request endpoint.

Tests cover:
- participant (team member) can request coordinator (200)
- system chat message is created
- SYSTEM feed event is created
- no per-team cap: repeated requests all succeed
- non-team-member participant is rejected (403)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.chat_message import ChatMessageRepository
from app.repositories.feed_event import FeedEventRepository
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
    """Create a run (active) with one team. Returns (run_id, team_id)."""
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
async def test_request_coordinator_success(client: AsyncClient, db_session: AsyncSession):
    """A team member participant can request coordinator assistance."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(db_session, email="part@test.com", role=UserRole.PARTICIPANT, organization=org)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    join_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=get_auth_headers(participant),
    )
    assert join_resp.status_code == 204

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=get_auth_headers(participant),
    )

    assert response.status_code == 200
    data = response.json()
    assert "message" in data


@pytest.mark.asyncio
async def test_request_coordinator_creates_chat_message(client: AsyncClient, db_session: AsyncSession):
    """A successful request creates an is_system_message=True chat message."""
    from uuid import UUID

    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(db_session, email="part@test.com", role=UserRole.PARTICIPANT, organization=org)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=get_auth_headers(participant),
    )

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=get_auth_headers(participant),
    )

    await db_session.commit()

    chat_repo = ChatMessageRepository(db_session, organization_id=org.id)
    messages = await chat_repo.list_by_team(UUID(team_id))
    system_msgs = [m for m in messages if m.is_system_message]

    assert len(system_msgs) == 1
    assert "[coordinator_request]" in system_msgs[0].content


@pytest.mark.asyncio
async def test_request_coordinator_creates_feed_event(client: AsyncClient, db_session: AsyncSession):
    """A successful request creates a SYSTEM feed event."""
    from uuid import UUID

    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(db_session, email="part@test.com", role=UserRole.PARTICIPANT, organization=org)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=get_auth_headers(participant),
    )

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=get_auth_headers(participant),
    )

    await db_session.commit()

    feed_repo = FeedEventRepository(db_session, organization_id=org.id)
    events = await feed_repo.list_by_run(UUID(run_id))

    assert len(events) == 1
    assert events[0].event_type.value == "system"


@pytest.mark.asyncio
async def test_request_coordinator_no_cap(client: AsyncClient, db_session: AsyncSession):
    """Coordinator requests are unlimited. Repeated requests all succeed."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    participant = await create_test_user(db_session, email="part@test.com", role=UserRole.PARTICIPANT, organization=org)

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=get_auth_headers(participant),
    )

    url = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator"
    headers = get_auth_headers(participant)

    for _ in range(5):
        r = await client.post(url, headers=headers)
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_request_coordinator_non_member_rejected(client: AsyncClient, db_session: AsyncSession):
    """A participant not in the team receives 403."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    non_member = await create_test_user(
        db_session, email="nobody@test.com", role=UserRole.PARTICIPANT, organization=org
    )

    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=get_auth_headers(non_member),
    )

    assert response.status_code == 403
