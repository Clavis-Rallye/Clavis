"""Integration tests for POST /api/orgs/{slug}/runs/{run_id}/teams/{team_id}/members/{member_id}/move.

Coordinator-initiated member move. Unlike removal, a move
is not a departure: no RunDeparture row, submissions keep their team_id, and
both teams plus the run channel get a `team_member_moved` WS event.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_departure import RunDeparture
from app.models.run_station import RunStation
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from tests.journey_helpers import setup_journey_environment


async def _make_run(
    db_session: AsyncSession,
    org_id: UUID,
    state: RunState = RunState.ACTIVE,
    max_members_per_team: int = 10,
) -> Run:
    """Insert a Run directly (no API round-trips needed for setup)."""
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name=f"Test Run {uuid4().hex[:6]}",
        state=state,
        join_code=uuid4().hex[:6].upper(),
        settings={"max_members_per_team": max_members_per_team},
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _make_team(db_session: AsyncSession, org_id: UUID, run: Run, name: str) -> Team:
    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name=name,
        color="#123456",
        share_code=uuid4().hex[:6].upper(),
    )
    db_session.add(team)
    await db_session.flush()
    return team


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


async def _add_guest_member(db_session: AsyncSession, org_id: UUID, team: Team, name: str) -> TeamMember:
    member = TeamMember(
        organization_id=org_id,
        team_id=team.id,
        user_id=None,
        display_name=name,
        is_guest=True,
        joined_at=datetime.now(UTC),
    )
    db_session.add(member)
    await db_session.flush()
    return member


async def _add_submission(db_session: AsyncSession, org_id: UUID, run: Run, team: Team) -> Submission:
    """Insert one answered challenge for the team (frozen-snapshot rows included)."""
    run_station = RunStation(organization_id=org_id, run_id=run.id, sort_order=0)
    db_session.add(run_station)
    await db_session.flush()
    run_challenge = RunChallenge(
        organization_id=org_id,
        run_id=run.id,
        run_station_id=run_station.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "Frage"},
        instructions={"de": "Antworte!"},
        scoring_rules={"max_points": 10},
        sort_order=0,
    )
    db_session.add(run_challenge)
    await db_session.flush()
    submission = Submission(
        organization_id=org_id,
        run_id=run.id,
        team_id=team.id,
        run_challenge_id=run_challenge.id,
        content={"text": "alte Antwort"},
    )
    db_session.add(submission)
    await db_session.flush()
    return submission


def _move_url(slug: str, run_id: UUID, team_id: UUID, member_id: UUID) -> str:
    return f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/members/{member_id}/move"


@pytest.mark.asyncio
async def test_move_member_success(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """204 — coordinator moves a member; membership re-points, `team.member_moved` is audited."""
    import app.routers.teams as teams_router

    audit_events: list[str] = []

    def audit_spy(event: str, **kwargs: Any) -> None:
        audit_events.append(event)

    monkeypatch.setattr(teams_router, "record_audit_event", audit_spy)

    env = await setup_journey_environment(db_session, client, org_slug="mv-member-ok")
    run = await _make_run(db_session, env.org.id)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 204

    # The membership moved: the old team lost its last member, the target gained
    # them. Verified through the API (member_count is computed from the DB),
    # like test_remove_team_member does — ORM objects created in this test's
    # session are not reliable witnesses after an API-side commit.
    get_a = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team_a.id}",
        headers=env.auth_headers["coordinator"],
    )
    get_b = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team_b.id}",
        headers=env.auth_headers["coordinator"],
    )
    # The moved member was the last one of team A: team A is dissolved.
    assert get_a.status_code == 404
    assert get_b.json()["member_count"] == 1

    assert "team.member_moved" in audit_events


@pytest.mark.asyncio
async def test_move_member_paused_run(client: AsyncClient, db_session: AsyncSession):
    """204 — a paused run is still live, so the move is allowed."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-paused")
    run = await _make_run(db_session, env.org.id, state=RunState.PAUSED)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 204

    get_b = await client.get(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team_b.id}",
        headers=env.auth_headers["coordinator"],
    )
    assert get_b.json()["member_count"] == 1


@pytest.mark.asyncio
async def test_move_member_broadcasts_to_both_teams_and_run(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """A `team_member_moved` event reaches both chat channels and the run channel."""
    import app.routers.teams as teams_router

    broadcasts: list[tuple[str, dict]] = []

    class BroadcastSpy:
        async def broadcast(self, channel: str, message: dict) -> None:
            broadcasts.append((channel, message))

        async def close_shared(self, channel: str, other_channel: str, code: int, reason: str) -> None:
            """The move also closes the old team's chat socket; no socket here."""

    monkeypatch.setattr(teams_router, "ws_manager", BroadcastSpy())

    env = await setup_journey_environment(db_session, client, org_slug="mv-member-ws")
    run = await _make_run(db_session, env.org.id)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 204

    assert {channel for channel, _ in broadcasts} == {
        f"chat:{team_a.id}",
        f"chat:{team_b.id}",
        f"run_state:{run.id}",
    }
    for _, message in broadcasts:
        assert message["type"] == "team_member_moved"
        data = message["data"]
        assert data["run_id"] == str(run.id)
        assert data["member_id"] == str(member.id)
        assert data["user_id"] == str(env.users["participant1"].id)
        assert data["from_team_id"] == str(team_a.id)
        assert data["to_team_id"] == str(team_b.id)


@pytest.mark.asyncio
async def test_move_member_writes_no_departure_even_for_guests(client: AsyncClient, db_session: AsyncSession):
    """A move is not a departure: no RunDeparture row, not even for a guest."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-nodep")
    run = await _make_run(db_session, env.org.id)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    guest = User(
        email=f"guest-{uuid4().hex[:8]}@example.com",
        display_name="Move Guest",
        password_hash="x",
        role=UserRole.GUEST,
        organization_id=env.org.id,
        is_active=True,
    )
    db_session.add(guest)
    await db_session.flush()
    member = await _add_member(db_session, env.org.id, team_a, guest)
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 204

    departures = await db_session.execute(select(RunDeparture).where(RunDeparture.run_id == run.id))
    assert departures.scalars().first() is None


@pytest.mark.asyncio
async def test_move_member_answers_stay_with_old_team(client: AsyncClient, db_session: AsyncSession):
    """The answers stay with the old team even when the move empties and dissolves it."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-answers")
    run = await _make_run(db_session, env.org.id)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    submission = await _add_submission(db_session, env.org.id, run, team_a)
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 204

    # The submission still belongs to the old team, and the target team has
    # none. Compare as str(): rows loaded from asyncpg carry stdlib uuid.UUID
    # ids while locally created ORM objects carry uuid_utils.UUID (UUIDMixin's
    # uuid7 default) — identical values, unequal `==`.
    old_team_submissions = await db_session.execute(select(Submission).where(Submission.team_id == team_a.id))
    kept = old_team_submissions.scalars().one()
    assert str(kept.id) == str(submission.id)
    assert str(kept.team_id) == str(team_a.id)

    submissions_for_b = await db_session.execute(select(Submission).where(Submission.team_id == team_b.id))
    assert submissions_for_b.scalars().first() is None


@pytest.mark.asyncio
async def test_move_member_target_team_full(client: AsyncClient, db_session: AsyncSession):
    """A full target team answers 422 TEAM_FULL and nothing changes."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-full")
    run = await _make_run(db_session, env.org.id, max_members_per_team=2)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await _add_guest_member(db_session, env.org.id, team_b, "B Guest 1")
    await _add_guest_member(db_session, env.org.id, team_b, "B Guest 2")
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "TEAM_FULL"
    assert member.team_id == team_a.id


@pytest.mark.asyncio
async def test_move_member_target_in_other_run(client: AsyncClient, db_session: AsyncSession):
    """A target team of another run answers a coded error and nothing changes."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-otherrun")
    run = await _make_run(db_session, env.org.id)
    other_run = await _make_run(db_session, env.org.id)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    foreign_team = await _make_team(db_session, env.org.id, other_run, "Other Run Team")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(foreign_team.id)},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "TARGET_NOT_IN_RUN"
    assert member.team_id == team_a.id


@pytest.mark.asyncio
async def test_move_member_to_same_team(client: AsyncClient, db_session: AsyncSession):
    """Moving to the team the member is already in answers 422 SAME_TEAM."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-same")
    run = await _make_run(db_session, env.org.id)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_a.id)},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "SAME_TEAM"
    assert member.team_id == team_a.id


@pytest.mark.asyncio
async def test_move_member_ended_run(client: AsyncClient, db_session: AsyncSession):
    """An ended run answers 422 RUN_ENDED and nothing changes."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-ended")
    run = await _make_run(db_session, env.org.id, state=RunState.ENDED)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "RUN_ENDED"
    assert member.team_id == team_a.id


@pytest.mark.asyncio
async def test_move_member_not_started_run(client: AsyncClient, db_session: AsyncSession):
    """A configured run answers 422 RUN_NOT_STARTED and nothing changes."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-notstarted")
    run = await _make_run(db_session, env.org.id, state=RunState.CONFIGURED)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "RUN_NOT_STARTED"
    assert member.team_id == team_a.id


@pytest.mark.asyncio
async def test_move_member_requires_coordinator(client: AsyncClient, db_session: AsyncSession):
    """403 — a participant cannot move members, not even their own membership."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-403")
    run = await _make_run(db_session, env.org.id)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    member = await _add_member(db_session, env.org.id, team_a, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, member.id),
        headers=env.auth_headers["participant1"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 403
    assert member.team_id == team_a.id


@pytest.mark.asyncio
async def test_move_member_concurrent_moves_into_one_free_place(
    client: AsyncClient, db_session: AsyncSession, concurrent_client: AsyncClient
):
    """Two moves race into a team with one free place: exactly one
    succeeds, the other answers TEAM_FULL, and the target ends at its max.

    Two concurrent moves must not both pass the occupancy check and overfill the team.
    Setup goes through `client` + `db_session` as usual. The race goes through
    `concurrent_client`, which gives each request its own session so asyncio.gather()
    can run them at the same time, the pattern used by the concurrency tests in
    test_instance_legal.py."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-race")
    run = await _make_run(db_session, env.org.id, max_members_per_team=2)
    team_source_1 = await _make_team(db_session, env.org.id, run, "Source 1")
    team_source_2 = await _make_team(db_session, env.org.id, run, "Source 2")
    team_target = await _make_team(db_session, env.org.id, run, "Target")
    member_1 = await _add_guest_member(db_session, env.org.id, team_source_1, "Mover 1")
    member_2 = await _add_guest_member(db_session, env.org.id, team_source_2, "Mover 2")
    await _add_guest_member(db_session, env.org.id, team_target, "Already There")
    await db_session.commit()

    responses = await asyncio.gather(
        concurrent_client.post(
            _move_url(env.slug, run.id, team_source_1.id, member_1.id),
            headers=env.auth_headers["coordinator"],
            json={"target_team_id": str(team_target.id), "confirm_dissolution": True},
        ),
        concurrent_client.post(
            _move_url(env.slug, run.id, team_source_2.id, member_2.id),
            headers=env.auth_headers["coordinator"],
            json={"target_team_id": str(team_target.id), "confirm_dissolution": True},
        ),
    )

    assert sorted(r.status_code for r in responses) == [204, 422]
    failed = next(r for r in responses if r.status_code == 422)
    assert failed.json()["error_code"] == "TEAM_FULL"

    target_members = await db_session.execute(select(TeamMember).where(TeamMember.team_id == team_target.id))
    assert len(target_members.scalars().all()) == 2


@pytest.mark.asyncio
async def test_move_member_unknown_member_404(client: AsyncClient, db_session: AsyncSession):
    """404 — a member id that does not exist in this team."""
    env = await setup_journey_environment(db_session, client, org_slug="mv-member-404")
    run = await _make_run(db_session, env.org.id)
    team_a = await _make_team(db_session, env.org.id, run, "Team A")
    team_b = await _make_team(db_session, env.org.id, run, "Team B")
    await db_session.commit()

    response = await client.post(
        _move_url(env.slug, run.id, team_a.id, uuid4()),
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(team_b.id), "confirm_dissolution": True},
    )
    assert response.status_code == 404
