"""Integration tests for the station tips and links popup.

A station may carry an optional ``tips`` blob: a customizable multilingual
popup header plus a list of external links with customizable multilingual
labels. Validated by ``schemas.StationTips``; frozen into ``run_stations``
at run start.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.journey_helpers import setup_journey_environment

TIPS = {
    "header": {"de": "Mehr über den Dom", "en": "More about the cathedral"},
    "links": [
        {
            "label": {"de": "Integreat-Artikel", "en": "Integreat article"},
            "url": "https://integreat.app/augsburg/de",
        },
        {"label": {"de": "Öffnungszeiten"}, "url": "https://example.org/hours"},
    ],
}


@pytest.mark.asyncio
async def test_create_station_with_tips_roundtrip(client: AsyncClient, db_session: AsyncSession):
    """201 — tips are stored and returned verbatim on create and get."""
    env = await setup_journey_environment(db_session, client, org_slug="tips-create")

    response = await client.post(
        f"/api/orgs/{env.slug}/stations/",
        headers=env.auth_headers["creator"],
        json={
            "title": {"de": "Dom"},
            "description": {"de": "Der Augsburger Dom"},
            "tips": TIPS,
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["tips"] == TIPS

    get_response = await client.get(
        f"/api/orgs/{env.slug}/stations/{created['id']}",
        headers=env.auth_headers["creator"],
    )
    assert get_response.status_code == 200
    assert get_response.json()["tips"] == TIPS


@pytest.mark.asyncio
async def test_create_station_tips_rejects_non_http_url(client: AsyncClient, db_session: AsyncSession):
    """422 — javascript:/other schemes are rejected."""
    env = await setup_journey_environment(db_session, client, org_slug="tips-badurl")

    response = await client.post(
        f"/api/orgs/{env.slug}/stations/",
        headers=env.auth_headers["creator"],
        json={
            "title": {"de": "Dom"},
            "description": {"de": "x"},
            "tips": {
                "header": {"de": "Tipps"},
                "links": [{"label": {"de": "Böse"}, "url": "javascript:alert(1)"}],
            },
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_station_tips_takes_a_heading_of_several_sentences(client: AsyncClient, db_session: AsyncSession):
    """A rich text heading of two sentences is longer than 120 characters with its markup, and is accepted."""
    env = await setup_journey_environment(db_session, client, org_slug="tips-long")
    heading = (
        "<p>Hier findest du mehr zum Dom. Die Seite der Stadt nennt auch die Öffnungszeiten, "
        "den Eintritt und den Weg dorthin.</p>"
    )
    label = (
        "Öffnungszeiten, Eintritt und Führungen im Dom, auf der Seite der Stadt Augsburg "
        "und des Bistums, mit einem Plan für Gruppen"
    )
    assert 120 < len(heading) <= 2000
    assert 120 < len(label) <= 200
    tips = {"header": {"de": heading}, "links": [{"label": {"de": label}, "url": "https://www.augsburg.de"}]}

    resp = await client.post(
        f"/api/orgs/{env.slug}/stations/",
        json={"title": {"de": "Dom"}, "description": {"de": "x"}, "tips": tips},
        headers=env.auth_headers["creator"],
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["tips"]["header"]["de"] == heading

    too_long = {"header": {"de": "x" * 2001}, "links": tips["links"]}
    resp = await client.post(
        f"/api/orgs/{env.slug}/stations/",
        json={"title": {"de": "Dom 2"}, "description": {"de": "x"}, "tips": too_long},
        headers=env.auth_headers["creator"],
    )
    assert resp.status_code == 422
    assert "at most 2000 characters" in resp.text

    too_long_label = {
        "header": {"de": "Mehr"},
        "links": [{"label": {"de": "x" * 201}, "url": "https://www.augsburg.de"}],
    }
    resp = await client.post(
        f"/api/orgs/{env.slug}/stations/",
        json={"title": {"de": "Dom 3"}, "description": {"de": "x"}, "tips": too_long_label},
        headers=env.auth_headers["creator"],
    )
    assert resp.status_code == 422
    assert "at most 200 characters" in resp.text


@pytest.mark.asyncio
async def test_station_created_inside_a_rallye_keeps_its_tips(client: AsyncClient, db_session: AsyncSession):
    """The route that creates a station inside a rallye stores the tips."""
    env = await setup_journey_environment(db_session, client, org_slug="tips-rallye")
    headers = env.auth_headers["creator"]

    rallye = await client.post(
        f"/api/orgs/{env.slug}/rallyes/",
        headers=headers,
        json={"title": {"de": "Domviertel"}, "description": {"de": "x"}},
    )
    assert rallye.status_code == 201, rallye.text

    response = await client.post(
        f"/api/orgs/{env.slug}/rallyes/{rallye.json()['id']}/stations",
        headers=headers,
        json={"title": {"de": "Dom"}, "description": {"de": "x"}, "tips": TIPS},
    )
    assert response.status_code == 201, response.text
    station = response.json()["station"]
    assert station["tips"] == TIPS

    stored = await client.get(f"/api/orgs/{env.slug}/stations/{station['id']}", headers=headers)
    assert stored.status_code == 200
    assert stored.json()["tips"] == TIPS


@pytest.mark.asyncio
async def test_create_station_tips_rejects_more_than_ten_links(client: AsyncClient, db_session: AsyncSession):
    """422 — link list is capped at 10."""
    env = await setup_journey_environment(db_session, client, org_slug="tips-toomany")

    response = await client.post(
        f"/api/orgs/{env.slug}/stations/",
        headers=env.auth_headers["creator"],
        json={
            "title": {"de": "Dom"},
            "description": {"de": "x"},
            "tips": {
                "header": {"de": "Tipps"},
                "links": [{"label": {"de": f"Link {i}"}, "url": f"https://example.org/{i}"} for i in range(11)],
            },
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_station_tips_set_and_clear(client: AsyncClient, db_session: AsyncSession):
    """Tips can be added via update; explicit null clears; omission keeps."""
    env = await setup_journey_environment(db_session, client, org_slug="tips-update")
    station_id = str(env.station_ids[0])

    set_response = await client.put(
        f"/api/orgs/{env.slug}/stations/{station_id}",
        headers=env.auth_headers["creator"],
        json={"tips": TIPS},
    )
    assert set_response.status_code == 200
    assert set_response.json()["tips"] == TIPS

    keep_response = await client.put(
        f"/api/orgs/{env.slug}/stations/{station_id}",
        headers=env.auth_headers["creator"],
        json={"address": "Domplatz 1"},
    )
    assert keep_response.status_code == 200
    assert keep_response.json()["tips"] == TIPS

    clear_response = await client.put(
        f"/api/orgs/{env.slug}/stations/{station_id}",
        headers=env.auth_headers["creator"],
        json={"tips": None},
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["tips"] is None


@pytest.mark.asyncio
async def test_run_snapshot_freezes_tips(client: AsyncClient, db_session: AsyncSession):
    """Tips are copied into the run snapshot at start and survive later template edits."""
    env = await setup_journey_environment(db_session, client, org_slug="tips-freeze")
    station_id = str(env.station_ids[0])

    set_response = await client.put(
        f"/api/orgs/{env.slug}/stations/{station_id}",
        headers=env.auth_headers["creator"],
        json={"tips": TIPS},
    )
    assert set_response.status_code == 200

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Tips Freeze Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]
    start_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_response.status_code == 200

    # Mutate the template AFTER the snapshot
    clear_response = await client.put(
        f"/api/orgs/{env.slug}/stations/{station_id}",
        headers=env.auth_headers["creator"],
        json={"tips": None},
    )
    assert clear_response.status_code == 200

    # The frozen run station still carries the tips
    list_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["coordinator"],
    )
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    frozen = [s for s in items if s["source_station_id"] == station_id]
    assert len(frozen) == 1
    assert frozen[0]["tips"] == TIPS
    others = [s for s in items if s["source_station_id"] != station_id]
    assert all(s["tips"] is None for s in others)
