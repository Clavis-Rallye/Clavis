"""
The outside search service of the address search: Nominatim or Photon.

Every request leaves from this server with a User-Agent that names the
application, as both public services ask. The service sees the address of the
server and the search text. It does not see the creator's browser.

The public Nominatim instance allows one request per second, so a gate spaces
the calls of this process. Answers are kept for a day, which its policy asks for.
"""

import asyncio
import time
from typing import Any, Literal

import httpx

from app import ABOUT_URL, __version__
from app.config import get_settings
from app.schemas.geocode import GeocodeHit
from app.services.geo_text import classify

_TIMEOUT_SECONDS = 8.0
_NOMINATIM_INTERVAL_SECONDS = 1.1
# Half width of the region box around the point the creator looks at, in degrees.
_REGION_HALF_SPAN = 0.25
_CACHE_SECONDS = 24 * 3600
_CACHE_ENTRIES = 500

_TOWN_KEYS = ("city", "town", "village", "municipality", "hamlet")
_DISTRICT_KEYS = ("suburb", "city_district", "quarter", "neighbourhood")


class UpstreamError(Exception):
    """The outside service did not answer or refused."""


_gate_lock = asyncio.Lock()
_last_nominatim_call = 0.0
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


def _user_agent(task: str = "") -> str:
    """The User-Agent of every outside request: product, version, optional task, public site, contact.

    `task` names a job that is not the address search, for example "geo import".
    """
    contact = get_settings().geocoder.contact.strip()
    about = ABOUT_URL + (f"; {contact}" if contact else "")
    return f"Clavis/{__version__}{f' {task}' if task else ''} (+{about})"


async def _get(path: str, params: dict[str, str], language: str) -> Any:
    """One GET against the configured service, cached, and gated for Nominatim."""
    global _last_nominatim_call
    geocoder = get_settings().geocoder
    key = (geocoder.upstream, geocoder.base_url, path, tuple(sorted(params.items())), language)
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    headers = {"User-Agent": _user_agent(), "Accept": "application/json", "Accept-Language": language}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            if geocoder.upstream == "nominatim":
                async with _gate_lock:
                    wait = _last_nominatim_call + _NOMINATIM_INTERVAL_SECONDS - time.monotonic()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    _last_nominatim_call = time.monotonic()
                    response = await client.get(f"{geocoder.base_url}{path}", params=params, headers=headers)
            else:
                response = await client.get(f"{geocoder.base_url}{path}", params=params, headers=headers)
    except httpx.HTTPError as error:
        raise UpstreamError(str(error)) from error
    if response.status_code != 200:
        raise UpstreamError(f"{geocoder.upstream} answered {response.status_code}")
    data = response.json()
    if len(_cache) >= _CACHE_ENTRIES:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic() + _CACHE_SECONDS, data)
    return data


def _first(values: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    return next((str(values[key]) for key in keys if values.get(key)), None)


def _kind(name: str | None, housenumber: str | None, is_street: bool) -> Literal["poi", "street", "address"]:
    if is_street:
        return "street"
    if housenumber and not name:
        return "address"
    return "poi" if name else "street"


def nominatim_hit(item: dict[str, Any]) -> GeocodeHit:
    """A hit from one object of a Nominatim answer with ``addressdetails=1``."""
    address: dict[str, Any] = item.get("address") or {}
    street = _first(address, ("road", "pedestrian", "square", "footway"))
    housenumber = address.get("house_number")
    is_street = item.get("category") == "highway"
    name = item.get("name") or None
    if name in (street, housenumber) or is_street:
        street, name = street or name, None
    classified = classify({str(item.get("category", "")): str(item.get("type", ""))}) if name else None
    kind = _kind(name, housenumber, is_street)
    return GeocodeHit(
        lat=float(item["lat"]),
        lng=float(item["lon"]),
        kind=kind,
        category=classified[0] if classified and kind == "poi" else kind,
        name=name,
        street=street,
        housenumber=housenumber,
        postcode=address.get("postcode"),
        city=_first(address, _TOWN_KEYS),
        district=_first(address, _DISTRICT_KEYS),
        source="nominatim",
    )


def photon_hit(feature: dict[str, Any]) -> GeocodeHit:
    """A hit from one feature of a Photon answer."""
    properties: dict[str, Any] = feature.get("properties") or {}
    lng, lat = feature["geometry"]["coordinates"][:2]
    is_street = properties.get("type") == "street" or properties.get("osm_key") == "highway"
    name = properties.get("name") or None
    street = properties.get("street")
    housenumber = properties.get("housenumber")
    if is_street:
        street, name = street or name, None
    classified = classify({str(properties.get("osm_key", "")): str(properties.get("osm_value", ""))}) if name else None
    kind = _kind(name, housenumber, is_street)
    return GeocodeHit(
        lat=float(lat),
        lng=float(lng),
        kind=kind,
        category=classified[0] if classified and kind == "poi" else kind,
        name=name,
        street=street,
        housenumber=housenumber,
        postcode=properties.get("postcode"),
        city=_first(properties, ("city", "town", "village")),
        district=_first(properties, ("district", "locality")),
        source="photon",
    )


def _with_origin(hits: list[GeocodeHit], origin: Literal["region", "outside"]) -> list[GeocodeHit]:
    """The hits of the open pass come from outside the region: they say so."""
    if origin == "region":
        return hits
    return [hit.model_copy(update={"origin": "outside"}) for hit in hits]


async def upstream_search(
    query: str, lat: float, lng: float, limit: int, language: str
) -> tuple[list[GeocodeHit], bool]:
    """Hits of the outside service, and whether the search was widened.

    The region box is asked first. Only when it is empty the service is asked
    for everywhere, and the second value is True. Hits of the bounded pass lie
    in the region, hits of the open pass outside it.
    """
    upstream = get_settings().geocoder.upstream
    west, east = lng - _REGION_HALF_SPAN, lng + _REGION_HALF_SPAN
    south, north = lat - _REGION_HALF_SPAN, lat + _REGION_HALF_SPAN
    if upstream == "nominatim":
        params = {
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": str(limit),
            "q": query,
            "viewbox": f"{west},{north},{east},{south}",
        }
        data = await _get("/search", {**params, "bounded": "1"}, language)
        widened = not data
        if widened:
            data = await _get("/search", {**params, "bounded": "0"}, language)
        return _with_origin([nominatim_hit(item) for item in data], "outside" if widened else "region"), widened
    if upstream == "photon":
        params = {"q": query, "limit": str(limit), "lat": str(lat), "lon": str(lng), "lang": _photon_language(language)}
        data = await _get("/api", {**params, "bbox": f"{west},{south},{east},{north}"}, language)
        widened = not data.get("features")
        if widened:
            data = await _get("/api", params, language)
        return _with_origin(
            [photon_hit(feature) for feature in data.get("features", [])], "outside" if widened else "region"
        ), widened
    return [], False


async def upstream_reverse(lat: float, lng: float, language: str) -> GeocodeHit | None:
    """The address of a point from the outside service, or None."""
    upstream = get_settings().geocoder.upstream
    if upstream == "nominatim":
        params = {"format": "jsonv2", "addressdetails": "1", "lat": str(lat), "lon": str(lng)}
        data = await _get("/reverse", params, language)
        if not isinstance(data, dict) or "lat" not in data:
            return None
        return nominatim_hit({**data, "lat": lat, "lon": lng})
    if upstream == "photon":
        data = await _get("/reverse", {"lat": str(lat), "lon": str(lng), "lang": _photon_language(language)}, language)
        features = data.get("features") or []
        if not features:
            return None
        hit = photon_hit(features[0])
        return hit.model_copy(update={"lat": lat, "lng": lng})
    return None


def _photon_language(language: str) -> str:
    return language if language in {"de", "en", "fr"} else "default"
