"""
Address search for the station form, at /api/geocode.

- GET /config    what the search field may do: suggestions while typing, the sources, and
                 where the map starts
- GET /suggest   hits while the creator types. Never asks the public Nominatim instance
- GET /search    hits for a search the creator sent with Enter or the button
- GET /reverse   the address of a point, for a pin the creator set on the map

A creator or an org admin may call the search endpoints. The read-only config
answers every signed-in role, guest included, because both maps need it. The
regional place index answers first. The outside service of
``GEOCODER_UPSTREAM`` fills up what is missing, from this server, so it never
sees the creator's browser.
"""

import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import require_role
from app.config import TILE_URL_DEFAULT, get_settings
from app.database import get_db
from app.middleware.rate_limit import limiter
from app.schemas.geocode import GeocodeConfig, GeocodeHit, GeocodePoint, GeocodeResults, GeocodeReverse
from app.services.geo_search import index_summary, nearest_address, search_places
from app.services.geo_upstream import UpstreamError, upstream_reverse, upstream_search

logger = logging.getLogger(__name__)

router = APIRouter()

# Creator and org admin only: the search endpoints must not be an open proxy
# to the outside service. The config endpoint below carries a weaker rule of
# its own, because every map viewer reads it.
_SEARCH_ONLY = [Depends(require_role("creator"))]

_LIMIT = 8
_SIZE_TTL_SECONDS = 60.0
# Expiry, row count and middle of the index. Both change only with an import.
_size_cache: tuple[float, int, tuple[float, float] | None] | None = None


def reset_index_size_cache() -> None:
    """Forget the cached index size: after an import, and between tests."""
    global _size_cache
    _size_cache = None


async def _index(db: AsyncSession) -> tuple[int, tuple[float, float] | None]:
    global _size_cache
    if _size_cache is None or _size_cache[0] < time.monotonic():
        size, middle = await index_summary(db)
        _size_cache = (time.monotonic() + _SIZE_TTL_SECONDS, size, middle)
    return _size_cache[1], _size_cache[2]


async def _has_index(db: AsyncSession) -> bool:
    return (await _index(db))[0] > 0


def _language(request: Request, lang: str | None) -> str:
    """The language of the interface, else the first language of the browser, as two letters.

    The outside service names towns in it: "München" or "Munich". German when neither names one.
    """
    header = request.headers.get("Accept-Language", "")
    for candidate in (lang or "", header.split(",")[0]):
        tag = candidate.split("-")[0].strip().lower()
        if len(tag) == 2 and tag.isalpha():
            return tag
    return "de"


def _label(hit: GeocodeHit) -> tuple[str | None, str | None, str | None, str | None]:
    return (hit.name, hit.street, hit.housenumber, hit.city)


def _merge(local: list[GeocodeHit], upstream: list[GeocodeHit]) -> list[GeocodeHit]:
    """Local hits first, then what the outside service adds."""
    merged = list(local)
    for hit in upstream:
        if all(_label(hit) != _label(other) for other in merged):
            merged.append(hit)
    return merged[:_LIMIT]


@router.get("/config", response_model=GeocodeConfig, dependencies=[Depends(require_role("guest"))])
async def geocode_config(
    db: AsyncSession = Depends(get_db),
) -> GeocodeConfig:
    """Say whether the installation suggests addresses while typing, where hits come from, and where the map starts.

    Open to every signed-in role, guests included, because every map needs it. The
    response lists the sources in use (`local` for the regional place index,
    `nominatim` or `photon` for the outside service), the start of the map and the
    tile server of the map.
    """
    settings = get_settings().geocoder
    upstream = settings.upstream
    size, middle = await _index(db)
    has_index = size > 0
    sources: list[Literal["local", "nominatim", "photon"]] = ["local"] if has_index else []
    if upstream != "none":
        sources.append(upstream)
    # The operator's setting wins over the middle of the index.
    start = settings.centre or middle
    centre = GeocodePoint(lat=round(start[0], 5), lng=round(start[1], 5)) if start else None
    return GeocodeConfig(
        typeahead=has_index or upstream == "photon",
        sources=sources,
        centre=centre,
        tile_url=settings.map_tile_url or TILE_URL_DEFAULT,
        tile_attribution=settings.map_tile_attribution,
    )


@router.get("/suggest", response_model=GeocodeResults, dependencies=_SEARCH_ONLY)
@limiter.limit("240/minute")
async def geocode_suggest(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    lang: str | None = Query(default=None, max_length=10),
    db: AsyncSession = Depends(get_db),
) -> GeocodeResults:
    """Suggest addresses and places while the creator types.

    Open to creators and organization admins, up to 240 requests per minute. The
    regional place index answers when the installation has one. Otherwise a
    Photon service answers when it is configured and the query has at least three
    characters. In every other case, and when the service fails, the list is empty.
    """
    if await _has_index(db):
        return GeocodeResults(results=await search_places(db, q, lat, lng, _LIMIT))
    if get_settings().geocoder.upstream == "photon" and len(q.strip()) >= 3:
        try:
            hits, widened = await upstream_search(q, lat, lng, _LIMIT, _language(request, lang))
            return GeocodeResults(results=hits[:_LIMIT], widened=widened and bool(hits))
        except UpstreamError as error:
            logger.warning("geocode suggest: upstream failed: %s", error)
    return GeocodeResults(results=[])


@router.get("/search", response_model=GeocodeResults, dependencies=_SEARCH_ONLY)
@limiter.limit("60/minute")
async def geocode_search(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    lang: str | None = Query(default=None, max_length=10),
    db: AsyncSession = Depends(get_db),
) -> GeocodeResults:
    """Search addresses and places for a search that the creator sent.

    Open to creators and organization admins, up to 60 requests per minute. The
    regional place index answers first. When it holds fewer than eight hits and an
    outside service is configured, the service adds hits after the local ones.
    When the service fails, the local hits stand. When the service fails and there
    is no local hit, the route answers 502.
    """
    local = await search_places(db, q, lat, lng, _LIMIT) if await _has_index(db) else []
    if len(local) >= _LIMIT or get_settings().geocoder.upstream == "none":
        return GeocodeResults(results=local)
    try:
        upstream, widened = await upstream_search(q, lat, lng, _LIMIT, _language(request, lang))
    except UpstreamError as error:
        logger.warning("geocode search: upstream failed: %s", error)
        if local:
            return GeocodeResults(results=local)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The address search service did not answer",
        ) from error
    # Hits from everywhere stand next to nothing from the region: the form says so.
    return GeocodeResults(results=_merge(local, upstream), widened=widened and not local and bool(upstream))


@router.get("/reverse", response_model=GeocodeReverse, dependencies=_SEARCH_ONLY)
@limiter.limit("60/minute")
async def geocode_reverse(
    request: Request,
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    lang: str | None = Query(default=None, max_length=10),
    db: AsyncSession = Depends(get_db),
) -> GeocodeReverse:
    """Return the address nearest to a point on the map.

    Open to creators and organization admins, up to 60 requests per minute. The
    regional place index answers first, then the outside service. The result is
    null when no source knows an address or the service fails. The pin counts
    without an address.
    """
    if await _has_index(db):
        hit = await nearest_address(db, lat, lng)
        if hit is not None:
            return GeocodeReverse(result=hit.model_copy(update={"lat": lat, "lng": lng}))
    try:
        return GeocodeReverse(result=await upstream_reverse(lat, lng, _language(request, lang)))
    except UpstreamError as error:
        logger.warning("geocode reverse: upstream failed: %s", error)
        return GeocodeReverse(result=None)
