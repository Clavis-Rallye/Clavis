"""
Integration tests for the station library search, filter and sort.

`GET /api/orgs/{slug}/stations/` takes `q`, `sort`, `order` and `lang`, and returns
`usage_count` on every item. Covers search, usage sort, other sorts and paging for
stations, and the shared edge cases.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from tests.factories import (
    create_test_organization,
    create_test_rallye,
    create_test_station,
    create_test_user,
    create_test_user_with_org,
    get_auth_headers,
    link_station_to_rallye,
)


async def _list_stations(client: AsyncClient, org_slug: str, headers: dict, **params: object) -> dict:
    query = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items()}
    response = await client.get(f"/api/orgs/{org_slug}/stations/", headers=headers, params=query)
    assert response.status_code == 200, response.text
    return response.json()


async def _set_created_at(session: AsyncSession, station, when: datetime) -> None:
    station.created_at = when
    session.add(station)
    await session.commit()
    await session.refresh(station)


# ============================================================================
# Search
# ============================================================================


@pytest.mark.asyncio
async def test_search_matches_title_in_non_interface_language(client: AsyncClient, db_session: AsyncSession):
    """A German-interface search matches an English title value."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    await create_test_station(db_session, org, title={"de": "Rathaus", "en": "City Hall"})

    payload = await _list_stations(client, org.slug, headers, q="city", lang="de")
    assert payload["total"] == 1
    assert payload["items"][0]["title"]["de"] == "Rathaus"


@pytest.mark.asyncio
async def test_search_matches_any_stored_language(client: AsyncClient, db_session: AsyncSession):
    """A Cyrillic query matches even though it is not the interface language."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    await create_test_station(db_session, org, title={"de": "Rathaus", "uk": "Ратуша"})

    payload = await _list_stations(client, org.slug, headers, q="Ратуша", lang="de")
    assert payload["total"] == 1
    assert payload["items"][0]["title"]["de"] == "Rathaus"


@pytest.mark.asyncio
async def test_search_matches_address_case_insensitive(client: AsyncClient, db_session: AsyncSession):
    """The address is searched too, case-insensitively."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    await create_test_station(db_session, org, title={"de": "Rathaus"}, address="Rathausplatz 2, 86150 Augsburg")

    payload = await _list_stations(client, org.slug, headers, q="RATHAUSPLATZ")
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_search_no_match_returns_empty_with_zero_total(client: AsyncClient, db_session: AsyncSession):
    """A query that matches nothing gives items: [] and total: 0."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    await create_test_station(db_session, org, title={"de": "Rathaus"})

    payload = await _list_stations(client, org.slug, headers, q="zzz-nothing-matches")
    assert payload == {"items": [], "total": 0, "skip": 0, "limit": 100}


@pytest.mark.asyncio
async def test_search_finds_item_beyond_first_page(client: AsyncClient, db_session: AsyncSession):
    """Search runs over the whole library, not only the first page."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    target = await create_test_station(db_session, org, title={"de": "Einzigartige Zielstation"})
    for i in range(9):
        await create_test_station(db_session, org, title={"de": f"Filler {i}"})

    payload = await _list_stations(client, org.slug, headers, q="Zielstation", limit=3)
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(target.id)


# ============================================================================
# Usage sort
# ============================================================================


@pytest.mark.asyncio
async def test_sort_usage_desc_with_counts(client: AsyncClient, db_session: AsyncSession):
    """Stations sort by usage count, each reporting its own count."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    high = await create_test_station(db_session, org, title={"de": "Viel genutzt"})
    mid = await create_test_station(db_session, org, title={"de": "Wenig genutzt"})
    low = await create_test_station(db_session, org, title={"de": "Ungenutzt"})

    for i in range(3):
        rallye = await create_test_rallye(db_session, org, title={"de": f"R-high-{i}"})
        await link_station_to_rallye(db_session, org, rallye, high)
    rallye_mid = await create_test_rallye(db_session, org, title={"de": "R-mid"})
    await link_station_to_rallye(db_session, org, rallye_mid, mid)

    payload = await _list_stations(client, org.slug, headers, sort="usage", order="desc")
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(high.id), str(mid.id), str(low.id)]
    counts = {item["id"]: item["usage_count"] for item in payload["items"]}
    assert counts[str(high.id)] == 3
    assert counts[str(mid.id)] == 1
    assert counts[str(low.id)] == 0


@pytest.mark.asyncio
async def test_sort_usage_ties_break_by_title_ascending(client: AsyncClient, db_session: AsyncSession):
    """Items with equal usage count sort in title order."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    banana = await create_test_station(db_session, org, title={"de": "Banana"})
    apple = await create_test_station(db_session, org, title={"de": "Apple"})
    for station in (banana, apple):
        rallye = await create_test_rallye(db_session, org, title={"de": f"R-{station.id}"})
        await link_station_to_rallye(db_session, org, rallye, station)

    payload = await _list_stations(client, org.slug, headers, sort="usage", order="desc")
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(apple.id), str(banana.id)]


@pytest.mark.asyncio
async def test_sort_usage_excludes_archived_rallye(client: AsyncClient, db_session: AsyncSession):
    """An archived rallye no longer counts towards usage."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    station = await create_test_station(db_session, org, title={"de": "Station"})
    rallye_a = await create_test_rallye(db_session, org, title={"de": "R-a"})
    rallye_b = await create_test_rallye(db_session, org, title={"de": "R-b"})
    await link_station_to_rallye(db_session, org, rallye_a, station)
    await link_station_to_rallye(db_session, org, rallye_b, station)

    payload = await _list_stations(client, org.slug, headers, sort="usage")
    assert payload["items"][0]["usage_count"] == 2

    rallye_repo = RallyeRepository(db_session, org.id)
    await rallye_repo.soft_delete(rallye_a.id)
    await db_session.commit()

    payload = await _list_stations(client, org.slug, headers, sort="usage")
    assert payload["items"][0]["usage_count"] == 1


@pytest.mark.asyncio
async def test_search_keeps_usage_order(client: AsyncClient, db_session: AsyncSession):
    """A search over a filtered set keeps sorting by usage."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    high = await create_test_station(db_session, org, title={"de": "Suchtreffer viel"})
    low = await create_test_station(db_session, org, title={"de": "Suchtreffer wenig"})
    await create_test_station(db_session, org, title={"de": "Kein Treffer"})

    for i in range(2):
        rallye = await create_test_rallye(db_session, org, title={"de": f"R-{i}"})
        await link_station_to_rallye(db_session, org, rallye, high)

    payload = await _list_stations(client, org.slug, headers, q="Suchtreffer", sort="usage", order="desc")
    assert payload["total"] == 2
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(high.id), str(low.id)]


# ============================================================================
# Other sorts and paging
# ============================================================================


@pytest.mark.asyncio
async def test_title_sort_follows_lang_then_de_then_en(client: AsyncClient, db_session: AsyncSession):
    """Title order follows lang, then de, then en, ignoring case."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    # "en" value wins over "de" when lang=en; case must not affect the order.
    station_a = await create_test_station(db_session, org, title={"de": "Zebra", "en": "apple"})
    # No "en" value at all: falls back to "de".
    station_b = await create_test_station(db_session, org, title={"de": "Banana"})

    payload = await _list_stations(client, org.slug, headers, sort="title", order="asc", lang="en")
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(station_a.id), str(station_b.id)]


@pytest.mark.asyncio
async def test_created_at_sort_both_directions_across_pages(client: AsyncClient, db_session: AsyncSession):
    """The created_at sort works in both directions and stays correct across pages."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    stations = []
    for i in range(5):
        station = await create_test_station(db_session, org, title={"de": f"Station {i}"})
        await _set_created_at(db_session, station, base + timedelta(days=i))
        stations.append(station)

    asc_payload = await _list_stations(client, org.slug, headers, sort="created_at", order="asc")
    assert [item["id"] for item in asc_payload["items"]] == [str(s.id) for s in stations]

    desc_payload = await _list_stations(client, org.slug, headers, sort="created_at", order="desc")
    assert [item["id"] for item in desc_payload["items"]] == [str(s.id) for s in reversed(stations)]

    page_2 = await _list_stations(client, org.slug, headers, sort="created_at", order="asc", skip=2, limit=2)
    assert [item["id"] for item in page_2["items"]] == [str(stations[2].id), str(stations[3].id)]


@pytest.mark.asyncio
async def test_paging_keeps_total_and_never_repeats_a_row(client: AsyncClient, db_session: AsyncSession):
    """Paging keeps `total` stable and every row is returned exactly once."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    created = [await create_test_station(db_session, org, title={"de": f"Station {i}"}) for i in range(7)]

    seen: list[str] = []
    for skip in (0, 3, 6):
        payload = await _list_stations(client, org.slug, headers, sort="created_at", order="asc", skip=skip, limit=3)
        assert payload["total"] == 7
        seen.extend(item["id"] for item in payload["items"])

    assert len(seen) == len(created)
    assert len(set(seen)) == len(created)
    assert set(seen) == {str(s.id) for s in created}


@pytest.mark.asyncio
async def test_include_deleted_rows_take_part(client: AsyncClient, db_session: AsyncSession):
    """Archived rows take part in search and listing when include_deleted=true."""
    admin, org = await create_test_user_with_org(db_session, role=UserRole.ORG_ADMIN)
    headers = get_auth_headers(admin)

    station = await create_test_station(db_session, org, title={"de": "Archivierte Station"})
    station_repo = StationRepository(db_session, org.id)
    await station_repo.soft_delete(station.id)
    await db_session.commit()

    without = await _list_stations(client, org.slug, headers, q="Archivierte")
    assert without["total"] == 0

    with_deleted = await _list_stations(client, org.slug, headers, q="Archivierte", include_deleted=True)
    assert with_deleted["total"] == 1
    assert with_deleted["items"][0]["is_deleted"] is True


# ============================================================================
# Edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_blank_query_behaves_like_no_query(client: AsyncClient, db_session: AsyncSession):
    """A blank (whitespace-only) query is normalized away and matches everything."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    await create_test_station(db_session, org, title={"de": "Station A"})
    await create_test_station(db_session, org, title={"de": "Station B"})

    baseline = await _list_stations(client, org.slug, headers)
    blank = await _list_stations(client, org.slug, headers, q="   ")
    assert blank["total"] == baseline["total"] == 2


@pytest.mark.asyncio
async def test_long_query_is_cut_not_rejected(client: AsyncClient, db_session: AsyncSession):
    """A 250-character query is cut to 200 characters, not rejected with 422."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    await create_test_station(db_session, org, title={"de": "Station"})

    response = await client.get(
        f"/api/orgs/{org.slug}/stations/",
        headers=headers,
        params={"q": "x" * 250},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_percent_sign_matches_literally(client: AsyncClient, db_session: AsyncSession):
    """`%` in the query is a literal character, not a wildcard."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    percent_station = await create_test_station(db_session, org, title={"de": "100%"})
    await create_test_station(db_session, org, title={"de": "100 Punkte"})

    payload = await _list_stations(client, org.slug, headers, q="100%")
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(percent_station.id)


@pytest.mark.asyncio
async def test_second_organization_station_neither_listed_nor_counted(client: AsyncClient, db_session: AsyncSession):
    """A matching station of another organisation is invisible."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR, org_slug="org-one")
    headers = get_auth_headers(creator)

    other_org = await create_test_organization(db_session, name="Other Org", slug="org-two")
    await create_test_station(db_session, other_org, title={"de": "SharedTermStation"})
    await create_test_station(db_session, org, title={"de": "SharedTermStation"})

    payload = await _list_stations(client, org.slug, headers, q="SharedTermStation")
    assert payload["total"] == 1
    assert payload["items"][0]["organization_id"] == str(org.id)


@pytest.mark.asyncio
async def test_participant_search_over_published_stations_only(client: AsyncClient, db_session: AsyncSession):
    """The participant path applies the same search over its smaller, published-only set."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    participant = await create_test_user(db_session, email="participant@example.com", organization=org)
    participant_headers = get_auth_headers(participant)

    published_rallye = await create_test_rallye(db_session, org, is_published=True)
    visible = await create_test_station(db_session, org, title={"de": "Sichtbare Station"})
    await link_station_to_rallye(db_session, org, published_rallye, visible)
    await create_test_station(db_session, org, title={"de": "Sichtbare, aber nicht verlinkt"})

    payload = await _list_stations(client, org.slug, participant_headers, q="Sichtbare")
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(visible.id)


@pytest.mark.asyncio
async def test_default_call_keeps_todays_order(client: AsyncClient, db_session: AsyncSession):
    """A call without the search and sort parameters returns items in created_at desc order."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    older = await create_test_station(db_session, org, title={"de": "Aelter"})
    await _set_created_at(db_session, older, base)
    newer = await create_test_station(db_session, org, title={"de": "Neuer"})
    await _set_created_at(db_session, newer, base + timedelta(days=1))

    payload = await _list_stations(client, org.slug, headers)
    assert [item["id"] for item in payload["items"]] == [str(newer.id), str(older.id)]


@pytest.mark.asyncio
async def test_unknown_sort_key_returns_422(client: AsyncClient, db_session: AsyncSession):
    """An unknown sort key is rejected, a long q is not."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    response = await client.get(
        f"/api/orgs/{org.slug}/stations/",
        headers=headers,
        params={"sort": "not-a-real-sort-key"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_usage_count_matches_usage_rallye_ids_length_on_every_row(client: AsyncClient, db_session: AsyncSession):
    """usage_count == len(usage.rallye_ids) on every returned row."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    unused = await create_test_station(db_session, org, title={"de": "Unbenutzt"})
    used = await create_test_station(db_session, org, title={"de": "Benutzt"})
    rallye = await create_test_rallye(db_session, org)
    await link_station_to_rallye(db_session, org, rallye, used)
    assert unused.id  # keep reference, avoid unused-var lint

    payload = await _list_stations(client, org.slug, headers)
    assert payload["items"]
    for item in payload["items"]:
        assert item["usage_count"] == len(item["usage"]["rallye_ids"])
