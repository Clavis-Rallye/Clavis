"""Schemas of the address search."""

from typing import Literal

from pydantic import BaseModel, Field


class GeocodeHit(BaseModel):
    """One search hit as address parts. The frontend builds the short address from them."""

    lat: float
    lng: float
    kind: Literal["poi", "street", "address"]
    category: str = Field(description="Coarse group for the result list, for example 'authority' or 'stop'")
    name: str | None = None
    street: str | None = None
    housenumber: str | None = None
    postcode: str | None = None
    city: str | None = None
    district: str | None = None
    source: Literal["local", "nominatim", "photon"]
    origin: Literal["region", "outside"] = Field(
        default="region",
        description=(
            "Where a hit lies: the place index and the bounded pass of the outside service stay in "
            "the region, only the open pass reaches outside it"
        ),
    )


class GeocodeResults(BaseModel):
    """The hits for one query."""

    results: list[GeocodeHit]
    widened: bool = Field(
        default=False,
        description="The region gave nothing, so every hit comes from outside it",
    )


class GeocodeReverse(BaseModel):
    """The address of a point, or null when no source knows one."""

    result: GeocodeHit | None


class GeocodePoint(BaseModel):
    """A point on the map."""

    lat: float
    lng: float


class GeocodeConfig(BaseModel):
    """What the search field needs to know about the installation."""

    typeahead: bool = Field(description="Suggestions while typing are available")
    sources: list[Literal["local", "nominatim", "photon"]] = Field(
        description="Where hits come from, for the attribution line"
    )
    centre: GeocodePoint | None = Field(
        default=None,
        description=(
            "Where the station map starts before a pin exists: GEOCODER_MAP_CENTRE, else the middle "
            "of the place index. Null when neither exists, and the frontend uses its own fallback"
        ),
    )
    tile_url: str = Field(
        default="",
        description="Tile server of both maps, with {z}, {x} and {y}. The maps load tiles only from here",
    )
    tile_attribution: str = Field(
        default="",
        description=(
            "Attribution of the map tiles as HTML, the setting MAP_TILE_ATTRIBUTION. "
            "Empty: the frontend derives the attribution from the tile host"
        ),
    )
