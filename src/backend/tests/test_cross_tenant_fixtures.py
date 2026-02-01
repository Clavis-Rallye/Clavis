"""
Validation test for cross-tenant fixtures.

Checks that the org_a, org_b, user_in_org_a, user_in_org_b,
client_org_a, and client_org_b fixtures work correctly.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_cross_tenant_fixtures_created(
    db_session: AsyncSession,
    org_a: Organization,
    org_b: Organization,
    user_in_org_a: User,
    user_in_org_b: User,
    client_org_a: AsyncClient,
    client_org_b: AsyncClient,
):
    """The fixtures give two separate organizations, an org_admin in each and an authenticated client for each."""
    assert org_a.id != org_b.id
    assert org_a.slug == "org-a"
    assert org_b.slug == "org-b"
    assert org_a.name == "Organization A"
    assert org_b.name == "Organization B"

    assert user_in_org_a.id != user_in_org_b.id
    assert user_in_org_a.email == "admin-a@example.com"
    assert user_in_org_b.email == "admin-b@example.com"
    assert user_in_org_a.role == UserRole.ORG_ADMIN
    assert user_in_org_b.role == UserRole.ORG_ADMIN

    assert user_in_org_a.organization_id == org_a.id
    assert user_in_org_b.organization_id == org_b.id

    assert "Authorization" in client_org_a.headers
    assert "Authorization" in client_org_b.headers
    assert client_org_a.headers["Authorization"].startswith("Bearer ")
    assert client_org_b.headers["Authorization"].startswith("Bearer ")

    assert client_org_a.headers["Authorization"] != client_org_b.headers["Authorization"]
