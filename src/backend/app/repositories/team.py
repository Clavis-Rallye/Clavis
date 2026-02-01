"""Team repository, scoped to an organization, and the lock helpers of the team rows."""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import Team
from app.models.team_member import TeamMember
from app.repositories.base import TenantScopedRepository
from app.services import team_share_code as share_code_service


@dataclass(frozen=True)
class LockedTeam:
    """A team row as read under its lock: whether it is dissolved, and how many members it has."""

    id: UUID
    run_id: UUID
    is_deleted: bool
    member_count: int


async def lock_team_rows(
    session: AsyncSession, team_ids: Iterable[UUID], *, organization_id: UUID | None
) -> dict[UUID, LockedTeam]:
    """
    Lock team rows one by one in ascending id order and return their state as read under
    the lock. A request takes its team locks here. A sweep takes them with
    ``lock_team_ids``, in the same order.

    ``organization_id`` scopes the rows as a repository does. None is for the erasure of
    accounts, which serves no single organization and takes its team ids from the member
    rows of the accounts it erases.
    """
    locked: dict[UUID, LockedTeam] = {}
    for team_id in sorted(set(team_ids)):
        stmt = select(Team.id, Team.run_id, Team.is_deleted).where(Team.id == team_id).with_for_update()
        if organization_id is not None:
            stmt = stmt.where(Team.organization_id == organization_id)
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            continue
        member_count = (
            await session.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id))
        ).scalar_one()
        locked[team_id] = LockedTeam(id=row.id, run_id=row.run_id, is_deleted=row.is_deleted, member_count=member_count)
    return locked


# Ids per statement of a sweep. A sweep may hold the teams of many runs, and the driver
# limits the number of parameters of one statement.
_ID_CHUNK = 5000


def id_chunks(ids: Sequence[UUID]) -> Iterator[Sequence[UUID]]:
    """Split ``ids`` into pieces that fit one statement."""
    for start in range(0, len(ids), _ID_CHUNK):
        yield ids[start : start + _ID_CHUNK]


async def lock_team_ids(
    session: AsyncSession, *conditions: ColumnElement[bool], skip_locked: bool = False
) -> list[UUID]:
    """
    Lock every team row that meets ``conditions`` in one statement, in ascending id order
    (the order of ``lock_team_rows``), and return the ids now held. For the sweeps, which
    write member rows of many teams at once and need no member count.

    With ``skip_locked`` a row that another transaction holds is left out instead of
    waited for, and the caller then writes only in the teams returned.
    """
    stmt = select(Team.id).where(*conditions).order_by(Team.id).with_for_update(skip_locked=skip_locked, of=Team)
    return list((await session.execute(stmt)).scalars())


class TeamRepository(TenantScopedRepository[Team]):
    """Repository for Team entities, filtered by organization and soft-delete."""

    model = Team

    async def list_by_run(
        self,
        run_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Team]:
        """List teams of a run, newest first."""
        stmt = (
            self._base_query().where(Team.run_id == run_id).order_by(Team.created_at.desc()).offset(skip).limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_run(self, run_id: UUID) -> int:
        """Count the teams of a run, dissolved teams left out."""
        stmt = (
            select(func.count(Team.id))
            .where(Team.organization_id == self.organization_id)
            .where(Team.run_id == run_id)
            .where(Team.is_deleted == False)  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def lock_rows(self, team_ids: Iterable[UUID]) -> dict[UUID, LockedTeam]:
        """
        Lock team rows with ``SELECT ... FOR UPDATE``, one by one in ascending id order, and
        return their state as read under the lock. The locks hold until the transaction ends.

        Every path that writes or deletes a member row takes its team locks here, before it
        decides anything about the team. The fixed order lets two paths that need the same
        two teams (a move A to B and a move B to A) wait for each other instead of
        deadlocking. The state is read after the lock is granted: a request that waited for
        a dissolution sees ``is_deleted``, and one that waited for a join sees the new member
        in ``member_count``. A team that does not exist in this organization is missing from
        the result. A dissolved team is in it with ``is_deleted`` set.

        Only columns are selected, so no ORM instance enters the session.
        """
        return await lock_team_rows(self.session, team_ids, organization_id=self.organization_id)

    async def lock_live_row(self, team_id: UUID) -> LockedTeam | None:
        """
        Lock one team row through ``lock_rows`` and return its state, or None when the team
        is gone or dissolved. A caller that gets None must not write a member row.

        Join paths call this before their capacity check: two joins for the last free place
        of a team serialize on the row, and the second counts the member of the first.
        """
        state = (await self.lock_rows([team_id])).get(team_id)
        if state is None or state.is_deleted:
            return None
        return state

    async def lock_row(self, team_id: UUID) -> bool:
        """
        Lock one team row. Return True when the team is live under the lock.

        The short form of ``lock_live_row`` for paths that need no count. False means the
        team is gone or dissolved, and the caller answers as for a team that does not
        exist.
        """
        return await self.lock_live_row(team_id) is not None

    # ---- share codes ----------------------------------------------------

    async def get_by_share_code(self, run_id: UUID, code: str) -> Team | None:
        """The run scopes the lookup, so a code of one run finds no team in another run."""
        normalized = share_code_service.normalize(code)
        stmt = self._base_query().where(Team.run_id == run_id).where(Team.share_code == normalized).limit(1)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create(self, **kwargs: Any) -> Team:
        """Without a ``share_code`` argument, generate one unique in the run: the column is NOT NULL."""
        if "share_code" not in kwargs:
            run_id = kwargs.get("run_id")
            if run_id is None:
                raise ValueError("run_id is required to auto-generate share_code")
            kwargs["share_code"] = await self.generate_unique_share_code(run_id)
        return await super().create(**kwargs)

    async def generate_unique_share_code(self, run_id: UUID, max_attempts: int = 5) -> str:
        """
        Generate a share code that no live team of this run has. Raise RuntimeError when all
        attempts collide. A collision is very unlikely (about one in a million per attempt at
        20 teams), so the retry is a safeguard.
        """
        for _ in range(max_attempts):
            code = share_code_service.generate()
            existing = await self.get_by_share_code(run_id, code)
            if existing is None:
                return code
        raise RuntimeError(f"Could not generate a unique share-code for run {run_id} after {max_attempts} tries")
