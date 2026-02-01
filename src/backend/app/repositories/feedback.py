"""Feedback repository, scoped to an organization."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.models.feedback import Feedback
from app.repositories.base import TenantScopedRepository


class FeedbackRepository(TenantScopedRepository[Feedback]):
    """
    Feedback has no ``is_deleted`` column. It is removed only with its run or organization,
    so ``_base_query`` filters by organization only. ``get_run_summary`` does not start
    from ``_base_query``, so its selects repeat the organization filter themselves.
    """

    model = Feedback
    has_soft_delete = False

    async def list_by_run(
        self,
        run_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Feedback]:
        """List the feedback of a run, newest first."""
        stmt = (
            self._base_query()
            .where(Feedback.run_id == run_id)
            .order_by(Feedback.created_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_run_summary(self, run_id: UUID) -> dict[str, Any]:
        """
        Aggregate the feedback of a run into a dict:

        - average_rating: the mean rating as a float, None without feedback
        - count: the number of feedback rows
        - rating_distribution: the number of rows per rating value, {} without feedback
        """
        avg_and_count_stmt = (
            select(
                func.avg(Feedback.rating).label("average_rating"),
                func.count(Feedback.id).label("count"),
            )
            .where(Feedback.organization_id == self.organization_id)
            .where(Feedback.run_id == run_id)
        )

        avg_and_count_result = await self.session.execute(avg_and_count_stmt)
        avg_and_count_row = avg_and_count_result.one()

        average_rating = avg_and_count_row.average_rating
        count = avg_and_count_row.count

        distribution_stmt = (
            select(
                Feedback.rating,
                func.count(Feedback.id).label("count"),
            )
            .where(Feedback.organization_id == self.organization_id)
            .where(Feedback.run_id == run_id)
            .group_by(Feedback.rating)
            .order_by(Feedback.rating)
        )

        distribution_result = await self.session.execute(distribution_stmt)
        distribution_rows = distribution_result.all()

        rating_distribution = {row.rating: row.count for row in distribution_rows}

        return {
            "average_rating": float(average_rating) if average_rating is not None else None,
            "count": count,
            "rating_distribution": rating_distribution,
        }
