"""
Integration tests for the library Station endpoints + rallye<->station composition.

Stations live in the organization-wide library. They are created standalone via
`POST /stations` or created-and-linked via `POST /rallyes/{rid}/stations`, and are
composed into rallyes by reference. Per-rallye ordering lives on the rallye_stations
link (exposed as `sort_order` on the composition response, NOT on the station itself).
"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import (
    create_test_rallye,
    create_test_station,
    create_test_user,
    create_test_user_with_org,
    get_auth_headers,
    link_station_to_rallye,
)


async def _make_rallye(client: AsyncClient, org_slug: str, headers: dict) -> str:
    resp = await client.post(
        f"/api/orgs/{org_slug}/rallyes/",
        headers=headers,
        json={"title": {"de": "Test Rallye"}, "description": {"de": "Test"}},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_create_station_with_multilingual_content(client: AsyncClient, db_session: AsyncSession):
    """Creating a station under a rallye returns the link + nested library station."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    creator_headers = get_auth_headers(creator)
    rallye_id = await _make_rallye(client, org.slug, creator_headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
        headers=creator_headers,
        json={
            "title": {"de": "Rathaus", "en": "City Hall"},
            "description": {"de": "Das historische Rathaus", "en": "The historic city hall"},
            "address": "Rathausplatz 1, 86150 Augsburg",
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
    )
    assert response.status_code == 201
    data = response.json()

    assert data["rallye_id"] == rallye_id
    assert data["sort_order"] == 0
    station = data["station"]
    assert station["title"] == {"de": "Rathaus", "en": "City Hall"}
    assert station["address"] == "Rathausplatz 1, 86150 Augsburg"
    assert station["location_lat"] == 48.3705
    assert station["location_lng"] == 10.8978
    assert station["organization_id"] == str(org.id)
    UUID(station["id"])


@pytest.mark.asyncio
async def test_create_standalone_library_station(client: AsyncClient, db_session: AsyncSession):
    """POST /stations creates a standalone library station (not linked to any rallye)."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)

    response = await client.post(
        f"/api/orgs/{org.slug}/stations/",
        headers=headers,
        json={"title": {"de": "Standalone"}, "description": {"de": "Lib"}},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == {"de": "Standalone"}
    assert data["usage"]["rallye_ids"] == []


@pytest.mark.asyncio
async def test_create_station_requires_creator_role(client: AsyncClient, db_session: AsyncSession):
    """Participants cannot create stations."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    creator_headers = get_auth_headers(creator)
    rallye_id = await _make_rallye(client, org.slug, creator_headers)

    participant = await create_test_user(
        db_session, email="participant@example.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
        headers=participant_headers,
        json={"title": {"de": "Nope"}, "description": {"de": "Nope"}},
    )
    assert response.status_code == 403

    response = await client.post(
        f"/api/orgs/{org.slug}/stations/",
        headers=participant_headers,
        json={"title": {"de": "Nope"}, "description": {"de": "Nope"}},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_stations_ordered_by_sort_order(client: AsyncClient, db_session: AsyncSession):
    """Listing a rallye's stations returns them ordered by per-rallye sort_order."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye = await create_test_rallye(db_session, org)

    s_a = await create_test_station(db_session, org, title={"de": "A"})
    s_b = await create_test_station(db_session, org, title={"de": "B"})
    s_c = await create_test_station(db_session, org, title={"de": "C"})
    # Link out of natural order; sort_order decides the listing order.
    await link_station_to_rallye(db_session, org, rallye, s_b, sort_order=0)
    await link_station_to_rallye(db_session, org, rallye, s_c, sort_order=1)
    await link_station_to_rallye(db_session, org, rallye, s_a, sort_order=2)

    response = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["sort_order"] for item in items] == [0, 1, 2]
    assert [item["station"]["title"]["de"] for item in items] == ["B", "C", "A"]


@pytest.mark.asyncio
async def test_get_station_by_id(client: AsyncClient, db_session: AsyncSession):
    """GET /stations/{id} returns the library station with usage."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    station = await create_test_station(db_session, org, title={"de": "Rathaus", "en": "City Hall"})
    rallye = await create_test_rallye(db_session, org)
    await link_station_to_rallye(db_session, org, rallye, station, sort_order=0)

    response = await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(station.id)
    assert data["title"] == {"de": "Rathaus", "en": "City Hall"}
    assert str(rallye.id) in data["usage"]["rallye_ids"]


@pytest.mark.asyncio
async def test_get_nonexistent_station_returns_404(client: AsyncClient, db_session: AsyncSession):
    """GET /stations/{id} for an unknown id returns 404."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    from uuid import uuid4

    response = await client.get(f"/api/orgs/{org.slug}/stations/{uuid4()}", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_station_propagates_to_all_rallyes(client: AsyncClient, db_session: AsyncSession):
    """Updating a library station propagates to every rallye linking it."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    station = await create_test_station(db_session, org, title={"de": "Old"})
    rallye = await create_test_rallye(db_session, org)
    await link_station_to_rallye(db_session, org, rallye, station, sort_order=0)

    response = await client.put(
        f"/api/orgs/{org.slug}/stations/{station.id}",
        headers=headers,
        json={
            "title": {"de": "Neu", "en": "New"},
            "description": {"de": "Beschreibung"},
            "location_lat": 49.0,
            "location_lng": 11.0,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == {"de": "Neu", "en": "New"}
    assert data["location_lat"] == 49.0

    listing = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations", headers=headers)
    assert listing.json()["items"][0]["station"]["title"] == {"de": "Neu", "en": "New"}


@pytest.mark.asyncio
async def test_update_nonexistent_station_returns_404(client: AsyncClient, db_session: AsyncSession):
    """PUT /stations/{id} for unknown id returns 404."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    from uuid import uuid4

    response = await client.put(
        f"/api/orgs/{org.slug}/stations/{uuid4()}", headers=headers, json={"title": {"de": "X"}}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_station_returns_usage_and_cascade_unlinks(client: AsyncClient, db_session: AsyncSession):
    """DELETE /stations/{id} returns a usage body (200) and cascade-unlinks everywhere."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    station = await create_test_station(db_session, org)
    rallye = await create_test_rallye(db_session, org)
    await link_station_to_rallye(db_session, org, rallye, station, sort_order=0)

    response = await client.delete(f"/api/orgs/{org.slug}/stations/{station.id}", headers=headers)
    assert response.status_code == 200
    usage = response.json()
    assert str(rallye.id) in usage["rallye_ids"]

    listing = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations", headers=headers)
    assert listing.json()["items"] == []


@pytest.mark.asyncio
async def test_deleted_station_not_in_list(client: AsyncClient, db_session: AsyncSession):
    """A soft-deleted station no longer appears in the library list."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    keep = await create_test_station(db_session, org, title={"de": "Keep"})
    drop = await create_test_station(db_session, org, title={"de": "Drop"})

    del_resp = await client.delete(f"/api/orgs/{org.slug}/stations/{drop.id}", headers=headers)
    assert del_resp.status_code == 200

    listing = await client.get(f"/api/orgs/{org.slug}/stations/", headers=headers)
    assert listing.status_code == 200
    ids = [item["id"] for item in listing.json()["items"]]
    assert str(keep.id) in ids
    assert str(drop.id) not in ids


@pytest.mark.asyncio
async def test_get_deleted_station_returns_404(client: AsyncClient, db_session: AsyncSession):
    """GET a soft-deleted station returns 404."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    station = await create_test_station(db_session, org)

    await client.delete(f"/api/orgs/{org.slug}/stations/{station.id}", headers=headers)
    response = await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cannot_create_station_under_other_org_rallye(client: AsyncClient, db_session: AsyncSession):
    """A creator cannot create a station under another org's rallye (via their own slug)."""
    creator_a, org_a = await create_test_user_with_org(
        db_session, email="creator-a@example.com", role=UserRole.CREATOR, org_slug="org-a"
    )
    headers_a = get_auth_headers(creator_a)

    _creator_b, org_b = await create_test_user_with_org(
        db_session, email="creator-b@example.com", role=UserRole.CREATOR, org_slug="org-b"
    )
    rallye_b = await create_test_rallye(db_session, org_b)

    # Org A's slug + org B's rallye id -> rallye not found in org A.
    response = await client.post(
        f"/api/orgs/{org_a.slug}/rallyes/{rallye_b.id}/stations",
        headers=headers_a,
        json={"title": {"de": "X"}, "description": {"de": "X"}},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_participant_cannot_see_unlinked_library_station(client: AsyncClient, db_session: AsyncSession):
    """Participants cannot see a library station that is not linked into any rallye."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    participant = await create_test_user(
        db_session, email="participant@example.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)
    creator_headers = get_auth_headers(creator)

    station = await create_test_station(db_session, org)

    # Creator can see it; participant gets 404 (no rallye usage).
    assert (await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", headers=creator_headers)).status_code == 200
    response = await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", headers=participant_headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_station_description_and_tips_header_are_sanitised(client: AsyncClient, db_session: AsyncSession):
    """A script in a description or tips header never reaches the database."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    own_image = f'<img src="/api/orgs/{org.slug}/media/{org.id}/media/x_bild.png" alt="Bild">'
    response = await client.post(
        f"/api/orgs/{org.slug}/stations/",
        headers=headers,
        json={
            "title": {"de": "Rathaus"},
            "description": {"de": f'<p onclick="x()">Hallo<script>evil()</script>{own_image}</p>'},
            "tips": {
                "header": {"de": '<p>Tipp<iframe src="https://evil.example"></iframe></p>'},
                "links": [{"label": {"de": "Karte"}, "url": "https://example.com"}],
            },
        },
    )
    assert response.status_code == 201, response.text
    station = response.json()
    assert "<script" not in station["description"]["de"]
    assert "onclick" not in station["description"]["de"]
    assert own_image.split(" alt")[0] in station["description"]["de"]
    assert station["tips"]["header"]["de"] == "<p>Tipp</p>"

    update = await client.put(
        f"/api/orgs/{org.slug}/stations/{station['id']}",
        headers=headers,
        json={"description": {"de": '<p>Neu<img src="https://tracker.example/p.gif"></p>'}},
    )
    assert update.status_code == 200, update.text
    assert update.json()["description"]["de"] == "<p>Neu</p>"


@pytest.mark.asyncio
async def test_station_address_needs_coordinates(client: AsyncClient, db_session: AsyncSession):
    """An address without a map position is refused on create and on update."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    refused = await client.post(
        f"/api/orgs/{org.slug}/stations/",
        headers=headers,
        json={"title": {"de": "Rathaus"}, "description": {"de": "Text"}, "address": "Rathausplatz 1"},
    )
    assert refused.status_code == 422

    created = await client.post(
        f"/api/orgs/{org.slug}/stations/",
        headers=headers,
        json={
            "title": {"de": "Rathaus"},
            "description": {"de": "Text"},
            "address": "Rathausplatz 1",
            "location_lat": 48.3688,
            "location_lng": 10.8986,
        },
    )
    assert created.status_code == 201, created.text
    station_id = created.json()["id"]

    moved = await client.put(
        f"/api/orgs/{org.slug}/stations/{station_id}",
        headers=headers,
        json={"address": "Rathausplatz 2"},
    )
    assert moved.status_code == 200, moved.text

    # A station without a place is fine, but giving it an address alone is not.
    without_address = await client.post(
        f"/api/orgs/{org.slug}/stations/",
        headers=headers,
        json={"title": {"de": "Ohne Ort"}, "description": {"de": "Text"}},
    )
    assert without_address.status_code == 201
    address_only = await client.put(
        f"/api/orgs/{org.slug}/stations/{without_address.json()['id']}",
        headers=headers,
        json={"address": "Domplatz 1"},
    )
    assert address_only.status_code == 422
