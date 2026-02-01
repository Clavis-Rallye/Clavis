"""TeamMember repository, scoped to an organization. A member has no soft-delete."""

from uuid import UUID

from sqlalchemy import func, select

from app.models.team import Team
from app.models.team_member import TeamMember
from app.repositories.base import TenantScopedRepository


class TeamMemberRepository(TenantScopedRepository[TeamMember]):
    """
    Repository for TeamMember entities.

    A member has no soft-delete. A member who leaves the team is removed (hard delete).
    So ``_base_query`` filters by organization only.
    """

    model = TeamMember
    has_soft_delete = False

    async def list_by_team(
        self,
        team_id: UUID,
    ) -> list[TeamMember]:
        """List the members of a team."""
        stmt = self._base_query().where(TeamMember.team_id == team_id)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_user_and_run(
        self,
        user_id: UUID,
        run_id: UUID,
    ) -> TeamMember | None:
        """
        Get the membership of a user in any team of a run.

        Callers use it to enforce that a user is in one team per run. Return None when the user is in no
        team of this run.
        """
        stmt = (
            self._base_query()
            .join(Team, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id == user_id)
            .where(Team.run_id == run_id)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def team_id_by_user_and_run(self, user_id: UUID, run_id: UUID) -> UUID | None:
        """
        The team the user is in for this run, read from the database now.

        A column-only select: unlike ``get_by_user_and_run`` it never answers from a
        member row that this session loaded earlier. Paths that hold their locks ask here
        which team the account is in at that moment.
        """
        stmt = (
            select(TeamMember.team_id)
            .join(Team, TeamMember.team_id == Team.id)
            .where(TeamMember.organization_id == self.organization_id)
            .where(TeamMember.user_id == user_id)
            .where(Team.run_id == run_id)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_by_team(
        self,
        team_id: UUID,
    ) -> int:
        """Count the members of a team, registered users and guests alike."""
        stmt = (
            select(func.count(TeamMember.id))
            .where(TeamMember.organization_id == self.organization_id)
            .where(TeamMember.team_id == team_id)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def count_by_teams(
        self,
        team_ids: list[UUID],
    ) -> dict[UUID, int]:
        """
        Count the members of several teams in one query, to avoid one query per team.

        Every given team is in the result. A team without members maps to 0.
        """
        if not team_ids:
            return {}

        stmt = (
            select(
                TeamMember.team_id,
                func.count(TeamMember.id).label("member_count"),
            )
            .where(TeamMember.organization_id == self.organization_id)
            .where(TeamMember.team_id.in_(team_ids))
            .group_by(TeamMember.team_id)
        )

        result = await self.session.execute(stmt)
        counts = {row.team_id: row.member_count for row in result.all()}

        return {tid: counts.get(tid, 0) for tid in team_ids}

    async def list_by_teams(
        self,
        team_ids: list[UUID],
    ) -> dict[UUID, list[TeamMember]]:
        """
        List the members of several teams in one query, oldest member first.

        The run page names the members of every team in its row, and one query per team
        would grow with the run. Every given team is in the result. A team without
        members maps to an empty list.
        """
        if not team_ids:
            return {}

        stmt = self._base_query().where(TeamMember.team_id.in_(team_ids)).order_by(TeamMember.joined_at, TeamMember.id)
        result = await self.session.execute(stmt)
        members: dict[UUID, list[TeamMember]] = {tid: [] for tid in team_ids}
        for member in result.scalars().all():
            members[member.team_id].append(member)
        return members

    async def get_by_user_and_team(
        self,
        user_id: UUID,
        team_id: UUID,
    ) -> TeamMember | None:
        """Get the membership of a user in a team. Cheaper than ``list_by_team`` when one member is needed."""
        stmt = self._base_query().where(TeamMember.team_id == team_id).where(TeamMember.user_id == user_id)

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
