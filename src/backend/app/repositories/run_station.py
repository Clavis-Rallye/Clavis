"""Run station repository. A run station is the frozen copy of a station, made when the run starts."""

from uuid import UUID

from app.models.run_station import RunStation
from app.repositories.base import TenantScopedRepository


class RunStationRepository(TenantScopedRepository[RunStation]):
    """
    Run stations are never changed or soft-deleted. They are removed only with their run
    or organization, so ``_base_query`` filters by organization and not by ``is_deleted``.
    """

    model = RunStation
    has_soft_delete = False

    async def list_by_run(self, run_id: UUID) -> list[RunStation]:
        stmt = self._base_query().where(self.model.run_id == run_id).order_by(self.model.sort_order.asc())

        result = await self.session.execute(stmt)
        return list(result.scalars().all())
