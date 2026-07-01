"""
Run departure repository. It records that a guest left a team of a run, because such a
guest may not join the run again.
"""

from uuid import UUID

from app.models.run_departure import RunDeparture
from app.repositories.base import TenantScopedRepository


class RunDepartureRepository(TenantScopedRepository[RunDeparture]):
    """Departures are immutable evidence rows without soft delete, so ``_base_query`` has no ``is_deleted`` filter."""

    model = RunDeparture
    has_soft_delete = False

    async def has_departed(self, user_id: UUID, run_id: UUID) -> bool:
        """Return True if this user has left a team in this run before."""
        stmt = self._base_query().where(
            self.model.user_id == user_id,
            self.model.run_id == run_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def record(self, user_id: UUID, run_id: UUID) -> None:
        """
        Record a departure. A second leave of the same run by the same user is a no-op: the
        check before the insert skips it, and the unique constraint on (run_id, user_id) is
        the backstop.
        """
        if await self.has_departed(user_id, run_id):
            return
        departure = RunDeparture(
            organization_id=self.organization_id,
            run_id=run_id,
            user_id=user_id,
        )
        self.session.add(departure)
        await self.session.flush()
