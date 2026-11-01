"""
Search in the regional place index.

A query matches a row when every word of the query begins a word of the row.
"Rathaus Augs" finds "Rathaus, Rathausplatz 2, 86150 Augsburg". When that finds
too little, a second pass tolerates typing errors with trigram similarity.

The order is one score: how well the name fits, how prominent the place is, and
how far it is from the point the creator looks at.
"""

from typing import Any

from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.geo_place import GeoPlace
from app.schemas.geocode import GeocodeHit
from app.services.geo_text import is_house_number, normalize

# A query of one letter matches a large part of the index and helps nobody.
MIN_QUERY_LENGTH = 2
# The typing-error pass runs only when the exact pass finds nothing, and keeps
# what is nearly as similar as its best hit. Otherwise it adds noise.
_FUZZY_MIN_LENGTH = 4
_FUZZY_THRESHOLD = 0.55
_FUZZY_BAND = 0.15
# Two hits with the same name closer than this are the same place to a creator.
_SAME_PLACE_DEGREES = 0.004
# The side of a cell when the index looks for the centre of its town.
_CELL_DEGREES = 0.005

_COLUMNS = "kind, category, weight, name, street, housenumber, postcode, city, district, lat, lng"

# One degree of longitude is about 0.66 of a degree of latitude at 48 degrees
# north. 3 points per kilometre, at most 45: a place in the next town loses
# against the same kind of place in this town, and still shows.
_DISTANCE = "LEAST(45, 3 * 111 * sqrt(power(lat - :lat, 2) + power((lng - :lng) * 0.66, 2)))"

# name fit: 0 the whole name, 5 the name begins with the whole query as words,
# 8 the name begins with the query, 15 every word is in the name, 60 a word
# matches only the street, the town or a second name.
_SCORE = (
    """
    (CASE WHEN name_norm = :q THEN 0
          WHEN name_norm LIKE :q_words THEN 5
          WHEN name_norm LIKE :q_prefix THEN 8
          WHEN {name_match} THEN 15
          ELSE 60 END)
    - weight + """
    + _DISTANCE
)


def _like(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"% {escaped}%"


def _hit(row: Any) -> GeocodeHit:
    return GeocodeHit(
        lat=row.lat,
        lng=row.lng,
        kind=row.kind,
        category=row.category,
        name=row.name,
        street=row.street,
        housenumber=row.housenumber,
        postcode=row.postcode,
        city=row.city,
        district=row.district,
        source="local",
    )


def _without_repeats(hits: list[GeocodeHit], limit: int) -> list[GeocodeHit]:
    """Drop a hit that names a place the list already holds: a stop next to the square of the same name."""
    kept: list[GeocodeHit] = []
    for hit in hits:
        label = (hit.name, hit.street, hit.housenumber, hit.city)
        repeated = any(
            (other.name, other.street, other.housenumber, other.city) == label
            or (
                hit.name is not None
                and other.name == hit.name
                and abs(other.lat - hit.lat) < _SAME_PLACE_DEGREES
                and abs(other.lng - hit.lng) < _SAME_PLACE_DEGREES
            )
            for other in kept
        )
        if not repeated:
            kept.append(hit)
        if len(kept) == limit:
            break
    return kept


async def index_size(db: AsyncSession) -> int:
    """The number of rows in the index. Zero means the operator imported no region."""
    return int((await db.execute(select(func.count()).select_from(GeoPlace))).scalar_one())


async def index_summary(db: AsyncSession) -> tuple[int, tuple[float, float] | None]:
    """The number of rows in the index, and the centre of its town.

    The centre is the mean position of the rows in the densest cell of about
    500 m by 400 m. Shops, sights and houses crowd in the old town, so that
    cell lies there. The mean of all rows missed it: in Augsburg it fell 2.3 km
    south of the Rathaus, the densest cell 140 m. None when the index is empty.
    """
    count = await index_size(db)
    if count == 0:
        return 0, None
    y = func.floor(GeoPlace.lat / _CELL_DEGREES)
    x = func.floor(GeoPlace.lng / _CELL_DEGREES)
    densest = select(y.label("y"), x.label("x")).group_by(y, x).order_by(func.count().desc(), y, x).limit(1).subquery()
    row = (
        await db.execute(
            select(func.avg(GeoPlace.lat), func.avg(GeoPlace.lng)).where(y == densest.c.y, x == densest.c.x)
        )
    ).one()
    if row[0] is None or row[1] is None:
        return count, None
    return count, (float(row[0]), float(row[1]))


async def search_places(db: AsyncSession, query: str, lat: float, lng: float, limit: int = 8) -> list[GeocodeHit]:
    """The best matches of the index for a text, nearest and most prominent first."""
    q = normalize(query)
    tokens = q.split()
    if len(q) < MIN_QUERY_LENGTH or not tokens:
        return []
    with_number = any(is_house_number(token) for token in tokens)
    escaped = _like(q)[2:-1]
    params: dict[str, Any] = {
        "q": q,
        "q_words": escaped + " %",
        "q_prefix": escaped + "%",
        "lat": lat,
        "lng": lng,
        "n": limit * 3,
    }
    conditions = []
    name_conditions = []
    for index, token in enumerate(tokens):
        params[f"t{index}"] = _like(token)
        conditions.append(f"search LIKE :t{index}")
        name_conditions.append(f"(' ' || name_norm) LIKE :t{index}")
    # Without a house number in the query the answer is a place or a street,
    # never one of the hundred houses of that street.
    if not with_number:
        conditions.append("kind <> 'address'")
    score = _SCORE.format(name_match=" AND ".join(name_conditions))
    sql = f"SELECT {_COLUMNS} FROM geo_places WHERE {' AND '.join(conditions)} ORDER BY {score}, id LIMIT :n"  # noqa: S608
    hits = [_hit(row) for row in (await db.execute(text(sql), params)).all()]
    hits = _without_repeats(hits, limit)
    if hits or len(q) < _FUZZY_MIN_LENGTH:
        return hits

    # Second pass for typing errors: "Fugerei", "Rathausplaz 1".
    fuzzy_q = q
    fuzzy_params: dict[str, Any] = {"lat": lat, "lng": lng, "n": limit * 3, "band": _FUZZY_BAND}
    if with_number:
        numbers = [token for token in tokens if is_house_number(token)]
        fuzzy_q = " ".join(token for token in tokens if not is_house_number(token)) or q
        fuzzy_params["number"] = numbers[-1]
        kind_filter = "kind = 'address' AND lower(replace(housenumber, ' ', '')) = :number"
    else:
        kind_filter = "kind <> 'address'"
    fuzzy_params["q"] = fuzzy_q
    # The operator uses the trigram index. Its threshold is a setting of the transaction.
    await db.execute(
        text("SELECT set_config('pg_trgm.word_similarity_threshold', :threshold, true)"),
        {"threshold": str(_FUZZY_THRESHOLD)},
    )
    fuzzy_sql = f"""
        WITH found AS (
            SELECT {_COLUMNS}, id, word_similarity(:q, name_norm) AS similarity
            FROM geo_places WHERE {kind_filter} AND :q <% name_norm
        )
        SELECT * FROM found WHERE similarity >= (SELECT max(similarity) FROM found) - :band
        ORDER BY (1 - similarity) * 100 - weight + {_DISTANCE}, id
        LIMIT :n
    """  # noqa: S608
    fuzzy = [_hit(row) for row in (await db.execute(text(fuzzy_sql), fuzzy_params)).all()]
    return _without_repeats(fuzzy, limit)


async def nearest_address(db: AsyncSession, lat: float, lng: float) -> GeocodeHit | None:
    """The house address within about 60 m of a point, else the street within about 120 m."""
    for kind, reach in (("address", 0.0006), ("street", 0.0012)):
        sql = f"""
            SELECT {_COLUMNS} FROM geo_places
            WHERE kind = :kind AND lat BETWEEN :south AND :north AND lng BETWEEN :west AND :east
            ORDER BY power(lat - :lat, 2) + power((lng - :lng) * 0.66, 2), id LIMIT 1
        """  # noqa: S608
        box = {"south": lat - reach, "north": lat + reach, "west": lng - reach * 1.5, "east": lng + reach * 1.5}
        row = (await db.execute(text(sql), {"kind": kind, "lat": lat, "lng": lng, **box})).first()
        if row is not None:
            return _hit(row)
    return None


async def replace_index(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Replace the whole index with new rows in the caller's transaction."""
    await db.execute(text("DELETE FROM geo_places"))
    for start in range(0, len(rows), 2000):
        await db.execute(insert(GeoPlace), rows[start : start + 2000])
    return len(rows)
