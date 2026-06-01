"""
Integration tests for the organization-wide station and challenge library.

Library items are composed into rallyes by reference:

- reuse: one library station linked into two rallyes; editing it propagates
- unlink: removing a rallye<->station link keeps the library station
- reorder: PUT .../stations/order updates the per-rallye sort_order
- delete-in-use: DELETE returns a usage body then unlinks everywhere
- snapshot isolation: starting a run freezes content; later library edits/deletes
  do not change the run snapshot
- cross-tenant: a station from another org cannot be imported, and links are
  organization-scoped
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.user import UserRole
from tests.factories import (
    create_test_challenge,
    create_test_rallye,
    create_test_station,
    create_test_user_with_org,
    get_auth_headers,
    link_challenge_to_rallye_station,
    link_station_to_rallye,
)


@pytest.mark.asyncio
async def test_reuse_station_across_two_rallyes_edit_propagates(client: AsyncClient, db_session: AsyncSession) -> None:
    """One library station linked into two rallyes; editing it shows in both."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@reuse.test", role=UserRole.CREATOR, org_slug="reuse-org"
    )
    headers = get_auth_headers(creator)

    station = await create_test_station(db_session, org, title={"de": "Rathaus", "en": "City Hall"})
    rallye_a = await create_test_rallye(db_session, org, title={"de": "Rallye A"})
    rallye_b = await create_test_rallye(db_session, org, title={"de": "Rallye B"})
    await link_station_to_rallye(db_session, org, rallye_a, station, sort_order=0)
    await link_station_to_rallye(db_session, org, rallye_b, station, sort_order=0)

    for rallye in (rallye_a, rallye_b):
        resp = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations", headers=headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["station"]["id"] == str(station.id)

    detail = await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", headers=headers)
    assert detail.status_code == 200
    assert len(detail.json()["usage"]["rallye_ids"]) == 2

    upd = await client.put(
        f"/api/orgs/{org.slug}/stations/{station.id}",
        headers=headers,
        json={"title": {"de": "Neues Rathaus", "en": "New City Hall"}},
    )
    assert upd.status_code == 200

    for rallye in (rallye_a, rallye_b):
        resp = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations", headers=headers)
        title = resp.json()["items"][0]["station"]["title"]
        assert title == {"de": "Neues Rathaus", "en": "New City Hall"}


@pytest.mark.asyncio
async def test_unlink_station_keeps_library_station(client: AsyncClient, db_session: AsyncSession) -> None:
    """Unlinking a station from a rallye keeps the library station alive."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@unlink.test", role=UserRole.CREATOR, org_slug="unlink-org"
    )
    headers = get_auth_headers(creator)

    station = await create_test_station(db_session, org)
    rallye = await create_test_rallye(db_session, org)
    await link_station_to_rallye(db_session, org, rallye, station, sort_order=0)

    unlink = await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{station.id}", headers=headers)
    assert unlink.status_code == 204

    listing = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations", headers=headers)
    assert listing.json()["items"] == []

    detail = await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["usage"]["rallye_ids"] == []


@pytest.mark.asyncio
async def test_reorder_updates_per_rallye_order(client: AsyncClient, db_session: AsyncSession) -> None:
    """PUT .../stations/order changes the per-rallye sort_order only."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@order.test", role=UserRole.CREATOR, org_slug="order-org"
    )
    headers = get_auth_headers(creator)

    s1 = await create_test_station(db_session, org, title={"de": "S1"})
    s2 = await create_test_station(db_session, org, title={"de": "S2"})
    s3 = await create_test_station(db_session, org, title={"de": "S3"})
    rallye = await create_test_rallye(db_session, org)
    await link_station_to_rallye(db_session, org, rallye, s1, sort_order=0)
    await link_station_to_rallye(db_session, org, rallye, s2, sort_order=1)
    await link_station_to_rallye(db_session, org, rallye, s3, sort_order=2)

    reorder = await client.put(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/order",
        headers=headers,
        json={"ids": [str(s3.id), str(s2.id), str(s1.id)]},
    )
    assert reorder.status_code == 200
    ordered = [item["station"]["id"] for item in reorder.json()["items"]]
    assert ordered == [str(s3.id), str(s2.id), str(s1.id)]

    bad = await client.put(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/order",
        headers=headers,
        json={"ids": [str(s1.id), str(s2.id)]},
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_delete_station_in_use_returns_usage_then_unlinks_everywhere(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """DELETE /stations/{id} returns a usage body, then cascade-unlinks all rallyes."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@del.test", role=UserRole.CREATOR, org_slug="del-org"
    )
    headers = get_auth_headers(creator)

    station = await create_test_station(db_session, org)
    challenge = await create_test_challenge(db_session, org, scoring_rules={"max_points": 25})
    rallye_a = await create_test_rallye(db_session, org, title={"de": "A"})
    rallye_b = await create_test_rallye(db_session, org, title={"de": "B"})
    link_a = await link_station_to_rallye(db_session, org, rallye_a, station, sort_order=0)
    await link_station_to_rallye(db_session, org, rallye_b, station, sort_order=0)
    await link_challenge_to_rallye_station(db_session, org, link_a, challenge, sort_order=0)

    resp = await client.delete(f"/api/orgs/{org.slug}/stations/{station.id}", headers=headers)
    assert resp.status_code == 200
    usage = resp.json()
    assert len(usage["rallye_ids"]) == 2

    assert (await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", headers=headers)).status_code == 404
    for rallye in (rallye_a, rallye_b):
        listing = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations", headers=headers)
        assert listing.json()["items"] == []


@pytest.mark.asyncio
async def test_delete_challenge_in_use_returns_usage_then_unlinks(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """DELETE /challenges/{id} returns usage, then unlinks from all stations."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@delc.test", role=UserRole.CREATOR, org_slug="delc-org"
    )
    headers = get_auth_headers(creator)

    station = await create_test_station(db_session, org)
    challenge = await create_test_challenge(db_session, org)
    rallye = await create_test_rallye(db_session, org)
    link = await link_station_to_rallye(db_session, org, rallye, station, sort_order=0)
    await link_challenge_to_rallye_station(db_session, org, link, challenge, sort_order=0)

    resp = await client.delete(f"/api/orgs/{org.slug}/challenges/{challenge.id}", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["station_ids"]) == 1

    listing = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges", headers=headers
    )
    assert listing.json()["items"] == []


@pytest.mark.asyncio
async def test_snapshot_isolation_from_later_library_edits(client: AsyncClient, db_session: AsyncSession) -> None:
    """Starting a run freezes content; later library edit/delete does not change it."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@snap.test", role=UserRole.CREATOR, org_slug="snap-org"
    )
    headers = get_auth_headers(creator)

    station = await create_test_station(db_session, org, title={"de": "Original Station"})
    challenge = await create_test_challenge(
        db_session, org, challenge_type=ChallengeType.TEXT, title={"de": "Original Challenge"}
    )
    rallye = await create_test_rallye(db_session, org, is_published=True)
    link = await link_station_to_rallye(db_session, org, rallye, station, sort_order=0)
    await link_challenge_to_rallye_station(db_session, org, link, challenge, sort_order=0)

    # Create + start a run (freezes the snapshot).
    run_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Frozen Run"},
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["id"]
    start = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start.status_code == 200

    # Mutate the library AFTER the snapshot: edit the station, delete the challenge.
    await client.put(
        f"/api/orgs/{org.slug}/stations/{station.id}",
        headers=headers,
        json={"title": {"de": "Changed Station"}},
    )
    await client.delete(f"/api/orgs/{org.slug}/challenges/{challenge.id}", headers=headers)

    stations = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    assert stations.status_code == 200
    run_stations = stations.json()["items"]
    assert len(run_stations) == 1
    assert run_stations[0]["title"]["de"] == "Original Station"
    run_station_id = run_stations[0]["id"]

    challenges = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=headers,
    )
    assert challenges.status_code == 200
    run_challenges = challenges.json()["items"]
    assert len(run_challenges) == 1
    assert run_challenges[0]["title"]["de"] == "Original Challenge"


@pytest.mark.asyncio
async def test_cross_tenant_cannot_import_other_orgs_station(client: AsyncClient, db_session: AsyncSession) -> None:
    """A rallye in org A cannot import (link) a library station owned by org B."""
    creator_a, org_a = await create_test_user_with_org(
        db_session, email="creator@org-a.test", role=UserRole.CREATOR, org_slug="ct-org-a"
    )
    _creator_b, org_b = await create_test_user_with_org(
        db_session, email="creator@org-b.test", role=UserRole.CREATOR, org_slug="ct-org-b"
    )
    headers_a = get_auth_headers(creator_a)

    station_b = await create_test_station(db_session, org_b, title={"de": "Org B Station"})
    rallye_a = await create_test_rallye(db_session, org_a)

    resp = await client.post(
        f"/api/orgs/{org_a.slug}/rallyes/{rallye_a.id}/stations/import",
        headers=headers_a,
        json={"station_id": str(station_b.id)},
    )
    assert resp.status_code == 404

    missing = await client.post(
        f"/api/orgs/{org_a.slug}/rallyes/{rallye_a.id}/stations/import",
        headers=headers_a,
        json={"station_id": str(uuid4())},
    )
    assert missing.status_code == 404

    detail = await client.get(f"/api/orgs/{org_a.slug}/stations/{station_b.id}", headers=headers_a)
    assert detail.status_code == 404


@pytest.mark.asyncio
async def test_import_existing_station_and_duplicate_link_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Importing an existing library station links it; a duplicate link is 409."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@import.test", role=UserRole.CREATOR, org_slug="import-org"
    )
    headers = get_auth_headers(creator)

    station = await create_test_station(db_session, org)
    rallye = await create_test_rallye(db_session, org)

    first = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/import",
        headers=headers,
        json={"station_id": str(station.id)},
    )
    assert first.status_code == 201
    assert first.json()["station"]["id"] == str(station.id)

    dup = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/import",
        headers=headers,
        json={"station_id": str(station.id)},
    )
    assert dup.status_code == 409
