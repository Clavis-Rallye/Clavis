"""Checks that the test infrastructure (conftest.py and factories.py) works."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.user import User, UserRole
from tests.factories import (
    create_multiple_users,
    create_org_admin_user,
    create_test_organization,
    create_test_user,
    create_test_user_with_org,
    create_user_with_2fa,
    get_auth_cookies,
    get_auth_headers,
)


@pytest.mark.integration
async def test_db_session_fixture(clean_db: AsyncSession) -> None:
    """The ``clean_db`` fixture provides a working session."""
    assert clean_db is not None
    assert isinstance(clean_db, AsyncSession)


@pytest.mark.integration
async def test_http_client_fixture(client: AsyncClient) -> None:
    """The ``client`` fixture provides a working HTTP client."""
    assert client is not None
    assert isinstance(client, AsyncClient)


@pytest.mark.integration
async def test_create_test_organization(clean_db: AsyncSession) -> None:
    """The factory creates an organization with the default language and empty settings."""
    org = await create_test_organization(
        clean_db,
        name="Test Org",
        slug="test-org",
    )

    assert org is not None
    assert isinstance(org, Organization)
    assert org.id is not None
    assert org.name == "Test Org"
    assert org.slug == "test-org"
    assert org.languages == ["de"]
    assert org.settings == {}


@pytest.mark.integration
async def test_create_test_user(clean_db: AsyncSession) -> None:
    """The factory creates a user with a hashed password and an organization."""
    user = await create_test_user(
        clean_db,
        email="test@example.com",
        password="TestPassword123!",
        display_name="Test User",
        role=UserRole.PARTICIPANT,
    )

    assert user is not None
    assert isinstance(user, User)
    assert user.id is not None
    assert user.email == "test@example.com"
    assert user.display_name == "Test User"
    assert user.role == UserRole.PARTICIPANT
    assert user.password_hash is not None
    assert user.password_hash != "TestPassword123!"
    assert user.organization_id is not None
    assert user.organization is not None


@pytest.mark.integration
async def test_create_test_user_with_org(clean_db: AsyncSession) -> None:
    """The factory creates a user together with its organization."""
    user, org = await create_test_user_with_org(
        clean_db,
        email="user@company.com",
        role=UserRole.COORDINATOR,
        org_name="Company Inc",
        org_slug="company",
    )

    assert user is not None
    assert org is not None
    assert user.organization_id == org.id
    assert org.name == "Company Inc"
    assert org.slug == "company"
    assert user.email == "user@company.com"
    assert user.role == UserRole.COORDINATOR


@pytest.mark.integration
async def test_create_multiple_users(clean_db: AsyncSession) -> None:
    """The factory creates several users in one organization, numbered from 1."""
    org = await create_test_organization(clean_db, slug="multi-user-org")
    users = await create_multiple_users(
        clean_db,
        organization=org,
        count=5,
        role=UserRole.PARTICIPANT,
        email_prefix="participant",
    )

    assert len(users) == 5
    for i, user in enumerate(users, 1):
        assert user.email == f"participant{i}@example.com"
        assert user.display_name == f"User {i}"
        assert user.role == UserRole.PARTICIPANT
        assert user.organization_id == org.id


@pytest.mark.integration
async def test_create_user_with_2fa(clean_db: AsyncSession) -> None:
    """The factory creates a user with the second factor enabled and eight recovery codes."""
    user, totp_secret = await create_user_with_2fa(
        clean_db,
        email="2fa@example.com",
    )

    assert user.totp_enabled is True
    assert user.totp_secret == totp_secret
    assert user.totp_secret is not None
    assert len(user.totp_secret) > 0
    assert user.totp_recovery_codes is not None
    assert "codes" in user.totp_recovery_codes
    assert len(user.totp_recovery_codes["codes"]) == 8


@pytest.mark.integration
async def test_create_org_admin_user(clean_db: AsyncSession) -> None:
    """The factory creates an organization admin together with its organization."""
    admin, org = await create_org_admin_user(
        clean_db,
        email="admin@org.com",
        org_name="Admin Org",
        org_slug="admin-org",
    )

    assert admin.role == UserRole.ORG_ADMIN
    assert admin.organization_id == org.id
    assert org.name == "Admin Org"


@pytest.mark.integration
async def test_get_auth_headers(clean_db: AsyncSession) -> None:
    """The auth headers carry a bearer token."""
    user = await create_test_user(clean_db, email="auth@example.com")
    headers = get_auth_headers(user)

    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Bearer ")
    token = headers["Authorization"].split(" ")[1]
    assert len(token) > 0


@pytest.mark.integration
async def test_get_auth_cookies(clean_db: AsyncSession) -> None:
    """The auth cookies carry an access token."""
    user = await create_test_user(clean_db, email="cookie@example.com")
    cookies = get_auth_cookies(user)

    assert "access_token" in cookies
    assert len(cookies["access_token"]) > 0


@pytest.mark.integration
async def test_auth_with_client(client: AsyncClient, db_session: AsyncSession) -> None:
    """The auth headers of a factory user are accepted by the API."""
    user = await create_test_user(
        db_session,
        email="client-test@example.com",
        role=UserRole.ORG_ADMIN,
    )

    headers = get_auth_headers(user)

    # The factory derives the organization slug from the email prefix.
    response = await client.get("/api/orgs/org-client-test", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "name" in data


@pytest.mark.integration
async def test_organization_isolation(clean_db: AsyncSession) -> None:
    """Factory users of different organizations belong to different organizations."""
    user1, org1 = await create_test_user_with_org(
        clean_db,
        email="user1@org1.com",
        org_slug="org-1",
    )

    user2, org2 = await create_test_user_with_org(
        clean_db,
        email="user2@org2.com",
        org_slug="org-2",
    )

    assert user1.organization_id != user2.organization_id
    assert org1.id != org2.id
