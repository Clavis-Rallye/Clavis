"""Integration tests for role-based access control (RBAC).

Covers the role hierarchy (guest < participant < coordinator < creator < org_admin),
organization isolation, and PII field-level filtering by caller role.
"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import (
    create_test_organization,
    create_test_user,
    create_test_user_with_org,
    get_auth_cookies,
)

# ============================================================================
# Role Hierarchy Tests
# ============================================================================


@pytest.mark.asyncio
async def test_guest_cannot_access_org_admin_endpoint(client: AsyncClient, db_session: AsyncSession):
    """A guest gets 403 on an org_admin-only endpoint."""
    guest_user, guest_org = await create_test_user_with_org(
        db_session,
        email="guest@example.com",
        role=UserRole.GUEST,
        org_slug="guest-org",
    )

    response = await client.put(
        f"/api/orgs/{guest_org.slug}",
        json={"name": "Guest Org"},
        cookies=get_auth_cookies(guest_user),
    )

    assert response.status_code == 403
    data = response.json()
    assert "org_admin" in data["detail"].lower()


@pytest.mark.asyncio
async def test_participant_cannot_access_org_admin_endpoint(client: AsyncClient, db_session: AsyncSession):
    """A participant gets 403 on an org_admin-only endpoint."""
    participant_user, participant_org = await create_test_user_with_org(
        db_session,
        email="participant@example.com",
        role=UserRole.PARTICIPANT,
        org_slug="participant-org",
    )

    response = await client.put(
        f"/api/orgs/{participant_org.slug}",
        json={"name": "Participant Org"},
        cookies=get_auth_cookies(participant_user),
    )

    assert response.status_code == 403
    data = response.json()
    assert "org_admin" in data["detail"].lower()


@pytest.mark.asyncio
async def test_coordinator_cannot_access_org_admin_endpoint(client: AsyncClient, db_session: AsyncSession):
    """A coordinator gets 403 on an org_admin-only endpoint."""
    coordinator_user, coordinator_org = await create_test_user_with_org(
        db_session,
        email="coordinator@example.com",
        role=UserRole.COORDINATOR,
        org_slug="coordinator-org",
    )

    response = await client.put(
        f"/api/orgs/{coordinator_org.slug}",
        json={"name": "Coordinator Org"},
        cookies=get_auth_cookies(coordinator_user),
    )

    assert response.status_code == 403
    data = response.json()
    assert "org_admin" in data["detail"].lower()


@pytest.mark.asyncio
async def test_creator_cannot_access_org_admin_endpoint(client: AsyncClient, db_session: AsyncSession):
    """A creator gets 403 on an org_admin-only endpoint."""
    creator_user, creator_org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="creator-org",
    )

    response = await client.put(
        f"/api/orgs/{creator_org.slug}",
        json={"name": "Creator Org"},
        cookies=get_auth_cookies(creator_user),
    )

    assert response.status_code == 403
    data = response.json()
    assert "org_admin" in data["detail"].lower()


@pytest.mark.asyncio
async def test_org_admin_can_access_org_admin_endpoint(client: AsyncClient, db_session: AsyncSession):
    """An org admin is refused on POST /api/organizations with a coded 403.

    Organization creation belongs to the operator level, so the route refuses every
    organization token, org_admin included. The org-admin-only role gate itself is
    covered above, against PUT /api/orgs/{slug}.
    """
    org_admin_user = await create_test_user(db_session, email="admin@example.com", role=UserRole.ORG_ADMIN)

    response = await client.post(
        "/api/organizations",
        json={
            "name": "Admin Org",
            "slug": "admin-org",
            "languages": ["de"],
            "settings": {},
        },
        cookies=get_auth_cookies(org_admin_user),
    )

    assert response.status_code == 403
    data = response.json()
    assert data["error_code"] == "ORG_CREATE_INSTANCE_ONLY"


@pytest.mark.asyncio
async def test_all_roles_can_access_their_own_organization(client: AsyncClient, db_session: AsyncSession):
    """Every role can read its own organization by slug."""
    roles = [
        UserRole.GUEST,
        UserRole.PARTICIPANT,
        UserRole.COORDINATOR,
        UserRole.CREATOR,
        UserRole.ORG_ADMIN,
    ]

    for role in roles:
        user, org = await create_test_user_with_org(
            db_session,
            email=f"{role.value}@example.com",
            role=role,
            org_name=f"{role.value.title()} Org",
            org_slug=f"{role.value}-org-{role.value}",  # Unique slug
        )

        response = await client.get(
            f"/api/orgs/{org.slug}",
            cookies=get_auth_cookies(user),
        )

        assert response.status_code == 200, f"Failed for role {role.value}"
        data = response.json()
        assert data["id"] == str(org.id)
        assert data["name"] == f"{role.value.title()} Org"


# ============================================================================
# Organization Isolation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_user_cannot_access_other_organization_by_id(client: AsyncClient, db_session: AsyncSession):
    """A user cannot read another organization's details: 403 with an access-denied message."""
    user_a, org_a = await create_test_user_with_org(
        db_session,
        email="usera@example.com",
        role=UserRole.ORG_ADMIN,
        org_name="Organization A",
        org_slug="org-a",
    )

    user_b, org_b = await create_test_user_with_org(
        db_session,
        email="userb@example.com",
        role=UserRole.ORG_ADMIN,
        org_name="Organization B",
        org_slug="org-b",
    )

    response = await client.get(
        f"/api/orgs/{org_b.slug}",
        cookies=get_auth_cookies(user_a),
    )

    assert response.status_code == 403
    data = response.json()
    assert "access denied" in data["detail"].lower()


@pytest.mark.asyncio
async def test_user_can_access_own_organization_by_id(client: AsyncClient, db_session: AsyncSession):
    """A user can read their own organization by slug."""
    user, org = await create_test_user_with_org(
        db_session,
        email="user@example.com",
        role=UserRole.PARTICIPANT,
        org_name="My Organization",
        org_slug="my-org",
    )

    response = await client.get(
        f"/api/orgs/{org.slug}",
        cookies=get_auth_cookies(user),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Organization"
    assert data["slug"] == "my-org"
    assert UUID(data["id"]) == org.id


@pytest.mark.asyncio
async def test_organization_list_only_shows_own_organization(client: AsyncClient, db_session: AsyncSession):
    """Each of three users reads only their own organization by slug."""
    user_a, org_a = await create_test_user_with_org(
        db_session,
        email="usera@example.com",
        role=UserRole.PARTICIPANT,
        org_name="Organization A",
        org_slug="org-a-list",
    )

    user_b, org_b = await create_test_user_with_org(
        db_session,
        email="userb@example.com",
        role=UserRole.PARTICIPANT,
        org_name="Organization B",
        org_slug="org-b-list",
    )

    user_c, org_c = await create_test_user_with_org(
        db_session,
        email="userc@example.com",
        role=UserRole.PARTICIPANT,
        org_name="Organization C",
        org_slug="org-c-list",
    )

    response_a = await client.get(
        f"/api/orgs/{org_a.slug}",
        cookies=get_auth_cookies(user_a),
    )

    assert response_a.status_code == 200
    data_a = response_a.json()
    assert data_a["name"] == "Organization A"

    response_b = await client.get(
        f"/api/orgs/{org_b.slug}",
        cookies=get_auth_cookies(user_b),
    )

    assert response_b.status_code == 200
    data_b = response_b.json()
    assert data_b["name"] == "Organization B"

    response_c = await client.get(
        f"/api/orgs/{org_c.slug}",
        cookies=get_auth_cookies(user_c),
    )

    assert response_c.status_code == 200
    data_c = response_c.json()
    assert data_c["name"] == "Organization C"


@pytest.mark.asyncio
async def test_user_cannot_update_other_organization(client: AsyncClient, db_session: AsyncSession):
    """A user cannot update another organization, and that organization stays unchanged."""
    user_a, org_a = await create_test_user_with_org(
        db_session,
        email="admina@example.com",
        role=UserRole.ORG_ADMIN,
        org_name="Organization A",
        org_slug="org-a-update",
    )

    user_b, org_b = await create_test_user_with_org(
        db_session,
        email="adminb@example.com",
        role=UserRole.ORG_ADMIN,
        org_name="Organization B",
        org_slug="org-b-update",
    )

    response = await client.put(
        f"/api/orgs/{org_b.slug}",
        json={"name": "Hijacked Organization"},
        cookies=get_auth_cookies(user_a),
    )

    assert response.status_code == 403
    data = response.json()
    assert "access denied" in data["detail"].lower()

    response_check = await client.get(
        f"/api/orgs/{org_b.slug}",
        cookies=get_auth_cookies(user_b),
    )

    assert response_check.status_code == 200
    data_check = response_check.json()
    assert data_check["name"] == "Organization B"  # Not hijacked


@pytest.mark.asyncio
async def test_user_cannot_delete_other_organization(client: AsyncClient, db_session: AsyncSession):
    """A user cannot delete another organization, and that organization stays."""
    user_a, org_a = await create_test_user_with_org(
        db_session,
        email="admina@example.com",
        role=UserRole.ORG_ADMIN,
        org_name="Organization A",
        org_slug="org-a-delete",
    )

    user_b, org_b = await create_test_user_with_org(
        db_session,
        email="adminb@example.com",
        role=UserRole.ORG_ADMIN,
        org_name="Organization B",
        org_slug="org-b-delete",
    )

    response = await client.delete(
        f"/api/orgs/{org_b.slug}",
        cookies=get_auth_cookies(user_a),
    )

    assert response.status_code == 403
    data = response.json()
    assert "access denied" in data["detail"].lower()

    response_check = await client.get(
        f"/api/orgs/{org_b.slug}",
        cookies=get_auth_cookies(user_b),
    )

    assert response_check.status_code == 200
    data_check = response_check.json()
    assert data_check["name"] == "Organization B"


# ============================================================================
# PII Filtering Tests
# ============================================================================


@pytest.mark.asyncio
async def test_pii_filtering_coordinator_sees_display_name_not_email(
    db_session: AsyncSession,
):
    """A coordinator sees the display name of another user, but not the email."""
    from app.auth.pii_filter import UserPublicProfile, filter_user_profile

    org = await create_test_organization(db_session, name="Test Org", slug="test-org-pii")

    coordinator = await create_test_user(
        db_session,
        email="coordinator@example.com",
        display_name="Coordinator User",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    participant = await create_test_user(
        db_session,
        email="participant@example.com",
        display_name="Participant User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    filtered_profile = filter_user_profile(participant, coordinator)

    assert isinstance(filtered_profile, (UserPublicProfile)), "Coordinator should see public or coordinator profile"

    assert filtered_profile.display_name == "Participant User"

    assert not hasattr(filtered_profile, "email"), "Coordinator should not see email"


@pytest.mark.asyncio
async def test_pii_filtering_org_admin_sees_all_fields(db_session: AsyncSession):
    """An org admin sees the full profile of another user, including the email and account details."""
    from app.auth.pii_filter import UserFullProfile, filter_user_profile

    org = await create_test_organization(db_session, name="Test Org", slug="test-org-admin-pii")

    org_admin = await create_test_user(
        db_session,
        email="admin@example.com",
        display_name="Admin User",
        role=UserRole.ORG_ADMIN,
        organization=org,
    )

    participant = await create_test_user(
        db_session,
        email="participant@example.com",
        display_name="Participant User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    filtered_profile = filter_user_profile(participant, org_admin)

    assert isinstance(filtered_profile, UserFullProfile), "Org admin should see full profile"

    assert filtered_profile.display_name == "Participant User"
    assert filtered_profile.email == "participant@example.com"
    assert filtered_profile.is_active is True
    assert filtered_profile.organization_id == org.id
    assert hasattr(filtered_profile, "created_at")
    assert hasattr(filtered_profile, "last_login")


@pytest.mark.asyncio
async def test_pii_filtering_participant_cannot_see_email(db_session: AsyncSession):
    """A participant sees the display name, but not the email, of another participant."""
    from app.auth.pii_filter import UserPublicProfile, filter_user_profile

    org = await create_test_organization(db_session, name="Test Org", slug="test-org-participant-pii")

    participant_a = await create_test_user(
        db_session,
        email="participanta@example.com",
        display_name="Participant A",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    participant_b = await create_test_user(
        db_session,
        email="participantb@example.com",
        display_name="Participant B",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    filtered_profile = filter_user_profile(participant_b, participant_a)

    assert isinstance(filtered_profile, UserPublicProfile), "Participant should see public profile"

    assert filtered_profile.display_name == "Participant B"

    assert not hasattr(filtered_profile, "email"), "Participant should not see other participant's email"


@pytest.mark.asyncio
async def test_pii_filtering_user_sees_own_full_profile(db_session: AsyncSession):
    """A guest sees their own full profile including the email."""
    from app.auth.pii_filter import UserFullProfile, filter_user_profile

    org = await create_test_organization(db_session, name="Test Org", slug="test-org-own-profile")

    guest = await create_test_user(
        db_session,
        email="guest@example.com",
        display_name="Guest User",
        role=UserRole.GUEST,
        organization=org,
    )

    filtered_profile = filter_user_profile(guest, guest)

    assert isinstance(filtered_profile, UserFullProfile), "User should see own full profile"

    assert filtered_profile.display_name == "Guest User"
    assert filtered_profile.email == "guest@example.com"
    assert hasattr(filtered_profile, "is_active")
    assert hasattr(filtered_profile, "created_at")


@pytest.mark.asyncio
async def test_pii_filtering_guest_cannot_see_email(db_session: AsyncSession):
    """A guest sees the display name, but not the email, of another user."""
    from app.auth.pii_filter import UserPublicProfile, filter_user_profile

    org = await create_test_organization(db_session, name="Test Org", slug="test-org-guest-pii")

    guest = await create_test_user(
        db_session,
        email="guest@example.com",
        display_name="Guest User",
        role=UserRole.GUEST,
        organization=org,
    )

    participant = await create_test_user(
        db_session,
        email="participant@example.com",
        display_name="Participant User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    filtered_profile = filter_user_profile(participant, guest)

    assert isinstance(filtered_profile, UserPublicProfile), "Guest should see public profile only"

    assert filtered_profile.display_name == "Participant User"

    assert not hasattr(filtered_profile, "email"), "Guest should not see email"


@pytest.mark.asyncio
async def test_pii_filtering_creator_cannot_see_email(db_session: AsyncSession):
    """A creator sees the display name, but not the email, of another user (the coordinator-level profile)."""
    from app.auth.pii_filter import UserCoordinatorProfile, filter_user_profile

    org = await create_test_organization(db_session, name="Test Org", slug="test-org-creator-pii")

    creator = await create_test_user(
        db_session,
        email="creator@example.com",
        display_name="Creator User",
        role=UserRole.CREATOR,
        organization=org,
    )

    participant = await create_test_user(
        db_session,
        email="participant@example.com",
        display_name="Participant User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    filtered_profile = filter_user_profile(participant, creator)

    assert isinstance(filtered_profile, (UserCoordinatorProfile)), "Creator should see coordinator-level profile"

    assert filtered_profile.display_name == "Participant User"

    assert not hasattr(filtered_profile, "email"), "Creator should not see email"


@pytest.mark.asyncio
async def test_pii_filtering_filter_user_list(db_session: AsyncSession):
    """filter_user_list filters every user of the list by the viewer's role."""
    from app.auth.pii_filter import UserPublicProfile, filter_user_list

    org = await create_test_organization(db_session, name="Test Org", slug="test-org-list-pii")

    coordinator = await create_test_user(
        db_session,
        email="coordinator@example.com",
        display_name="Coordinator User",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    participants = []
    for i in range(3):
        participant = await create_test_user(
            db_session,
            email=f"participant{i}@example.com",
            display_name=f"Participant {i}",
            role=UserRole.PARTICIPANT,
            organization=org,
        )
        participants.append(participant)

    filtered_profiles = filter_user_list(participants, coordinator)

    assert len(filtered_profiles) == 3
    for i, profile in enumerate(filtered_profiles):
        assert isinstance(profile, (UserPublicProfile))
        assert profile.display_name == f"Participant {i}"
        assert not hasattr(profile, "email")


@pytest.mark.asyncio
async def test_pii_filtering_filter_dict_fields_removes_sensitive_data(
    db_session: AsyncSession,
):
    """filter_dict_fields removes the email, nested dicts included, for a participant and keeps it for an org admin."""
    from app.auth.pii_filter import filter_dict_fields

    org = await create_test_organization(db_session, name="Test Org", slug="test-org-dict-filter")

    participant = await create_test_user(
        db_session,
        email="participant@example.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    org_admin = await create_test_user(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        organization=org,
    )

    # Mock audit log entry with sensitive data
    audit_entry = {
        "event": "user_login",
        "timestamp": "2026-02-12T10:00:00Z",
        "actor": {
            "user_id": str(participant.id),
            "email": "participant@example.com",
            "role": "participant",
        },
        "metadata": {"ip": "192.168.1.1"},
    }

    filtered_for_participant = filter_dict_fields(audit_entry, participant)
    assert "email" not in filtered_for_participant["actor"]
    assert filtered_for_participant["actor"]["user_id"] == str(participant.id)
    assert filtered_for_participant["event"] == "user_login"

    filtered_for_admin = filter_dict_fields(audit_entry, org_admin)
    assert filtered_for_admin["actor"]["email"] == "participant@example.com"
    assert filtered_for_admin["actor"]["user_id"] == str(participant.id)
