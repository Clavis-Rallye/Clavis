"""
Integration tests for user management endpoints: list with filters, create, read,
update, deactivate, delete and anonymize, and the isolation between organizations.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import (
    create_multiple_users,
    create_org_admin_user,
    create_test_organization,
    create_test_user,
    create_test_user_with_org,
    get_auth_headers,
)

# ============================================================================
# GET /users/ - List users
# ============================================================================


@pytest.mark.asyncio
async def test_list_users_as_org_admin(client: AsyncClient, db_session: AsyncSession):
    """An org_admin sees every user of the org, paginated."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org",
    )

    await create_multiple_users(
        db_session,
        organization=org,
        count=3,
        role=UserRole.PARTICIPANT,
    )

    admin_headers = get_auth_headers(admin)

    response = await client.get(
        f"/api/orgs/{org.slug}/users/",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()

    # 4 users total: admin + 3 participants
    assert data["total"] == 4
    assert len(data["items"]) == 4
    assert data["skip"] == 0
    assert data["limit"] == 50


@pytest.mark.asyncio
async def test_list_users_as_coordinator_returns_403(client: AsyncClient, db_session: AsyncSession):
    """A coordinator cannot list users."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-coord",
    )

    coordinator = await create_test_user(
        db_session,
        email="coordinator@example.com",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    coordinator_headers = get_auth_headers(coordinator)

    response = await client.get(
        f"/api/orgs/{org.slug}/users/",
        headers=coordinator_headers,
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_users_with_role_filter(client: AsyncClient, db_session: AsyncSession):
    """The role filter returns only users with that role and counts only them."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-role",
    )

    await create_test_user(
        db_session,
        email="participant1@example.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    await create_test_user(
        db_session,
        email="participant2@example.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    await create_test_user(
        db_session,
        email="coordinator@example.com",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    admin_headers = get_auth_headers(admin)

    response = await client.get(
        f"/api/orgs/{org.slug}/users/?role=participant",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert all(item["role"] == "participant" for item in data["items"])


@pytest.mark.asyncio
async def test_list_users_with_active_filter(client: AsyncClient, db_session: AsyncSession):
    """The active filter returns only active users."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-active",
    )

    await create_test_user(
        db_session,
        email="active@example.com",
        role=UserRole.PARTICIPANT,
        organization=org,
        is_active=True,
    )

    await create_test_user(
        db_session,
        email="inactive@example.com",
        role=UserRole.PARTICIPANT,
        organization=org,
        is_active=False,
    )

    admin_headers = get_auth_headers(admin)

    response = await client.get(
        f"/api/orgs/{org.slug}/users/?is_active=true",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()

    # admin + 1 active participant
    assert data["total"] == 2
    assert all(item["is_active"] is True for item in data["items"])


# ============================================================================
# POST /users/ - Create user
# ============================================================================


@pytest.mark.asyncio
async def test_create_user_with_valid_data(client: AsyncClient, db_session: AsyncSession):
    """A valid payload creates the user and returns its profile."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-create",
    )

    admin_headers = get_auth_headers(admin)

    response = await client.post(
        f"/api/orgs/{org.slug}/users/",
        headers=admin_headers,
        json={
            "email": "newuser@example.com",
            "display_name": "New User",
            "password": "SecurePassword123!",
            "role": "participant",
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert data["email"] == "newuser@example.com"
    assert data["display_name"] == "New User"
    assert data["role"] == "participant"
    assert data["is_active"] is True
    assert data["totp_enabled"] is False
    assert "id" in data
    UUID(data["id"])


@pytest.mark.asyncio
async def test_create_user_with_duplicate_email_returns_409(client: AsyncClient, db_session: AsyncSession):
    """A duplicate email is refused with 409 and the code EMAIL_BELONGS_TO_USER."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-dup",
    )

    await create_test_user(
        db_session,
        email="existing@example.com",
        organization=org,
    )

    admin_headers = get_auth_headers(admin)

    response = await client.post(
        f"/api/orgs/{org.slug}/users/",
        headers=admin_headers,
        json={
            "email": "existing@example.com",
            "display_name": "Duplicate User",
            "password": "SecurePassword123!",
            "role": "participant",
        },
    )

    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]
    assert response.json()["error_code"] == "EMAIL_BELONGS_TO_USER"


@pytest.mark.asyncio
async def test_create_user_with_soft_deleted_email_returns_409_with_restore_hint(
    client: AsyncClient, db_session: AsyncSession
):
    """A soft-deleted account still owns its address.

    ``ix_users_email_not_null`` covers deleted rows too, so without the wider
    lookup the INSERT fails on the unique index with a 500 instead of a
    readable 409.
    """
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin-softdel@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-softdel",
    )
    deleted_user = await create_test_user(
        db_session,
        email="ausgeschieden@example.com",
        organization=org,
    )
    deleted_user.is_deleted = True
    deleted_user.deleted_at = datetime.now(UTC)
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/users/",
        headers=get_auth_headers(admin),
        json={
            "email": "ausgeschieden@example.com",
            "display_name": "New User",
            "password": "SecurePassword123!",
            "role": "participant",
        },
    )

    assert response.status_code == 409
    assert "deleted account" in response.json()["detail"]
    # The code lets the dialog explain it in German.
    assert response.json()["error_code"] == "EMAIL_BELONGS_TO_DELETED_USER"


@pytest.mark.asyncio
async def test_create_user_with_weak_password_returns_400(client: AsyncClient, db_session: AsyncSession):
    """A password that breaks the policy is refused with 400."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-weak",
    )

    admin_headers = get_auth_headers(admin)

    response = await client.post(
        f"/api/orgs/{org.slug}/users/",
        headers=admin_headers,
        json={
            "email": "newuser@example.com",
            "display_name": "New User",
            "password": "weakpassword",  # Weak: no uppercase, no special char
            "role": "participant",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_create_user_as_org_admin_role_allowed(client: AsyncClient, db_session: AsyncSession):
    """An org_admin can create another org_admin."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-admin-role",
    )

    admin_headers = get_auth_headers(admin)

    response = await client.post(
        f"/api/orgs/{org.slug}/users/",
        headers=admin_headers,
        json={
            "email": "newadmin@example.com",
            "display_name": "New Admin",
            "password": "SecurePassword123!",
            "role": "org_admin",
        },
    )

    assert response.status_code == 201
    assert response.json()["role"] == "org_admin"


@pytest.mark.asyncio
async def test_create_user_as_guest_role_rejected(client: AsyncClient, db_session: AsyncSession):
    """A user cannot be created with the guest role."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-guest-role",
    )

    admin_headers = get_auth_headers(admin)

    response = await client.post(
        f"/api/orgs/{org.slug}/users/",
        headers=admin_headers,
        json={
            "email": "guest@example.com",
            "display_name": "Guest User",
            "password": "SecurePassword123!",
            "role": "guest",
        },
    )

    # The schema pattern allows participant, coordinator, creator and org_admin, so guest is rejected.
    assert response.status_code == 422


# ============================================================================
# GET /users/{user_id} - Get user details
# ============================================================================


@pytest.mark.asyncio
async def test_get_user_details(client: AsyncClient, db_session: AsyncSession):
    """An org_admin reads the full profile of a user of the org."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-get",
    )

    user = await create_test_user(
        db_session,
        email="user@example.com",
        display_name="Test User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    admin_headers = get_auth_headers(admin)

    response = await client.get(
        f"/api/orgs/{org.slug}/users/{user.id}",
        headers=admin_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == str(user.id)
    assert data["email"] == "user@example.com"
    assert data["display_name"] == "Test User"
    assert data["role"] == "participant"
    assert data["is_active"] is True
    assert data["is_guest"] is False
    assert data["organization_id"] == str(org.id)
    assert "created_at" in data
    assert "updated_at" in data


# ============================================================================
# PUT /users/{user_id} - Update user
# ============================================================================


@pytest.mark.asyncio
async def test_update_user_role(client: AsyncClient, db_session: AsyncSession):
    """An org_admin can change a user's role."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-update",
    )

    user = await create_test_user(
        db_session,
        email="user@example.com",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    admin_headers = get_auth_headers(admin)

    response = await client.put(
        f"/api/orgs/{org.slug}/users/{user.id}",
        headers=admin_headers,
        json={
            "role": "creator",
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["role"] == "creator"


@pytest.mark.asyncio
async def test_update_user_to_org_admin_allowed(client: AsyncClient, db_session: AsyncSession):
    """An org_admin can promote a user to org_admin by editing the user."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-promote",
    )
    user = await create_test_user(
        db_session,
        email="user@example.com",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    response = await client.put(
        f"/api/orgs/{org.slug}/users/{user.id}",
        headers=get_auth_headers(admin),
        json={"role": "org_admin"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "org_admin"


@pytest.mark.asyncio
async def test_update_org_admin_keeps_role_when_role_unchanged(client: AsyncClient, db_session: AsyncSession):
    """Editing another org_admin's details with the unchanged role leaves the role intact.

    The edit dialog sends the role on every save, so a PUT that carries the unchanged role must not change the user's rights.
    """
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-peer-edit",
    )
    peer = await create_test_user(
        db_session,
        email="peer@example.com",
        role=UserRole.ORG_ADMIN,
        organization=org,
    )

    response = await client.put(
        f"/api/orgs/{org.slug}/users/{peer.id}",
        headers=get_auth_headers(admin),
        json={"display_name": "Renamed Peer", "is_active": True, "role": "org_admin"},
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Renamed Peer"
    assert response.json()["role"] == "org_admin"


@pytest.mark.asyncio
async def test_cannot_change_own_role(client: AsyncClient, db_session: AsyncSession):
    """An org_admin cannot change their own role, which prevents self-demotion."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-self-role",
    )

    admin_headers = get_auth_headers(admin)

    response = await client.put(
        f"/api/orgs/{org.slug}/users/{admin.id}",
        headers=admin_headers,
        json={
            "role": "participant",
        },
    )

    assert response.status_code == 422
    assert "own role" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_deactivate_user(client: AsyncClient, db_session: AsyncSession):
    """An org_admin can deactivate a user."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="test-org-deactivate",
    )

    user = await create_test_user(
        db_session,
        email="user@example.com",
        role=UserRole.PARTICIPANT,
        organization=org,
        is_active=True,
    )

    admin_headers = get_auth_headers(admin)

    response = await client.put(
        f"/api/orgs/{org.slug}/users/{user.id}",
        headers=admin_headers,
        json={
            "is_active": False,
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["is_active"] is False


# ============================================================================
# DELETE /users/{user_id} - Delete/anonymize user
# ============================================================================


@pytest.mark.asyncio
async def test_delete_user_returns_204(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An org_admin can delete a user."""
    admin, org = await create_org_admin_user(
        db_session,
        email="admin@example.com",
        org_slug="test-delete-org",
    )
    target_user = await create_test_user(
        db_session,
        email="target@example.com",
        display_name="Target User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    headers = get_auth_headers(admin)
    response = await client.delete(
        f"/api/orgs/{org.slug}/users/{target_user.id}",
        headers=headers,
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_user_anonymizes_data(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Deleting with mode=immediate anonymizes the personal data right away."""
    admin, org = await create_org_admin_user(
        db_session,
        email="admin@example.com",
        org_slug="test-anon-org",
    )
    target_user = await create_test_user(
        db_session,
        email="target@example.com",
        display_name="Target User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    target_user_id = target_user.id

    headers = get_auth_headers(admin)
    response = await client.delete(
        f"/api/orgs/{org.slug}/users/{target_user_id}?mode=immediate",
        headers=headers,
    )
    assert response.status_code == 204

    await db_session.refresh(target_user)

    assert target_user.email == f"deleted-user-{target_user_id}@anonymized.local"
    assert target_user.display_name == "Deleted User"
    assert target_user.is_active is False
    assert target_user.is_deleted is True
    assert target_user.password_hash == ""
    assert target_user.totp_secret is None


@pytest.mark.asyncio
async def test_delete_self_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An org_admin cannot delete themselves."""
    admin, org = await create_org_admin_user(
        db_session,
        email="admin@example.com",
        org_slug="test-self-delete-org",
    )

    headers = get_auth_headers(admin)
    response = await client.delete(
        f"/api/orgs/{org.slug}/users/{admin.id}",
        headers=headers,
    )

    assert response.status_code == 422
    data = response.json()
    assert "Cannot delete yourself" in data["detail"]


@pytest.mark.asyncio
async def test_delete_nonexistent_user_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Deleting an unknown user answers 404."""
    admin, org = await create_org_admin_user(
        db_session,
        email="admin@example.com",
        org_slug="test-notfound-org",
    )

    headers = get_auth_headers(admin)
    response = await client.delete(
        f"/api/orgs/{org.slug}/users/00000000-0000-0000-0000-000000000000",
        headers=headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_user_cross_org_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An org_admin cannot delete a user of another org."""
    admin, org_a = await create_org_admin_user(
        db_session,
        email="admin-a@example.com",
        org_slug="org-a-delete",
    )

    org_b = await create_test_organization(
        db_session,
        name="Org B",
        slug="org-b-delete",
    )
    target_user = await create_test_user(
        db_session,
        email="target-b@example.com",
        display_name="Target User B",
        role=UserRole.PARTICIPANT,
        organization=org_b,
    )

    headers = get_auth_headers(admin)
    response = await client.delete(
        f"/api/orgs/{org_a.slug}/users/{target_user.id}",
        headers=headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_user_requires_org_admin(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A coordinator cannot delete a user."""
    org = await create_test_organization(
        db_session,
        name="Test Org",
        slug="test-perm-org",
    )
    coordinator = await create_test_user(
        db_session,
        email="coordinator@example.com",
        display_name="Coordinator",
        role=UserRole.COORDINATOR,
        organization=org,
    )
    target_user = await create_test_user(
        db_session,
        email="target@example.com",
        display_name="Target User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    headers = get_auth_headers(coordinator)
    response = await client.delete(
        f"/api/orgs/{org.slug}/users/{target_user.id}",
        headers=headers,
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_user_without_auth_returns_401(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A delete request without a session answers 401."""
    admin, org = await create_org_admin_user(
        db_session,
        email="admin@example.com",
        org_slug="test-noauth-org",
    )
    target_user = await create_test_user(
        db_session,
        email="target@example.com",
        display_name="Target User",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    response = await client.delete(
        f"/api/orgs/{org.slug}/users/{target_user.id}",
    )

    assert response.status_code == 401


# ============================================================================
# Cross-org access prevention
# ============================================================================


@pytest.mark.asyncio
async def test_cross_org_access_prevented(client: AsyncClient, db_session: AsyncSession):
    """A user of another org answers 404, not 403, so the existence of the user does not leak."""
    admin_a, org_a = await create_test_user_with_org(
        db_session,
        email="admin_a@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="org-a-cross",
        org_name="Organization A",
    )

    admin_b, org_b = await create_test_user_with_org(
        db_session,
        email="admin_b@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="org-b-cross",
        org_name="Organization B",
    )

    user_in_b = await create_test_user(
        db_session,
        email="user_b@example.com",
        role=UserRole.PARTICIPANT,
        organization=org_b,
    )

    admin_a_headers = get_auth_headers(admin_a)

    response = await client.get(
        f"/api/orgs/{org_a.slug}/users/{user_in_b.id}",
        headers=admin_a_headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_org_list_returns_only_own_users(client: AsyncClient, db_session: AsyncSession):
    """The list shows only the users of the authenticated org."""
    admin_a, org_a = await create_test_user_with_org(
        db_session,
        email="admin_a@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="org-a-list",
        org_name="Organization A",
    )
    await create_test_user(
        db_session,
        email="user_a@example.com",
        role=UserRole.PARTICIPANT,
        organization=org_a,
    )

    await create_test_user_with_org(
        db_session,
        email="admin_b@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="org-b-list",
        org_name="Organization B",
    )

    admin_a_headers = get_auth_headers(admin_a)

    response = await client.get(
        f"/api/orgs/{org_a.slug}/users/",
        headers=admin_a_headers,
    )

    assert response.status_code == 200
    data = response.json()

    # 2 users: admin_a + user_a
    assert data["total"] == 2

    emails = [item["email"] for item in data["items"]]
    assert "admin_a@example.com" in emails
    assert "user_a@example.com" in emails
    assert "admin_b@example.com" not in emails


@pytest.mark.asyncio
async def test_update_user_email(client: AsyncClient, db_session: AsyncSession):
    """An admin can edit a user's email, and it is stored normalized."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin@example.com", role=UserRole.ORG_ADMIN, org_slug="test-org-email"
    )
    user = await create_test_user(db_session, email="old@example.com", role=UserRole.PARTICIPANT, organization=org)

    resp = await client.put(
        f"/api/orgs/{org.slug}/users/{user.id}",
        headers=get_auth_headers(admin),
        json={"email": "New@Example.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "new@example.com"  # normalized lowercase


@pytest.mark.asyncio
async def test_update_user_email_duplicate_rejected(client: AsyncClient, db_session: AsyncSession):
    """Changing a user's email to one already in use is refused with 409."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin@example.com", role=UserRole.ORG_ADMIN, org_slug="test-org-dupemail"
    )
    await create_test_user(db_session, email="taken@example.com", role=UserRole.PARTICIPANT, organization=org)
    user_b = await create_test_user(db_session, email="userb@example.com", role=UserRole.PARTICIPANT, organization=org)

    resp = await client.put(
        f"/api/orgs/{org.slug}/users/{user_b.id}",
        headers=get_auth_headers(admin),
        json={"email": "taken@example.com"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_user_email_to_soft_deleted_returns_409_with_restore_hint(
    client: AsyncClient, db_session: AsyncSession
):
    """Changing a user's email to a soft-deleted address answers 409, not 500."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin@example.com", role=UserRole.ORG_ADMIN, org_slug="test-org-dupemail-softdel"
    )
    deleted_user = await create_test_user(db_session, email="ausgeschieden@example.com", organization=org)
    deleted_user.is_deleted = True
    deleted_user.deleted_at = datetime.now(UTC)
    await db_session.commit()
    user_b = await create_test_user(db_session, email="userb2@example.com", role=UserRole.PARTICIPANT, organization=org)

    resp = await client.put(
        f"/api/orgs/{org.slug}/users/{user_b.id}",
        headers=get_auth_headers(admin),
        json={"email": "ausgeschieden@example.com"},
    )

    assert resp.status_code == 409
    assert "deleted account" in resp.json()["detail"]
    assert resp.json()["error_code"] == "EMAIL_BELONGS_TO_DELETED_USER"


@pytest.mark.asyncio
async def test_cannot_edit_guest_role_or_email(client: AsyncClient, db_session: AsyncSession):
    """An admin cannot change a guest's role or email, because guests upgrade themselves."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin@example.com", role=UserRole.ORG_ADMIN, org_slug="test-org-guestlock"
    )
    guest = await create_test_user(db_session, email="guest@example.com", role=UserRole.GUEST, organization=org)
    headers = get_auth_headers(admin)

    role_resp = await client.put(
        f"/api/orgs/{org.slug}/users/{guest.id}", headers=headers, json={"role": "coordinator"}
    )
    assert role_resp.status_code == 422

    email_resp = await client.put(
        f"/api/orgs/{org.slug}/users/{guest.id}", headers=headers, json={"email": "new@example.com"}
    )
    assert email_resp.status_code == 422

    # A non-role/email change (display_name) on a guest is still allowed.
    ok_resp = await client.put(
        f"/api/orgs/{org.slug}/users/{guest.id}", headers=headers, json={"display_name": "Renamed Guest"}
    )
    assert ok_resp.status_code == 200
