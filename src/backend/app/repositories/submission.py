"""
Submission repository, scoped to an organization.

A submission has no soft-delete. A team replaces its unscored submission in place with
``update_content``.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app.models.submission import Submission
from app.repositories.base import TenantScopedRepository


class SubmissionRepository(TenantScopedRepository[Submission]):
    """
    Repository for Submission entities.

    A submission has no soft-delete. It is never deleted on its own and is removed only
    when its run is hard-deleted. So ``_base_query`` filters by organization only.
    """

    model = Submission
    has_soft_delete = False

    async def list_by_team(
        self,
        team_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Submission]:
        """List the submissions of a team, most recently submitted first."""
        stmt = (
            self._base_query()
            .where(Submission.team_id == team_id)
            .order_by(Submission.submitted_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_run(
        self,
        run_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Submission]:
        """
        List the submissions of a run across all teams and challenges, most recently
        submitted first. A dissolved team shows nowhere, so its submissions are left out.
        """
        from app.models.team import Team

        stmt = (
            self._base_query()
            .join(Team, Team.id == Submission.team_id)
            .where(Submission.run_id == run_id)
            .where(Team.is_deleted == False)  # noqa: E712
            .order_by(Submission.submitted_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_by_id(self, submission_id: UUID) -> Submission | None:
        """
        Get a submission by id unless its team is dissolved.

        The readers of one answer by id use this, so that the answer of a dissolved team
        is not found and they answer 404 as for an answer that does not exist.
        """
        from app.models.team import Team

        stmt = (
            self._base_query()
            .join(Team, Team.id == Submission.team_id)
            .where(Submission.id == submission_id)
            .where(Team.is_deleted == False)  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_team_and_challenge(
        self,
        team_id: UUID,
        run_challenge_id: UUID,
    ) -> Submission | None:
        """
        Get the submission of a team for a challenge.

        A team has at most one live answer per challenge.
        """
        stmt = (
            self._base_query()
            .where(Submission.team_id == team_id)
            .where(Submission.run_challenge_id == run_challenge_id)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_content(
        self,
        submission_id: UUID,
        content: dict[str, Any],
    ) -> Submission | None:
        """
        Replace the content of a submission and set ``submitted_at`` to now.

        Used when a team replaces its unscored answer. The caller must check that the
        submission has no score yet, because a graded answer must be re-opened by a
        coordinator first. Return None when the submission is not found in this
        organization.
        """
        submission = await self.get_by_id(submission_id)
        if submission is None:
            return None

        submission.content = content
        flag_modified(submission, "content")
        submission.submitted_at = datetime.now(UTC)  # type: ignore[assignment]

        await self.session.flush()
        await self.session.refresh(submission)

        return submission

    async def count_by_run(
        self,
        run_id: UUID,
    ) -> int:
        """Count the submissions of a run, those of a dissolved team left out."""
        from app.models.team import Team

        stmt = (
            select(func.count(Submission.id))
            .join(Team, Team.id == Submission.team_id)
            .where(Submission.organization_id == self.organization_id)
            .where(Submission.run_id == run_id)
            .where(Team.is_deleted == False)  # noqa: E712
        )

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def count_unscored_by_run(self, run_id: UUID) -> int:
        from app.models.score import Score
        from app.models.team import Team

        stmt = (
            select(func.count(Submission.id))
            .outerjoin(Score, Score.submission_id == Submission.id)
            .join(Team, Team.id == Submission.team_id)
            .where(Submission.organization_id == self.organization_id)
            .where(Submission.run_id == run_id)
            .where(Score.id.is_(None))
            .where(Team.is_deleted == False)  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
