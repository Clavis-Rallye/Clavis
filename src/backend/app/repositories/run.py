"""Run repository, scoped to an organization, and the lock helpers of the run row."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.run import Run, RunState
from app.repositories.base import TenantScopedRepository


async def lock_run_rows(session: AsyncSession, run_ids: Iterable[UUID]) -> None:
    """
    Lock run rows one by one in ascending id order, with the lock of ``RunRepository.lock_row``.
    The locks hold until the transaction ends. A run row that is gone is passed over.

    Not scoped to an organization: the erasure of accounts serves no single organization
    and learns its runs from the member rows of the accounts it erases.
    """
    for run_id in sorted(set(run_ids)):
        await session.execute(select(Run.id).where(Run.id == run_id).with_for_update(key_share=True))


async def lock_run_ids(session: AsyncSession, run_ids: Iterable[UUID]) -> list[UUID]:
    """
    Lock the run rows with these ids in one statement, in ascending id order, with the lock
    of ``RunRepository.lock_row``, and return the ids now held. The locks hold until the
    transaction ends. The lock is waited for. A run row that is gone is passed over.

    For a sweep, which works on many runs in one transaction and serves no single
    organization. The order is the order of ``lock_run_rows``, so two sweeps and an act
    that holds one run row cannot wait for each other in a ring.
    """
    ids = sorted(set(run_ids))
    if not ids:
        return []
    stmt = select(Run.id).where(Run.id.in_(ids)).order_by(Run.id).with_for_update(key_share=True, of=Run)
    return list((await session.execute(stmt)).scalars())


class RunRepository(TenantScopedRepository[Run]):
    """Repository for Run entities, filtered by organization and soft-delete."""

    model = Run

    async def get_by_join_code(self, join_code: str) -> Run | None:
        """Join codes are unique within an organization, so at most one run matches."""
        stmt = self._base_query().where(Run.join_code == join_code)

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_state(
        self,
        state: RunState,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Run]:
        """List runs of this organization in one state, newest first."""
        stmt = self._base_query().where(Run.state == state).order_by(Run.created_at.desc()).offset(skip).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def lock_row(self, run_id: UUID) -> None:
        """
        Lock the run's row with ``FOR NO KEY UPDATE`` until the transaction ends.

        Lock order: run, account, team, member, departure (``services/team_dissolution.py``).
        This is the first lock of every path that takes it. A new path that needs it beside
        one of the other four takes it first. Only the id is selected, so no ORM instance
        enters the session.

        Callers:

        - A team creation under a team limit, before ``count_by_run``: two creations for the
          last free slot serialize on the row instead of both counting below ``max_teams``.
        - The publish of a result, its withdrawal and the end of the run (``routers/runs.py``).
          Each takes the lock before it reads the run, its grading state or its active result
          link, and holds it until the link and the feed change are committed.
        - Every act that dissolves a team: one of them at a time builds and writes, or
          rewrites, the snapshot of a run.
        - The start of a run (``routers/runs.py``). It takes the lock before it reads the
          run and its state, and holds it until the snapshot of the rallye and the new state
          are committed. A second start waits and then reads ``active``. The automatic start
          (``services/run_start.py``) takes its own lock with ``SKIP LOCKED`` and passes a run
          held here, so a run gets one snapshot. Resume takes no lock: it accepts only a
          paused run, which no start touches, and its update of the row waits here.

        ``FOR NO KEY UPDATE`` is the lock an update of the row takes. Its holders wait for
        each other and for an update of the run. A row that only points to the run (a team,
        an answer, a departure, a feed entry, a result link) takes ``FOR KEY SHARE`` on it
        for its foreign key, and PostgreSQL grants that beside this lock. So a new team or a
        feed entry never waits for a publish or a dissolution of the same run, and a path
        that inserts such a row with a team or an account lock in hand cannot close a cycle
        through the run row.

        An answer and a grading do wait: they take ``share_row`` as their first row lock,
        and ``FOR SHARE`` and this lock exclude each other. They hold nothing when they ask,
        and a holder of ``share_row`` never asks for this lock, so the wait closes no cycle.
        """
        stmt = (
            select(Run.id)
            .where(Run.organization_id == self.organization_id)
            .where(Run.id == run_id)
            .with_for_update(key_share=True)
        )
        await self.session.execute(stmt)

    async def share_row(self, run_id: UUID) -> RunState | None:
        """
        Lock the run's row with ``FOR SHARE`` until the transaction ends, and return the
        run's state as read under the lock. Return ``None`` when the run does not exist in
        this organization or is deleted.

        For the paths that write an answer or a grading of the run: the submission and the
        replacement of an answer (``routers/submissions.py``) and the three score routes
        (``routers/scores.py``). ``FOR SHARE`` and the ``FOR NO KEY UPDATE`` of ``lock_row``
        exclude each other, and two holders of ``FOR SHARE`` do not wait for each other. So:

        - The publish and the end of the run, which hold ``lock_row``, never read beside
          an open answer or grading of that run. Such a write has committed before they
          read, or it starts after they committed.
        - Answers and gradings of one run do not queue on each other here.

        The state comes from the locking statement itself. After a wait PostgreSQL reads
        the row again, so a caller that waited for the end of the run gets ``ended``,
        whatever a run object loaded earlier in the session says. An answer decides on
        this value.

        Lock order (``services/team_dissolution.py``): this is the first row lock of its
        path, taken before any account, team or member row. A transaction that holds it
        never calls ``lock_row`` and never updates the run row, because two holders that
        both asked for the stronger lock would wait for each other.
        """
        stmt = (
            select(Run.state)
            .where(Run.organization_id == self.organization_id)
            .where(Run.id == run_id)
            .where(Run.is_deleted.is_(False))
            .with_for_update(read=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_by_state(self, state: RunState) -> int:
        """Count the runs of this organization in one state, soft-deleted runs left out."""
        stmt = (
            select(func.count(Run.id))
            .where(Run.organization_id == self.organization_id)
            .where(Run.state == state)
            .where(Run.is_deleted == False)  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def list_by_rallye(
        self,
        rallye_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Run]:
        """List runs of this organization created from one rallye, newest first."""
        stmt = (
            self._base_query()
            .where(Run.rallye_id == rallye_id)
            .order_by(Run.created_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_rallye(self, rallye_id: UUID) -> int:
        """Count the runs of this organization created from one rallye, soft-deleted runs left out."""
        stmt = (
            select(func.count(Run.id))
            .where(Run.organization_id == self.organization_id)
            .where(Run.rallye_id == rallye_id)
            .where(Run.is_deleted == False)  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    # ============================================================================
    # UNSCOPED METHODS (Security-sensitive exceptions)
    # ============================================================================
    # These methods bypass organization scoping for the guest join flow and the
    # participant flows. Use with extreme caution.

    @staticmethod
    async def get_by_join_code_unscoped(
        session: AsyncSession,
        join_code: str,
        *,
        include_archived_orgs: bool = False,
    ) -> Run | None:
        """
        Get a run by join code, case-insensitively, WITHOUT organization scoping.

        SECURITY WARNING: this bypasses multi-tenant isolation. Use it only where the
        organization is not yet known, such as the guest join, which has only the join code.

        A run of an archived organization is found only with ``include_archived_orgs``. Only
        the guest join sets it, because it answers 403 ORG_ARCHIVED instead of a bare 404.
        """
        stmt = select(Run).where(
            func.upper(Run.join_code) == join_code.upper(),
            Run.is_deleted == False,  # noqa: E712
        )
        if not include_archived_orgs:
            stmt = stmt.join(Run.organization).where(Organization.archived_at.is_(None))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id_unscoped(
        session: AsyncSession,
        run_id: UUID,
    ) -> "Run | None":
        """
        Get a run by id WITHOUT organization scoping.

        SECURITY WARNING: this bypasses multi-tenant isolation. Use it only for
        participant flows that authorize by team membership, not by organization role,
        and resolve the organization from the run, such as the participant run-state
        read. The caller MUST verify the requester's membership in the returned run
        before it discloses anything.
        """
        stmt = select(Run).where(
            Run.id == run_id,
            Run.is_deleted == False,  # noqa: E712
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
