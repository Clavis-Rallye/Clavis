"""
Address search: text rules, the import, the search in the regional
index, the outside service and the endpoints.

No test reaches the network. The outside service is replaced in every test
that could call it.
"""

from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import ABOUT_URL, __version__, cli
from app.cli import _fetch_overpass, _parse_bbox, main
from app.config import GeocoderSettings, get_settings
from app.models.user import UserRole
from app.routers import geocode as geocode_router
from app.schemas.geocode import GeocodeHit
from app.services import geo_upstream
from app.services.geo_import import build_rows
from app.services.geo_search import nearest_address, replace_index, search_places
from app.services.geo_text import classify, is_house_number, normalize
from tests.factories import create_test_organization, create_test_user, get_auth_headers

CENTRE = {"lat": 48.3705, "lng": 10.8978}


def _node(lat: float, lng: float, **tags: str) -> dict[str, Any]:
    return {
        "type": "node",
        "lat": lat,
        "lon": lng,
        "tags": {key.replace("__", ":"): value for key, value in tags.items()},
    }


def _address(lat: float, lng: float, street: str, number: str, postcode: str = "86150", city: str = "Augsburg") -> dict:
    return _node(lat, lng, addr__street=street, addr__housenumber=number, addr__postcode=postcode, addr__city=city)


# A small Augsburg: real names, positions rounded.
ELEMENTS: list[dict[str, Any]] = [
    _address(48.3688, 10.8987, "Rathausplatz", "1"),
    _address(48.3687, 10.8986, "Rathausplatz", "2"),
    _address(48.3660, 10.8990, "Maximilianstraße", "4"),
    _address(48.3640, 10.8995, "Maximilianstraße", "41"),
    _address(48.4050, 10.8850, "Rathausplatz", "1", "86368", "Gersthofen"),
    _node(
        48.3687,
        10.8986,
        name="Rathaus",
        amenity="townhall",
        addr__street="Rathausplatz",
        addr__housenumber="2",
        addr__postcode="86150",
        addr__city="Augsburg",
        wikidata="Q1",
    ),
    _node(48.3689, 10.8984, name="Rathausplatz", highway="pedestrian"),
    _node(48.3690, 10.8985, name="Rathausplatz", highway="bus_stop"),
    _node(48.3691, 10.8986, name="Rathausplatz", highway="bus_stop"),
    _node(48.4051, 10.8851, name="Rathausplatz", highway="residential"),
    _node(48.3661, 10.8990, name="Maximilianstraße", highway="residential"),
    _node(48.3641, 10.8994, name="Maximilianstraße", highway="residential"),
    _node(48.3750, 10.8930, name="Karolinenstraße", highway="residential"),
    _node(48.3730, 10.8960, name="Hofgarten", leisure="park", wikidata="Q2"),
    _node(48.3690, 10.9050, name="Fuggerei", tourism="attraction", wikidata="Q3"),
    _node(48.3655, 10.8860, name="Augsburg Hauptbahnhof", railway="station"),
    _node(48.3330, 10.8980, name="Universität Augsburg", amenity="university", wikidata="Q4"),
    _node(48.3340, 10.8990, name="Universitätsviertel", place="suburb"),
    _node(48.3840, 10.8370, name="Universitätsklinikum Augsburg", amenity="hospital", short_name="Uniklinik"),
    _node(48.3660, 10.8940, name="Neue Stadtbücherei", amenity="library"),
    _node(
        48.2670,
        10.8300,
        name="Stadtbücherei",
        amenity="library",
        addr__city="Bobingen",
        addr__postcode="86399",
        addr__street="Pestalozzistraße",
        addr__housenumber="1",
    ),
    _node(48.3700, 10.9000, name="Bus 32: Zoo => Hauptbahnhof", type="route", route="bus"),
    {
        "type": "way",
        "center": {"lat": 48.3727, "lon": 10.8965},
        "tags": {"name": "Dom Mariä Heimsuchung", "amenity": "place_of_worship", "building": "cathedral"},
    },
]


class TestTextRules:
    def test_umlaut_spellings_share_one_form(self) -> None:
        assert normalize("Königsplatz") == normalize("Konigsplatz") == normalize("Koenigsplatz") == "konigsplatz"

    def test_street_spellings_share_one_form(self) -> None:
        forms = {normalize(text) for text in ("Maximilianstraße 4", "Maximilianstrasse 4", "Maximilianstr. 4")}
        assert forms == {"maximilianstr 4"}

    def test_hyphen_and_case_do_not_matter(self) -> None:
        assert normalize("Holbein-Gymnasium") == "holbein gymnasium"

    @pytest.mark.parametrize(
        ("token", "expected"), [("4", True), ("4a", True), ("26/3", True), ("86150", False), ("rathaus", False)]
    )
    def test_house_number_is_not_a_postcode(self, token: str, expected: bool) -> None:
        assert is_house_number(token) is expected

    def test_town_hall_outranks_street_and_shop(self) -> None:
        town_hall = classify({"amenity": "townhall"})
        street = classify({"highway": "residential"})
        shop = classify({"shop": "bakery"})
        assert town_hall and street and shop
        assert town_hall[1] > street[1] > shop[1]
        assert street[0] == "street"

    def test_bus_route_is_left_out(self) -> None:
        assert classify({"type": "route", "route": "bus"}) is None

    def test_known_place_gains_weight(self) -> None:
        plain = classify({"leisure": "park"})
        known = classify({"leisure": "park", "wikidata": "Q1"})
        assert plain and known and known[1] == plain[1] + 15


class TestImport:
    def test_street_segments_become_one_street_per_town(self) -> None:
        streets = [row for row in build_rows(ELEMENTS) if row["kind"] == "street" and row["name"] == "Maximilianstraße"]
        assert len(streets) == 1
        assert streets[0]["city"] == "Augsburg"

    def test_same_street_name_in_two_towns_stays_apart(self) -> None:
        towns = {
            row["city"] for row in build_rows(ELEMENTS) if row["kind"] == "street" and row["name"] == "Rathausplatz"
        }
        assert towns == {"Augsburg", "Gersthofen"}

    def test_platforms_of_one_stop_become_one_row(self) -> None:
        stops = [row for row in build_rows(ELEMENTS) if row["category"] == "stop"]
        assert len(stops) == 1

    def test_place_without_address_takes_the_town_around_it(self) -> None:
        hofgarten = next(row for row in build_rows(ELEMENTS) if row["name"] == "Hofgarten")
        assert (hofgarten["postcode"], hofgarten["city"]) == ("86150", "Augsburg")

    def test_way_uses_its_centre(self) -> None:
        dom = next(row for row in build_rows(ELEMENTS) if row["name"] == "Dom Mariä Heimsuchung")
        assert (dom["lat"], dom["lng"]) == (48.3727, 10.8965)

    def test_second_name_is_searchable(self) -> None:
        clinic = next(row for row in build_rows(ELEMENTS) if row["name"] == "Universitätsklinikum Augsburg")
        assert " uniklinik" in clinic["search"]

    def test_route_and_nameless_object_give_no_place(self) -> None:
        names = {row["name"] for row in build_rows(ELEMENTS)}
        assert "Bus 32: Zoo => Hauptbahnhof" not in names
        assert build_rows([{"type": "node", "lat": 1.0, "lon": 1.0}]) == []


@pytest_asyncio.fixture
async def index(db_session: AsyncSession) -> AsyncSession:
    """The small Augsburg, imported."""
    await replace_index(db_session, build_rows(ELEMENTS))
    await db_session.flush()
    geocode_router.reset_index_size_cache()
    return db_session


def _labels(hits: list[GeocodeHit]) -> list[str]:
    return [
        ", ".join(part for part in (hit.name, f"{hit.street or ''} {hit.housenumber or ''}".strip(), hit.city) if part)
        for hit in hits
    ]


class TestSearch:
    async def test_beginning_of_a_word_is_enough(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Hofgar", **CENTRE)
        assert _labels(hits)[0] == "Hofgarten, Augsburg"

    async def test_typing_error_still_finds_the_place(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Fugerei", **CENTRE)
        assert _labels(hits) == ["Fuggerei, Augsburg"]

    async def test_missing_umlaut_finds_the_place(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Stadtbucherei", **CENTRE)
        assert "Neue Stadtbücherei, Augsburg" in _labels(hits)

    async def test_street_without_number_gives_the_street_and_no_house(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Maximilianstr", **CENTRE)
        assert [hit.kind for hit in hits] == ["street"]

    async def test_street_with_number_gives_that_house_first(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Maximilianstr. 4", **CENTRE)
        assert _labels(hits)[:2] == ["Maximilianstraße 4, Augsburg", "Maximilianstraße 41, Augsburg"]
        assert hits[0].kind == "address"

    async def test_typing_error_in_a_street_with_number(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Maximiliansrtaße 4", **CENTRE)
        assert _labels(hits) == ["Maximilianstraße 4, Augsburg"]

    async def test_same_address_in_this_town_comes_before_the_next_town(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Rathausplatz 1", **CENTRE)
        assert [hit.city for hit in hits[:2]] == ["Augsburg", "Gersthofen"]

    async def test_place_in_this_town_beats_the_exact_name_far_away(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Stadtbücherei", **CENTRE)
        assert [hit.city for hit in hits] == ["Augsburg", "Bobingen"]

    async def test_whole_word_beats_the_longer_word(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Universität", **CENTRE)
        assert hits[0].name == "Universität Augsburg"

    async def test_town_narrows_the_search(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Rathausplatz Gersthofen", **CENTRE)
        assert [hit.city for hit in hits] == ["Gersthofen"]

    async def test_second_name_finds_the_place(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Uniklinik", **CENTRE)
        assert [hit.name for hit in hits] == ["Universitätsklinikum Augsburg"]

    async def test_stop_next_to_the_square_of_the_same_name_shows_once(self, index: AsyncSession) -> None:
        hits = await search_places(index, "Rathausplatz", **CENTRE)
        augsburg = [hit for hit in hits if hit.name is None and hit.street is None or hit.city == "Augsburg"]
        assert [hit.kind for hit in augsburg if hit.name == "Rathausplatz"] == ["street"]

    async def test_one_letter_and_unknown_text_find_nothing(self, index: AsyncSession) -> None:
        assert await search_places(index, "R", **CENTRE) == []
        assert await search_places(index, "Ausländerbehörde", **CENTRE) == []

    async def test_like_wildcards_in_the_query_are_text(self, index: AsyncSession) -> None:
        assert await search_places(index, "%_%", **CENTRE) == []

    async def test_point_gives_the_nearest_house_then_the_street(self, index: AsyncSession) -> None:
        house = await nearest_address(index, 48.36872, 10.89862)
        assert house is not None and (house.street, house.housenumber) == ("Rathausplatz", "2")
        street = await nearest_address(index, 48.3751, 10.8931)
        assert street is not None and (street.kind, street.name) == ("street", "Karolinenstraße")
        assert await nearest_address(index, 48.30, 10.80) is None


class TestUpstreamMapping:
    def test_nominatim_place_gives_parts_without_districts_state_and_country(self) -> None:
        hit = geo_upstream.nominatim_hit(
            {
                "lat": "48.3731",
                "lon": "10.8960",
                "name": "Hofgarten",
                "category": "leisure",
                "type": "park",
                "address": {
                    "park": "Hofgarten",
                    "suburb": "Innenstadt",
                    "city": "Augsburg",
                    "state": "Bayern",
                    "country": "Deutschland",
                },
            }
        )
        assert (hit.name, hit.street, hit.city, hit.district, hit.kind, hit.category) == (
            "Hofgarten",
            None,
            "Augsburg",
            "Innenstadt",
            "poi",
            "leisure",
        )

    def test_nominatim_house_has_no_name(self) -> None:
        hit = geo_upstream.nominatim_hit(
            {
                "lat": "1",
                "lon": "2",
                "name": "",
                "category": "building",
                "type": "yes",
                "address": {"house_number": "4", "road": "Maximilianstraße", "postcode": "86150", "city": "Augsburg"},
            }
        )
        assert (hit.kind, hit.name, hit.street, hit.housenumber, hit.postcode) == (
            "address",
            None,
            "Maximilianstraße",
            "4",
            "86150",
        )

    def test_nominatim_street_is_a_street(self) -> None:
        hit = geo_upstream.nominatim_hit(
            {
                "lat": "1",
                "lon": "2",
                "name": "Rathausplatz",
                "category": "highway",
                "type": "pedestrian",
                "address": {"road": "Rathausplatz", "city": "Augsburg"},
            }
        )
        assert (hit.kind, hit.name, hit.street) == ("street", None, "Rathausplatz")

    def test_photon_feature_gives_parts(self) -> None:
        hit = geo_upstream.photon_hit(
            {
                "geometry": {"coordinates": [10.8986, 48.3687]},
                "properties": {
                    "name": "Goldener Saal",
                    "street": "Rathausplatz",
                    "housenumber": "2",
                    "postcode": "86150",
                    "city": "Augsburg",
                    "district": "Innenstadt",
                    "osm_key": "tourism",
                    "osm_value": "attraction",
                    "type": "house",
                },
            }
        )
        assert (hit.lat, hit.lng, hit.name, hit.street, hit.housenumber, hit.category, hit.source) == (
            48.3687,
            10.8986,
            "Goldener Saal",
            "Rathausplatz",
            "2",
            "sight",
            "photon",
        )


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Replace the HTTP call of the outside service and record every request."""
    calls: list[tuple[str, dict[str, str], str]] = []
    answers: dict[str, Any] = {"bounded": [], "open": [], "fail": False}

    async def fake_get(path: str, params: dict[str, str], language: str) -> Any:
        calls.append((path, params, language))
        if answers["fail"]:
            raise geo_upstream.UpstreamError("down")
        return answers["bounded"] if params.get("bounded") == "1" or "bbox" in params else answers["open"]

    monkeypatch.setattr(geo_upstream, "_get", fake_get)
    # The tests of the outside service run with the service on. The default is none.
    monkeypatch.setattr(get_settings().geocoder, "upstream", "nominatim")
    geocode_router.reset_index_size_cache()
    return calls, answers


NOMINATIM_MUNICH = {
    "lat": "48.15",
    "lon": "11.58",
    "name": "Ausländerbehörde",
    "category": "office",
    "type": "government",
    "address": {"road": "Seidlstraße", "house_number": "27", "postcode": "80335", "city": "München"},
}


class TestUpstreamSearch:
    async def test_region_is_asked_first_and_everywhere_only_when_it_is_empty(self, upstream) -> None:  # type: ignore[no-untyped-def]
        calls, answers = upstream
        answers["open"] = [NOMINATIM_MUNICH]
        hits, widened = await geo_upstream.upstream_search("Ausländerbehörde", 48.3705, 10.8978, 8, "de")
        assert widened is True
        assert [call[1]["bounded"] for call in calls] == ["1", "0"]
        assert calls[0][1]["viewbox"] == "10.6478,48.6205,11.1478,48.1205"
        assert [hit.city for hit in hits] == ["München"]

    async def test_a_hit_of_the_region_box_is_a_region_hit(self, upstream) -> None:  # type: ignore[no-untyped-def]
        _, answers = upstream
        answers["bounded"] = [NOMINATIM_MUNICH]
        hits, widened = await geo_upstream.upstream_search("Ausländerbehörde", 48.3705, 10.8978, 8, "de")
        assert widened is False
        assert [hit.origin for hit in hits] == ["region"]

    async def test_a_hit_of_the_open_search_is_an_outside_hit(self, upstream) -> None:  # type: ignore[no-untyped-def]
        _, answers = upstream
        answers["open"] = [NOMINATIM_MUNICH]
        hits, widened = await geo_upstream.upstream_search("Ausländerbehörde", 48.3705, 10.8978, 8, "de")
        assert widened is True
        assert [hit.origin for hit in hits] == ["outside"]

    async def test_hit_in_the_region_needs_one_request(self, upstream) -> None:  # type: ignore[no-untyped-def]
        calls, answers = upstream
        answers["bounded"] = [NOMINATIM_MUNICH]
        _, widened = await geo_upstream.upstream_search("x", 48.3705, 10.8978, 8, "de")
        assert widened is False
        assert len(calls) == 1

    def test_user_agent_names_the_application_and_the_contact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings().geocoder, "contact", "it@example.org")
        agent = geo_upstream._user_agent()
        assert agent.startswith("Clavis/") and "it@example.org" in agent
        assert __version__ in agent and ABOUT_URL in agent

    def test_no_user_agent_names_a_host_under_github(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The search and the import both identify the application by its public site."""
        monkeypatch.setattr(get_settings().geocoder, "contact", "it@example.org")
        agents = [geo_upstream._user_agent(), geo_upstream._user_agent("geo import")]
        for agent in agents:
            assert "github.com" not in agent
            assert "Clavis-Rallye/App" not in agent
            assert agent.startswith(f"Clavis/{__version__}") and ABOUT_URL in agent and "it@example.org" in agent
        assert agents[1].startswith(f"Clavis/{__version__} geo import (")


@pytest_asyncio.fixture
async def creator_client(client: AsyncClient, db_session: AsyncSession) -> AsyncClient:
    org = await create_test_organization(db_session, name="Geo Org", slug="geo-org")
    user = await create_test_user(db_session, email="creator@example.com", role=UserRole.CREATOR, organization=org)
    client.headers.update(get_auth_headers(user))
    return client


class TestEndpoints:
    async def test_needs_a_login(self, client: AsyncClient) -> None:
        response = await client.get("/api/geocode/suggest", params={"q": "Rathaus", **CENTRE})
        assert response.status_code == 401

    async def test_participant_is_refused(self, client: AsyncClient, db_session: AsyncSession) -> None:
        org = await create_test_organization(db_session, name="Geo Org", slug="geo-org")
        user = await create_test_user(db_session, email="p@example.com", role=UserRole.PARTICIPANT, organization=org)
        client.headers.update(get_auth_headers(user))
        response = await client.get("/api/geocode/search", params={"q": "Rathaus", **CENTRE})
        assert response.status_code == 403

    @pytest.mark.parametrize("role", [UserRole.GUEST, UserRole.PARTICIPANT])
    async def test_every_map_viewer_reads_the_config(
        self, client: AsyncClient, db_session: AsyncSession, role: UserRole
    ) -> None:
        """Both maps need the tile URL, and guests and participants see maps too."""
        org = await create_test_organization(db_session, name="Geo Org", slug="geo-org")
        user = await create_test_user(db_session, email=f"{role.value}@example.com", role=role, organization=org)
        client.headers.update(get_auth_headers(user))
        config = await client.get("/api/geocode/config")
        assert config.status_code == 200
        assert config.json()["tile_url"] == "https://tile.openstreetmap.de/{z}/{x}/{y}.png"

    @pytest.mark.parametrize("path", ["/api/geocode/suggest", "/api/geocode/search", "/api/geocode/reverse"])
    async def test_guest_and_participant_still_cannot_search(
        self, client: AsyncClient, db_session: AsyncSession, path: str
    ) -> None:
        """The search endpoints stay creator-only, so they are no open proxy to the outside service."""
        org = await create_test_organization(db_session, name="Geo Org", slug="geo-org")
        user = await create_test_user(db_session, email="g@example.com", role=UserRole.GUEST, organization=org)
        client.headers.update(get_auth_headers(user))
        response = await client.get(path, params={"q": "Rathaus", **CENTRE})
        assert response.status_code == 403

    async def test_the_default_sends_no_search_outside(
        self, creator_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def fake_get(path: str, params: dict[str, str], language: str) -> Any:
            calls.append(path)
            return []

        monkeypatch.setattr(geo_upstream, "_get", fake_get)
        geocode_router.reset_index_size_cache()
        assert GeocoderSettings().upstream == "none"
        assert get_settings().geocoder.upstream == "none"
        config = await creator_client.get("/api/geocode/config")
        assert config.json() == {
            "typeahead": False,
            "sources": [],
            "centre": None,
            "tile_url": "https://tile.openstreetmap.de/{z}/{x}/{y}.png",
            "tile_attribution": "",
        }
        search = await creator_client.get("/api/geocode/search", params={"q": "Rathaus", **CENTRE})
        assert search.json() == {"results": [], "widened": False}
        assert calls == []

    async def test_nominatim_without_an_index_searches_on_enter_only(
        self, creator_client: AsyncClient, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        response = await creator_client.get("/api/geocode/config")
        assert response.json() == {
            "typeahead": False,
            "sources": ["nominatim"],
            "centre": None,
            "tile_url": "https://tile.openstreetmap.de/{z}/{x}/{y}.png",
            "tile_attribution": "",
        }

    async def test_imported_index_switches_typeahead_on(
        self, creator_client: AsyncClient, index: AsyncSession, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        response = await creator_client.get("/api/geocode/config")
        body = response.json()
        assert (body["typeahead"], body["sources"]) == (True, ["local", "nominatim"])
        # The map starts in the old town, where the small Augsburg crowds.
        assert body["centre"]["lat"] == pytest.approx(48.3687, abs=0.003)
        assert body["centre"]["lng"] == pytest.approx(10.8986, abs=0.003)

    async def test_the_map_starts_where_the_places_crowd_and_not_at_their_mean(
        self, creator_client: AsyncClient, db_session: AsyncSession, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        town = [_address(48.36880 + i * 0.0001, 10.89860, "Rathausplatz", str(i + 1)) for i in range(4)]
        villages = [_address(48.20 + i * 0.05, 10.70, "Dorfstraße", "1", "86000", f"Dorf {i}") for i in range(3)]
        await replace_index(db_session, build_rows(town + villages))
        await db_session.flush()
        geocode_router.reset_index_size_cache()
        centre = (await creator_client.get("/api/geocode/config")).json()["centre"]
        assert centre == {"lat": pytest.approx(48.36895, abs=1e-5), "lng": pytest.approx(10.8986, abs=1e-5)}

    async def test_the_operator_setting_places_the_map_before_the_index(
        self, creator_client: AsyncClient, index: AsyncSession, upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(get_settings().geocoder, "map_centre", "48.1351, 11.582")
        response = await creator_client.get("/api/geocode/config")
        assert response.json()["centre"] == {"lat": 48.1351, "lng": 11.582}

    async def test_typing_never_asks_nominatim(self, creator_client: AsyncClient, upstream) -> None:  # type: ignore[no-untyped-def]
        calls, _ = upstream
        response = await creator_client.get("/api/geocode/suggest", params={"q": "Rathaus", **CENTRE})
        assert response.json() == {"results": [], "widened": False}
        assert calls == []

    async def test_typing_asks_the_index(self, creator_client: AsyncClient, index: AsyncSession, upstream) -> None:  # type: ignore[no-untyped-def]
        calls, _ = upstream
        response = await creator_client.get("/api/geocode/suggest", params={"q": "Hofgar", **CENTRE})
        first = response.json()["results"][0]
        assert (first["name"], first["city"], first["source"], first["category"]) == (
            "Hofgarten",
            "Augsburg",
            "local",
            "leisure",
        )
        assert calls == []

    async def test_typing_asks_photon_when_there_is_no_index(
        self, creator_client: AsyncClient, upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        calls, answers = upstream
        monkeypatch.setattr(get_settings().geocoder, "upstream", "photon")
        answers["bounded"] = {
            "features": [
                {
                    "geometry": {"coordinates": [10.9, 48.37]},
                    "properties": {
                        "name": "Zoo Augsburg",
                        "city": "Augsburg",
                        "osm_key": "tourism",
                        "osm_value": "zoo",
                    },
                }
            ]
        }
        config = await creator_client.get("/api/geocode/config")
        assert config.json() == {
            "typeahead": True,
            "sources": ["photon"],
            "centre": None,
            "tile_url": "https://tile.openstreetmap.de/{z}/{x}/{y}.png",
            "tile_attribution": "",
        }
        response = await creator_client.get("/api/geocode/suggest", params={"q": "Zoo", **CENTRE})
        assert [hit["name"] for hit in response.json()["results"]] == ["Zoo Augsburg"]
        assert calls[0][0] == "/api" and "bbox" in calls[0][1]

    async def test_sent_search_adds_the_outside_service_after_the_index(
        self, creator_client: AsyncClient, index: AsyncSession, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        _, answers = upstream
        answers["bounded"] = [
            {
                "lat": "48.3731",
                "lon": "10.8960",
                "name": "Hofgarten",
                "category": "leisure",
                "type": "park",
                "address": {"city": "Augsburg"},
            },
            {
                "lat": "48.37",
                "lon": "10.89",
                "name": "Hofgarten-Café",
                "category": "amenity",
                "type": "cafe",
                "address": {"city": "Augsburg"},
            },
        ]
        response = await creator_client.get("/api/geocode/search", params={"q": "Hofgarten", **CENTRE})
        assert [(hit["name"], hit["source"], hit["origin"]) for hit in response.json()["results"]] == [
            ("Hofgarten", "local", "region"),
            ("Hofgarten-Café", "nominatim", "region"),
        ]

    async def test_hits_from_outside_the_region_are_marked(
        self, creator_client: AsyncClient, index: AsyncSession, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        _, answers = upstream
        answers["open"] = [NOMINATIM_MUNICH]
        far = await creator_client.get("/api/geocode/search", params={"q": "Ausländerbehörde", **CENTRE})
        assert far.json()["widened"] is True
        assert [hit["city"] for hit in far.json()["results"]] == ["München"]
        # A hit of the index is in the region, so nothing is marked.
        near = await creator_client.get("/api/geocode/search", params={"q": "Hofgarten", **CENTRE})
        assert near.json()["widened"] is False

    async def test_a_far_hit_of_the_open_search_is_marked_next_to_near_misses_of_the_index(
        self, creator_client: AsyncClient, index: AsyncSession, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        # Near-misses of the index come first, then far hits of the open search. The far hit is marked.
        _, answers = upstream
        answers["open"] = [NOMINATIM_MUNICH]
        response = await creator_client.get("/api/geocode/search", params={"q": "Stadtbücherei", **CENTRE})
        body = response.json()
        # The index gave near-misses, so the search counts as not widened …
        assert body["widened"] is False
        # … and the hit from everywhere still says where it lies.
        assert [(hit["city"], hit["origin"]) for hit in body["results"]] == [
            ("Augsburg", "region"),
            ("Bobingen", "region"),
            ("München", "outside"),
        ]

    async def test_outside_service_is_asked_in_the_language_of_the_interface(
        self, creator_client: AsyncClient, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        calls, _ = upstream
        await creator_client.get(
            "/api/geocode/search",
            params={"q": "Dom", "lang": "de-DE", **CENTRE},
            headers={"Accept-Language": "en-US,en"},
        )
        await creator_client.get(
            "/api/geocode/search", params={"q": "Zoo", **CENTRE}, headers={"Accept-Language": "en-US,en"}
        )
        await creator_client.get("/api/geocode/search", params={"q": "Uni", **CENTRE}, headers={"Accept-Language": "*"})
        assert [call[2] for call in calls if call[1].get("bounded") == "1"] == ["de", "en", "de"]

    async def test_no_outside_service_means_no_request(
        self, creator_client: AsyncClient, index: AsyncSession, upstream, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        calls, _ = upstream
        monkeypatch.setattr(get_settings().geocoder, "upstream", "none")
        config = await creator_client.get("/api/geocode/config")
        assert (config.json()["typeahead"], config.json()["sources"]) == (True, ["local"])
        response = await creator_client.get("/api/geocode/search", params={"q": "Ausländerbehörde", **CENTRE})
        assert response.json() == {"results": [], "widened": False}
        reverse = await creator_client.get("/api/geocode/reverse", params={"lat": 48.30, "lng": 10.80})
        assert reverse.json() == {"result": None}
        assert calls == []

    async def test_failed_outside_service_is_an_error_only_without_local_hits(
        self, creator_client: AsyncClient, index: AsyncSession, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        _, answers = upstream
        answers["fail"] = True
        with_local = await creator_client.get("/api/geocode/search", params={"q": "Hofgarten", **CENTRE})
        assert with_local.status_code == 200 and len(with_local.json()["results"]) == 1
        without = await creator_client.get("/api/geocode/search", params={"q": "Ausländerbehörde", **CENTRE})
        assert without.status_code == 502

    async def test_pin_gets_the_address_of_the_index_at_the_pin(
        self, creator_client: AsyncClient, index: AsyncSession, upstream
    ) -> None:  # type: ignore[no-untyped-def]
        calls, _ = upstream
        response = await creator_client.get("/api/geocode/reverse", params={"lat": 48.36872, "lng": 10.89862})
        result = response.json()["result"]
        assert (result["street"], result["housenumber"], result["lat"], result["lng"]) == (
            "Rathausplatz",
            "2",
            48.36872,
            10.89862,
        )
        assert calls == []

    async def test_pin_outside_the_index_asks_the_outside_service(self, creator_client: AsyncClient, upstream) -> None:  # type: ignore[no-untyped-def]
        _, answers = upstream
        answers["open"] = {
            "lat": "52.5",
            "lon": "13.4",
            "name": "",
            "category": "building",
            "type": "yes",
            "address": {"road": "Unter den Linden", "house_number": "1", "city": "Berlin"},
        }
        response = await creator_client.get("/api/geocode/reverse", params={"lat": 52.5, "lng": 13.4})
        assert response.json()["result"]["street"] == "Unter den Linden"

    async def test_empty_query_is_refused(self, creator_client: AsyncClient) -> None:
        response = await creator_client.get("/api/geocode/suggest", params={"q": "", **CENTRE})
        assert response.status_code == 422


class TestMapCentreSetting:
    def test_a_point_with_blanks_is_read(self) -> None:
        assert GeocoderSettings(map_centre=" 48.3705 , 10.8978 ").centre == (48.3705, 10.8978)

    def test_empty_means_no_setting(self) -> None:
        assert GeocoderSettings(map_centre="").centre is None

    @pytest.mark.parametrize("value", ["48.37", "91,10", "48,181", "Augsburg", "48.3,10.9,0"])
    def test_a_point_the_map_cannot_show_stops_the_start(self, value: str) -> None:
        with pytest.raises(ValidationError):
            GeocoderSettings(map_centre=value)


class TestMapTileSettings:
    def test_empty_means_the_default_tiles(self) -> None:
        assert GeocoderSettings(map_tile_url="").map_tile_url == "https://tile.openstreetmap.de/{z}/{x}/{y}.png"

    def test_blanks_around_a_setting_are_forgotten(self) -> None:
        assert GeocoderSettings(map_tile_url=" https://tiles.example.org/{z}/{x}/{y}.png ").map_tile_url == (
            "https://tiles.example.org/{z}/{x}/{y}.png"
        )

    @pytest.mark.parametrize("value", ["tiles.example.org/{z}/{x}/{y}.png", "https://tiles.example.org/{z}/{x}.png"])
    def test_an_address_the_maps_cannot_use_stops_the_start(self, value: str) -> None:
        with pytest.raises(ValidationError):
            GeocoderSettings(map_tile_url=value)

    async def test_the_config_names_the_tiles_and_the_attribution(
        self, creator_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings().geocoder, "map_tile_url", "https://tiles.example.org/{z}/{x}/{y}.png")
        monkeypatch.setattr(get_settings().geocoder, "map_tile_attribution", "&copy; Example")
        response = await creator_client.get("/api/geocode/config")
        body = response.json()
        assert body["tile_url"] == "https://tiles.example.org/{z}/{x}/{y}.png"
        assert body["tile_attribution"] == "&copy; Example"

    async def test_without_the_attribution_setting_the_field_stays_empty(
        self, creator_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings().geocoder, "map_tile_attribution", "")
        response = await creator_client.get("/api/geocode/config")
        assert response.json()["tile_attribution"] == ""


class TestImportCommand:
    def test_region_is_four_ordered_numbers(self) -> None:
        assert _parse_bbox("48.22,10.72,48.48,11.03") == (48.22, 10.72, 48.48, 11.03)

    @pytest.mark.parametrize("value", ["48.2,10.7,48.4", "48.4,10.7,48.2,11.0", "47,9,50,13", "a,b,c,d"])
    def test_wrong_region_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError):
            _parse_bbox(value)


OVERPASS_TEST_URL = "https://overpass.test/api/interpreter"
AUGSBURG_BOX = (48.22, 10.72, 48.48, 11.03)


def _overpass_answer(status: int, body: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {}, request=httpx.Request("POST", OVERPASS_TEST_URL))


@pytest.fixture
def overpass(monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], list[str], list[float]]:
    """A fake Overpass API. A test fills `answers`, one per request: a response or an exception."""
    answers: list[Any] = []
    calls: list[str] = []
    pauses: list[float] = []

    def fake_post(url: str, **_: Any) -> httpx.Response:
        calls.append(url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[no-any-return]

    monkeypatch.setattr(cli.httpx, "post", fake_post)
    monkeypatch.setattr(cli.time, "sleep", pauses.append)
    return answers, calls, pauses


def test_the_import_sends_the_user_agent_of_the_application(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Overpass download names product, version, public site and contact, and no GitHub host."""
    sent: list[dict[str, str]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        sent.append(kwargs["headers"])
        return _overpass_answer(200, {"elements": []})

    monkeypatch.setattr(cli.httpx, "post", fake_post)
    monkeypatch.setattr(get_settings().geocoder, "contact", "it@example.org")
    _fetch_overpass(OVERPASS_TEST_URL, AUGSBURG_BOX)
    agent = sent[0]["User-Agent"]
    assert agent == f"Clavis/{__version__} geo import (+{ABOUT_URL}; it@example.org)"
    assert "github.com" not in agent

    monkeypatch.setattr(get_settings().geocoder, "contact", "")
    _fetch_overpass(OVERPASS_TEST_URL, AUGSBURG_BOX)
    assert sent[1]["User-Agent"] == f"Clavis/{__version__} geo import (+{ABOUT_URL})"


class TestOverpassRetry:
    def test_a_busy_instance_is_asked_again_with_a_growing_pause(self, overpass, capsys) -> None:  # type: ignore[no-untyped-def]
        answers, calls, pauses = overpass
        answers += [_overpass_answer(502), _overpass_answer(504), _overpass_answer(200, {"elements": ELEMENTS[:2]})]
        assert _fetch_overpass(OVERPASS_TEST_URL, AUGSBURG_BOX) == ELEMENTS[:2]
        assert len(calls) == 3
        assert pauses == [20.0, 40.0]
        out = capsys.readouterr().out
        assert "Overpass attempt 1 of 4 failed: HTTP 502, the instance is busy. Next attempt in 20 s" in out
        assert "Overpass attempt 2 of 4 failed: HTTP 504" in out
        assert "Overpass attempt 3 of 4:" in out

    def test_a_timeout_and_a_rate_limit_count_as_busy(self, overpass) -> None:  # type: ignore[no-untyped-def]
        answers, calls, pauses = overpass
        answers += [httpx.ReadTimeout("slow"), _overpass_answer(429), _overpass_answer(200, {"elements": []})]
        assert _fetch_overpass(OVERPASS_TEST_URL, AUGSBURG_BOX) == []
        assert (len(calls), pauses) == (3, [20.0, 40.0])

    def test_a_refused_query_ends_at_once(self, overpass) -> None:  # type: ignore[no-untyped-def]
        answers, calls, pauses = overpass
        answers += [_overpass_answer(400)]
        with pytest.raises(httpx.HTTPStatusError):
            _fetch_overpass(OVERPASS_TEST_URL, AUGSBURG_BOX)
        assert (len(calls), pauses) == (1, [])

    def test_four_busy_answers_fail_the_import_and_leave_the_index(self, overpass, capsys) -> None:  # type: ignore[no-untyped-def]
        answers, calls, pauses = overpass
        answers += [_overpass_answer(503) for _ in range(4)]
        assert main(["geo", "import", "--bbox", "48.22,10.72,48.48,11.03"]) == 2
        assert (len(calls), pauses) == (4, [20.0, 40.0, 80.0])
        assert "Import failed, the index is unchanged" in capsys.readouterr().err

    def test_the_import_asks_the_endpoint_of_the_setting(self, overpass, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
        answers, calls, _ = overpass
        monkeypatch.setattr(get_settings().geocoder, "overpass_url", "https://overpass.example.org/api/interpreter")
        answers += [_overpass_answer(200, {"elements": []})]
        # An empty region ends the import before the database.
        assert main(["geo", "import", "--bbox", "48.22,10.72,48.48,11.03"]) == 2
        assert calls == ["https://overpass.example.org/api/interpreter"]

    def test_without_the_setting_the_import_asks_the_public_instance(self) -> None:
        assert GeocoderSettings(overpass_url="").overpass_endpoint == "https://overpass-api.de/api/interpreter"

    def test_an_endpoint_that_is_no_web_address_stops_the_start(self) -> None:
        with pytest.raises(ValidationError):
            GeocoderSettings(overpass_url="overpass.example.org/api/interpreter")
