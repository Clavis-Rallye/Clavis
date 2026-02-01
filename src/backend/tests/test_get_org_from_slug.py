"""
The ``get_org_from_slug`` dependency: resolution of an own organization, 404 for an unknown
or soft-deleted one, 403 for a foreign one.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import create_test_organization, create_test_user


@pytest.mark.asyncio
async def test_get_org_from_slug_success(client: AsyncClient, db_session: AsyncSession):
    """A member resolves the organization of their own slug."""
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
async def test_get_org_from_slug_not_found(client: AsyncClient, db_session: AsyncSession):
    """An unknown slug answers 404 with the message that the organization is not found."""
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
async def test_get_org_from_slug_wrong_org(client: AsyncClient, db_session: AsyncSession):
    """A user of another organization is denied with 403, so tenants stay apart."""
    from fastapi import HTTPException

    org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
    # Org B exists, but user A does not belong to it.
    await create_test_organization(db_session, name="Org B", slug="org-b")

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
async def test_get_org_from_slug_soft_deleted_org(client: AsyncClient, db_session: AsyncSession):
    """A soft-deleted organization answers 404 as if it did not exist."""

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
