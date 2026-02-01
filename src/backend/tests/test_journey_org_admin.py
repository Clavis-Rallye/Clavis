"""
Org admin governance journeys, through the API.

Sections of this file:
- Organization bootstrap: read, update, and a refused create
- User role management: the role hierarchy and what each role may do
- Organization soft delete: a deleted organization and its nested resources are inaccessible
- Cross-organization isolation: no access across tenants at organization level
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from app.repositories.organization import OrganizationRepository
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers

# =============================================================================
# Scenario: Organization Bootstrap
# =============================================================================


@pytest.mark.asyncio
async def test_org_admin_reads_and_updates_the_organization(client: AsyncClient, db_session: AsyncSession) -> None:
    """An org admin reads and updates the organization, and a participant of it cannot update it."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@test.com",
        role=UserRole.ORG_ADMIN,
        org_slug="bootstrap-org",
        org_name="Bootstrap Organization",
    )
    admin_headers = get_auth_headers(admin)

    response = await client.get(f"/api/orgs/{org.slug}", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(org.id)
    assert data["name"] == "Bootstrap Organization"
    assert data["slug"] == "bootstrap-org"
    assert data["languages"] == ["de"]  # Default from factory

    # Update the organization
    response = await client.put(
        f"/api/orgs/{org.slug}",
        headers=admin_headers,
        json={
            "name": "Updated Organization Name",
            "slug": org.slug,  # Must provide slug to avoid router passing None to update()
            "languages": ["de", "en"],
            "settings": {"theme": "dark", "max_teams": 20, "new_setting": "value"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Organization Name"
    assert data["languages"] == ["de", "en"]
    assert data["settings"]["theme"] == "dark"
    assert data["settings"]["max_teams"] == 20
    assert data["settings"]["new_setting"] == "value"

    # The update is persisted
    response = await client.get(f"/api/orgs/{org.slug}", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Organization Name"
    assert data["languages"] == ["de", "en"]
    assert data["settings"]["theme"] == "dark"
    assert data["settings"]["max_teams"] == 20

    # A non-admin user of the same organization cannot update it
    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    participant_headers = get_auth_headers(participant)

    response = await client.put(
        f"/api/orgs/{org.slug}",
        headers=participant_headers,
        json={"name": "Hijacked Name"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_organization_creation_refused_for_org_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    """Organization tokens cannot create organizations.

    Organizations are created by the operator through the instance endpoint.
    Every organization token, org_admin included, gets a coded 403 and nothing is created.
    """
    admin, _org = await create_test_user_with_org(
        db_session,
        email="admin@test.com",
        role=UserRole.ORG_ADMIN,
        org_slug="admin-org",
    )
    admin_headers = get_auth_headers(admin)
    org_count_before = await OrganizationRepository(db_session).count()

    response = await client.post(
        "/api/organizations",
        headers=admin_headers,
        json={"name": "Another Org", "slug": "another-org"},
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "ORG_CREATE_INSTANCE_ONLY"

    assert await OrganizationRepository(db_session).count() == org_count_before


# =============================================================================
# Scenario: User Role Management
# =============================================================================


@pytest.mark.asyncio
async def test_each_role_reads_back_as_itself_and_only_creator_and_above_create_a_rallye(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Each role reads back as itself, and only creator and above can create a rallye."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@org.test",
        role=UserRole.ORG_ADMIN,
        org_slug="role-test-org",
    )

    participant = await create_test_user(
        db_session,
        email="participant@org.test",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    coordinator = await create_test_user(
        db_session,
        email="coordinator@org.test",
        role=UserRole.COORDINATOR,
        organization=org,
    )
    creator = await create_test_user(
        db_session,
        email="creator@org.test",
        role=UserRole.CREATOR,
        organization=org,
    )

    # Each user reads back the own role
    for user, expected_role in [
        (participant, "participant"),
        (coordinator, "coordinator"),
        (creator, "creator"),
        (admin, "org_admin"),
    ]:
        headers = get_auth_headers(user)
        response = await client.get("/api/auth/me", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == expected_role
        assert data["email"] == user.email

    # Rallye creation requires the CREATOR role or higher
    rallye_payload = {
        "title": {"de": "Test Rallye", "en": "Test Rally"},
        "description": {"de": "Beschreibung", "en": "Description"},
        "is_published": True,
    }

    # A participant cannot create a rallye
    participant_headers = get_auth_headers(participant)
    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=participant_headers,
        json=rallye_payload,
    )
    assert response.status_code == 403

    # A coordinator cannot create a rallye
    coordinator_headers = get_auth_headers(coordinator)
    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=coordinator_headers,
        json=rallye_payload,
    )
    assert response.status_code == 403

    # A creator can create a rallye
    creator_headers = get_auth_headers(creator)
    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json=rallye_payload,
    )
    assert response.status_code == 201
    assert response.json()["title"]["de"] == "Test Rallye"

    # An org admin can create a rallye, because the hierarchy is cumulative
    admin_headers = get_auth_headers(admin)
    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=admin_headers,
        json={
            "title": {"de": "Admin Rallye", "en": "Admin Rally"},
            "description": {"de": "Admin", "en": "Admin"},
        },
    )
    assert response.status_code == 201
    assert response.json()["title"]["de"] == "Admin Rallye"


@pytest.mark.asyncio
async def test_coordinator_cannot_manage_org(client: AsyncClient, db_session: AsyncSession) -> None:
    """A coordinator can read the organization, but updating and deleting need org_admin."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="coord-org",
    )
    coord_headers = get_auth_headers(coordinator)

    response = await client.get(f"/api/orgs/{org.slug}", headers=coord_headers)
    assert response.status_code == 200

    response = await client.put(
        f"/api/orgs/{org.slug}",
        headers=coord_headers,
        json={"name": "New Name"},
    )
    assert response.status_code == 403

    response = await client.delete(f"/api/orgs/{org.slug}", headers=coord_headers)
    assert response.status_code == 403


# =============================================================================
# Scenario: Organization Soft Delete
# =============================================================================


@pytest.mark.asyncio
async def test_org_soft_delete_hides_its_rallyes_stations_and_challenges(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A deleted organization is not found, and neither are its rallyes, stations and challenges."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@delete.test",
        role=UserRole.ORG_ADMIN,
        org_slug="delete-org",
    )
    admin_headers = get_auth_headers(admin)

    rallye_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=admin_headers,
        json={
            "title": {"de": "Rallye", "en": "Rally"},
            "description": {"de": "Test", "en": "Test"},
        },
    )
    assert rallye_response.status_code == 201
    rallye_id = rallye_response.json()["id"]

    # The station is created in the library and linked to the rallye
    station_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
        headers=admin_headers,
        json={
            "title": {"de": "Station", "en": "Station"},
            "description": {"de": "Test", "en": "Test"},
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
    )
    assert station_response.status_code == 201
    station_id = station_response.json()["station"]["id"]
    rallye_station_id = station_response.json()["link_id"]

    # The challenge is created in the library and linked to the station
    challenge_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
        headers=admin_headers,
        json={
            "challenge_type": "text",
            "title": {"de": "Challenge", "en": "Challenge"},
            "instructions": {"de": "Test", "en": "Test"},
            "scoring_rules": {"max_points": 10},
        },
    )
    assert challenge_response.status_code == 201
    challenge_id = challenge_response.json()["challenge"]["id"]

    # Soft delete the organization
    response = await client.delete(f"/api/orgs/{org.slug}", headers=admin_headers)
    assert response.status_code == 200  # OrganizationDeleteResponse returns 200
    assert "deleted successfully" in response.json()["message"].lower()

    # The deleted organization is not visible
    response = await client.get(f"/api/orgs/{org.slug}", headers=admin_headers)
    assert response.status_code == 404

    # Nested resources are inaccessible. The organization lookup by slug already fails.
    response = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=admin_headers)
    assert response.status_code == 404

    response = await client.get(
        f"/api/orgs/{org.slug}/stations/{station_id}",
        headers=admin_headers,
    )
    assert response.status_code == 404

    response = await client.get(
        f"/api/orgs/{org.slug}/challenges/{challenge_id}",
        headers=admin_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_org_soft_delete_is_idempotent(client: AsyncClient, db_session: AsyncSession) -> None:
    """Deleting an already deleted organization returns 404."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@idem.test",
        role=UserRole.ORG_ADMIN,
        org_slug="idem-org",
    )
    admin_headers = get_auth_headers(admin)

    response = await client.delete(f"/api/orgs/{org.slug}", headers=admin_headers)
    assert response.status_code == 200

    response = await client.delete(f"/api/orgs/{org.slug}", headers=admin_headers)
    assert response.status_code == 404


# =============================================================================
# Scenario: Cross-Organization Isolation at Org Level
# =============================================================================


@pytest.mark.asyncio
async def test_cross_org_isolation_at_org_level(client: AsyncClient, db_session: AsyncSession) -> None:
    """An org admin gets 403 on reading, updating and deleting another organization."""
    admin_a, org_a = await create_test_user_with_org(
        db_session,
        email="admin-a@test.com",
        role=UserRole.ORG_ADMIN,
        org_slug="org-a",
        org_name="Organization A",
    )
    admin_b, org_b = await create_test_user_with_org(
        db_session,
        email="admin-b@test.com",
        role=UserRole.ORG_ADMIN,
        org_slug="org-b",
        org_name="Organization B",
    )

    admin_a_headers = get_auth_headers(admin_a)

    response = await client.get(f"/api/orgs/{org_b.slug}", headers=admin_a_headers)
    assert response.status_code == 403

    response = await client.put(
        f"/api/orgs/{org_b.slug}",
        headers=admin_a_headers,
        json={"name": "Hijacked Name"},
    )
    assert response.status_code == 403

    response = await client.delete(f"/api/orgs/{org_b.slug}", headers=admin_a_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cross_org_isolation_rallye_access(client: AsyncClient, db_session: AsyncSession) -> None:
    """A user of one organization gets 403 on the rallye list and on a rallye of another organization."""
    user_a, org_a = await create_test_user_with_org(
        db_session,
        email="user-a@test.com",
        role=UserRole.PARTICIPANT,
        org_slug="org-a-rallye",
    )
    user_a_headers = get_auth_headers(user_a)

    creator_b, org_b = await create_test_user_with_org(
        db_session,
        email="creator-b@test.com",
        role=UserRole.CREATOR,
        org_slug="org-b-rallye",
    )
    creator_b_headers = get_auth_headers(creator_b)

    rallye_response = await client.post(
        f"/api/orgs/{org_b.slug}/rallyes/",
        headers=creator_b_headers,
        json={
            "title": {"de": "Org B Rallye", "en": "Org B Rally"},
            "description": {"de": "Test", "en": "Test"},
        },
    )
    assert rallye_response.status_code == 201
    rallye_id = rallye_response.json()["id"]

    response = await client.get(f"/api/orgs/{org_b.slug}/rallyes/", headers=user_a_headers)
    assert response.status_code == 403

    response = await client.get(f"/api/orgs/{org_b.slug}/rallyes/{rallye_id}", headers=user_a_headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cross_org_user_cannot_create_in_other_org(client: AsyncClient, db_session: AsyncSession) -> None:
    """A creator of one organization cannot create a rallye in another organization."""
    creator_a, org_a = await create_test_user_with_org(
        db_session,
        email="creator-a@test.com",
        role=UserRole.CREATOR,
        org_slug="org-a-create",
    )
    creator_a_headers = get_auth_headers(creator_a)

    _admin_b, org_b = await create_test_user_with_org(
        db_session,
        email="admin-b@test.com",
        role=UserRole.ORG_ADMIN,
        org_slug="org-b-create",
    )

    response = await client.post(
        f"/api/orgs/{org_b.slug}/rallyes/",
        headers=creator_a_headers,
        json={
            "title": {"de": "Hijacked Rallye", "en": "Hijacked Rally"},
            "description": {"de": "Test", "en": "Test"},
        },
    )
    assert response.status_code == 403
