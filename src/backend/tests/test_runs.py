"""
Integration tests for run CRUD endpoints.

Tests cover:
- Create run (with join_code generation and configured state)
- List runs with pagination
- Get run by ID
- Update run name/settings
- Soft-delete run in configured state
- Cannot delete active run
- Role enforcement (participant cannot create)
- Cross-tenant isolation (404 for cross-org access)
"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run import RunState
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_user_with_org, get_auth_headers


@pytest.mark.asyncio
async def test_create_run_returns_201_with_join_code(client: AsyncClient, db_session: AsyncSession):
    """A new run answers 201 with a six-character upper-case join code, the configured state and no timestamps."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye", "en": "Test Rally"},
        description={"de": "Test", "en": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Test Run Spring 2026",
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert "id" in data
    UUID(data["id"])
    assert data["name"] == "Test Run Spring 2026"
    assert data["organization_id"] == str(org.id)
    assert data["rallye_id"] == str(rallye.id)

    # Verify join_code generated (6 chars, alphanumeric, uppercase letters only — no lowercase)
    assert "join_code" in data
    assert len(data["join_code"]) == 6
    assert data["join_code"].isalnum()
    assert data["join_code"] == data["join_code"].upper()  # No lowercase letters (digits are fine)

    assert data["state"] == RunState.CONFIGURED.value

    assert data["started_at"] is None
    assert data["paused_at"] is None
    assert data["ended_at"] is None
    assert data["scheduled_for"] is None


@pytest.mark.asyncio
async def test_create_run_requires_coordinator_role(client: AsyncClient, db_session: AsyncSession):
    """A participant cannot create a run. The answer is 403."""
    user, org = await create_test_user_with_org(
        db_session,
        email="participant@example.com",
        role=UserRole.PARTICIPANT,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Unauthorized Run",
        },
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "Insufficient permissions" in detail or "Requires coordinator" in detail


@pytest.mark.asyncio
async def test_list_runs_with_pagination(client: AsyncClient, db_session: AsyncSession):
    """The run list honours skip and limit and reports the total."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    run_ids = []
    for i in range(1, 4):
        response = await client.post(
            f"/api/orgs/{org.slug}/runs/",
            headers=headers,
            json={
                "rallye_id": str(rallye.id),
                "name": f"Run {i}",
            },
        )
        assert response.status_code == 201
        run_ids.append(response.json()["id"])

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 3
    assert len(data["items"]) == 3
    assert data["skip"] == 0
    assert data["limit"] == 100

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/?skip=1&limit=1",
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 3
    assert len(data["items"]) == 1
    assert data["skip"] == 1
    assert data["limit"] == 1


@pytest.mark.asyncio
async def test_get_run_by_id(client: AsyncClient, db_session: AsyncSession):
    """A run is returned by its id with its details."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "My Test Run",
        },
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == run_id
    assert data["name"] == "My Test Run"
    assert data["state"] == RunState.CONFIGURED.value
    assert data["rallye_id"] == str(rallye.id)
    assert len(data["join_code"]) == 6


@pytest.mark.asyncio
async def test_get_nonexistent_run_returns_404(client: AsyncClient, db_session: AsyncSession):
    """An unknown run id gives 404."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{fake_uuid}",
        headers=headers,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_run_name_and_settings(client: AsyncClient, db_session: AsyncSession):
    """A run in the configured state takes a new name and new settings."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Original Name",
        },
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    response = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=headers,
        json={
            "name": "Updated Name",
            "settings": {"difficulty": "hard", "points_multiplier": 1.5},
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == run_id
    assert data["name"] == "Updated Name"
    assert data["settings"] == {"difficulty": "hard", "points_multiplier": 1.5}
    assert data["state"] == RunState.CONFIGURED.value


@pytest.mark.asyncio
async def test_update_nonexistent_run_returns_404(client: AsyncClient, db_session: AsyncSession):
    """An unknown run id gives 404 on update."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.put(
        f"/api/orgs/{org.slug}/runs/{fake_uuid}",
        headers=headers,
        json={"name": "New Name"},
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_soft_delete_run_in_configured_state(client: AsyncClient, db_session: AsyncSession):
    """Deleting a configured run answers 204, and the run leaves the list."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Run to Delete",
        },
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    response = await client.delete(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=headers,
    )

    assert response.status_code == 204

    list_response = await client.get(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
    )
    assert list_response.status_code == 200
    data = list_response.json()
    assert data["total"] == 0
    assert len(data["items"]) == 0


@pytest.mark.asyncio
async def test_cannot_delete_active_run(client: AsyncClient, db_session: AsyncSession):
    """An active run cannot be deleted. The answer is 422."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    # Create rallye with stations and challenges (required for snapshot)
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
        location_lat=48.3705,
        location_lng=10.8978,
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
    await db_session.refresh(rallye)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Active Run",
        },
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    start_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=headers,
    )
    assert start_response.status_code == 200
    assert start_response.json()["state"] == RunState.ACTIVE.value

    response = await client.delete(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=headers,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Cannot delete run in active state" in detail or "Deletion only allowed" in detail


@pytest.mark.asyncio
async def test_cross_org_access_returns_404(client: AsyncClient, db_session: AsyncSession):
    """A run of another organization answers 404, not 403, so its existence is not leaked."""
    user1, org1 = await create_test_user_with_org(
        db_session,
        email="coord1@example.com",
        role=UserRole.COORDINATOR,
        org_slug="org-1",
    )
    user2, org2 = await create_test_user_with_org(
        db_session,
        email="coord2@example.com",
        role=UserRole.COORDINATOR,
        org_slug="org-2",
    )
    headers1 = get_auth_headers(user1)
    headers2 = get_auth_headers(user2)

    rallye_repo = RallyeRepository(db_session, org1.id)
    rallye = await rallye_repo.create(
        title={"de": "Org 1 Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    create_response = await client.post(
        f"/api/orgs/{org1.slug}/runs/",
        headers=headers1,
        json={
            "rallye_id": str(rallye.id),
            "name": "Org 1 Run",
        },
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    response = await client.get(
        f"/api/orgs/{org2.slug}/runs/{run_id}",
        headers=headers2,
    )

    # Should return 404 (not 403) to avoid leaking existence
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_deleted_run_not_in_list(client: AsyncClient, db_session: AsyncSession):
    """A deleted run drops out of the list, and the other runs stay."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    create_response1 = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Run 1",
        },
    )
    assert create_response1.status_code == 201
    run1_id = create_response1.json()["id"]

    create_response2 = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Run 2",
        },
    )
    assert create_response2.status_code == 201
    run2_id = create_response2.json()["id"]

    delete_response = await client.delete(
        f"/api/orgs/{org.slug}/runs/{run1_id}",
        headers=headers,
    )
    assert delete_response.status_code == 204

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == run2_id


@pytest.mark.asyncio
async def test_get_deleted_run_returns_404(client: AsyncClient, db_session: AsyncSession):
    """A deleted run answers 404 on GET."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    await db_session.commit()
    await db_session.refresh(rallye)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Run to Delete",
        },
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    delete_response = await client.delete(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=headers,
    )
    assert delete_response.status_code == 204

    response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=headers,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
