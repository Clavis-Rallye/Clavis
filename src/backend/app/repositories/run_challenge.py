"""Run challenge repository, scoped to an organization. Run challenges are frozen copies."""

from uuid import UUID

from app.models.run_challenge import RunChallenge
from app.repositories.base import TenantScopedRepository


class RunChallengeRepository(TenantScopedRepository[RunChallenge]):
    """
    Run challenges have no soft delete. They are removed only with their run or
    organization, so ``_base_query`` filters by organization and not by ``is_deleted``.
    """

    model = RunChallenge
    has_soft_delete = False

    async def list_by_run_station(
        self,
        run_station_id: UUID,
    ) -> list[RunChallenge]:
        stmt = (
            self._base_query()
            .where(RunChallenge.run_station_id == run_station_id)
            .order_by(RunChallenge.sort_order.asc())
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_run(
        self,
        run_id: UUID,
    ) -> list[RunChallenge]:
        """List the challenges of all run stations of a run, ordered by ``sort_order``."""
        stmt = self._base_query().where(RunChallenge.run_id == run_id).order_by(RunChallenge.sort_order.asc())

        result = await self.session.execute(stmt)
        return list(result.scalars().all())
