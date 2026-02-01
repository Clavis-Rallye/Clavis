"""
Station repository.

CRUD on library stations, scoped to the organization. A station is an
organization-wide library item that rallyes reference. The repository also
aggregates where a station is used, for the library views.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import ColumnElement, asc, desc, distinct, func, or_, select
from sqlalchemy.orm import lazyload, selectinload

from app.models.rallye import Rallye
from app.models.rallye_station import RallyeStation
from app.models.station import Station
from app.models.user import User
from app.repositories.base import TenantScopedRepository
from app.repositories.library_query import base_lang, display_title_key, normalize_query, title_matches


class StationRepository(TenantScopedRepository[Station]):
    """
    Repository for library Station entities.

    All queries filter by organization_id and by is_deleted == False.

    Custom Methods:
        get_usage: Aggregate where a station is used
        search_library: Search, sort and page the library
    """

    model = Station

    async def get_usage(self, station_id: UUID, *, published_only: bool = False) -> dict[str, Any]:
        """
        Build the usage payload for a single library station.

        Returns a dict matching StationUsage:
            rallye_ids / rallye_titles: the non-deleted rallyes that link this station,
            in link order

        Challenges belong to a rallye station, so they are not part of the usage of
        a library station. Queries are scoped to the organization and skip
        soft-deleted rallyes. ``published_only`` also skips the rallyes that are not
        published (the participant view, the same rule as ``search_library``).
        """
        rallye_stmt = (
            select(Rallye)
            .join(RallyeStation, RallyeStation.rallye_id == Rallye.id)
            .where(RallyeStation.organization_id == self.organization_id)
            .where(RallyeStation.station_id == station_id)
            .where(Rallye.is_deleted == False)  # noqa: E712
            .order_by(RallyeStation.sort_order.asc())
            .options(lazyload("*"))
        )
        if published_only:
            rallye_stmt = rallye_stmt.where(Rallye.is_published == True)  # noqa: E712
        rallye_rows = (await self.session.execute(rallye_stmt)).scalars().all()
        rallye_ids = [r.id for r in rallye_rows]
        rallye_titles = [r.title for r in rallye_rows]

        return {
            "rallye_ids": rallye_ids,
            "rallye_titles": rallye_titles,
        }

    def _usage_count_expr(self, *, published_only: bool = False) -> ColumnElement[int]:
        """Correlated scalar subquery: distinct non-archived rallyes using this station.

        ``published_only`` counts only the published rallyes, so that the order of
        ``sort=usage`` follows the same count as the usage the caller sees.
        """
        stmt = (
            select(func.count(distinct(RallyeStation.rallye_id)))
            .join(Rallye, Rallye.id == RallyeStation.rallye_id)
            .where(RallyeStation.station_id == Station.id)
            .where(RallyeStation.organization_id == self.organization_id)
            .where(Rallye.is_deleted == False)  # noqa: E712
        )
        if published_only:
            stmt = stmt.where(Rallye.is_published == True)  # noqa: E712
        return stmt.correlate(Station).scalar_subquery()

    async def search_library(
        self,
        *,
        q: str | None,
        sort: str,
        order: str,
        lang: str,
        skip: int = 0,
        limit: int = 100,
        include_deleted: bool = False,
        published_only: bool = False,
    ) -> tuple[list[Station], int]:
        """
        Search, sort and page the station library.

        One filtered select scoped to this organization, excluding archived rows
        unless ``include_deleted``. ``q`` matches any title language or the address.
        ``published_only`` narrows to stations linked into a published, non-archived
        rallye of this organization (the participant view), and ``sort=usage`` then
        counts only such rallyes. The total counts the same filtered select before
        paging.
        """
        normalized_q = normalize_query(q)
        lang_code = base_lang(lang)

        filtered = select(Station).where(Station.organization_id == self.organization_id)
        if not include_deleted:
            filtered = filtered.where(Station.is_deleted == False)  # noqa: E712
        if published_only:
            published_ids = (
                select(RallyeStation.station_id)
                .join(Rallye, Rallye.id == RallyeStation.rallye_id)
                .where(RallyeStation.organization_id == self.organization_id)
                .where(Rallye.is_deleted == False)  # noqa: E712
                .where(Rallye.is_published == True)  # noqa: E712
            )
            filtered = filtered.where(Station.id.in_(published_ids))
        if normalized_q is not None:
            filtered = filtered.where(
                or_(
                    title_matches(Station.title, normalized_q),
                    Station.address.icontains(normalized_q, autoescape=True),
                )
            )

        total_stmt = select(func.count()).select_from(filtered.subquery())
        total = (await self.session.execute(total_stmt)).scalar_one()

        title_key = display_title_key(Station.title, lang_code)
        sort_expr: ColumnElement[Any]
        if sort == "title":
            sort_expr = title_key
        elif sort == "usage":
            sort_expr = self._usage_count_expr(published_only=published_only)
        elif sort == "author":
            sort_expr = func.lower(func.coalesce(User.display_name, "")).collate("und-x-icu")
        else:
            sort_expr = cast("ColumnElement[Any]", Station.created_at)

        direction = asc if order == "asc" else desc
        page_stmt = filtered.order_by(direction(sort_expr), title_key.asc(), Station.id.asc())
        if sort == "author":
            page_stmt = page_stmt.outerjoin(User, User.id == Station.created_by_user_id)
        page_stmt = page_stmt.offset(skip).limit(limit).options(lazyload("*"), selectinload(Station.created_by))

        result = await self.session.execute(page_stmt)
        stations = list(result.scalars().all())
        return stations, total
