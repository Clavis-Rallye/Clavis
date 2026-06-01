"""
RallyeStation repository.

Repository for the rallye<->station association (composition links). All queries
are organization-scoped. Links are hard rows without soft delete: unlinking
deletes the row.
"""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.rallye_station import RallyeStation
from app.models.station import Station


class RallyeStationRepository:
    """
    Repository for RallyeStation link rows.

    Not a TenantScopedRepository subclass, because links are hard rows with no
    soft-delete column. Every query is still scoped by organization_id.
    """

    def __init__(self, session: AsyncSession, organization_id: UUID):
        self.session = session
        self.organization_id = organization_id

    async def list_by_rallye(self, rallye_id: UUID) -> list[RallyeStation]:
        """List links for a rallye, ordered by sort_order, with non-deleted stations."""
        stmt = (
            select(RallyeStation)
            .join(Station, Station.id == RallyeStation.station_id)
            .where(RallyeStation.organization_id == self.organization_id)
            .where(RallyeStation.rallye_id == rallye_id)
            .where(Station.is_deleted == False)  # noqa: E712
            .order_by(RallyeStation.sort_order.asc())
            .options(selectinload(RallyeStation.station).selectinload(Station.created_by))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_rallye(self, rallye_id: UUID) -> int:
        """Count links for a rallye whose station is not soft-deleted."""
        stmt = (
            select(func.count(RallyeStation.id))
            .join(Station, Station.id == RallyeStation.station_id)
            .where(RallyeStation.organization_id == self.organization_id)
            .where(RallyeStation.rallye_id == rallye_id)
            .where(Station.is_deleted == False)  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def get_by_id(self, rallye_station_id: UUID) -> RallyeStation | None:
        """Fetch a single rallye<->station link by its id, scoped to the org (station eager-loaded)."""
        stmt = (
            select(RallyeStation)
            .where(RallyeStation.organization_id == self.organization_id)
            .where(RallyeStation.id == rallye_station_id)
            .options(selectinload(RallyeStation.station).selectinload(Station.created_by))
        )
        result = await self.session.execute(stmt)
        link: RallyeStation | None = result.scalar_one_or_none()
        return link

    async def get_link(self, rallye_id: UUID, station_id: UUID) -> RallyeStation | None:
        """Fetch a single rallye<->station link, scoped to the org."""
        stmt = (
            select(RallyeStation)
            .where(RallyeStation.organization_id == self.organization_id)
            .where(RallyeStation.rallye_id == rallye_id)
            .where(RallyeStation.station_id == station_id)
        )
        result = await self.session.execute(stmt)
        link: RallyeStation | None = result.scalar_one_or_none()
        return link

    async def next_sort_order(self, rallye_id: UUID) -> int:
        """Return max(sort_order)+1 for the rallye, or 0 if empty."""
        stmt = (
            select(func.max(RallyeStation.sort_order))
            .where(RallyeStation.organization_id == self.organization_id)
            .where(RallyeStation.rallye_id == rallye_id)
        )
        result = await self.session.execute(stmt)
        max_order = result.scalar_one_or_none()
        return (max_order + 1) if max_order is not None else 0

    async def create(self, rallye_id: UUID, station_id: UUID, sort_order: int) -> RallyeStation:
        """Create a new link row (organization_id forced to the scoped org)."""
        link = RallyeStation(
            organization_id=self.organization_id,
            rallye_id=rallye_id,
            station_id=station_id,
            sort_order=sort_order,
        )
        self.session.add(link)
        await self.session.flush()
        await self.session.refresh(link)
        return link

    async def delete_link(self, rallye_id: UUID, station_id: UUID) -> bool:
        """Hard-delete a single link. Returns True if a row was removed."""
        link = await self.get_link(rallye_id, station_id)
        if link is None:
            return False
        await self.session.delete(link)
        await self.session.flush()
        return True

    async def delete_all_for_station(self, station_id: UUID) -> None:
        """Remove every rallye link pointing at a station (cascade on station delete)."""
        stmt = (
            delete(RallyeStation)
            .where(RallyeStation.organization_id == self.organization_id)
            .where(RallyeStation.station_id == station_id)
        )
        await self.session.execute(stmt)
        await self.session.flush()
