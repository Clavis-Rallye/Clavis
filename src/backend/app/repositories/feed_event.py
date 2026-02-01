"""Feed event repository, scoped to an organization."""

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select

from app.models.feed_event import FeedEvent, FeedEventType
from app.repositories.base import TenantScopedRepository

# The run-end notice while a submission is still ungraded. The publication finds it again
# by the ``interim`` key, and an older row without that key by its German line.
INTERIM_RESULT_NOTICE: dict[str, Any] = {
    "de": "Der Lauf ist beendet. Die Bewertung läuft noch, die Rangliste ist ein Zwischenstand.",
    "en": "The run has ended. Grading is still under way, the leaderboard is an interim one.",
    "interim": True,
}


class FeedEventRepository(TenantScopedRepository[FeedEvent]):
    """Feed events have no soft delete, so ``_base_query`` filters by organization and not by ``is_deleted``."""

    model = FeedEvent
    has_soft_delete = False

    async def list_by_run(
        self,
        run_id: UUID,
        skip: int = 0,
        limit: int = 100,
        viewer_team_id: UUID | None = None,
        latest: bool = False,
        broadcast_only: bool = False,
    ) -> list[FeedEvent]:
        """
        List the feed events of a run, oldest first.

        A broadcast (``target_team_id`` NULL) reaches everyone, a targeted event only its team.
        ``viewer_team_id=None`` returns every event, for a coordinator or above.
        ``broadcast_only`` returns the broadcasts only, for a participant in no team of the run.
        ``latest`` takes the window from the newest end, and ``skip`` then counts back from
        the newest event. The result stays oldest first.
        """
        stmt = self._base_query().where(FeedEvent.run_id == run_id)
        if broadcast_only:
            stmt = stmt.where(FeedEvent.target_team_id.is_(None))
        elif viewer_team_id is not None:
            stmt = stmt.where(
                or_(
                    FeedEvent.target_team_id.is_(None),
                    FeedEvent.target_team_id == viewer_team_id,
                )
            )
        if latest:
            stmt = stmt.order_by(FeedEvent.created_at.desc()).offset(skip).limit(limit)
            result = await self.session.execute(stmt)
            return list(reversed(result.scalars().all()))

        stmt = stmt.order_by(FeedEvent.created_at.asc()).offset(skip).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_run(self, run_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(FeedEvent)
            .where(FeedEvent.organization_id == self.organization_id)
            .where(FeedEvent.run_id == run_id)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def delete_result_link_notices(self, run_id: UUID) -> int:
        """
        Remove the run's "result is published" notices (``result_link`` true) and return their
        number. Does not commit.

        A publish replaces the earlier notice, and a withdrawal and the sweep of an expired
        link remove it. So the feed holds one such notice while the result is published and
        none otherwise. The run-end RESULTS_AVAILABLE event has no ``result_link`` and stays,
        as does every other event.
        """
        stmt = (
            delete(FeedEvent)
            .where(FeedEvent.organization_id == self.organization_id)
            .where(FeedEvent.run_id == run_id)
            .where(FeedEvent.event_type == FeedEventType.RESULTS_AVAILABLE)
            .where(FeedEvent.content["result_link"].as_boolean().is_(True))
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def delete_interim_result_notices(self, run_id: UUID) -> int:
        """
        Remove the run's interim run-end notices ("grading is still under way") and return
        their number. Does not commit.

        The publication of the result calls this. A result is published only once every
        submission is graded, so the interim line is false from then on and the "result is
        published" notice stands alone. Nothing writes the interim notice again, so a
        withdrawal does not bring it back. The run-end notice of a fully graded run and every
        other event stay.
        """
        stmt = (
            delete(FeedEvent)
            .where(FeedEvent.organization_id == self.organization_id)
            .where(FeedEvent.run_id == run_id)
            .where(FeedEvent.event_type == FeedEventType.RESULTS_AVAILABLE)
            .where(
                or_(
                    FeedEvent.content["interim"].as_boolean().is_(True),
                    FeedEvent.content["de"].as_string() == INTERIM_RESULT_NOTICE["de"],
                )
            )
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
