"""
Build the rows of the regional place index from OpenStreetMap objects.

The input is the ``elements`` list of an Overpass API answer with ``out tags
center``. The output is plain dicts, one per row of ``geo_places``. Nothing here
touches the database or the network, so the rules are testable on their own.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Any

from app.services.geo_text import classify, normalize

# One cell is about 300 m. The town of a place without an address comes from
# the addresses around it.
_CELL = 0.003
# Two objects with the same name and category in the same 500 m cell are one place.
_DEDUP_CELL = 0.005


# A place is also found by its second names: "Uniklinik" for "Universitätsklinikum".
_OTHER_NAME_TAGS = ("alt_name", "short_name", "official_name", "old_name", "loc_name")


def overpass_query(south: float, west: float, north: float, east: float) -> str:
    """The Overpass query for everything with a name or a house number in the box."""
    return (
        f"[out:json][timeout:300][bbox:{south},{west},{north},{east}];"
        '(nwr["name"];nwr["addr:housenumber"];);out tags center qt;'
    )


def _position(element: dict[str, Any]) -> tuple[float, float] | None:
    centre = element.get("center") or element
    lat, lng = centre.get("lat"), centre.get("lon")
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def _cell(lat: float, lng: float, size: float = _CELL) -> tuple[int, int]:
    return int(lat / size), int(lng / size)


class _TownLookup:
    """The most common postcode and town of the addresses near a point."""

    def __init__(self) -> None:
        self._cells: dict[tuple[int, int], Counter[tuple[str, str]]] = {}

    def add(self, lat: float, lng: float, postcode: str, city: str) -> None:
        self._cells.setdefault(_cell(lat, lng), Counter())[(postcode, city)] += 1

    def near(self, lat: float, lng: float) -> tuple[str | None, str | None]:
        row, col = _cell(lat, lng)
        for ring in range(4):
            votes: Counter[tuple[str, str]] = Counter()
            for d_row in range(-ring, ring + 1):
                for d_col in range(-ring, ring + 1):
                    if max(abs(d_row), abs(d_col)) == ring:
                        votes.update(self._cells.get((row + d_row, col + d_col), {}))
            if votes:
                postcode, city = votes.most_common(1)[0][0]
                return postcode or None, city or None
        return None, None


def _cut(value: str | None, length: int) -> str | None:
    return value[:length] if value else value


def _row(
    *,
    kind: str,
    category: str,
    weight: int,
    name: str | None,
    street: str | None,
    housenumber: str | None,
    postcode: str | None,
    city: str | None,
    district: str | None,
    lat: float,
    lng: float,
    other_names: str = "",
) -> dict[str, Any]:
    name_norm = normalize(f"{street} {housenumber}") if kind == "address" else normalize(name or "")
    words = [name_norm]
    if kind == "poi":
        words.append(normalize(f"{street or ''} {housenumber or ''}"))
    words.append(normalize(f"{postcode or ''} {city or ''} {district or ''} {other_names}"))
    return {
        "kind": kind,
        "category": category,
        "weight": weight,
        "name": _cut(name, 255),
        "street": _cut(street, 255),
        "housenumber": _cut(housenumber, 30),
        "postcode": _cut(postcode, 10),
        "city": _cut(city, 120),
        "district": _cut(district, 120),
        "lat": lat,
        "lng": lng,
        "name_norm": name_norm[:300],
        "search": (" " + " ".join(word for word in words if word))[:700],
    }


def build_rows(elements: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Places, streets and addresses from OpenStreetMap objects, without duplicates."""
    items: list[tuple[dict[str, str], float, float]] = []
    towns = _TownLookup()
    for element in elements:
        tags = element.get("tags") or {}
        position = _position(element)
        if position is None or not tags:
            continue
        items.append((tags, position[0], position[1]))
        if tags.get("addr:city"):
            towns.add(position[0], position[1], tags.get("addr:postcode", ""), tags["addr:city"])

    addresses: dict[tuple[str, str, str], dict[str, Any]] = {}
    places: dict[tuple[str, str, Any], dict[str, Any]] = {}
    streets: dict[tuple[str, str | None], list[tuple[float, float, str | None]]] = {}

    for tags, lat, lng in items:
        near_postcode, near_city = towns.near(lat, lng)
        postcode = tags.get("addr:postcode") or near_postcode
        city = tags.get("addr:city") or near_city
        district = tags.get("addr:suburb")
        street = tags.get("addr:street") or tags.get("addr:place")
        housenumber = tags.get("addr:housenumber")

        if street and housenumber:
            key = (normalize(street), housenumber.lower().replace(" ", ""), postcode or city or "")
            # A building with only an address is a better position than a shop inside it.
            if key not in addresses or "name" not in tags:
                addresses[key] = _row(
                    kind="address",
                    category="address",
                    weight=50,
                    name=None,
                    street=street,
                    housenumber=housenumber,
                    postcode=postcode,
                    city=city,
                    district=district,
                    lat=lat,
                    lng=lng,
                )

        name = tags.get("name")
        if not name:
            continue
        classified = classify(tags)
        if classified is None:
            continue
        category, weight = classified
        if category == "street":
            streets.setdefault((name, city), []).append((lat, lng, postcode))
            continue
        # A stop has one row per town, however many platforms it has.
        where: Any = city if category == "stop" else _cell(lat, lng, _DEDUP_CELL)
        place_key = (normalize(name), category, where)
        known = places.get(place_key)
        if known is None or weight > known["weight"] or (weight == known["weight"] and street and not known["street"]):
            places[place_key] = _row(
                kind="poi",
                category=category,
                weight=weight,
                name=name,
                street=street,
                housenumber=housenumber if street else None,
                postcode=postcode,
                city=city,
                district=district,
                lat=lat,
                lng=lng,
                other_names=" ".join(tags[key] for key in _OTHER_NAME_TAGS if key in tags),
            )

    rows = list(addresses.values()) + list(places.values())
    for (name, city), segments in streets.items():
        # The segment nearest to the middle of all segments stands for the street.
        mid_lat = sum(segment[0] for segment in segments) / len(segments)
        mid_lng = sum(segment[1] for segment in segments) / len(segments)
        lat, lng, postcode = min(segments, key=lambda s: (s[0] - mid_lat) ** 2 + (s[1] - mid_lng) ** 2)
        rows.append(
            _row(
                kind="street",
                category="street",
                weight=55,
                name=name,
                street=None,
                housenumber=None,
                postcode=postcode,
                city=city,
                district=None,
                lat=lat,
                lng=lng,
            )
        )
    return rows
