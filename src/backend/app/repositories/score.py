"""Score repository, scoped to an organization."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.repositories.base import TenantScopedRepository


class ScoreRepository(TenantScopedRepository[Score]):
    """A score has no soft delete, so ``_base_query`` filters by organization only."""

    model = Score
    has_soft_delete = False

    async def get_by_submission(self, submission_id: UUID) -> Score | None:
        """Get the score of a submission. A submission has at most one score (unique constraint)."""
        stmt = self._base_query().where(Score.submission_id == submission_id)

        result = await self.session.execute(stmt)
        score: Score | None = result.scalar_one_or_none()
        return score

    async def delete(self, score_id: UUID) -> bool:
        """
        Hard-delete a score, when a coordinator re-opens a graded submission so that the
        team can replace the submission. The scoreboard is computed from the remaining
        scores. Return False when the score is not found in this organization.
        """
        score = await self.get_by_id(score_id)
        if score is None:
            return False

        await self.session.delete(score)
        await self.session.flush()

        return True

    async def list_by_run(
        self,
        run_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Score]:
        """
        List the scores of a run, most recently scored first. A dissolved team shows
        nowhere, so its scores are left out.
        """
        stmt = (
            self._base_query()
            .join(Submission, Score.submission_id == Submission.id)
            .join(Team, Team.id == Submission.team_id)
            .where(Submission.run_id == run_id)
            .where(Team.is_deleted == False)  # noqa: E712
            .order_by(Score.scored_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_run(self, run_id: UUID) -> int:
        """Count the scores of a run, those of a dissolved team left out."""
        stmt = (
            select(func.count(Score.id))
            .join(Submission, Score.submission_id == Submission.id)
            .join(Team, Team.id == Submission.team_id)
            .where(Score.organization_id == self.organization_id)
            .where(Submission.run_id == run_id)
            .where(Team.is_deleted == False)  # noqa: E712
        )

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_scoreboard(self, run_id: UUID) -> list[dict[str, Any]]:
        """
        Rank the teams of a run by total points, highest first.

        A team without a score is listed with zeros. Ties are broken by team name, then
        by team id. Teams that are dissolved are left out. Each entry has ``team_id``,
        ``team_name``, ``total_points``, ``total_max_points``, ``scored_count`` and
        ``last_scored_at`` (the team's most recent score, or None).
        """
        stmt = (
            select(
                Team.id.label("team_id"),
                Team.name.label("team_name"),
                func.coalesce(func.sum(Score.points), 0).label("total_points"),
                func.coalesce(func.sum(Score.max_points), 0).label("total_max_points"),
                func.count(Score.id).label("scored_count"),
                func.max(Score.scored_at).label("last_scored_at"),
            )
            .select_from(Team)
            .outerjoin(Submission, Submission.team_id == Team.id)
            .outerjoin(Score, Score.submission_id == Submission.id)
            .where(Team.organization_id == self.organization_id)
            .where(Team.run_id == run_id)
            .where(Team.is_deleted == False)  # noqa: E712
            .group_by(Team.id, Team.name)
            .order_by(func.coalesce(func.sum(Score.points), 0).desc(), Team.name, Team.id)
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        return [
            {
                "team_id": str(row.team_id),
                "team_name": row.team_name,
                "total_points": int(row.total_points),
                "total_max_points": int(row.total_max_points),
                "scored_count": int(row.scored_count),
                "last_scored_at": row.last_scored_at,
            }
            for row in rows
        ]
