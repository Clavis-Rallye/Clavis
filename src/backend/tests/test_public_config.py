"""GET /api/public/config: instance configuration for unauthenticated callers.

The endpoint answers the two questions the frontend has before any authentication.
Which brand name does this instance run under? May the one-time organization bootstrap still run,
and is there exactly one organization whose picker step can be skipped?
Every test calls the endpoint anonymously. The answers cover the three states of the organization
table: empty, exactly one, more than one.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.organization import OrganizationRepository
from tests.factories import create_test_organization


@pytest.mark.asyncio
async def test_config_on_an_empty_instance(client: AsyncClient, db_session: AsyncSession):
    resp = await client.get("/api/public/config")
    assert resp.status_code == 200
    data = resp.json()

    assert set(data.keys()) == {"brand_name", "bootstrap_open", "single_org_slug", "qa_feedback"}
    # The feedback module is off unless QA_FEEDBACK_ENABLED says otherwise.
    assert data["qa_feedback"] is False
    # BRAND_NAME unset gives the default brand.
    assert data["brand_name"] == "Clavis"
    assert data["bootstrap_open"] is True
    assert data["single_org_slug"] is None


@pytest.mark.asyncio
async def test_config_on_a_single_organization_instance(client: AsyncClient, db_session: AsyncSession):
    await create_test_organization(db_session, name="Solo Org", slug="solo-org")

    resp = await client.get("/api/public/config")
    assert resp.status_code == 200
    data = resp.json()

    assert data["brand_name"] == "Clavis"
    assert data["bootstrap_open"] is False
    assert data["single_org_slug"] == "solo-org"


@pytest.mark.asyncio
async def test_config_on_a_multi_organization_instance(client: AsyncClient, db_session: AsyncSession):
    await create_test_organization(db_session, name="Org One", slug="org-one")
    await create_test_organization(db_session, name="Org Two", slug="org-two")

    resp = await client.get("/api/public/config")
    assert resp.status_code == 200
    data = resp.json()

    assert data["brand_name"] == "Clavis"
    assert data["bootstrap_open"] is False
    assert data["single_org_slug"] is None


@pytest.mark.asyncio
async def test_config_reports_open_bootstrap_with_the_operator_flag(
    client: AsyncClient, db_session: AsyncSession, open_org_bootstrap
):
    """`allow_unlimited_org_registrations` re-opens bootstrap even with an
    organization present, the same rule the register endpoint enforces."""
    await create_test_organization(db_session, name="Open Org", slug="open-org")

    resp = await client.get("/api/public/config")
    assert resp.status_code == 200
    data = resp.json()

    assert data["bootstrap_open"] is True
    assert data["single_org_slug"] == "open-org"


@pytest.mark.asyncio
async def test_config_ignores_soft_deleted_organizations(client: AsyncClient, db_session: AsyncSession):
    """A soft-deleted organization counts for neither bootstrap nor the slug,
    mirroring the register endpoint's gate (`org_repo.count()`)."""
    org = await create_test_organization(db_session, name="Doomed Org", slug="doomed-org")
    await OrganizationRepository(db_session).soft_delete(org.id)
    await db_session.commit()

    resp = await client.get("/api/public/config")
    assert resp.status_code == 200
    data = resp.json()

    assert data["bootstrap_open"] is True
    assert data["single_org_slug"] is None


@pytest.mark.asyncio
async def test_config_rate_limited_like_the_other_public_endpoints(client: AsyncClient, db_session: AsyncSession):
    for i in range(60):
        resp = await client.get("/api/public/config")
        assert resp.status_code == 200, f"Request {i + 1} failed with {resp.status_code}"

    resp = await client.get("/api/public/config")
    assert resp.status_code == 429
