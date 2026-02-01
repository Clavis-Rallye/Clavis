"""
Organization repository. It is not tenant-scoped, because each organization is a tenant.

Each count of the operator list is one grouped query over all organizations, not one
query per organization.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.rallye import Rallye
from app.models.run import Run, RunState
from app.models.submission import Submission
from app.models.user import User, UserRole
from app.repositories.base import SystemRepository


class OrganizationRepository(SystemRepository[Organization]):
    model = Organization

    async def get_by_slug(self, slug: str) -> Organization | None:
        """Soft-deleted organizations are not found."""
        stmt = self._base_query().where(Organization.slug == slug)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def slug_taken(self, slug: str) -> bool:
        """
        True when any organization holds the slug, a soft-deleted one included.

        The unique constraint on ``slug`` spans deleted rows, so an operator create must
        refuse a retired slug like a live one. ``get_by_slug`` hides deleted organizations.
        """
        stmt = select(Organization.id).where(Organization.slug == slug).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def is_archived(session: AsyncSession, organization_id: UUID) -> bool:
        """
        True while the organization is not deleted and has ``archived_at`` set.

        The one archive check of every login path and session guard: password login, the
        TOTP step, the guest join, ``get_current_user`` and the WebSocket auth.
        Archiving stops access only. The row and all its data stay.
        """
        stmt = select(Organization.archived_at).where(
            Organization.id == organization_id,
            Organization.is_deleted == False,  # noqa: E712
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def due_for_deletion(self, cutoff: datetime) -> list[tuple[UUID, str]]:
        """
        Archived organizations whose deletion is due, as (id, slug) tuples, oldest request first.

        Due means archived, with ``deletion_requested_at`` on or before ``cutoff``. The caller
        (``services/org_deletion.py``) passes ``now - DELETION_GRACE_DAYS``.

        Only columns are selected: a full entity would eager-load every user of every
        candidate through ``users``, and the sweep needs only the id. The result is a
        candidate set, not a guarantee. The sweep locks each one with ``SELECT ... FOR
        UPDATE`` and checks it again, because it may no longer be archived or due.
        """
        stmt = (
            select(Organization.id, Organization.slug)
            .where(
                Organization.is_deleted == False,  # noqa: E712
                Organization.archived_at.isnot(None),
                Organization.deletion_requested_at.isnot(None),
                Organization.deletion_requested_at <= cutoff,
            )
            .order_by(Organization.deletion_requested_at)
        )
        result = await self.session.execute(stmt)
        return list(result.tuples().all())

    async def count_users_by_organization(self) -> dict[UUID, int]:
        """Non-deleted user counts, one grouped query over all organizations."""
        stmt = (
            select(User.organization_id, func.count(User.id))
            .where(User.is_deleted == False)  # noqa: E712
            .group_by(User.organization_id)
        )
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())

    async def count_rallyes_by_organization(self) -> dict[UUID, int]:
        """Non-deleted rallye counts, one grouped query over all organizations."""
        stmt = (
            select(Rallye.organization_id, func.count(Rallye.id))
            .where(Rallye.is_deleted == False)  # noqa: E712
            .group_by(Rallye.organization_id)
        )
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())

    async def count_runs_by_organization(self) -> dict[UUID, int]:
        """Non-deleted run counts, one grouped query over all organizations."""
        stmt = (
            select(Run.organization_id, func.count(Run.id))
            .where(Run.is_deleted == False)  # noqa: E712
            .group_by(Run.organization_id)
        )
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())

    async def count_org_admins_by_organization(self) -> dict[UUID, int]:
        """
        Non-deleted, active org_admin counts, one grouped query over all organizations.

        A deactivated administrator cannot sign in, so it does not count.
        """
        stmt = (
            select(User.organization_id, func.count(User.id))
            .where(User.is_deleted == False, User.is_active == True, User.role == UserRole.ORG_ADMIN)  # noqa: E712
            .group_by(User.organization_id)
        )
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())

    async def count_active_runs_by_organization(self) -> dict[UUID, int]:
        """Active, non-deleted run counts, one grouped query over all organizations."""
        stmt = (
            select(Run.organization_id, func.count(Run.id))
            .where(Run.is_deleted == False, Run.state == RunState.ACTIVE)  # noqa: E712
            .group_by(Run.organization_id)
        )
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())

    async def last_activity_by_organization(self) -> dict[UUID, datetime]:
        """
        Latest activity per organization: the newer of a submission's ``created_at`` and a
        run's ``started_at``. Submissions on a soft-deleted run are left out, as in
        ``count_runs_by_organization``.
        """
        submission_activity = (
            select(Submission.organization_id.label("organization_id"), Submission.created_at.label("activity_at"))
            .join(Run, Run.id == Submission.run_id)
            .where(Run.is_deleted == False)  # noqa: E712
        )
        run_activity = select(Run.organization_id.label("organization_id"), Run.started_at.label("activity_at")).where(
            Run.is_deleted == False,  # noqa: E712
            Run.started_at.is_not(None),
        )

        activity = union_all(submission_activity, run_activity).subquery()
        stmt = select(activity.c.organization_id, func.max(activity.c.activity_at)).group_by(activity.c.organization_id)
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())
