"""
Integration tests for the challenge library search, filter and sort.

`GET /api/orgs/{slug}/challenges/` takes `q`, `type`, `sort`, `order` and `lang`,
and returns `usage_count` on every item. Covers search, usage sort, type filter,
other sorts and paging for challenges, and the shared edge cases.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import Challenge, ChallengeType
from app.models.organization import Organization
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from tests.factories import (
    create_test_challenge,
    create_test_organization,
    create_test_rallye,
    create_test_station,
    create_test_user,
    create_test_user_with_org,
    get_auth_headers,
    link_challenge_to_rallye_station,
    link_station_to_rallye,
)


async def _list_challenges(client: AsyncClient, org_slug: str, headers: dict, **params: object) -> dict:
    query = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items()}
    response = await client.get(f"/api/orgs/{org_slug}/challenges/", headers=headers, params=query)
    assert response.status_code == 200, response.text
    return response.json()


async def _set_created_at(session: AsyncSession, challenge: Challenge, when: datetime) -> None:
    challenge.created_at = when
    session.add(challenge)
    await session.commit()
    await session.refresh(challenge)


async def _use_challenge_in_rallye(db_session: AsyncSession, org: Organization, challenge: Challenge) -> None:
    """Place a challenge on a fresh station of a fresh rallye (one distinct use)."""
    rallye = await create_test_rallye(db_session, org)
    station = await create_test_station(db_session, org)
    link = await link_station_to_rallye(db_session, org, rallye, station)
    await link_challenge_to_rallye_station(db_session, org, link, challenge)


async def _bulk_create_challenges(
    db_session: AsyncSession,
    org: Organization,
    count: int,
    *,
    challenge_type: ChallengeType = ChallengeType.TEXT,
    title_prefix: str = "Aufgabe",
) -> None:
    """Seed many library challenges, committed once (the paging tests need 250 rows).

    Inserts row by row like tests.factories does. A single add_all batch trips
    SQLAlchemy's insertmanyvalues sentinel matching on the uuid7 defaults.
    """
    repo = ChallengeRepository(db_session, org.id)
    for i in range(count):
        await repo.create(
            challenge_type=challenge_type,
            title={"de": f"{title_prefix} {i:03d}"},
            instructions={"de": "Anleitung"},
            scoring_rules={"max_points": 10},
        )
    await db_session.commit()


# ============================================================================
# Search
# ============================================================================


@pytest.mark.asyncio
async def test_search_matches_title_in_non_interface_language(client: AsyncClient, db_session: AsyncSession):
    """A German-interface search matches an English title value."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    await create_test_challenge(db_session, org, title={"de": "Foto vom Brunnen", "en": "Photo of the fountain"})

    payload = await _list_challenges(client, org.slug, headers, q="fountain", lang="de")
    assert payload["total"] == 1
    assert payload["items"][0]["title"]["de"] == "Foto vom Brunnen"


@pytest.mark.asyncio
async def test_search_no_match_returns_empty_with_zero_total(client: AsyncClient, db_session: AsyncSession):
    """A query that matches nothing gives items: [] and total: 0."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)
    await create_test_challenge(db_session, org, title={"de": "Foto vom Brunnen"})

    payload = await _list_challenges(client, org.slug, headers, q="zzz-nothing-matches")
    assert payload == {"items": [], "total": 0, "skip": 0, "limit": 100}


# ============================================================================
# Usage sort
# ============================================================================


@pytest.mark.asyncio
async def test_sort_usage_desc_with_counts(client: AsyncClient, db_session: AsyncSession):
    """Challenges sort by usage count, each reporting its own count."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    high = await create_test_challenge(db_session, org, title={"de": "Viel genutzt"})
    mid = await create_test_challenge(db_session, org, title={"de": "Wenig genutzt"})
    low = await create_test_challenge(db_session, org, title={"de": "Ungenutzt"})
    for _ in range(3):
        await _use_challenge_in_rallye(db_session, org, high)
    await _use_challenge_in_rallye(db_session, org, mid)

    payload = await _list_challenges(client, org.slug, headers, sort="usage", order="desc")
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(high.id), str(mid.id), str(low.id)]
    counts = {item["id"]: item["usage_count"] for item in payload["items"]}
    assert counts[str(high.id)] == 3
    assert counts[str(mid.id)] == 1
    assert counts[str(low.id)] == 0


@pytest.mark.asyncio
async def test_sort_usage_ties_break_by_title_ascending(client: AsyncClient, db_session: AsyncSession):
    """Two challenges with the same count sort in title order."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    banana = await create_test_challenge(db_session, org, title={"de": "Banane"})
    apple = await create_test_challenge(db_session, org, title={"de": "Apfel"})
    for challenge in (banana, apple):
        for _ in range(2):
            await _use_challenge_in_rallye(db_session, org, challenge)

    payload = await _list_challenges(client, org.slug, headers, sort="usage", order="desc")
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(apple.id), str(banana.id)]
    assert all(item["usage_count"] == 2 for item in payload["items"])


@pytest.mark.asyncio
async def test_sort_usage_excludes_archived_rallye(client: AsyncClient, db_session: AsyncSession):
    """An archived rallye no longer counts towards usage."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    challenge = await create_test_challenge(db_session, org, title={"de": "Aufgabe"})
    rallye_a = await create_test_rallye(db_session, org, title={"de": "R-a"})
    rallye_b = await create_test_rallye(db_session, org, title={"de": "R-b"})
    for rallye in (rallye_a, rallye_b):
        station = await create_test_station(db_session, org)
        link = await link_station_to_rallye(db_session, org, rallye, station)
        await link_challenge_to_rallye_station(db_session, org, link, challenge)

    payload = await _list_challenges(client, org.slug, headers, sort="usage")
    assert payload["items"][0]["usage_count"] == 2

    rallye_repo = RallyeRepository(db_session, org.id)
    await rallye_repo.soft_delete(rallye_a.id)
    await db_session.commit()

    payload = await _list_challenges(client, org.slug, headers, sort="usage")
    assert payload["items"][0]["usage_count"] == 1


@pytest.mark.asyncio
async def test_search_and_type_filter_keep_usage_order(client: AsyncClient, db_session: AsyncSession):
    """A search plus the type filter keeps sorting by usage."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    high = await create_test_challenge(
        db_session, org, challenge_type=ChallengeType.PHOTO, title={"de": "Foto viel genutzt"}
    )
    low = await create_test_challenge(db_session, org, challenge_type=ChallengeType.PHOTO, title={"de": "Foto wenig"})
    # Matches the search but is filtered out by the type.
    await create_test_challenge(db_session, org, challenge_type=ChallengeType.QUIZ, title={"de": "Foto Quiz"})
    # Matches the type but not the search.
    await create_test_challenge(db_session, org, challenge_type=ChallengeType.PHOTO, title={"de": "Anderes Thema"})
    for _ in range(2):
        await _use_challenge_in_rallye(db_session, org, high)

    payload = await _list_challenges(client, org.slug, headers, q="Foto", type="photo", sort="usage", order="desc")
    assert payload["total"] == 2
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(high.id), str(low.id)]


@pytest.mark.asyncio
async def test_challenge_on_two_stations_of_one_rallye_counts_once(client: AsyncClient, db_session: AsyncSession):
    """One challenge placed on two stations of one rallye is a single use."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    challenge = await create_test_challenge(db_session, org, title={"de": "Doppelt platziert"})
    rallye = await create_test_rallye(db_session, org)
    for _ in range(2):
        station = await create_test_station(db_session, org)
        link = await link_station_to_rallye(db_session, org, rallye, station)
        await link_challenge_to_rallye_station(db_session, org, link, challenge)

    payload = await _list_challenges(client, org.slug, headers, sort="usage", order="desc")
    assert payload["items"][0]["id"] == str(challenge.id)
    assert payload["items"][0]["usage_count"] == 1
    assert len(payload["items"][0]["usage"]["rallye_ids"]) == 1


# ============================================================================
# Type filter, other sorts and paging
# ============================================================================


@pytest.mark.asyncio
async def test_type_filter_pages_whole_library(client: AsyncClient, db_session: AsyncSession):
    """250 challenges, 30 quizzes — type=quiz gives total 30 and only quizzes on every page."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    await _bulk_create_challenges(db_session, org, 220, title_prefix="Textaufgabe")
    await _bulk_create_challenges(db_session, org, 30, challenge_type=ChallengeType.QUIZ, title_prefix="Quiz")

    unfiltered = await _list_challenges(client, org.slug, headers, limit=1)
    assert unfiltered["total"] == 250

    seen: list[str] = []
    for skip in (0, 25):
        payload = await _list_challenges(
            client, org.slug, headers, type="quiz", sort="title", order="asc", skip=skip, limit=25
        )
        assert payload["total"] == 30
        assert len(payload["items"]) == (5 if skip else 25)
        for item in payload["items"]:
            assert item["challenge_type"] == "quiz"
        seen.extend(item["id"] for item in payload["items"])

    assert len(seen) == len(set(seen)) == 30


@pytest.mark.asyncio
async def test_title_sort_follows_lang_then_de_then_en(client: AsyncClient, db_session: AsyncSession):
    """Title order follows lang, then de, then en, ignoring case."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    # "en" value wins over "de" when lang=en; case must not affect the order.
    challenge_a = await create_test_challenge(db_session, org, title={"de": "Zebra", "en": "apple"})
    # No "en" value at all: falls back to "de".
    challenge_b = await create_test_challenge(db_session, org, title={"de": "Banana"})

    payload = await _list_challenges(client, org.slug, headers, sort="title", order="asc", lang="en")
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(challenge_a.id), str(challenge_b.id)]


@pytest.mark.asyncio
async def test_created_at_sort_both_directions_across_pages(client: AsyncClient, db_session: AsyncSession):
    """The created_at sort works in both directions and stays correct across pages."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    challenges = []
    for i in range(5):
        challenge = await create_test_challenge(db_session, org, title={"de": f"Aufgabe {i}"})
        await _set_created_at(db_session, challenge, base + timedelta(days=i))
        challenges.append(challenge)

    asc_payload = await _list_challenges(client, org.slug, headers, sort="created_at", order="asc")
    assert [item["id"] for item in asc_payload["items"]] == [str(c.id) for c in challenges]

    desc_payload = await _list_challenges(client, org.slug, headers, sort="created_at", order="desc")
    assert [item["id"] for item in desc_payload["items"]] == [str(c.id) for c in reversed(challenges)]

    page_2 = await _list_challenges(client, org.slug, headers, sort="created_at", order="asc", skip=2, limit=2)
    assert [item["id"] for item in page_2["items"]] == [str(challenges[2].id), str(challenges[3].id)]


@pytest.mark.asyncio
async def test_paging_keeps_total_and_never_repeats_a_row(client: AsyncClient, db_session: AsyncSession):
    """Paging keeps `total` stable and every row is returned exactly once."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    created = [await create_test_challenge(db_session, org, title={"de": f"Aufgabe {i}"}) for i in range(7)]

    seen: list[str] = []
    for skip in (0, 3, 6):
        payload = await _list_challenges(client, org.slug, headers, sort="created_at", order="asc", skip=skip, limit=3)
        assert payload["total"] == 7
        seen.extend(item["id"] for item in payload["items"])

    assert len(seen) == len(created)
    assert len(set(seen)) == len(created)
    assert set(seen) == {str(c.id) for c in created}


@pytest.mark.asyncio
async def test_include_deleted_rows_take_part(client: AsyncClient, db_session: AsyncSession):
    """Archived rows take part in search and listing when include_deleted=true."""
    admin, org = await create_test_user_with_org(db_session, role=UserRole.ORG_ADMIN)
    headers = get_auth_headers(admin)

    challenge = await create_test_challenge(db_session, org, title={"de": "Archivierte Aufgabe"})
    challenge_repo = ChallengeRepository(db_session, org.id)
    await challenge_repo.soft_delete(challenge.id)
    await db_session.commit()

    without = await _list_challenges(client, org.slug, headers, q="Archivierte")
    assert without["total"] == 0

    with_deleted = await _list_challenges(client, org.slug, headers, q="Archivierte", include_deleted=True)
    assert with_deleted["total"] == 1
    assert with_deleted["items"][0]["is_deleted"] is True


# ============================================================================
# Challenge-specific sorts: points, type, duration, author
# ============================================================================


@pytest.mark.asyncio
async def test_sort_points_numeric_max_points_else_zero(client: AsyncClient, db_session: AsyncSession):
    """Points sort: a numeric max_points, anything else counts as 0."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    two_hundred = await create_test_challenge(
        db_session, org, title={"de": "Zweihundert"}, scoring_rules={"max_points": 200}
    )
    ten = await create_test_challenge(db_session, org, title={"de": "Zehn"}, scoring_rules={"max_points": 10})
    # A string max_points is not a number, and no key at all is neither: both are 0,
    # ordered by title among themselves.
    await create_test_challenge(db_session, org, title={"de": "Null A"}, scoring_rules={"max_points": "viele"})
    await create_test_challenge(db_session, org, title={"de": "Null B"}, scoring_rules={})

    payload = await _list_challenges(client, org.slug, headers, sort="points", order="desc")
    ids = [item["id"] for item in payload["items"]]
    assert ids[:2] == [str(two_hundred.id), str(ten.id)]
    titles = [item["title"]["de"] for item in payload["items"]]
    assert titles == ["Zweihundert", "Zehn", "Null A", "Null B"]


@pytest.mark.asyncio
async def test_sort_type_alphabetical(client: AsyncClient, db_session: AsyncSession):
    """Type sort orders by the name the interface shows, not by the enum code.

    The codes would put "Kreativ" (creative) before "Foto" (photo).
    The sort follows the names of the UI language.
    """
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    for challenge_type in ChallengeType:
        await create_test_challenge(
            db_session, org, challenge_type=challenge_type, title={"de": f"Aufgabe {challenge_type.value}"}
        )

    payload = await _list_challenges(client, org.slug, headers, sort="type", order="asc", lang="de")
    types = [item["challenge_type"] for item in payload["items"]]
    # Audio, Foto, Kreativ, QR-Code, Quiz, Text, Video
    assert types == ["audio", "photo", "creative", "qr_scan", "quiz", "text", "video"]

    payload = await _list_challenges(client, org.slug, headers, sort="type", order="asc", lang="en")
    types = [item["challenge_type"] for item in payload["items"]]
    # Audio, Creative, Photo, QR code, Quiz, Text, Video
    assert types == ["audio", "creative", "photo", "qr_scan", "quiz", "text", "video"]


@pytest.mark.asyncio
async def test_sort_duration_null_counts_as_zero(client: AsyncClient, db_session: AsyncSession):
    """Duration sort: time_estimate_minutes, an empty estimate counts as 0."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    without_estimate = await create_test_challenge(db_session, org, title={"de": "Ohne Angabe"})
    short = await create_test_challenge(db_session, org, title={"de": "Kurz"}, time_estimate_minutes=5)
    long = await create_test_challenge(db_session, org, title={"de": "Lang"}, time_estimate_minutes=30)

    asc_payload = await _list_challenges(client, org.slug, headers, sort="duration", order="asc")
    assert [item["id"] for item in asc_payload["items"]] == [str(without_estimate.id), str(short.id), str(long.id)]

    desc_payload = await _list_challenges(client, org.slug, headers, sort="duration", order="desc")
    assert [item["id"] for item in desc_payload["items"]] == [str(long.id), str(short.id), str(without_estimate.id)]


@pytest.mark.asyncio
async def test_sort_author_case_insensitive_missing_last(client: AsyncClient, db_session: AsyncSession):
    """Author sort: creator display name case-insensitively; no creator sorts as empty."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    zoe = await create_test_user(db_session, email="zoe@example.com", organization=org, display_name="Zoe")
    adam = await create_test_user(db_session, email="adam@example.com", organization=org, display_name="adam")
    challenge_zoe = await create_test_challenge(db_session, org, title={"de": "Von Zoe"}, created_by_user_id=zoe.id)
    challenge_adam = await create_test_challenge(db_session, org, title={"de": "Von adam"}, created_by_user_id=adam.id)
    challenge_orphan = await create_test_challenge(db_session, org, title={"de": "Ohne Autor"})

    payload = await _list_challenges(client, org.slug, headers, sort="author", order="asc")
    ids = [item["id"] for item in payload["items"]]
    assert ids == [str(challenge_orphan.id), str(challenge_adam.id), str(challenge_zoe.id)]


# ============================================================================
# Edge cases and compatibility
# ============================================================================


@pytest.mark.asyncio
async def test_second_organization_challenge_neither_listed_nor_counted(client: AsyncClient, db_session: AsyncSession):
    """A matching challenge of another organisation is invisible."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR, org_slug="org-one")
    headers = get_auth_headers(creator)

    other_org = await create_test_organization(db_session, name="Other Org", slug="org-two")
    await create_test_challenge(db_session, other_org, title={"de": "Gemeinsamer Begriff"})
    await create_test_challenge(db_session, org, title={"de": "Gemeinsamer Begriff"})

    payload = await _list_challenges(client, org.slug, headers, q="Gemeinsamer")
    assert payload["total"] == 1
    assert payload["items"][0]["organization_id"] == str(org.id)


@pytest.mark.asyncio
async def test_default_call_keeps_todays_order(client: AsyncClient, db_session: AsyncSession):
    """A call without the search and sort parameters returns items in created_at desc order."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    older = await create_test_challenge(db_session, org, title={"de": "Aelter"})
    await _set_created_at(db_session, older, base)
    newer = await create_test_challenge(db_session, org, title={"de": "Neuer"})
    await _set_created_at(db_session, newer, base + timedelta(days=1))

    payload = await _list_challenges(client, org.slug, headers)
    assert [item["id"] for item in payload["items"]] == [str(newer.id), str(older.id)]


@pytest.mark.asyncio
async def test_unknown_type_returns_422(client: AsyncClient, db_session: AsyncSession):
    """An unknown type value is rejected with 422."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    response = await client.get(
        f"/api/orgs/{org.slug}/challenges/",
        headers=headers,
        params={"type": "bogus"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_sort_key_returns_422(client: AsyncClient, db_session: AsyncSession):
    """An unknown sort key is rejected with 422."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    response = await client.get(
        f"/api/orgs/{org.slug}/challenges/",
        headers=headers,
        params={"sort": "not-a-real-sort-key"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_usage_count_matches_usage_rallye_ids_length_on_every_row(client: AsyncClient, db_session: AsyncSession):
    """usage_count == len(usage.rallye_ids) on every returned row."""
    creator, org = await create_test_user_with_org(db_session, role=UserRole.CREATOR)
    headers = get_auth_headers(creator)

    await create_test_challenge(db_session, org, title={"de": "Unbenutzt"})
    used = await create_test_challenge(db_session, org, title={"de": "Benutzt"})
    await _use_challenge_in_rallye(db_session, org, used)

    payload = await _list_challenges(client, org.slug, headers)
    assert payload["items"]
    for item in payload["items"]:
        assert item["usage_count"] == len(item["usage"]["rallye_ids"])
