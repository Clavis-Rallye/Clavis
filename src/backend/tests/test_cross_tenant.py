"""
Cross-tenant isolation and security tests.

Tests cover:
- get_org_from_slug dependency (valid slug, 404, 403)
- Repository-level isolation
- API-level isolation
- Org slug resolution in the API (403 for a foreign org, 404 for an unknown slug)
"""

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
from tests.factories import create_test_organization, create_test_user

# =============================================================================
# Tests for get_org_from_slug dependency
# =============================================================================


@pytest.mark.asyncio
async def test_get_org_from_slug_valid_slug_correct_user(client: AsyncClient, db_session: AsyncSession):
    """A user resolves their own organization by slug, with all attributes populated."""
    org = await create_test_organization(db_session, name="Test Org", slug="test-org")
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    await db_session.commit()

    from app.auth.dependencies import get_org_from_slug

    resolved_org = await get_org_from_slug(
        slug="test-org",
        db=db_session,
        current_user=user,
    )

    assert resolved_org is not None
    assert resolved_org.id == org.id
    assert resolved_org.slug == "test-org"
    assert resolved_org.name == "Test Org"


@pytest.mark.asyncio
async def test_get_org_from_slug_valid_slug_wrong_user_returns_403(client: AsyncClient, db_session: AsyncSession):
    """A user of another organization is refused with 403 and an access denied message."""
    from fastapi import HTTPException

    org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
    _org_b = await create_test_organization(db_session, name="Org B", slug="org-b")

    user_a = await create_test_user(
        db_session,
        email="usera@example.com",
        password="SecurePass123!",
        role=UserRole.PARTICIPANT,
        organization=org_a,
    )
    await db_session.commit()

    from app.auth.dependencies import get_org_from_slug

    with pytest.raises(HTTPException) as exc_info:
        await get_org_from_slug(
            slug="org-b",
            db=db_session,
            current_user=user_a,
        )

    assert exc_info.value.status_code == 403
    assert "Access denied" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_org_from_slug_invalid_slug_returns_404(client: AsyncClient, db_session: AsyncSession):
    """An unknown slug gives 404 with an organization not found message."""
    from fastapi import HTTPException

    org = await create_test_organization(db_session, name="Test Org", slug="test-org")
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    await db_session.commit()

    from app.auth.dependencies import get_org_from_slug

    with pytest.raises(HTTPException) as exc_info:
        await get_org_from_slug(
            slug="nonexistent-org",
            db=db_session,
            current_user=user,
        )

    assert exc_info.value.status_code == 404
    assert "Organization not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_org_from_slug_soft_deleted_org_returns_404(client: AsyncClient, db_session: AsyncSession):
    """A soft-deleted organization gives 404, even for its own user."""
    from fastapi import HTTPException

    from app.repositories.organization import OrganizationRepository

    org = await create_test_organization(db_session, name="Test Org", slug="test-org")
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    await db_session.commit()

    org_repo = OrganizationRepository(db_session)
    await org_repo.soft_delete(org.id)
    await db_session.commit()

    from app.auth.dependencies import get_org_from_slug

    with pytest.raises(HTTPException) as exc_info:
        await get_org_from_slug(
            slug="test-org",
            db=db_session,
            current_user=user,
        )

    assert exc_info.value.status_code == 404
    assert "Organization not found" in exc_info.value.detail


# =============================================================================
# Repository-level cross-tenant isolation tests
# =============================================================================


@pytest.mark.asyncio
async def test_rallye_get_by_id_cross_tenant_isolation(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """A rallye is found by its own organization and not by another, even with a valid id."""
    repo_org_a = RallyeRepository(db_session, org_a.id)
    rallye_in_org_a = await repo_org_a.create(
        title={"de": "Rallye A", "en": "Rally A"},
        description={"de": "Test Rallye in Org A"},
        is_published=True,
    )
    await db_session.commit()

    found_by_org_a = await repo_org_a.get_by_id(rallye_in_org_a.id)
    assert found_by_org_a is not None
    assert found_by_org_a.id == rallye_in_org_a.id

    repo_org_b = RallyeRepository(db_session, org_b.id)
    found_by_org_b = await repo_org_b.get_by_id(rallye_in_org_a.id)
    assert found_by_org_b is None, "Org B should not be able to access Org A's rallye"


@pytest.mark.asyncio
async def test_rallye_list_cross_tenant_isolation(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """list returns only the rallyes of the scoped organization, even when other organizations have rows in the same table."""
    repo_org_a = RallyeRepository(db_session, org_a.id)
    rallye_a1 = await repo_org_a.create(
        title={"de": "Rallye A1", "en": "Rally A1"},
        description={"de": "First rallye in Org A"},
        is_published=True,
    )
    rallye_a2 = await repo_org_a.create(
        title={"de": "Rallye A2", "en": "Rally A2"},
        description={"de": "Second rallye in Org A"},
        is_published=False,
    )

    repo_org_b = RallyeRepository(db_session, org_b.id)
    rallye_b1 = await repo_org_b.create(
        title={"de": "Rallye B1", "en": "Rally B1"},
        description={"de": "First rallye in Org B"},
        is_published=True,
    )
    await db_session.commit()

    # Org A should only see its own rallyes
    rallyes_in_org_a = await repo_org_a.list(skip=0, limit=100)
    assert len(rallyes_in_org_a) == 2, "Org A should see exactly 2 rallyes"
    rallye_ids_in_org_a = {r.id for r in rallyes_in_org_a}
    assert rallye_ids_in_org_a == {rallye_a1.id, rallye_a2.id}
    assert rallye_b1.id not in rallye_ids_in_org_a, "Org A should not see Org B's rallyes"

    # Org B should only see its own rallyes
    rallyes_in_org_b = await repo_org_b.list(skip=0, limit=100)
    assert len(rallyes_in_org_b) == 1, "Org B should see exactly 1 rallye"
    assert rallyes_in_org_b[0].id == rallye_b1.id
    assert rallye_a1.id not in {r.id for r in rallyes_in_org_b}, "Org B should not see Org A's rallyes"


@pytest.mark.asyncio
async def test_station_get_by_id_cross_tenant_isolation(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """A station is found by its own organization and not by another."""
    rallye_repo_a = RallyeRepository(db_session, org_a.id)
    rallye_a = await rallye_repo_a.create(
        title={"de": "Rallye A"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo_a = StationRepository(db_session, org_a.id)
    station_a = await station_repo_a.create(
        title={"de": "Station A"},
        description={"de": "Test station in Org A"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station_a = RallyeStation(organization_id=org_a.id, rallye_id=rallye_a.id, station_id=station_a.id, sort_order=0)
    db_session.add(rs_station_a)
    await db_session.flush()
    await db_session.flush()
    await db_session.commit()

    found_by_org_a = await station_repo_a.get_by_id(station_a.id)
    assert found_by_org_a is not None
    assert found_by_org_a.id == station_a.id

    station_repo_b = StationRepository(db_session, org_b.id)
    found_by_org_b = await station_repo_b.get_by_id(station_a.id)
    assert found_by_org_b is None, "Org B should not be able to access Org A's station"


@pytest.mark.asyncio
async def test_station_list_cross_tenant_isolation(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """list returns only the stations of the scoped organization."""
    rallye_repo_a = RallyeRepository(db_session, org_a.id)
    rallye_a = await rallye_repo_a.create(
        title={"de": "Rallye A"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo_a = StationRepository(db_session, org_a.id)
    station_a1 = await station_repo_a.create(
        title={"de": "Station A1"},
        description={"de": "First station in Org A"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station_a1 = RallyeStation(
        organization_id=org_a.id, rallye_id=rallye_a.id, station_id=station_a1.id, sort_order=0
    )
    db_session.add(rs_station_a1)
    await db_session.flush()
    await db_session.flush()
    station_a2 = await station_repo_a.create(
        title={"de": "Station A2"},
        description={"de": "Second station in Org A"},
        location_lat=48.3715,
        location_lng=10.8988,
    )
    rs_station_a2 = RallyeStation(
        organization_id=org_a.id, rallye_id=rallye_a.id, station_id=station_a2.id, sort_order=1
    )
    db_session.add(rs_station_a2)
    await db_session.flush()
    await db_session.flush()

    rallye_repo_b = RallyeRepository(db_session, org_b.id)
    rallye_b = await rallye_repo_b.create(
        title={"de": "Rallye B"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo_b = StationRepository(db_session, org_b.id)
    station_b1 = await station_repo_b.create(
        title={"de": "Station B1"},
        description={"de": "First station in Org B"},
        location_lat=48.3725,
        location_lng=10.8998,
    )
    rs_station_b1 = RallyeStation(
        organization_id=org_b.id, rallye_id=rallye_b.id, station_id=station_b1.id, sort_order=0
    )
    db_session.add(rs_station_b1)
    await db_session.flush()
    await db_session.flush()
    await db_session.commit()

    # Org A should only see its own stations
    stations_in_org_a = await station_repo_a.list(skip=0, limit=100)
    assert len(stations_in_org_a) == 2, "Org A should see exactly 2 stations"
    station_ids_in_org_a = {s.id for s in stations_in_org_a}
    assert station_ids_in_org_a == {station_a1.id, station_a2.id}
    assert station_b1.id not in station_ids_in_org_a, "Org A should not see Org B's stations"

    # Org B should only see its own stations
    stations_in_org_b = await station_repo_b.list(skip=0, limit=100)
    assert len(stations_in_org_b) == 1, "Org B should see exactly 1 station"
    assert stations_in_org_b[0].id == station_b1.id
    assert station_a1.id not in {s.id for s in stations_in_org_b}, "Org B should not see Org A's stations"


@pytest.mark.asyncio
async def test_challenge_get_by_id_cross_tenant_isolation(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """A challenge is found by its own organization and not by another."""
    rallye_repo_a = RallyeRepository(db_session, org_a.id)
    rallye_a = await rallye_repo_a.create(
        title={"de": "Rallye A"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo_a = StationRepository(db_session, org_a.id)
    station_a = await station_repo_a.create(
        title={"de": "Station A"},
        description={"de": "Test station in Org A"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station_a = RallyeStation(organization_id=org_a.id, rallye_id=rallye_a.id, station_id=station_a.id, sort_order=0)
    db_session.add(rs_station_a)
    await db_session.flush()
    await db_session.flush()

    challenge_repo_a = ChallengeRepository(db_session, org_a.id)
    challenge_a = await challenge_repo_a.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Challenge A"},
        instructions={"de": "Take a photo"},
        scoring_rules={"points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_a.id, rallye_station_id=rs_station_a.id, challenge_id=challenge_a.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    found_by_org_a = await challenge_repo_a.get_by_id(challenge_a.id)
    assert found_by_org_a is not None
    assert found_by_org_a.id == challenge_a.id

    challenge_repo_b = ChallengeRepository(db_session, org_b.id)
    found_by_org_b = await challenge_repo_b.get_by_id(challenge_a.id)
    assert found_by_org_b is None, "Org B should not be able to access Org A's challenge"


@pytest.mark.asyncio
async def test_challenge_list_cross_tenant_isolation(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """list returns only the challenges of the scoped organization."""
    rallye_repo_a = RallyeRepository(db_session, org_a.id)
    rallye_a = await rallye_repo_a.create(
        title={"de": "Rallye A"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo_a = StationRepository(db_session, org_a.id)
    station_a = await station_repo_a.create(
        title={"de": "Station A"},
        description={"de": "Test station in Org A"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station_a = RallyeStation(organization_id=org_a.id, rallye_id=rallye_a.id, station_id=station_a.id, sort_order=0)
    db_session.add(rs_station_a)
    await db_session.flush()
    await db_session.flush()

    challenge_repo_a = ChallengeRepository(db_session, org_a.id)
    challenge_a1 = await challenge_repo_a.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Challenge A1"},
        instructions={"de": "Take a photo at the fountain"},
        scoring_rules={"points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_a.id, rallye_station_id=rs_station_a.id, challenge_id=challenge_a1.id, sort_order=0
        )
    )
    await db_session.flush()
    challenge_a2 = await challenge_repo_a.create(
        challenge_type=ChallengeType.QUIZ,
        title={"de": "Challenge A2"},
        instructions={"de": "Answer the question"},
        scoring_rules={"points": 5},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_a.id, rallye_station_id=rs_station_a.id, challenge_id=challenge_a2.id, sort_order=1
        )
    )
    await db_session.flush()

    rallye_repo_b = RallyeRepository(db_session, org_b.id)
    rallye_b = await rallye_repo_b.create(
        title={"de": "Rallye B"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo_b = StationRepository(db_session, org_b.id)
    station_b = await station_repo_b.create(
        title={"de": "Station B"},
        description={"de": "Test station in Org B"},
        location_lat=48.3725,
        location_lng=10.8998,
    )
    rs_station_b = RallyeStation(organization_id=org_b.id, rallye_id=rallye_b.id, station_id=station_b.id, sort_order=0)
    db_session.add(rs_station_b)
    await db_session.flush()
    await db_session.flush()

    challenge_repo_b = ChallengeRepository(db_session, org_b.id)
    challenge_b1 = await challenge_repo_b.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge B1"},
        instructions={"de": "Write a poem"},
        scoring_rules={"points": 15},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_b.id, rallye_station_id=rs_station_b.id, challenge_id=challenge_b1.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    # Org A should only see its own challenges
    challenges_in_org_a = await challenge_repo_a.list(skip=0, limit=100)
    assert len(challenges_in_org_a) == 2, "Org A should see exactly 2 challenges"
    challenge_ids_in_org_a = {c.id for c in challenges_in_org_a}
    assert challenge_ids_in_org_a == {challenge_a1.id, challenge_a2.id}
    assert challenge_b1.id not in challenge_ids_in_org_a, "Org A should not see Org B's challenges"

    # Org B should only see its own challenges
    challenges_in_org_b = await challenge_repo_b.list(skip=0, limit=100)
    assert len(challenges_in_org_b) == 1, "Org B should see exactly 1 challenge"
    assert challenges_in_org_b[0].id == challenge_b1.id
    assert challenge_a1.id not in {c.id for c in challenges_in_org_b}, "Org B should not see Org A's challenges"


@pytest.mark.asyncio
async def test_update_cross_tenant_protection(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """update from another organization returns None and leaves the rallye unchanged."""
    repo_org_a = RallyeRepository(db_session, org_a.id)
    rallye_in_org_a = await repo_org_a.create(
        title={"de": "Original Title"},
        description={"de": "Original description"},
        is_published=False,
    )
    await db_session.commit()

    repo_org_b = RallyeRepository(db_session, org_b.id)
    result = await repo_org_b.update(
        rallye_in_org_a.id,
        title={"de": "Malicious Update"},
        is_published=True,
    )
    assert result is None, "Update should fail silently and return None"

    await db_session.refresh(rallye_in_org_a)
    assert rallye_in_org_a.title["de"] == "Original Title"
    assert rallye_in_org_a.is_published is False


@pytest.mark.asyncio
async def test_soft_delete_cross_tenant_protection(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """soft_delete from another organization returns False and leaves the rallye active."""
    repo_org_a = RallyeRepository(db_session, org_a.id)
    rallye_in_org_a = await repo_org_a.create(
        title={"de": "Important Rallye"},
        description={"de": "Should not be deleted by org B"},
        is_published=True,
    )
    await db_session.commit()

    repo_org_b = RallyeRepository(db_session, org_b.id)
    result = await repo_org_b.soft_delete(rallye_in_org_a.id)
    assert result is False, "Soft delete should fail silently and return False"

    await db_session.refresh(rallye_in_org_a)
    assert rallye_in_org_a.is_deleted is False
    assert rallye_in_org_a.deleted_at is None


@pytest.mark.asyncio
async def test_nested_entity_isolation(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """The link-table list methods return nothing for another organization.

    Custom repository methods respect tenant boundaries, not only the base CRUD."""
    from app.repositories.rallye_station import RallyeStationRepository
    from app.repositories.rallye_station_challenge import RallyeStationChallengeRepository

    rallye_repo_a = RallyeRepository(db_session, org_a.id)
    rallye_a = await rallye_repo_a.create(
        title={"de": "Rallye A"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo_a = StationRepository(db_session, org_a.id)
    station_a = await station_repo_a.create(
        title={"de": "Station A"},
        description={"de": "Test"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station_a = RallyeStation(organization_id=org_a.id, rallye_id=rallye_a.id, station_id=station_a.id, sort_order=0)
    db_session.add(rs_station_a)
    await db_session.flush()
    await db_session.flush()

    challenge_repo_a = ChallengeRepository(db_session, org_a.id)
    _challenge_a = await challenge_repo_a.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Challenge A"},
        instructions={"de": "Test"},
        scoring_rules={"points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_a.id, rallye_station_id=rs_station_a.id, challenge_id=_challenge_a.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    # Org B tries to list station links for org A's rallye — should return empty list
    rs_repo_b = RallyeStationRepository(db_session, org_b.id)
    stations_from_org_b = await rs_repo_b.list_by_rallye(rallye_a.id)
    assert len(stations_from_org_b) == 0, "Org B should not see stations from Org A's rallye"

    # Org B tries to list challenge links for org A's rallye-station — should return empty list
    rsc_repo_b = RallyeStationChallengeRepository(db_session, org_b.id)
    challenges_from_org_b = await rsc_repo_b.list_by_rallye_station(rs_station_a.id)
    assert len(challenges_from_org_b) == 0, "Org B should not see challenges from Org A's rallye-station"


@pytest.mark.asyncio
async def test_count_respects_tenant_isolation(
    db_session: AsyncSession,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
) -> None:
    """count() counts only the rows of the scoped organization, so counts do not leak across organizations."""
    repo_org_a = RallyeRepository(db_session, org_a.id)
    for i in range(3):
        await repo_org_a.create(
            title={"de": f"Rallye A{i}"},
            description={"de": "Test"},
            is_published=True,
        )

    repo_org_b = RallyeRepository(db_session, org_b.id)
    for i in range(2):
        await repo_org_b.create(
            title={"de": f"Rallye B{i}"},
            description={"de": "Test"},
            is_published=True,
        )
    await db_session.commit()

    count_org_a = await repo_org_a.count()
    count_org_b = await repo_org_b.count()

    assert count_org_a == 3, "Org A should count only its 3 rallyes"
    assert count_org_b == 2, "Org B should count only its 2 rallyes"


# =============================================================================
# API-level cross-tenant isolation tests
# =============================================================================


@pytest.mark.asyncio
async def test_api_get_rallye_cross_tenant_returns_404(
    db_session: AsyncSession,
    client: AsyncClient,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
    user_in_org_a,  # type: ignore[no-untyped-def]
) -> None:
    """GET of a rallye of another organization by id gives 404, not 403, so its existence does not leak."""
    from tests.factories import get_auth_headers

    repo_org_b = RallyeRepository(db_session, org_b.id)
    rallye_b = await repo_org_b.create(
        title={"de": "Rallye B", "en": "Rally B"},
        description={"de": "Test Rallye in Org B"},
        is_published=True,
    )
    await db_session.commit()

    headers = get_auth_headers(user_in_org_a)
    response = await client.get(
        f"/api/orgs/{org_a.slug}/rallyes/{rallye_b.id}",
        headers=headers,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_api_list_rallyes_cross_org_slug_returns_403(
    db_session: AsyncSession,
    client: AsyncClient,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
    user_in_org_a,  # type: ignore[no-untyped-def]
) -> None:
    """GET on the slug of another organization gives 403, because get_org_from_slug checks the membership of the user."""
    from tests.factories import get_auth_headers

    repo_org_b = RallyeRepository(db_session, org_b.id)
    await repo_org_b.create(
        title={"de": "Rallye B", "en": "Rally B"},
        description={"de": "Test Rallye in Org B"},
        is_published=True,
    )
    await db_session.commit()

    headers = get_auth_headers(user_in_org_a)
    response = await client.get(
        f"/api/orgs/{org_b.slug}/rallyes/",
        headers=headers,
    )

    assert response.status_code == 403
    assert "access denied" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_api_create_station_cross_tenant_returns_404(
    db_session: AsyncSession,
    client: AsyncClient,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
    user_in_org_a,  # type: ignore[no-untyped-def]
) -> None:
    """POST of a station under another organization's rallye through the own slug gives 404 and links nothing."""
    from tests.factories import get_auth_headers

    repo_org_b = RallyeRepository(db_session, org_b.id)
    rallye_b = await repo_org_b.create(
        title={"de": "Rallye B", "en": "Rally B"},
        description={"de": "Test Rallye in Org B"},
        is_published=True,
    )
    await db_session.commit()

    # User from org A tries to create a station under org B's rallye
    # Using org A's slug in the URL
    headers = get_auth_headers(user_in_org_a)
    response = await client.post(
        f"/api/orgs/{org_a.slug}/rallyes/{rallye_b.id}/stations",
        headers=headers,
        json={
            "title": {"de": "Malicious Station"},
            "description": {"de": "Should not be created"},
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

    # Verify no station was linked into org B's rallye
    from app.repositories.rallye_station import RallyeStationRepository

    rs_repo_b = RallyeStationRepository(db_session, org_b.id)
    assert await rs_repo_b.count_by_rallye(rallye_b.id) == 0, "No station should have been created in org B"


@pytest.mark.asyncio
async def test_api_create_station_via_wrong_org_slug_returns_403(
    db_session: AsyncSession,
    client: AsyncClient,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
    user_in_org_a,  # type: ignore[no-untyped-def]
) -> None:
    """POST of a station through the slug of another organization gives 403 and links nothing."""
    from tests.factories import get_auth_headers

    repo_org_b = RallyeRepository(db_session, org_b.id)
    rallye_b = await repo_org_b.create(
        title={"de": "Rallye B", "en": "Rally B"},
        description={"de": "Test Rallye in Org B"},
        is_published=True,
    )
    await db_session.commit()

    # User from org A tries to create a station via org B's slug
    headers = get_auth_headers(user_in_org_a)
    response = await client.post(
        f"/api/orgs/{org_b.slug}/rallyes/{rallye_b.id}/stations",
        headers=headers,
        json={
            "title": {"de": "Malicious Station"},
            "description": {"de": "Should not be created"},
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
    )

    assert response.status_code == 403
    assert "access denied" in response.json()["detail"].lower()

    # Verify no station was linked into org B's rallye
    from app.repositories.rallye_station import RallyeStationRepository

    rs_repo_b = RallyeStationRepository(db_session, org_b.id)
    assert await rs_repo_b.count_by_rallye(rallye_b.id) == 0, "No station should have been created in org B"


# =============================================================================
# Org slug middleware security tests
# =============================================================================


@pytest.mark.asyncio
async def test_api_get_org_wrong_user_returns_403(
    db_session: AsyncSession,
    client: AsyncClient,
    org_a,  # type: ignore[no-untyped-def]
    org_b,  # type: ignore[no-untyped-def]
    user_in_org_a,  # type: ignore[no-untyped-def]
) -> None:
    """GET /api/orgs/{slug} gives 403 for a user who does not belong to that organization."""
    from tests.factories import get_auth_headers

    headers = get_auth_headers(user_in_org_a)
    response = await client.get(
        f"/api/orgs/{org_b.slug}",
        headers=headers,
    )

    assert response.status_code == 403
    assert "access denied" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_api_get_org_nonexistent_slug_returns_404(
    db_session: AsyncSession,
    client: AsyncClient,
    user_in_org_a,  # type: ignore[no-untyped-def]
) -> None:
    """GET /api/orgs/{slug} gives 404 for a slug that matches no organization."""
    from tests.factories import get_auth_headers

    headers = get_auth_headers(user_in_org_a)
    response = await client.get(
        "/api/orgs/nonexistent-org-slug",
        headers=headers,
    )

    assert response.status_code == 404
    assert "organization not found" in response.json()["detail"].lower()
