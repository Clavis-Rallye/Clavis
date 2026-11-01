"""
Who sees which library station, and which rallyes its usage names.

One organization holds a published and an unpublished rallye. Station A is linked only
into the unpublished rallye, station B into both. A participant and a coordinator see
only what a published, non-archived rallye links, at the list and at the detail route,
and the usage names only such rallyes. A creator and an organization admin see the
whole library with the full usage. A guest and a user of another organization are
refused.
"""

from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.rallye import Rallye
from app.models.station import Station
from app.models.user import UserRole
from tests.factories import (
    create_test_organization,
    create_test_rallye,
    create_test_station,
    create_test_user,
    get_auth_headers,
    link_station_to_rallye,
)

READERS = [UserRole.PARTICIPANT, UserRole.COORDINATOR]
EDITORS = [UserRole.CREATOR, UserRole.ORG_ADMIN]


@dataclass
class Library:
    org: Organization
    published: Rallye
    unpublished: Rallye
    station_a: Station
    station_b: Station


async def _library(session: AsyncSession) -> Library:
    org = await create_test_organization(session, name="Visibility Org", slug="visibility-org")
    published = await create_test_rallye(session, org, title={"de": "Veröffentlicht"}, is_published=True)
    unpublished = await create_test_rallye(session, org, title={"de": "Entwurf"}, is_published=False)
    station_a = await create_test_station(session, org, title={"de": "Nur im Entwurf"})
    station_b = await create_test_station(session, org, title={"de": "In beiden"})
    await link_station_to_rallye(session, org, unpublished, station_a, sort_order=0)
    await link_station_to_rallye(session, org, published, station_b, sort_order=0)
    await link_station_to_rallye(session, org, unpublished, station_b, sort_order=1)
    return Library(org, published, unpublished, station_a, station_b)


async def _headers(session: AsyncSession, org: Organization, role: UserRole) -> dict[str, str]:
    user = await create_test_user(session, email=f"{role.value}@example.com", role=role, organization=org)
    return get_auth_headers(user)


async def _list(client: AsyncClient, slug: str, headers: dict[str, str]) -> dict[str, dict]:
    response = await client.get(f"/api/orgs/{slug}/stations/", headers=headers)
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()["items"]}


def _usage_ids(item: dict) -> set[str]:
    return set(item["usage"]["rallye_ids"])


# ============================================================================
# A participant and a coordinator: published rallyes only
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("role", READERS)
async def test_reader_gets_404_for_a_station_only_in_an_unpublished_rallye(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """The detail answers 404 when no published, non-archived rallye links the station."""
    lib = await _library(db_session)
    headers = await _headers(db_session, lib.org, role)

    response = await client.get(f"/api/orgs/{lib.org.slug}/stations/{lib.station_a.id}", headers=headers)
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role", READERS)
async def test_reader_does_not_find_a_station_only_in_an_unpublished_rallye_in_the_list(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """The list and the detail follow the same rule."""
    lib = await _library(db_session)
    headers = await _headers(db_session, lib.org, role)

    items = await _list(client, lib.org.slug, headers)
    assert str(lib.station_a.id) not in items
    assert str(lib.station_b.id) in items


@pytest.mark.asyncio
@pytest.mark.parametrize("role", READERS)
async def test_reader_list_usage_names_only_the_published_rallye(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """In the list, the usage of station B names only the published rallye."""
    lib = await _library(db_session)
    headers = await _headers(db_session, lib.org, role)

    item = (await _list(client, lib.org.slug, headers))[str(lib.station_b.id)]
    assert item["usage"]["rallye_ids"] == [str(lib.published.id)]
    assert item["usage"]["rallye_titles"] == [{"de": "Veröffentlicht"}]
    assert item["usage_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", READERS)
async def test_reader_detail_usage_names_only_the_published_rallye(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """At the detail, the usage of station B names only the published rallye."""
    lib = await _library(db_session)
    headers = await _headers(db_session, lib.org, role)

    response = await client.get(f"/api/orgs/{lib.org.slug}/stations/{lib.station_b.id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usage"]["rallye_ids"] == [str(lib.published.id)]
    assert body["usage"]["rallye_titles"] == [{"de": "Veröffentlicht"}]
    assert body["usage_count"] == 1


# ============================================================================
# A creator and an organization admin: the whole library, as before
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("role", EDITORS)
async def test_editor_gets_a_station_only_in_an_unpublished_rallye(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """A creator and an organization admin read station A at the detail and in the list."""
    lib = await _library(db_session)
    headers = await _headers(db_session, lib.org, role)

    response = await client.get(f"/api/orgs/{lib.org.slug}/stations/{lib.station_a.id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usage"]["rallye_ids"] == [str(lib.unpublished.id)]
    assert body["usage_count"] == 1

    items = await _list(client, lib.org.slug, headers)
    assert str(lib.station_a.id) in items


@pytest.mark.asyncio
@pytest.mark.parametrize("role", EDITORS)
async def test_editor_sees_the_full_usage(client: AsyncClient, db_session: AsyncSession, role: UserRole) -> None:
    """The usage of station B names both rallyes, at the list and at the detail."""
    lib = await _library(db_session)
    headers = await _headers(db_session, lib.org, role)
    both = {str(lib.published.id), str(lib.unpublished.id)}

    item = (await _list(client, lib.org.slug, headers))[str(lib.station_b.id)]
    assert _usage_ids(item) == both
    assert item["usage_count"] == 2

    response = await client.get(f"/api/orgs/{lib.org.slug}/stations/{lib.station_b.id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert _usage_ids(body) == both
    assert body["usage_count"] == 2


# ============================================================================
# sort=usage: the order follows the count the caller sees
# ============================================================================


async def _usage_order(client: AsyncClient, slug: str, headers: dict[str, str], order: str) -> list[str]:
    response = await client.get(f"/api/orgs/{slug}/stations/?sort=usage&order={order}", headers=headers)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


async def _tied_library(session: AsyncSession) -> tuple[Organization, Station, Station]:
    """Alpha and Zulu each have one published link; Zulu also has a link into an unpublished rallye."""
    org = await create_test_organization(session, name="Usage Sort Org", slug="usage-sort-org")
    published = await create_test_rallye(session, org, title={"de": "Veröffentlicht"}, is_published=True)
    unpublished = await create_test_rallye(session, org, title={"de": "Entwurf"}, is_published=False)
    alpha = await create_test_station(session, org, title={"de": "Alpha"})
    zulu = await create_test_station(session, org, title={"de": "Zulu"})
    await link_station_to_rallye(session, org, published, alpha, sort_order=0)
    await link_station_to_rallye(session, org, published, zulu, sort_order=1)
    await link_station_to_rallye(session, org, unpublished, zulu, sort_order=0)
    return org, alpha, zulu


@pytest.mark.asyncio
@pytest.mark.parametrize("role", READERS)
async def test_reader_usage_sort_does_not_reveal_an_unpublished_link(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """With equal visible counts, both directions fall back to the title tie-break."""
    org, alpha, zulu = await _tied_library(db_session)
    headers = await _headers(db_session, org, role)
    by_title = [str(alpha.id), str(zulu.id)]

    assert await _usage_order(client, org.slug, headers, "asc") == by_title
    assert await _usage_order(client, org.slug, headers, "desc") == by_title


@pytest.mark.asyncio
async def test_creator_usage_sort_follows_the_full_count(client: AsyncClient, db_session: AsyncSession) -> None:
    """For a creator the unpublished link counts, so Zulu (2) and Alpha (1) are ordered by usage."""
    org, alpha, zulu = await _tied_library(db_session)
    headers = await _headers(db_session, org, UserRole.CREATOR)

    assert await _usage_order(client, org.slug, headers, "asc") == [str(alpha.id), str(zulu.id)]
    assert await _usage_order(client, org.slug, headers, "desc") == [str(zulu.id), str(alpha.id)]


# ============================================================================
# A guest and another organization: refused, as before
# ============================================================================


@pytest.mark.asyncio
async def test_guest_is_refused_at_both_routes(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest reaches neither the list nor the detail."""
    lib = await _library(db_session)
    headers = await _headers(db_session, lib.org, UserRole.GUEST)

    listing = await client.get(f"/api/orgs/{lib.org.slug}/stations/", headers=headers)
    assert listing.status_code == 403, listing.text
    for station in (lib.station_a, lib.station_b):
        detail = await client.get(f"/api/orgs/{lib.org.slug}/stations/{station.id}", headers=headers)
        assert detail.status_code == 403, detail.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.PARTICIPANT, UserRole.ORG_ADMIN])
async def test_user_of_another_organization_is_refused(
    client: AsyncClient, db_session: AsyncSession, role: UserRole
) -> None:
    """A user of another organization reaches neither route of this one."""
    lib = await _library(db_session)
    other = await create_test_organization(db_session, name="Other Org", slug="other-org")
    headers = await _headers(db_session, other, role)

    listing = await client.get(f"/api/orgs/{lib.org.slug}/stations/", headers=headers)
    assert listing.status_code == 403, listing.text
    for station in (lib.station_a, lib.station_b):
        detail = await client.get(f"/api/orgs/{lib.org.slug}/stations/{station.id}", headers=headers)
        assert detail.status_code == 403, detail.text
