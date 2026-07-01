"""Integration tests for DELETE /api/orgs/{slug}/runs/{run_id}/teams/{team_id}/members/{member_id}.

A coordinator removes a member. Like leaving a team, this hard-deletes the member row.
A guest also gets a run-departure record (the re-join block), a registered user does not.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run, RunState
from app.models.run_departure import RunDeparture
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from tests.journey_helpers import setup_journey_environment


async def _make_run_and_team(
    db_session: AsyncSession,
    org_id: UUID,
    state: RunState = RunState.ACTIVE,
    team_name: str = "Removal Team",
) -> tuple[Run, Team]:
    """Insert a Run + Team directly (no API round-trips needed for setup)."""
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name=f"Test Run {uuid4().hex[:6]}",
        state=state,
        join_code=uuid4().hex[:6].upper(),
        settings={"max_members_per_team": 10},
    )
    db_session.add(run)
    await db_session.flush()

    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name=team_name,
        color="#123456",
        share_code=uuid4().hex[:6].upper(),
    )
    db_session.add(team)
    await db_session.flush()
    return run, team


async def _add_member(db_session: AsyncSession, org_id: UUID, team: Team, user: User) -> TeamMember:
    member = TeamMember(
        organization_id=org_id,
        team_id=team.id,
        user_id=user.id,
        display_name=user.display_name or "Member",
        is_guest=user.role == UserRole.GUEST,
        joined_at=datetime.now(UTC),
    )
    db_session.add(member)
    await db_session.flush()
    return member


async def _make_guest(db_session: AsyncSession, org_id: UUID) -> User:
    guest = User(
        email=f"guest-{uuid4().hex[:8]}@example.com",
        display_name="Removal Guest",
        password_hash="x",
        role=UserRole.GUEST,
        organization_id=org_id,
        is_active=True,
    )
    db_session.add(guest)
    await db_session.flush()
    return guest


@pytest.mark.asyncio
async def test_remove_registered_member_success(client: AsyncClient, db_session: AsyncSession):
    """A coordinator removes a registered member: the member row goes and no departure record is written."""
    env = await setup_journey_environment(db_session, client, org_slug="rm-member-ok")
    run, team = await _make_run_and_team(db_session, env.org.id)
    member = await _add_member(db_session, env.org.id, team, env.users["participant1"])
    await db_session.commit()

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}/members/{member.id}",
        headers=env.auth_headers["coordinator"],
        params={"confirm_dissolution": "true"},
    )
    assert response.status_code == 204

    # The member row is gone, and the team was the member's alone: it is
    # dissolved with it, so the team no longer exists.
    get_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=env.auth_headers["coordinator"],
    )
    assert get_response.status_code == 404
    members = await db_session.execute(select(TeamMember).where(TeamMember.team_id == team.id))
    assert members.scalars().first() is None

    # Registered users get no departure record. They may re-join freely.
    departures = await db_session.execute(select(RunDeparture).where(RunDeparture.run_id == run.id))
    assert departures.scalars().first() is None


@pytest.mark.asyncio
async def test_remove_guest_member_records_departure(client: AsyncClient, db_session: AsyncSession):
    """Removing a guest writes the departure record (the re-join block)."""
    env = await setup_journey_environment(db_session, client, org_slug="rm-member-guest")
    run, team = await _make_run_and_team(db_session, env.org.id)
    guest = await _make_guest(db_session, env.org.id)
    member = await _add_member(db_session, env.org.id, team, guest)
    await db_session.commit()

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}/members/{member.id}",
        headers=env.auth_headers["coordinator"],
        params={"confirm_dissolution": "true"},
    )
    assert response.status_code == 204

    departures = await db_session.execute(
        select(RunDeparture).where(RunDeparture.run_id == run.id, RunDeparture.user_id == guest.id)
    )
    assert departures.scalars().first() is not None


@pytest.mark.asyncio
async def test_remove_member_requires_coordinator(client: AsyncClient, db_session: AsyncSession):
    """A participant, even a member of the team, cannot remove members."""
    env = await setup_journey_environment(db_session, client, org_slug="rm-member-403")
    run, team = await _make_run_and_team(db_session, env.org.id)
    member = await _add_member(db_session, env.org.id, team, env.users["participant1"])
    await _add_member(db_session, env.org.id, team, env.users["participant2"])
    await db_session.commit()

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}/members/{member.id}",
        headers=env.auth_headers["participant2"],
    )
    assert response.status_code == 403

    get_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=env.auth_headers["coordinator"],
    )
    assert get_response.json()["member_count"] == 2


@pytest.mark.asyncio
async def test_remove_member_unknown_member_404(client: AsyncClient, db_session: AsyncSession):
    """A member id that does not exist in this team returns 404."""
    env = await setup_journey_environment(db_session, client, org_slug="rm-member-404")
    run, team = await _make_run_and_team(db_session, env.org.id)
    await db_session.commit()

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}/members/{uuid4()}",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_member_wrong_team_404(client: AsyncClient, db_session: AsyncSession):
    """A member of team B cannot be removed via team A's URL."""
    env = await setup_journey_environment(db_session, client, org_slug="rm-member-xteam")
    run, team_a = await _make_run_and_team(db_session, env.org.id, team_name="Team A")
    team_b = Team(
        organization_id=env.org.id,
        run_id=run.id,
        name="Team B",
        color="#654321",
        share_code=uuid4().hex[:6].upper(),
    )
    db_session.add(team_b)
    await db_session.flush()
    member_b = await _add_member(db_session, env.org.id, team_b, env.users["participant1"])
    await db_session.commit()

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team_a.id}/members/{member_b.id}",
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 404

    get_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team_b.id}",
        headers=env.auth_headers["coordinator"],
    )
    assert get_response.json()["member_count"] == 1
