"""
Text rules of the address search: one normal form for the index and
for the query, and the coarse category of an OpenStreetMap object.
"""

import re
import unicodedata

_STREET_SUFFIX = re.compile(r"str(?:\.|asse\b)")
_NOT_WORD = re.compile(r"[^a-z0-9/]+")
_HOUSE_NUMBER = re.compile(r"^\d+(?:[a-z]|/\d+)?$")


def normalize(text: str) -> str:
    """Lower case, no umlauts or accents, "straße" and "str." as "str", words split by one blank.

    "Königsplatz", "Konigsplatz" and "Koenigsplatz" get the same form, and so do
    "Maximilianstraße", "Maximilianstrasse" and "Maximilianstr.".
    """
    lowered = text.lower().replace("ß", "ss")
    for umlaut, base in (("ä", "a"), ("ö", "o"), ("ü", "u")):
        lowered = lowered.replace(umlaut, base)
    folded = "".join(c for c in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(c))
    for digraph, base in (("ae", "a"), ("oe", "o"), ("ue", "u")):
        folded = folded.replace(digraph, base)
    folded = _STREET_SUFFIX.sub("str ", folded)
    return _NOT_WORD.sub(" ", folded).strip()


def is_house_number(token: str) -> bool:
    """True for "4", "4a" and "26/3". A postcode has five digits and is none."""
    return bool(_HOUSE_NUMBER.match(token)) and not (token.isdigit() and len(token) >= 5)


_CULTURE = {"theatre", "cinema", "arts_centre", "library", "community_centre", "conference_centre", "events_venue"}
_AUTHORITY = {"townhall", "courthouse", "police", "public_building", "fire_station", "post_office"}
_HEALTH = {"hospital", "clinic", "doctors", "pharmacy", "dentist"}
_FOOD = {"restaurant", "cafe", "bar", "pub", "fast_food", "biergarten", "ice_cream"}
_SIGHT = {"attraction", "viewpoint", "zoo", "theme_park", "artwork"}
_LEISURE_BIG = {"park", "garden", "stadium", "nature_reserve"}
_AREA = {"city", "town", "village", "suburb", "quarter", "neighbourhood", "hamlet", "square"}
_STOP_TRANSPORT = {"platform", "stop_position"}


def classify(tags: dict[str, str]) -> tuple[str, int] | None:
    """The category and the weight of a named OpenStreetMap object, or None to leave it out.

    The weight puts a town hall before a street and a street before a shop. An
    object with a Wikidata or Wikipedia entry is well known, so it gains 15.
    """
    if tags.get("type") in {"route", "route_master", "restriction", "multipolygon_role"} or "route" in tags:
        return None
    amenity = tags.get("amenity", "")
    tourism = tags.get("tourism", "")
    leisure = tags.get("leisure", "")
    railway = tags.get("railway", "")
    transport = tags.get("public_transport", "")
    category, weight = "other", 30
    if railway in {"station", "halt"} or transport == "station":
        category, weight = "station", 80
    elif tags.get("highway") == "bus_stop" or railway == "tram_stop" or transport in _STOP_TRANSPORT:
        category, weight = "stop", 30
    elif amenity == "townhall":
        category, weight = "authority", 80
    elif amenity in _AUTHORITY or "government" in tags or tags.get("office") in {"government", "employment_agency"}:
        category, weight = "authority", 65
    elif tourism in {"museum", "gallery"} or amenity in _CULTURE:
        category, weight = "culture", 65
    elif tourism in _SIGHT or "historic" in tags:
        category, weight = "sight", 65
    elif amenity in {"university", "college"}:
        category, weight = "university", 65
    elif amenity in {"school", "kindergarten", "music_school", "language_school"}:
        category, weight = "school", 60 if amenity == "school" else 45
    elif amenity == "place_of_worship":
        category, weight = "worship", 70 if tags.get("building") == "cathedral" else 60
    elif amenity in _HEALTH or "healthcare" in tags:
        category, weight = "health", 65 if amenity == "hospital" else 40
    elif leisure:
        category, weight = "leisure", 65 if leisure in _LEISURE_BIG else 45
    elif tags.get("place") in _AREA or tags.get("boundary") == "administrative":
        category, weight = "area", 70
    elif "highway" in tags:
        return "street", 55
    elif tags.get("shop"):
        category, weight = "shop", 55 if tags["shop"] == "mall" else 35
    elif amenity in _FOOD:
        category, weight = "food", 35
    elif tourism in {"hotel", "hostel", "guest_house", "apartment"}:
        category, weight = "lodging", 35
    elif "office" in tags or amenity:
        category, weight = "other", 40
    elif "natural" in tags or "waterway" in tags or tags.get("landuse") in {"forest", "cemetery"}:
        category, weight = "nature", 40
    elif "building" in tags:
        category, weight = "building", 40
    if "wikidata" in tags or "wikipedia" in tags:
        weight = min(100, weight + 15)
    return category, weight
