"""Rallye repository, scoped to an organization."""

from sqlalchemy import func, select

from app.models.rallye import Rallye
from app.repositories.base import TenantScopedRepository


class RallyeRepository(TenantScopedRepository[Rallye]):
    model = Rallye

    async def list_published(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Rallye]:
        """List the published rallyes of this organization, newest first."""
        stmt = (
            self._base_query()
            .where(Rallye.is_published == True)  # noqa: E712
            .order_by(Rallye.created_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_published(self) -> int:
        """Count the published, non-deleted rallyes of this organization."""
        stmt = (
            select(func.count(Rallye.id))
            .where(Rallye.organization_id == self.organization_id)
            .where(Rallye.is_deleted == False)  # noqa: E712
            .where(Rallye.is_published == True)  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
