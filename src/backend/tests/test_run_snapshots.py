"""
Integration tests for the run snapshot read endpoints.

Covers:
- list run stations (GET /{run_id}/stations/)
- list run challenges (GET /{run_id}/stations/{station_id}/challenges/)
- state validation (snapshots are readable only when the run is active, paused or ended)
- role enforcement (participant and above can read)
- cross-tenant isolation
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
from tests.factories import create_test_user_with_org, get_auth_headers


@pytest.mark.asyncio
async def test_list_run_stations_success(client: AsyncClient, db_session: AsyncSession):
    """A started run lists its stations as frozen copies of the template, in sort order, with all fields."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station1 = await station_repo.create(
        title={"de": "Station 1", "en": "Station 1"},
        description={"de": "First station"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station1 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station1.id, sort_order=0)
    db_session.add(rs_station1)
    await db_session.flush()
    station2 = await station_repo.create(
        title={"de": "Station 2"},
        description={"de": "Second station"},
    )
    rs_station2 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station2.id, sort_order=1)
    db_session.add(rs_station2)
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    # Start run (creates snapshots)
    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert len(data["items"]) == 2

    s1 = data["items"][0]
    assert s1["title"] == {"de": "Station 1", "en": "Station 1"}
    assert s1["description"] == {"de": "First station"}
    assert s1["location_lat"] == 48.3705
    assert s1["location_lng"] == 10.8978
    assert s1["sort_order"] == 0
    assert s1["source_station_id"] == str(station1.id)
    assert s1["run_id"] == run_id
    assert UUID(s1["id"])

    s2 = data["items"][1]
    assert s2["title"] == {"de": "Station 2"}
    assert s2["sort_order"] == 1
    assert s2["source_station_id"] == str(station2.id)


@pytest.mark.asyncio
async def test_list_run_stations_requires_active_state(client: AsyncClient, db_session: AsyncSession):
    """A run in the configured state refuses the station list with 422 and names the states that allow it."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "configured" in detail.lower()
    assert "active, paused, or ended" in detail.lower()


@pytest.mark.asyncio
async def test_list_run_stations_participant_role(client: AsyncClient, db_session: AsyncSession):
    """A participant can list the stations of a run."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    _station_1 = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs__station_1 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=_station_1.id, sort_order=0)
    db_session.add(rs__station_1)
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    from app.repositories.user import UserRepository

    user_repo = UserRepository(db_session, org.id)
    participant = await user_repo.create(
        email="participant@test.com",
        display_name="Participant",
        organization_id=org.id,
        role=UserRole.PARTICIPANT,
        password_hash="dummy",  # Won't be used for auth headers
    )
    await db_session.commit()

    participant_headers = get_auth_headers(participant)

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=participant_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_run_challenges_success(client: AsyncClient, db_session: AsyncSession):
    """A started run lists the challenges of a station as frozen copies of the template, in sort order, with all fields."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

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
    challenge1 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1", "en": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=challenge1.id, sort_order=0
        )
    )
    await db_session.flush()
    challenge2 = await challenge_repo.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Challenge 2"},
        instructions={"de": "Take a photo"},
        scoring_rules={"max_points": 5},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=challenge2.id, sort_order=1
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    # Start run (creates snapshots)
    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert len(data["items"]) == 2

    c1 = data["items"][0]
    assert c1["title"] == {"de": "Challenge 1", "en": "Challenge 1"}
    assert c1["instructions"] == {"de": "Do something"}
    assert c1["challenge_type"] == "text"
    assert c1["scoring_rules"] == {"max_points": 10}
    assert c1["sort_order"] == 0
    assert c1["source_challenge_id"] == str(challenge1.id)
    assert c1["run_station_id"] == run_station_id
    assert UUID(c1["id"])

    c2 = data["items"][1]
    assert c2["challenge_type"] == "photo"
    assert c2["sort_order"] == 1
    assert c2["source_challenge_id"] == str(challenge2.id)


@pytest.mark.asyncio
async def test_list_run_challenges_requires_active_state(client: AsyncClient, db_session: AsyncSession):
    """A run in the configured state refuses the challenge list with 422."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

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
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    # Use station.id as a placeholder (won't matter since run isn't started)
    fake_station_id = str(station.id)

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{fake_station_id}/challenges/",
        headers=headers,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "configured" in detail.lower()


@pytest.mark.asyncio
async def test_list_run_challenges_station_not_found(client: AsyncClient, db_session: AsyncSession):
    """A station id that is not in the run gives 404."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    _station_2 = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs__station_2 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=_station_2.id, sort_order=0)
    db_session.add(rs__station_2)
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    from uuid import uuid4

    fake_station_id = str(uuid4())

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{fake_station_id}/challenges/",
        headers=headers,
    )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "not found" in detail.lower()


@pytest.mark.asyncio
async def test_list_run_stations_cross_tenant_isolation(client: AsyncClient, db_session: AsyncSession):
    """Another organization cannot read the stations of a run. The answer is 404, not 403."""
    user1, org1 = await create_test_user_with_org(
        db_session, email="coord1@test.com", role=UserRole.COORDINATOR, org_slug="org-1"
    )
    user2, org2 = await create_test_user_with_org(
        db_session, email="coord2@test.com", role=UserRole.COORDINATOR, org_slug="org-2"
    )

    headers1 = get_auth_headers(user1)
    headers2 = get_auth_headers(user2)

    rallye_repo = RallyeRepository(db_session, org1.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    station_repo = StationRepository(db_session, org1.id)
    _station_3 = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs__station_3 = RallyeStation(organization_id=org1.id, rallye_id=rallye.id, station_id=_station_3.id, sort_order=0)
    db_session.add(rs__station_3)
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org1.slug}/runs/",
        headers=headers1,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org1.slug}/runs/{run_id}/start", headers=headers1)

    response = await client.get(f"/api/orgs/{org2.slug}/runs/{run_id}/stations/", headers=headers2)

    assert response.status_code == 404
