"""
RallyeStationChallenge repository.

Repository for the rallye-station<->challenge association (per-rallye composition
links). All queries are organization-scoped. Links are hard rows without soft
delete: unlinking deletes the row.
"""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.challenge import Challenge
from app.models.rallye_station_challenge import RallyeStationChallenge


class RallyeStationChallengeRepository:
    """
    Repository for RallyeStationChallenge link rows.

    Not a TenantScopedRepository subclass, because links are hard rows with no
    soft-delete column. Every query is still scoped by organization_id.
    """

    def __init__(self, session: AsyncSession, organization_id: UUID):
        self.session = session
        self.organization_id = organization_id

    async def list_by_rallye_station(self, rallye_station_id: UUID) -> list[RallyeStationChallenge]:
        """List links for a rallye-station, ordered by sort_order, with non-deleted challenges."""
        stmt = (
            select(RallyeStationChallenge)
            .join(Challenge, Challenge.id == RallyeStationChallenge.challenge_id)
            .where(RallyeStationChallenge.organization_id == self.organization_id)
            .where(RallyeStationChallenge.rallye_station_id == rallye_station_id)
            .where(Challenge.is_deleted == False)  # noqa: E712
            .order_by(RallyeStationChallenge.sort_order.asc())
            .options(selectinload(RallyeStationChallenge.challenge).selectinload(Challenge.created_by))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_link(self, rallye_station_id: UUID, challenge_id: UUID) -> RallyeStationChallenge | None:
        """Fetch a single rallye-station<->challenge link, scoped to the org."""
        stmt = (
            select(RallyeStationChallenge)
            .where(RallyeStationChallenge.organization_id == self.organization_id)
            .where(RallyeStationChallenge.rallye_station_id == rallye_station_id)
            .where(RallyeStationChallenge.challenge_id == challenge_id)
        )
        result = await self.session.execute(stmt)
        link: RallyeStationChallenge | None = result.scalar_one_or_none()
        return link

    async def next_sort_order(self, rallye_station_id: UUID) -> int:
        """Return max(sort_order)+1 for the rallye-station, or 0 if empty."""
        stmt = (
            select(func.max(RallyeStationChallenge.sort_order))
            .where(RallyeStationChallenge.organization_id == self.organization_id)
            .where(RallyeStationChallenge.rallye_station_id == rallye_station_id)
        )
        result = await self.session.execute(stmt)
        max_order = result.scalar_one_or_none()
        return (max_order + 1) if max_order is not None else 0

    async def create(self, rallye_station_id: UUID, challenge_id: UUID, sort_order: int) -> RallyeStationChallenge:
        """Create a new link row (organization_id forced to the scoped org)."""
        link = RallyeStationChallenge(
            organization_id=self.organization_id,
            rallye_station_id=rallye_station_id,
            challenge_id=challenge_id,
            sort_order=sort_order,
        )
        self.session.add(link)
        await self.session.flush()
        await self.session.refresh(link)
        return link

    async def delete_link(self, rallye_station_id: UUID, challenge_id: UUID) -> bool:
        """Hard-delete a single link. Returns True if a row was removed."""
        link = await self.get_link(rallye_station_id, challenge_id)
        if link is None:
            return False
        await self.session.delete(link)
        await self.session.flush()
        return True

    async def delete_all_for_rallye_station(self, rallye_station_id: UUID) -> None:
        """Remove every challenge link held by a rallye-station (cascade on link delete)."""
        stmt = (
            delete(RallyeStationChallenge)
            .where(RallyeStationChallenge.organization_id == self.organization_id)
            .where(RallyeStationChallenge.rallye_station_id == rallye_station_id)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def delete_all_for_challenge(self, challenge_id: UUID) -> None:
        """Remove every rallye-station link pointing at a challenge (cascade on challenge delete)."""
        stmt = (
            delete(RallyeStationChallenge)
            .where(RallyeStationChallenge.organization_id == self.organization_id)
            .where(RallyeStationChallenge.challenge_id == challenge_id)
        )
        await self.session.execute(stmt)
        await self.session.flush()
