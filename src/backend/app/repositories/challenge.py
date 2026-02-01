"""
Challenge repository.

CRUD on library challenges, scoped to the organization. A challenge is an
organization-wide library item that stations reference. The repository also
aggregates where a challenge is used, for the library views.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import ColumnElement, Numeric, String, asc, case, desc, distinct, func, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.orm import lazyload, selectinload

from app.models.challenge import Challenge, ChallengeType
from app.models.rallye import Rallye
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.station import Station
from app.models.user import User
from app.repositories.base import TenantScopedRepository
from app.repositories.library_query import base_lang, display_title_key, normalize_query, title_matches

# The type names of the frontend's task library (content:challenge.types), the
# names a creator sorts by. Other UI languages sort by the German names.
_TYPE_SORT_NAMES: dict[str, dict[str, str]] = {
    "de": {
        "photo": "Foto",
        "text": "Text",
        "qr_scan": "QR-Code",
        "quiz": "Quiz",
        "creative": "Kreativ",
        "audio": "Audio",
        "video": "Video",
    },
    "en": {
        "photo": "Photo",
        "text": "Text",
        "qr_scan": "QR code",
        "quiz": "Quiz",
        "creative": "Creative",
        "audio": "Audio",
        "video": "Video",
    },
}


class ChallengeRepository(TenantScopedRepository[Challenge]):
    """
    Repository for library Challenge entities.

    All queries filter by organization_id and by is_deleted == False.

    Custom Methods:
        get_usage: Aggregate where a challenge is used (stations + rallyes)
        search_library: Search, filter, sort and page the library
    """

    model = Challenge

    async def get_usage(self, challenge_id: UUID) -> dict[str, Any]:
        """
        Build the usage payload for a single library challenge.

        A challenge is used where a rallye station holds it, so usage follows
        rallye_station_challenges -> rallye_stations -> (station, rallye).

        Returns a dict matching ChallengeUsage:
            station_ids / station_titles: the stations that use this challenge
            rallye_ids / rallye_titles: the rallyes that use this challenge

        Queries are scoped to the organization and skip soft-deleted stations and rallyes.
        """
        station_stmt = (
            select(Station)
            .join(RallyeStation, RallyeStation.station_id == Station.id)
            .join(
                RallyeStationChallenge,
                RallyeStationChallenge.rallye_station_id == RallyeStation.id,
            )
            .where(RallyeStationChallenge.organization_id == self.organization_id)
            .where(RallyeStationChallenge.challenge_id == challenge_id)
            .where(Station.is_deleted == False)  # noqa: E712
            .distinct()
            .order_by(Station.created_at.asc())
            .options(lazyload("*"))
        )
        stations = (await self.session.execute(station_stmt)).scalars().all()
        station_ids = [s.id for s in stations]
        station_titles = [s.title for s in stations]

        rallye_stmt = (
            select(Rallye)
            .join(RallyeStation, RallyeStation.rallye_id == Rallye.id)
            .join(
                RallyeStationChallenge,
                RallyeStationChallenge.rallye_station_id == RallyeStation.id,
            )
            .where(RallyeStationChallenge.organization_id == self.organization_id)
            .where(RallyeStationChallenge.challenge_id == challenge_id)
            .where(Rallye.is_deleted == False)  # noqa: E712
            .distinct()
            .order_by(Rallye.created_at.asc())
            .options(lazyload("*"))
        )
        rallyes = (await self.session.execute(rallye_stmt)).scalars().all()
        rallye_ids = [r.id for r in rallyes]
        rallye_titles = [r.title for r in rallyes]

        return {
            "station_ids": station_ids,
            "station_titles": station_titles,
            "rallye_ids": rallye_ids,
            "rallye_titles": rallye_titles,
        }

    def _usage_count_expr(self) -> ColumnElement[int]:
        """Correlated scalar subquery: distinct non-archived rallyes using this challenge.

        A challenge placed on two stations of one rallye counts once (DISTINCT
        rallye_id). Scoped on the link table's organization_id.
        """
        return (
            select(func.count(distinct(RallyeStation.rallye_id)))
            .select_from(RallyeStationChallenge)
            .join(RallyeStation, RallyeStation.id == RallyeStationChallenge.rallye_station_id)
            .join(Rallye, Rallye.id == RallyeStation.rallye_id)
            .where(RallyeStationChallenge.challenge_id == Challenge.id)
            .where(RallyeStationChallenge.organization_id == self.organization_id)
            .where(Rallye.is_deleted == False)  # noqa: E712
            .correlate(Challenge)
            .scalar_subquery()
        )

    async def search_library(
        self,
        *,
        q: str | None,
        challenge_type: ChallengeType | None,
        sort: str,
        order: str,
        lang: str,
        skip: int = 0,
        limit: int = 100,
        include_deleted: bool = False,
    ) -> tuple[list[Challenge], int]:
        """
        Search, filter, sort and page the challenge library.

        One filtered select scoped to this organization, excluding archived rows
        unless ``include_deleted``. ``q`` matches any title language. ``challenge_type``
        filters by type. The comparison stays a plain ORM comparison, so the
        parameter is never cast: production stores the native enum, the test
        suite a VARCHAR. The total counts the same filtered select before paging.
        There is no participant clause, because challenges are for creators and above.
        """
        normalized_q = normalize_query(q)
        lang_code = base_lang(lang)

        filtered = select(Challenge).where(Challenge.organization_id == self.organization_id)
        if not include_deleted:
            filtered = filtered.where(Challenge.is_deleted == False)  # noqa: E712
        if challenge_type is not None:
            filtered = filtered.where(Challenge.challenge_type == challenge_type)
        if normalized_q is not None:
            filtered = filtered.where(title_matches(Challenge.title, normalized_q))

        total_stmt = select(func.count()).select_from(filtered.subquery())
        total = (await self.session.execute(total_stmt)).scalar_one()

        title_key = display_title_key(Challenge.title, lang_code)
        sort_expr: ColumnElement[Any]
        if sort == "title":
            sort_expr = title_key
        elif sort == "usage":
            sort_expr = self._usage_count_expr()
        elif sort == "author":
            sort_expr = func.lower(func.coalesce(User.display_name, "")).collate("und-x-icu")
        elif sort == "points":
            # max_points is not type-checked on write, so only a JSON number counts.
            max_points = Challenge.scoring_rules["max_points"]
            sort_expr = case(
                (func.jsonb_typeof(max_points) == "number", sa_cast(max_points.astext, Numeric)),
                else_=0,
            )
        elif sort == "type":
            # Sort by the name the interface shows, not the enum code: the codes
            # put "Kreativ" (creative) before "Foto" (photo). The text cast makes
            # the native-enum schema and the test VARCHAR schema compare alike.
            names = _TYPE_SORT_NAMES.get(lang_code, _TYPE_SORT_NAMES["de"])
            type_code = sa_cast(Challenge.challenge_type, String)
            sort_expr = case(
                *((type_code == code, name) for code, name in names.items()),
                else_=type_code,
            )
        elif sort == "duration":
            sort_expr = func.coalesce(Challenge.time_estimate_minutes, 0)
        else:
            sort_expr = cast("ColumnElement[Any]", Challenge.created_at)

        direction = asc if order == "asc" else desc
        page_stmt = filtered.order_by(direction(sort_expr), title_key.asc(), Challenge.id.asc())
        if sort == "author":
            page_stmt = page_stmt.outerjoin(User, User.id == Challenge.created_by_user_id)
        page_stmt = page_stmt.offset(skip).limit(limit).options(lazyload("*"), selectinload(Challenge.created_by))

        result = await self.session.execute(page_stmt)
        challenges = list(result.scalars().all())
        return challenges, total
