"""A team dissolves with its last member; the Spielleitung can remove a whole team.

A dissolved team is a team row with ``is_deleted`` and
``deleted_at`` set, its member rows are gone, and its answers and scores stay
stored. The paths that must not dissolve a team have one test each at the end.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import tokens as auth_tokens
from app.config import settings
from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_departure import RunDeparture
from app.models.run_result_link import RunResultLink
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.repositories.run import RunRepository
from app.repositories.run_departure import RunDepartureRepository
from app.repositories.team import TeamRepository
from app.routers import teams as teams_router
from app.services import gdpr, retention_tasks, team_code_rate_limit, team_dissolution
from app.services.gdpr import ANONYMIZED_MEMBER_NAME, anonymize_user
from app.services.guest_cleanup import blank_guest_member_names, delete_run_guests
from app.services.team_dissolution import lock_account
from tests.factories import get_auth_headers
from tests.journey_helpers import JourneyEnv, setup_journey_environment


class _AuditSpy:
    """Stands in for ``record_audit_event`` in the modules that write the events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: str, **kwargs: Any) -> None:
        self.events.append({"event": event, **kwargs})

    def named(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == event]


@pytest.fixture
def audit_spy(monkeypatch: pytest.MonkeyPatch) -> _AuditSpy:
    spy = _AuditSpy()
    monkeypatch.setattr(teams_router, "record_audit_event", spy)
    monkeypatch.setattr(gdpr, "record_audit_event", spy)
    return spy


async def _run(db: AsyncSession, org_id: UUID, state: RunState = RunState.ACTIVE) -> Run:
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name=f"Run {uuid4().hex[:6]}",
        state=state,
        join_code=uuid4().hex[:6].upper(),
        settings={"max_members_per_team": 10},
    )
    db.add(run)
    await db.flush()
    return run


async def _team(db: AsyncSession, org_id: UUID, run: Run, name: str = "Team") -> Team:
    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name=name,
        color="#123456",
        share_code=uuid4().hex[:6].upper(),
    )
    db.add(team)
    await db.flush()
    return team


async def _guest(db: AsyncSession, org_id: UUID) -> User:
    guest = User(
        email=f"guest-{uuid4().hex[:8]}@example.com",
        display_name="Guest",
        password_hash="x",
        role=UserRole.GUEST,
        organization_id=org_id,
        is_active=True,
    )
    db.add(guest)
    await db.flush()
    return guest


async def _member(db: AsyncSession, org_id: UUID, team: Team, user: User | None, name: str = "Member") -> TeamMember:
    member = TeamMember(
        organization_id=org_id,
        team_id=team.id,
        user_id=user.id if user else None,
        display_name=user.display_name if user and user.display_name else name,
        is_guest=user is None or user.role == UserRole.GUEST,
        joined_at=datetime.now(UTC),
    )
    db.add(member)
    await db.flush()
    return member


async def _answer(db: AsyncSession, org_id: UUID, run: Run, team: Team, points: int | None = None) -> Submission:
    station = RunStation(organization_id=org_id, run_id=run.id, sort_order=0)
    db.add(station)
    await db.flush()
    challenge = RunChallenge(
        organization_id=org_id,
        run_id=run.id,
        run_station_id=station.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "Frage"},
        instructions={"de": "Antworte!"},
        scoring_rules={"max_points": 10},
        sort_order=0,
    )
    db.add(challenge)
    await db.flush()
    submission = Submission(
        organization_id=org_id,
        run_id=run.id,
        team_id=team.id,
        run_challenge_id=challenge.id,
        content={"text": "Antwort"},
    )
    db.add(submission)
    await db.flush()
    if points is not None:
        db.add(Score(organization_id=org_id, submission_id=submission.id, points=points, max_points=10))
        await db.flush()
    return submission


async def _team_row(db: AsyncSession, team_id: UUID) -> Team:
    stmt = select(Team).where(Team.id == team_id).execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one()


async def _member_count(db: AsyncSession, team_id: UUID) -> int:
    return (await db.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id))).scalar_one()


async def _assert_dissolved(db: AsyncSession, team_id: UUID) -> None:
    team = await _team_row(db, team_id)
    assert team.is_deleted is True
    assert team.deleted_at is not None
    assert await _member_count(db, team_id) == 0


async def _assert_live(db: AsyncSession, team_id: UUID) -> None:
    team = await _team_row(db, team_id)
    assert team.is_deleted is False
    assert team.deleted_at is None


# What a client sends when the person confirmed the dissolution: as a query
# parameter of the two DELETE routes, as a body field of leave and move.
_CONFIRM = {"confirm_dissolution": "true"}
_CONFIRM_BODY = {"confirm_dissolution": True}


def _team_url(env: JourneyEnv, run: Run, team: Team) -> str:
    return f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}"


# ---- the last member leaves -------------------------------------------------


@pytest.mark.asyncio
async def test_last_member_leaving_dissolves_the_team(client: AsyncClient, db_session: AsyncSession, audit_spy):
    env = await setup_journey_environment(db_session, client, org_slug="dis-leave")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    await _member(db_session, env.org.id, team, env.users["participant1"])
    await db_session.commit()

    response = await client.post(
        f"{_team_url(env, run, team)}/leave", headers=env.auth_headers["participant1"], json=_CONFIRM_BODY
    )
    assert response.status_code == 204

    await _assert_dissolved(db_session, team.id)
    (event,) = audit_spy.named("team.dissolved")
    assert event["target"]["id"] == str(team.id)
    assert event["target"]["run_id"] == str(run.id)
    assert event["target"]["cause"] == "last_member_left"
    assert event["actor"]["user_id"] == str(env.users["participant1"].id)


@pytest.mark.asyncio
async def test_leaving_with_other_members_left_keeps_the_team(client: AsyncClient, db_session: AsyncSession, audit_spy):
    env = await setup_journey_environment(db_session, client, org_slug="dis-leave-keep")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    await _member(db_session, env.org.id, team, env.users["participant1"])
    await _member(db_session, env.org.id, team, env.users["participant2"])
    await db_session.commit()

    response = await client.post(f"{_team_url(env, run, team)}/leave", headers=env.auth_headers["participant1"])
    assert response.status_code == 204

    await _assert_live(db_session, team.id)
    assert audit_spy.named("team.dissolved") == []


@pytest.mark.asyncio
async def test_last_guest_leaving_gets_departure_and_team_dissolves(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="dis-leave-guest")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    guest = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, guest)
    await db_session.commit()

    response = await client.post(
        f"{_team_url(env, run, team)}/leave", headers=get_auth_headers(guest), json=_CONFIRM_BODY
    )
    assert response.status_code == 204

    await _assert_dissolved(db_session, team.id)
    departures = (await db_session.execute(select(RunDeparture).where(RunDeparture.run_id == run.id))).scalars().all()
    assert [str(d.user_id) for d in departures] == [str(guest.id)]


# ---- the Spielleitung removes the last member -------------------------------


@pytest.mark.asyncio
async def test_removing_the_last_member_dissolves_the_team(client: AsyncClient, db_session: AsyncSession, audit_spy):
    env = await setup_journey_environment(db_session, client, org_slug="dis-remove")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    member = await _member(db_session, env.org.id, team, env.users["participant1"])
    await db_session.commit()

    response = await client.delete(
        f"{_team_url(env, run, team)}/members/{member.id}", headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert response.status_code == 204

    await _assert_dissolved(db_session, team.id)
    (event,) = audit_spy.named("team.dissolved")
    assert event["target"]["cause"] == "last_member_removed"
    assert event["actor"]["user_id"] == str(env.users["coordinator"].id)


@pytest.mark.asyncio
async def test_removing_one_of_two_members_keeps_the_team(client: AsyncClient, db_session: AsyncSession, audit_spy):
    env = await setup_journey_environment(db_session, client, org_slug="dis-remove-keep")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    member = await _member(db_session, env.org.id, team, env.users["participant1"])
    await _member(db_session, env.org.id, team, env.users["participant2"])
    await db_session.commit()

    response = await client.delete(
        f"{_team_url(env, run, team)}/members/{member.id}", headers=env.auth_headers["coordinator"]
    )
    assert response.status_code == 204

    await _assert_live(db_session, team.id)
    assert audit_spy.named("team.dissolved") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [RunState.CONFIGURED, RunState.ACTIVE, RunState.PAUSED, RunState.ENDED])
async def test_the_rule_holds_in_every_run_state(client: AsyncClient, db_session: AsyncSession, state: RunState):
    env = await setup_journey_environment(db_session, client, org_slug=f"dis-state-{state.value}".replace("_", "-"))
    run = await _run(db_session, env.org.id, state)
    team = await _team(db_session, env.org.id, run)
    member = await _member(db_session, env.org.id, team, env.users["participant1"])
    await db_session.commit()

    response = await client.delete(
        f"{_team_url(env, run, team)}/members/{member.id}", headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert response.status_code == 204
    await _assert_dissolved(db_session, team.id)


@pytest.mark.asyncio
async def test_two_removals_at_once_dissolve_the_team_once(
    client: AsyncClient, db_session: AsyncSession, concurrent_client: AsyncClient, audit_spy
):
    env = await setup_journey_environment(db_session, client, org_slug="dis-race")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    member_1 = await _member(db_session, env.org.id, team, env.users["participant1"])
    member_2 = await _member(db_session, env.org.id, team, env.users["participant2"])
    await db_session.commit()

    responses = await asyncio.gather(
        *(
            concurrent_client.delete(
                f"{_team_url(env, run, team)}/members/{member.id}",
                headers=env.auth_headers["coordinator"],
                params=_CONFIRM,
            )
            for member in (member_1, member_2)
        )
    )

    assert [r.status_code for r in responses] == [204, 204]
    await _assert_dissolved(db_session, team.id)
    assert len(audit_spy.named("team.dissolved")) == 1


# ---- the Spielleitung moves the last member ---------------------------------


@pytest.mark.asyncio
async def test_moving_the_last_member_dissolves_the_source_team(
    client: AsyncClient, db_session: AsyncSession, audit_spy
):
    env = await setup_journey_environment(db_session, client, org_slug="dis-move")
    run = await _run(db_session, env.org.id)
    source = await _team(db_session, env.org.id, run, "Source")
    target = await _team(db_session, env.org.id, run, "Target")
    member = await _member(db_session, env.org.id, source, env.users["participant1"])
    submission = await _answer(db_session, env.org.id, run, source)
    await db_session.commit()

    response = await client.post(
        f"{_team_url(env, run, source)}/members/{member.id}/move",
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(target.id), **_CONFIRM_BODY},
    )
    assert response.status_code == 204

    await _assert_dissolved(db_session, source.id)
    await _assert_live(db_session, target.id)
    assert await _member_count(db_session, target.id) == 1
    kept = (await db_session.execute(select(Submission).where(Submission.id == submission.id))).scalar_one()
    assert str(kept.team_id) == str(source.id)
    (event,) = audit_spy.named("team.dissolved")
    assert event["target"]["cause"] == "last_member_moved"
    assert event["target"]["answer_count"] == 1


@pytest.mark.asyncio
async def test_moving_one_of_two_members_keeps_the_source_team(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="dis-move-keep")
    run = await _run(db_session, env.org.id)
    source = await _team(db_session, env.org.id, run, "Source")
    target = await _team(db_session, env.org.id, run, "Target")
    member = await _member(db_session, env.org.id, source, env.users["participant1"])
    await _member(db_session, env.org.id, source, env.users["participant2"])
    await db_session.commit()

    response = await client.post(
        f"{_team_url(env, run, source)}/members/{member.id}/move",
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(target.id)},
    )
    assert response.status_code == 204
    await _assert_live(db_session, source.id)


@pytest.mark.asyncio
async def test_a_dissolved_team_is_no_move_target(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="dis-move-target")
    run = await _run(db_session, env.org.id)
    source = await _team(db_session, env.org.id, run, "Source")
    gone = await _team(db_session, env.org.id, run, "Gone")
    member = await _member(db_session, env.org.id, source, env.users["participant1"])
    gone.is_deleted = True
    gone.deleted_at = datetime.now(UTC)
    await db_session.commit()

    response = await client.post(
        f"{_team_url(env, run, source)}/members/{member.id}/move",
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(gone.id), **_CONFIRM_BODY},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "TARGET_NOT_IN_RUN"


# ---- nobody joins a team that is gone ---------------------------------------


@pytest.mark.asyncio
async def test_join_by_code_after_dissolution_is_an_unknown_code(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="dis-join")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    member = await _member(db_session, env.org.id, team, env.users["participant1"])
    code = team.share_code
    await db_session.commit()

    removed = await client.delete(
        f"{_team_url(env, run, team)}/members/{member.id}", headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert removed.status_code == 204

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/by-code",
        headers=env.auth_headers["participant2"],
        json={"team_code": code},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Team-Code unbekannt."
    assert await _member_count(db_session, team.id) == 0


@pytest.mark.asyncio
async def test_team_lock_reports_whether_the_team_is_still_live(db_session: AsyncSession):
    """The join paths lock the row and must learn that the team dissolved while they waited."""
    org_id, _user = await _bare_org_and_user(db_session, "dis-lock")
    run = await _run(db_session, org_id)
    team = await _team(db_session, org_id, run)
    repo = TeamRepository(db_session, organization_id=org_id)

    assert await repo.lock_row(team.id) is True
    team.is_deleted = True
    team.deleted_at = datetime.now(UTC)
    await db_session.flush()
    assert await repo.lock_row(team.id) is False
    assert await repo.lock_row(uuid4()) is False


# ---- the final erasure of an account ----------------------------------------


@pytest.mark.asyncio
async def test_erasing_the_last_member_in_a_live_run_dissolves_the_team(db_session: AsyncSession, audit_spy):
    org_id, user = await _bare_org_and_user(db_session, "erase-live")
    run = await _run(db_session, org_id, RunState.ACTIVE)
    team = await _team(db_session, org_id, run)
    await _member(db_session, org_id, team, user)
    await db_session.commit()

    assert await anonymize_user(db_session, user.id, {"system": "test"}) is True
    await db_session.commit()

    await _assert_dissolved(db_session, team.id)
    (event,) = audit_spy.named("team.dissolved")
    assert event["target"]["id"] == str(team.id)


@pytest.mark.asyncio
async def test_erasing_one_of_two_members_in_a_live_run_keeps_the_team(db_session: AsyncSession):
    org_id, user = await _bare_org_and_user(db_session, "erase-two")
    other = await _guest(db_session, org_id)
    run = await _run(db_session, org_id, RunState.PAUSED)
    team = await _team(db_session, org_id, run)
    await _member(db_session, org_id, team, user)
    await _member(db_session, org_id, team, other)
    await db_session.commit()

    await anonymize_user(db_session, user.id, {"system": "test"})
    await db_session.commit()

    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1


@pytest.mark.asyncio
async def test_erasing_the_last_member_in_an_ended_run_only_renames(db_session: AsyncSession, audit_spy):
    org_id, user = await _bare_org_and_user(db_session, "erase-ended")
    run = await _run(db_session, org_id, RunState.ENDED)
    team = await _team(db_session, org_id, run)
    member = await _member(db_session, org_id, team, user)
    await db_session.commit()

    await anonymize_user(db_session, user.id, {"system": "test"})
    await db_session.commit()

    await _assert_live(db_session, team.id)
    row = (
        await db_session.execute(
            select(TeamMember).where(TeamMember.id == member.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert row.display_name == ANONYMIZED_MEMBER_NAME
    assert audit_spy.named("team.dissolved") == []


async def _bare_org_and_user(db: AsyncSession, slug: str) -> tuple[UUID, User]:
    from tests.factories import create_test_user_with_org

    user, org = await create_test_user_with_org(db, email=f"{slug}@test.com", role=UserRole.PARTICIPANT, org_slug=slug)
    return org.id, user


# ---- the Spielleitung removes a whole team ----------------------------------


@pytest.mark.asyncio
async def test_removing_a_team_removes_members_and_records_departures(
    client: AsyncClient, db_session: AsyncSession, audit_spy
):
    env = await setup_journey_environment(db_session, client, org_slug="dis-staff")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    guest = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, guest)
    await _member(db_session, env.org.id, team, env.users["participant1"])
    await _answer(db_session, env.org.id, run, team, points=7)
    await db_session.commit()

    response = await client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM)
    assert response.status_code == 204

    await _assert_dissolved(db_session, team.id)
    departures = (await db_session.execute(select(RunDeparture).where(RunDeparture.run_id == run.id))).scalars().all()
    assert [str(d.user_id) for d in departures] == [str(guest.id)]
    (event,) = audit_spy.named("team.dissolved")
    assert event["target"]["cause"] == "removed_by_staff"
    assert event["target"]["member_count"] == 2
    assert event["target"]["answer_count"] == 1
    assert event["target"]["score_count"] == 1
    assert event["actor"]["user_id"] == str(env.users["coordinator"].id)
    # The answers and scores stay stored.
    assert (await db_session.execute(select(func.count(Submission.id)))).scalar_one() == 1
    assert (await db_session.execute(select(func.count(Score.id)))).scalar_one() == 1


@pytest.mark.asyncio
async def test_removing_a_team_twice_answers_404(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="dis-staff-twice")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    await db_session.commit()

    first = await client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM)
    second = await client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM)
    assert (first.status_code, second.status_code) == (204, 404)


@pytest.mark.asyncio
async def test_removing_a_team_needs_the_coordinator_role(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="dis-staff-role")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    await db_session.commit()

    response = await client.delete(_team_url(env, run, team), headers=env.auth_headers["participant1"], params=_CONFIRM)
    assert response.status_code == 403
    await _assert_live(db_session, team.id)


@pytest.mark.asyncio
async def test_removing_a_team_of_another_run_or_organization_answers_404(
    client: AsyncClient, db_session: AsyncSession
):
    env = await setup_journey_environment(db_session, client, org_slug="dis-staff-scope")
    other_env = await setup_journey_environment(db_session, client, org_slug="dis-staff-other")
    run = await _run(db_session, env.org.id)
    other_run = await _run(db_session, env.org.id)
    other_team = await _team(db_session, env.org.id, other_run)
    foreign_run = await _run(db_session, other_env.org.id)
    foreign_team = await _team(db_session, other_env.org.id, foreign_run)
    await db_session.commit()

    wrong_run = await client.delete(
        _team_url(env, run, other_team), headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    wrong_org = await client.delete(
        _team_url(env, foreign_run, foreign_team), headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert (wrong_run.status_code, wrong_org.status_code) == (404, 404)
    await _assert_live(db_session, other_team.id)
    await _assert_live(db_session, foreign_team.id)


@pytest.mark.asyncio
async def test_a_dissolved_team_leaves_the_list_and_frees_name_and_code(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="dis-hidden")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run, "Falken")
    kept = await _team(db_session, env.org.id, run, "Adler")
    await db_session.commit()

    assert (
        await client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM)
    ).status_code == 204

    listing = await client.get(f"/api/orgs/{env.slug}/runs/{run.id}/teams/", headers=env.auth_headers["coordinator"])
    assert [t["id"] for t in listing.json()["items"]] == [str(kept.id)]
    assert (await client.get(_team_url(env, run, team), headers=env.auth_headers["coordinator"])).status_code == 404
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/", headers=env.auth_headers["coordinator"], json={"name": "Falken"}
    )
    assert created.status_code == 201
    again = await _team(db_session, env.org.id, run, "Reuse")
    again.share_code = team.share_code
    await db_session.flush()


# ---- paths that must not dissolve a team ------------------------------------


async def _ended_run_with_guest_team(db: AsyncSession, slug: str) -> tuple[UUID, Run, Team, User]:
    org_id, _user = await _bare_org_and_user(db, slug)
    run = await _run(db, org_id, RunState.ENDED)
    run.ended_at = datetime.now(UTC) - timedelta(days=400)
    team = await _team(db, org_id, run)
    guest = await _guest(db, org_id)
    await _member(db, org_id, team, guest)
    await _answer(db, org_id, run, team, points=5)
    await db.commit()
    return org_id, run, team, guest


@pytest.mark.asyncio
async def test_guest_cleanup_dissolves_no_team(db_session: AsyncSession):
    org_id, run, team, _guest_user = await _ended_run_with_guest_team(db_session, "path-guest-cleanup")

    assert await delete_run_guests(db_session, run.id, org_id, {run.id}) == 1
    await db_session.commit()

    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1


@pytest.mark.asyncio
async def test_retention_guest_sweep_dissolves_no_team(db_session: AsyncSession):
    _org_id, _run_row, team, _guest_user = await _ended_run_with_guest_team(db_session, "path-sweep-guests")

    await retention_tasks.sweep_run_guests(db_session)
    await db_session.commit()

    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1


@pytest.mark.asyncio
async def test_retention_anonymize_sweep_dissolves_no_team(db_session: AsyncSession):
    _org_id, _run_row, team, _guest_user = await _ended_run_with_guest_team(db_session, "path-sweep-anon")

    await retention_tasks.sweep_run_anonymize(db_session)
    await retention_tasks.sweep_anonymized_member_names(db_session)
    await db_session.commit()

    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [RunState.ACTIVE, RunState.PAUSED, RunState.ENDED])
async def test_deactivating_an_account_leaves_team_and_member_row(
    client: AsyncClient, db_session: AsyncSession, state: RunState
):
    env = await setup_journey_environment(db_session, client, org_slug=f"path-deactivate-{state.value}")
    run = await _run(db_session, env.org.id, state)
    team = await _team(db_session, env.org.id, run)
    user = env.users["participant1"]
    await _member(db_session, env.org.id, team, user)
    await db_session.commit()

    response = await client.put(
        f"/api/orgs/{env.slug}/users/{user.id}", headers=env.auth_headers["org_admin"], json={"is_active": False}
    )
    assert response.status_code == 200

    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [RunState.ACTIVE, RunState.PAUSED, RunState.ENDED])
async def test_grace_deletion_of_an_account_leaves_team_and_member_row(
    client: AsyncClient, db_session: AsyncSession, state: RunState
):
    env = await setup_journey_environment(db_session, client, org_slug=f"path-grace-{state.value}")
    run = await _run(db_session, env.org.id, state)
    team = await _team(db_session, env.org.id, run)
    user = env.users["participant1"]
    await _member(db_session, env.org.id, team, user)
    await db_session.commit()

    response = await client.delete(f"/api/orgs/{env.slug}/users/{user.id}", headers=env.auth_headers["org_admin"])
    assert response.status_code == 204

    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1


@pytest.mark.asyncio
async def test_a_team_without_member_rows_in_an_ended_run_stays(db_session: AsyncSession):
    """The team of an ended run whose member rows are gone is not dissolved by any sweep."""
    org_id, _user = await _bare_org_and_user(db_session, "path-empty-ended")
    run = await _run(db_session, org_id, RunState.ENDED)
    run.ended_at = datetime.now(UTC) - timedelta(days=400)
    team = await _team(db_session, org_id, run)
    await _answer(db_session, org_id, run, team, points=4)
    await db_session.commit()

    await retention_tasks.sweep_run_guests(db_session)
    await retention_tasks.sweep_run_anonymize(db_session)
    await db_session.commit()

    await _assert_live(db_session, team.id)


# ---- a decision taken before a lock is checked under it ---------------------
#
# Each race holds one request at its first row lock, after it has looked the
# team up and before it locks it, lets a dissolution commit, then releases it.
# No sleeps: the hook below waits on events.


class _PauseAtFirstLock:
    """Holds the first row lock a session asks for until ``release`` is set.

    ``table`` narrows it to the first ``SELECT ... FOR UPDATE`` on that table:
    ``"teams"`` holds a request that already has its account lock right before
    its team lock. ``or_at_code_reset`` also arms the point where a join by
    code has accepted the code: a join that reaches it without having asked
    for a lock is held there, after its lookups and before its commit.
    """

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, table: str | None = None, or_at_code_reset: bool = False
    ) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self._armed = True
        original = AsyncSession.execute
        hook = self

        async def pause() -> None:
            hook._armed = False
            hook.reached.set()
            await hook.release.wait()

        async def execute(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
            if (
                hook._armed
                and getattr(statement, "_for_update_arg", None) is not None
                and (table is None or f"FROM {table}" in str(statement))
            ):
                await pause()
            return await original(session, statement, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "execute", execute)

        if or_at_code_reset:
            original_reset = team_code_rate_limit.reset

            async def reset(*args: Any, **kwargs: Any) -> Any:
                if hook._armed:
                    await pause()
                return await original_reset(*args, **kwargs)

            monkeypatch.setattr(team_code_rate_limit, "reset", reset)

    async def wait_reached(self) -> None:
        await asyncio.wait_for(self.reached.wait(), timeout=10)


async def _assert_no_member_in_dissolved_and_no_empty_live_team(db: AsyncSession, run_id: UUID) -> None:
    """The two states a race must never leave behind in the run."""
    members_in_dissolved = (
        await db.execute(
            select(func.count(TeamMember.id))
            .join(Team, Team.id == TeamMember.team_id)
            .where(Team.run_id == run_id, Team.is_deleted.is_(True))
        )
    ).scalar_one()
    assert members_in_dissolved == 0, "a member row exists for a dissolved team"
    live_teams = (
        (await db.execute(select(Team.id).where(Team.run_id == run_id, Team.is_deleted.is_(False)))).scalars().all()
    )
    for team_id in live_teams:
        assert await _member_count(db, team_id) > 0, f"live team {team_id} has no member"


@pytest.mark.asyncio
async def test_guest_join_that_waits_for_a_dissolution_gets_unknown_code(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """The guest join looked the code up, the team dissolves before its lock, and the guest gets an unknown code."""
    env = await setup_journey_environment(db_session, client, org_slug="race-guest-join")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    last = await _member(db_session, env.org.id, team, env.users["participant1"])
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch)
    join = asyncio.create_task(
        concurrent_client.post(
            f"/api/join/{run.join_code}",
            json={"mode": "join", "display_name": "Spaet", "team_code": team.share_code},
        )
    )
    await hook.wait_reached()
    removed = await concurrent_client.delete(
        f"{_team_url(env, run, team)}/members/{last.id}", headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert removed.status_code == 204
    hook.release.set()
    response = await join

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Team-Code unbekannt."
    await _assert_dissolved(db_session, team.id)
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_member_join_by_code_that_waits_for_a_dissolution_gets_unknown_code(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """The join by code for signed-in members, raced the same way as the guest join."""
    env = await setup_journey_environment(db_session, client, org_slug="race-member-join")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    last = await _member(db_session, env.org.id, team, env.users["participant1"])
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch)
    join = asyncio.create_task(
        concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run.id}/teams/by-code",
            headers=env.auth_headers["participant2"],
            json={"team_code": team.share_code},
        )
    )
    await hook.wait_reached()
    removed = await concurrent_client.delete(
        f"{_team_url(env, run, team)}/members/{last.id}", headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert removed.status_code == 204
    hook.release.set()
    response = await join

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Team-Code unbekannt."
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_move_into_a_target_that_dissolves_before_the_lock_is_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """The move resolved its target, the target dissolves before the locks, and the move is refused."""
    env = await setup_journey_environment(db_session, client, org_slug="race-move-target")
    run = await _run(db_session, env.org.id)
    source = await _team(db_session, env.org.id, run, "Source")
    target = await _team(db_session, env.org.id, run, "Target")
    moving = await _member(db_session, env.org.id, source, env.users["participant1"])
    await _member(db_session, env.org.id, target, env.users["participant2"])
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch)
    move = asyncio.create_task(
        concurrent_client.post(
            f"{_team_url(env, run, source)}/members/{moving.id}/move",
            headers=env.auth_headers["coordinator"],
            json={"target_team_id": str(target.id), **_CONFIRM_BODY},
        )
    )
    await hook.wait_reached()
    removed = await concurrent_client.delete(
        _team_url(env, run, target), headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert removed.status_code == 204
    hook.release.set()
    response = await move

    assert response.status_code == 422, response.text
    assert response.json()["error_code"] == "TARGET_NOT_IN_RUN"
    await _assert_live(db_session, source.id)
    await _assert_dissolved(db_session, target.id)
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_erasure_follows_a_member_moved_before_its_lock(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    audit_spy,
):
    """The erasure found the member in A, the row goes into the empty B before the team lock.

    The erasure holds the run row at that moment, so no move of the
    Spielleitung can commit beside it. The row is re-pointed
    directly, as a path that the lock order missed would do it: the erasure
    still follows the row into the team it is in under the locks.
    """
    env = await setup_journey_environment(db_session, client, org_slug="race-erase-move")
    run = await _run(db_session, env.org.id)
    team_a = await _team(db_session, env.org.id, run, "A")
    team_b = await _team(db_session, env.org.id, run, "B")
    user = env.users["participant1"]
    member = await _member(db_session, env.org.id, team_a, user)
    await _member(db_session, env.org.id, team_a, None, name="Stays")
    await db_session.commit()

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def erase() -> bool:
        async with sessions() as session:
            done = await anonymize_user(session, user.id, {"system": "test"})
            await session.commit()
            return done

    hook = _PauseAtFirstLock(monkeypatch, table="teams")
    erasure = asyncio.create_task(erase())
    await hook.wait_reached()
    async with sessions() as other:
        await other.execute(update(TeamMember).where(TeamMember.id == member.id).values(team_id=team_b.id))
        await other.commit()
    hook.release.set()
    assert await erasure is True

    await _assert_live(db_session, team_a.id)
    assert await _member_count(db_session, team_a.id) == 1
    await _assert_dissolved(db_session, team_b.id)
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)
    erased = [e for e in audit_spy.named("team.dissolved") if e["target"]["cause"] == "last_member_erased"]
    assert [e["target"]["id"] for e in erased] == [str(team_b.id)]


# ---- the account, the membership and the team are read again under the locks -
#
# Lock order of every path: the account row when the path writes for an
# account, then team rows in ascending id, then member rows. Each test holds
# one request between its lookups and a lock, lets a second transaction change
# what the request decided on, then releases it.


def _session_cookie(user: User) -> dict[str, str]:
    token = get_auth_headers(user)["Authorization"].split(" ", 1)[1]
    return {"Cookie": f"access_token={token}"}


async def _erase(test_engine: Any, user_id: UUID) -> bool:
    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        done = await anonymize_user(session, user_id, {"system": "test"})
        await session.commit()
        return done


async def _deactivate(test_engine: Any, user_id: UUID) -> None:
    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        await session.execute(update(User).where(User.id == user_id).values(is_active=False))
        await session.commit()


async def _rows_of(db: AsyncSession, user_id: UUID) -> list[tuple[UUID, str]]:
    stmt = select(TeamMember.team_id, TeamMember.display_name).where(TeamMember.user_id == user_id)
    return [(row.team_id, row.display_name) for row in (await db.execute(stmt)).all()]


async def _names_in(db: AsyncSession, team_id: UUID) -> list[str]:
    return list((await db.execute(select(TeamMember.display_name).where(TeamMember.team_id == team_id))).scalars())


@pytest.mark.asyncio
async def test_guest_replaying_the_team_code_during_a_dissolution_gets_unknown_code(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """A guest who is in the team sends the code again while the team is removed."""
    env = await setup_journey_environment(db_session, client, org_slug="race-guest-replay")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    guest = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, guest)
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch, table="teams", or_at_code_reset=True)
    replay = asyncio.create_task(
        concurrent_client.post(
            f"/api/join/{run.join_code}",
            json={"mode": "join", "display_name": "Gast", "team_code": team.share_code},
            headers=_session_cookie(guest),
        )
    )
    await hook.wait_reached()
    removed = await concurrent_client.delete(
        _team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert removed.status_code == 204
    hook.release.set()
    response = await replay

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Team-Code unbekannt."
    await _assert_dissolved(db_session, team.id)
    assert await _rows_of(db_session, guest.id) == []
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


def _join_url(env: JourneyEnv, run: Run, team: Team, route: str) -> tuple[str, dict[str, str] | None]:
    base = f"/api/orgs/{env.slug}/runs/{run.id}/teams"
    if route == "by-code":
        return f"{base}/by-code", {"team_code": team.share_code}
    return f"{base}/{team.id}/join", None


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["by-code", "by-id"])
async def test_join_in_flight_does_not_bring_an_erased_account_back(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
):
    """The join is authenticated, the account is erased, then the join goes on."""
    monkeypatch.setattr(settings.auth, "allow_team_join_by_id", True)
    env = await setup_journey_environment(db_session, client, org_slug=f"race-join-erase-{route}")
    run = await _run(db_session, env.org.id)
    team_a = await _team(db_session, env.org.id, run, "A")
    team_b = await _team(db_session, env.org.id, run, "B")
    user = env.users["participant1"]
    name_before = user.display_name
    await _member(db_session, env.org.id, team_a, user)
    await _member(db_session, env.org.id, team_b, env.users["participant2"], name="Stays")
    await db_session.commit()
    names_in_b = await _names_in(db_session, team_b.id)

    url, body = _join_url(env, run, team_b, route)
    hook = _PauseAtFirstLock(monkeypatch)
    join = asyncio.create_task(concurrent_client.post(url, headers=env.auth_headers["participant1"], json=body))
    await hook.wait_reached()
    assert await _erase(test_engine, user.id) is True
    hook.release.set()
    response = await join

    assert response.status_code == 401, response.text
    assert await _rows_of(db_session, user.id) == []
    assert await _names_in(db_session, team_b.id) == names_in_b
    assert name_before not in [name for _, name in await _rows_of(db_session, user.id)]
    await _assert_dissolved(db_session, team_a.id)
    await _assert_live(db_session, team_b.id)
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["by-code", "by-id"])
async def test_join_in_flight_of_a_deactivated_account_is_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
):
    """The account is deactivated between the authentication of the join and its locks."""
    monkeypatch.setattr(settings.auth, "allow_team_join_by_id", True)
    env = await setup_journey_environment(db_session, client, org_slug=f"race-join-off-{route}")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    await _member(db_session, env.org.id, team, env.users["participant2"])
    user = env.users["participant1"]
    await db_session.commit()

    url, body = _join_url(env, run, team, route)
    hook = _PauseAtFirstLock(monkeypatch)
    join = asyncio.create_task(concurrent_client.post(url, headers=env.auth_headers["participant1"], json=body))
    await hook.wait_reached()
    await _deactivate(test_engine, user.id)
    hook.release.set()
    response = await join

    assert response.status_code == 401, response.text
    assert response.json()["error_code"] == "ACCOUNT_DISABLED"
    assert await _rows_of(db_session, user.id) == []


@pytest.mark.asyncio
async def test_erasure_that_waits_for_a_join_takes_the_new_member_row_too(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The join holds the account lock first: the erasure waits for it and then removes what it wrote."""
    env = await setup_journey_environment(db_session, client, org_slug="race-erase-waits")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    await _member(db_session, env.org.id, team, env.users["participant2"])
    user = env.users["participant1"]
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch, table="teams")
    join = asyncio.create_task(
        concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run.id}/teams/by-code",
            headers=env.auth_headers["participant1"],
            json={"team_code": team.share_code},
        )
    )
    await hook.wait_reached()
    erasure = asyncio.create_task(_erase(test_engine, user.id))
    hook.release.set()
    response = await join
    assert await erasure is True

    assert response.status_code == 204, response.text
    assert await _rows_of(db_session, user.id) == []
    await _assert_live(db_session, team.id)
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_guest_create_in_flight_does_not_reuse_an_erased_account(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The join flow resolved a session to an account, the account is erased before its lock."""
    env = await setup_journey_environment(db_session, client, org_slug="race-create-erase")
    run = await _run(db_session, env.org.id)
    guest = await _guest(db_session, env.org.id)
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch)
    create = asyncio.create_task(
        concurrent_client.post(
            f"/api/join/{run.join_code}",
            json={"mode": "create", "team_name": "Neu"},
            headers=_session_cookie(guest),
        )
    )
    await hook.wait_reached()
    assert await _erase(test_engine, guest.id) is True
    hook.release.set()
    response = await create

    assert response.status_code == 201, response.text
    assert response.json()["user_id"] != str(guest.id)
    assert await _rows_of(db_session, guest.id) == []
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_guest_replaying_a_creation_during_the_removal_of_the_team_is_departed(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """The replay found the guest's team, the team is removed before its lock: no second team appears."""
    env = await setup_journey_environment(db_session, client, org_slug="race-create-replay")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    guest = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, guest)
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch, table="teams")
    replay = asyncio.create_task(
        concurrent_client.post(
            f"/api/join/{run.join_code}",
            json={"mode": "create", "team_name": "Nochmal"},
            headers=_session_cookie(guest),
        )
    )
    await hook.wait_reached()
    removed = await concurrent_client.delete(
        _team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert removed.status_code == 204
    hook.release.set()
    response = await replay

    assert response.status_code == 403, response.text
    assert response.json()["error_code"] == "RUN_DEPARTED"
    live = (await db_session.execute(select(Team.id).where(Team.run_id == run.id, Team.is_deleted.is_(False)))).all()
    assert live == []
    assert await _rows_of(db_session, guest.id) == []


@pytest.mark.asyncio
async def test_guest_join_in_flight_sees_its_removal_from_the_other_team(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """A guest of team A sends the code of B, and is removed from A before the join decides."""
    env = await setup_journey_environment(db_session, client, org_slug="race-guest-removed")
    run = await _run(db_session, env.org.id)
    team_a = await _team(db_session, env.org.id, run, "A")
    team_b = await _team(db_session, env.org.id, run, "B")
    guest = await _guest(db_session, env.org.id)
    seat = await _member(db_session, env.org.id, team_a, guest)
    await _member(db_session, env.org.id, team_a, env.users["participant1"])
    await _member(db_session, env.org.id, team_b, env.users["participant2"])
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch, table="teams")
    join = asyncio.create_task(
        concurrent_client.post(
            f"/api/join/{run.join_code}",
            json={"mode": "join", "display_name": "Gast", "team_code": team_b.share_code},
            headers=_session_cookie(guest),
        )
    )
    await hook.wait_reached()
    removed = await concurrent_client.delete(
        f"{_team_url(env, run, team_a)}/members/{seat.id}", headers=env.auth_headers["coordinator"], params=_CONFIRM
    )
    assert removed.status_code == 204
    hook.release.set()
    response = await join

    assert response.status_code == 403, response.text
    assert response.json()["error_code"] == "RUN_DEPARTED"
    assert await _rows_of(db_session, guest.id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("act", ["leave", "remove_member", "remove_team", "move"])
async def test_an_act_on_a_team_that_dissolves_before_its_lock_answers_404(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    act: str,
):
    """Leave, remove a member, remove the team, move out of it: each is held before its first lock, the run row.

    The team is removed meanwhile. An act that holds the run row lets no
    removal of the same run through, so the act is held before that lock.
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"race-act-{act.replace('_', '-')}")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run, "Goes")
    other = await _team(db_session, env.org.id, run, "Stays")
    seat = await _member(db_session, env.org.id, team, env.users["participant1"])
    await _member(db_session, env.org.id, other, env.users["participant2"])
    await db_session.commit()
    staff = env.auth_headers["coordinator"]
    url = _team_url(env, run, team)

    hook = _PauseAtFirstLock(monkeypatch, table="runs")
    if act == "leave":
        request = concurrent_client.post(f"{url}/leave", headers=env.auth_headers["participant1"], json=_CONFIRM_BODY)
    elif act == "remove_member":
        request = concurrent_client.delete(f"{url}/members/{seat.id}", headers=staff, params=_CONFIRM)
    elif act == "remove_team":
        request = concurrent_client.delete(url, headers=staff, params=_CONFIRM)
    else:
        request = concurrent_client.post(
            f"{url}/members/{seat.id}/move", headers=staff, json={"target_team_id": str(other.id), **_CONFIRM_BODY}
        )
    held = asyncio.create_task(request)
    await hook.wait_reached()
    removed = await concurrent_client.delete(url, headers=staff, params=_CONFIRM)
    assert removed.status_code == 204
    hook.release.set()
    response = await held

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Team not found in this run"
    await _assert_dissolved(db_session, team.id)
    assert await _member_count(db_session, other.id) == 1
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_move_of_a_member_whose_account_is_erased_before_the_locks_is_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The erasure removes the member row and the source team before the move takes its first lock, the run row."""
    env = await setup_journey_environment(db_session, client, org_slug="race-move-erased")
    run = await _run(db_session, env.org.id)
    source = await _team(db_session, env.org.id, run, "Source")
    target = await _team(db_session, env.org.id, run, "Target")
    user = env.users["participant1"]
    seat = await _member(db_session, env.org.id, source, user)
    await _member(db_session, env.org.id, target, env.users["participant2"])
    await db_session.commit()

    hook = _PauseAtFirstLock(monkeypatch, table="runs")
    move = asyncio.create_task(
        concurrent_client.post(
            f"{_team_url(env, run, source)}/members/{seat.id}/move",
            headers=env.auth_headers["coordinator"],
            json={"target_team_id": str(target.id), **_CONFIRM_BODY},
        )
    )
    await hook.wait_reached()
    assert await _erase(test_engine, user.id) is True
    hook.release.set()
    response = await move

    assert response.status_code == 404, response.text
    assert await _rows_of(db_session, user.id) == []
    await _assert_dissolved(db_session, source.id)
    assert await _member_count(db_session, target.id) == 1
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_move_and_erasure_at_once_do_not_deadlock(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
):
    """Move and erasure of the same member, both ways round, with nothing held back: both end, in either order."""
    env = await setup_journey_environment(db_session, client, org_slug="race-move-erase-free")
    run = await _run(db_session, env.org.id)
    staff = env.auth_headers["coordinator"]
    for round_no in range(4):
        team_a = await _team(db_session, env.org.id, run, f"A{round_no}")
        team_b = await _team(db_session, env.org.id, run, f"B{round_no}")
        user = User(
            email=f"mover-{uuid4().hex[:8]}@example.com",
            display_name="Mover",
            password_hash="x",
            role=UserRole.PARTICIPANT,
            organization_id=env.org.id,
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()
        # Both directions of the id order: the member starts in the lower or the higher team.
        source, target = (team_a, team_b) if round_no % 2 == 0 else (team_b, team_a)
        seat = await _member(db_session, env.org.id, source, user)
        await _member(db_session, env.org.id, target, None, name="Stays")
        await db_session.commit()

        moved, erased = await asyncio.wait_for(
            asyncio.gather(
                concurrent_client.post(
                    f"{_team_url(env, run, source)}/members/{seat.id}/move",
                    headers=staff,
                    json={"target_team_id": str(target.id), **_CONFIRM_BODY},
                ),
                _erase(test_engine, user.id),
            ),
            timeout=30,
        )
        assert moved.status_code in (204, 404), moved.text
        assert erased is True
        assert await _rows_of(db_session, user.id) == []
        await _assert_dissolved(db_session, source.id)
        await _assert_live(db_session, target.id)
        assert await _member_count(db_session, target.id) == 1
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_the_grace_sweep_erases_two_members_of_one_team_and_dissolves_it_once(
    db_session: AsyncSession, audit_spy
):
    """The sweep erases several accounts in one transaction: accounts first, then all their teams."""
    org_id, first = await _bare_org_and_user(db_session, "sweep-two")
    second = await _guest(db_session, org_id)
    run = await _run(db_session, org_id)
    team = await _team(db_session, org_id, run)
    long_ago = datetime.now(UTC) - timedelta(days=40)
    for account in (first, second):
        await _member(db_session, org_id, team, account)
        account.is_deleted = True
        account.deleted_at = long_ago
        account.is_active = False
    await db_session.flush()

    assert await gdpr.sweep_expired_grace_deletions(db_session) == 2
    # The entry is written when the transaction of the sweep has committed.
    await db_session.commit()

    await _assert_dissolved(db_session, team.id)
    assert len(audit_spy.named("team.dissolved")) == 1


# ---- the guest cleanup beside a dissolution ---------------------------------


async def _clean_guests(test_engine: Any, run_id: UUID, org_id: UUID) -> int:
    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        deleted = await delete_run_guests(session, run_id, org_id, {run_id})
        await session.commit()
        return deleted


@dataclass(frozen=True)
class _LockWait:
    """One backend that waits for a row lock: who holds it, and in which table the row is."""

    pid: int
    blocked_by: tuple[int, ...]
    table: str | None
    query: str


# A backend that waits for a row holds the tuple lock of that row while it
# waits for the transaction that has the row: ``pg_locks`` names the table,
# ``pg_blocking_pids`` the holder.
_LOCK_WAITS = text(
    """
    SELECT a.pid,
           pg_blocking_pids(a.pid) AS blocked_by,
           (SELECT min(l.relation::regclass::text) FROM pg_locks l
             WHERE l.pid = a.pid AND l.locktype = 'tuple') AS waits_in,
           a.query
      FROM pg_stat_activity a
     WHERE a.datname = current_database() AND a.wait_event_type = 'Lock'
     ORDER BY a.pid
    """
)


async def _backend_pid(session: AsyncSession) -> int:
    """The database backend that runs the transaction of ``session``."""
    return int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())


async def _read_lock_waits(test_engine: Any) -> list[_LockWait]:
    async with test_engine.connect() as connection:
        rows = (await connection.execute(_LOCK_WAITS)).all()
    return [_LockWait(row.pid, tuple(row.blocked_by), row.waits_in, row.query) for row in rows]


async def _lock_waits(test_engine: Any, task: "asyncio.Task[Any]") -> list[_LockWait]:
    """The lock waits in this database once one exists; empty when ``task`` ended without one.

    A statement that waits inside the database can set no event, so the state
    of the database is the signal. The caller asserts on the edge it expects:
    which backend waits, for which backend, at a row of which table.
    """

    async def watch() -> list[_LockWait]:
        while not task.done():
            waits = await _read_lock_waits(test_engine)
            if waits:
                return waits
            await asyncio.sleep(0.01)
        return []

    return await asyncio.wait_for(watch(), timeout=10)


async def _assert_one_wait(test_engine: Any, task: "asyncio.Task[Any]", *, holder: int, table: str) -> _LockWait:
    """``task`` waits, alone, for a row of ``table`` that backend ``holder`` has."""
    waits = await _lock_waits(test_engine, task)
    assert not task.done(), "the task ended without waiting for the lock"
    assert [(wait.blocked_by, wait.table) for wait in waits] == [((holder,), table)], waits
    return waits[0]


async def _never_waits(test_engine: Any, task: "asyncio.Task[Any]", pid: int) -> None:
    """Watch until ``task`` ends: backend ``pid`` must not wait for a lock at any moment."""

    async def watch() -> None:
        while not task.done():
            waits = [wait for wait in await _read_lock_waits(test_engine) if wait.pid == pid]
            assert not waits, f"backend {pid} waits for a lock: {waits}"
            await asyncio.sleep(0.01)

    await asyncio.wait_for(watch(), timeout=30)


async def _free_member_rows(test_engine: Any, team_id: UUID) -> int:
    """How many member rows of the team no transaction holds a lock on."""
    async with test_engine.connect() as connection:
        rows = await connection.execute(
            select(TeamMember.id).where(TeamMember.team_id == team_id).with_for_update(skip_locked=True)
        )
        free = len(rows.all())
        await connection.rollback()
    return free


class _PauseAfter:
    """Holds a session right after the first statement that ``matches`` until ``release`` is set.

    ``pid`` is the database backend of the held session, for the assertions on
    who waits for whom.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, matches: Callable[[Any], bool]) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.pid = 0
        self._armed = True
        original = AsyncSession.execute
        hook = self

        async def execute(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
            result = await original(session, statement, *args, **kwargs)
            if hook._armed and matches(statement):
                hook._armed = False
                hook.pid = int((await original(session, text("SELECT pg_backend_pid()"))).scalar_one())
                hook.reached.set()
                await hook.release.wait()
            return result

        monkeypatch.setattr(AsyncSession, "execute", execute)

    async def wait_reached(self) -> None:
        await asyncio.wait_for(self.reached.wait(), timeout=10)


def _locks_rows_of(table: str) -> Callable[[Any], bool]:
    """Matches a ``SELECT ... FOR UPDATE`` on ``table``."""
    return lambda statement: (
        getattr(statement, "_for_update_arg", None) is not None and f"FROM {table}" in str(statement)
    )


def _updates(table: str) -> Callable[[Any], bool]:
    """Matches an ``UPDATE`` of ``table``."""
    return lambda statement: (
        getattr(statement, "is_update", False) and getattr(getattr(statement, "table", None), "name", "") == table
    )


def _count_attempts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the attempts of the acts that run again after a lock failure: one entry per attempt."""
    attempts: list[int] = []
    original = teams_router.retry_on_lock_failure

    async def counting(db: AsyncSession, attempt: Callable[[], Awaitable[Any]], **kwargs: Any) -> Any:
        async def counted() -> Any:
            attempts.append(1)
            return await attempt()

        return await original(db, counted, **kwargs)

    monkeypatch.setattr(teams_router, "retry_on_lock_failure", counting)
    return attempts


async def _account_exists(db: AsyncSession, user_id: UUID) -> bool:
    return (await db.execute(select(func.count(User.id)).where(User.id == user_id))).scalar_one() == 1


@pytest.mark.asyncio
async def test_team_removal_and_guest_cleanup_at_once_do_not_deadlock(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The removal holds the team and its member rows and has not written the departure; the cleanup starts then."""
    env = await setup_journey_environment(db_session, client, org_slug="race-remove-cleanup")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    guest = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, guest)
    await db_session.commit()

    reached, release = asyncio.Event(), asyncio.Event()
    original_record = RunDepartureRepository.record

    async def record(self: RunDepartureRepository, *args: Any, **kwargs: Any) -> None:
        reached.set()
        await release.wait()
        await original_record(self, *args, **kwargs)

    monkeypatch.setattr(RunDepartureRepository, "record", record)

    removal = asyncio.create_task(
        concurrent_client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM)
    )
    await asyncio.wait_for(reached.wait(), timeout=10)
    try:
        # The removal is held by the event, so the cleanup must end on its own: it waits for no lock.
        deleted = await asyncio.wait_for(_clean_guests(test_engine, run.id, env.org.id), timeout=10)
        assert not removal.done()
    finally:
        release.set()
    response = await asyncio.wait_for(removal, timeout=30)

    assert response.status_code == 204, response.text
    # The guest was being removed at that moment: the cleanup leaves the account to the removal.
    assert deleted == 0
    await _assert_dissolved(db_session, team.id)
    assert await _account_exists(db_session, guest.id)
    departures = (
        await db_session.execute(
            select(func.count(RunDeparture.id)).where(RunDeparture.user_id == guest.id, RunDeparture.run_id == run.id)
        )
    ).scalar_one()
    assert departures == 1
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_guest_cleanup_takes_a_departed_guest_beside_a_dissolution_and_leaves_the_one_being_removed(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The cleanup selects guests who left, too, and they take the locks of the order.

    One guest left the team earlier: a departure record and no member row. The
    other is the last member. The removal of the team holds the team and its
    member row and has not written the departure of the last member; the
    cleanup runs then. The guest who left has no team to hold and goes. The
    guest being removed stays, and neither transaction waits for the other.
    """
    env = await setup_journey_environment(db_session, client, org_slug="race-remove-cleanup-departed")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    departed = await _guest(db_session, env.org.id)
    await RunDepartureRepository(db_session, organization_id=env.org.id).record(user_id=departed.id, run_id=run.id)
    last_member = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, last_member)
    await db_session.commit()

    reached, release = asyncio.Event(), asyncio.Event()
    original_record = RunDepartureRepository.record

    async def record(self: RunDepartureRepository, *args: Any, **kwargs: Any) -> None:
        reached.set()
        await release.wait()
        await original_record(self, *args, **kwargs)

    monkeypatch.setattr(RunDepartureRepository, "record", record)

    removal = asyncio.create_task(
        concurrent_client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM)
    )
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        # The removal is held by the event, so the cleanup must end on its own: it waits for no lock.
        deleted = await asyncio.wait_for(_clean_guests(test_engine, run.id, env.org.id), timeout=10)
        assert not removal.done()
    finally:
        release.set()
    response = await asyncio.wait_for(removal, timeout=30)

    assert response.status_code == 204, response.text
    assert deleted == 1
    assert not await _account_exists(db_session, departed.id)
    assert await _account_exists(db_session, last_member.id)
    await _assert_dissolved(db_session, team.id)
    departures = (await db_session.execute(select(RunDeparture.user_id).where(RunDeparture.run_id == run.id))).scalars()
    # The departure of the deleted account went with it; the removal wrote the one of the last member.
    # Compared as text: the ids that come back are not of the type of the ids the test holds.
    assert [str(user_id) for user_id in departures] == [str(last_member.id)]
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)

    # The next cleanup finds the removed guest by the departure record.
    assert await _clean_guests(test_engine, run.id, env.org.id) == 1
    assert not await _account_exists(db_session, last_member.id)


@pytest.mark.asyncio
async def test_team_removal_that_waits_for_the_guest_cleanup_removes_the_team(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
):
    """The other way round: the cleanup holds account and team, the removal waits at the team and then finds no account."""
    env = await setup_journey_environment(db_session, client, org_slug="race-cleanup-remove")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    guest = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, guest)
    await db_session.commit()

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as cleanup_session:
        assert await delete_run_guests(cleanup_session, run.id, env.org.id, {run.id}) == 1
        removal = asyncio.create_task(
            concurrent_client.delete(
                _team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM
            )
        )
        await _assert_one_wait(test_engine, removal, holder=await _backend_pid(cleanup_session), table="teams")
        await cleanup_session.commit()
    response = await asyncio.wait_for(removal, timeout=30)

    assert response.status_code == 204, response.text
    await _assert_dissolved(db_session, team.id)
    assert not await _account_exists(db_session, guest.id)
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_guest_cleanup_leaves_a_guest_whose_account_or_team_another_transaction_holds(
    db_session: AsyncSession, test_engine: Any
):
    """The cleanup waits for no lock: a guest in the middle of an act stays for the next sweep, the others go."""
    org_id, run, team, busy_guest = await _ended_run_with_guest_team(db_session, "cleanup-skips")
    held_team = await _team(db_session, org_id, run, "Held")
    guest_of_held_team = await _guest(db_session, org_id)
    await _member(db_session, org_id, held_team, guest_of_held_team)
    free_guest = await _guest(db_session, org_id)
    await _member(db_session, org_id, team, free_guest)
    await db_session.commit()

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as other:
        assert await lock_account(other, busy_guest.id) is not None
        assert await TeamRepository(other, organization_id=org_id).lock_row(held_team.id)

        assert await asyncio.wait_for(_clean_guests(test_engine, run.id, org_id), timeout=10) == 1
        await other.rollback()

    assert await _account_exists(db_session, busy_guest.id)
    assert await _account_exists(db_session, guest_of_held_team.id)
    assert not await _account_exists(db_session, free_guest.id)

    assert await _clean_guests(test_engine, run.id, org_id) == 2
    await _assert_live(db_session, team.id)
    await _assert_live(db_session, held_team.id)


# ---- the sweeps beside a dissolution: team before member, in every path ------


async def _in_own_transaction(test_engine: Any, task: Any) -> Any:
    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        result = await task(session)
        await session.commit()
        return result


@pytest.mark.asyncio
async def test_team_removal_and_anonymize_sweep_at_once_do_not_deadlock(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The sweep has renamed the member rows and not yet the team; the removal of that team starts then."""
    env = await setup_journey_environment(db_session, client, org_slug="race-remove-anonymize")
    run = await _run(db_session, env.org.id, RunState.ENDED)
    run.ended_at = datetime.now(UTC) - timedelta(days=400)
    team = await _team(db_session, env.org.id, run, "Die Wilden")
    await _member(db_session, env.org.id, team, None, name="Anna")
    await _member(db_session, env.org.id, team, None, name="Bela")
    await db_session.commit()

    attempts = _count_attempts(monkeypatch)
    hook = _PauseAfter(monkeypatch, _updates("team_members"))
    sweep = asyncio.create_task(_in_own_transaction(test_engine, retention_tasks.sweep_run_anonymize))
    await hook.wait_reached()
    removal = asyncio.create_task(
        concurrent_client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM)
    )
    try:
        # The sweep took the run row first, then the team, then its member
        # rows, so the removal waits at the run row, its own first lock, and
        # holds nothing. With the run row after the team the removal would hold
        # the run and wait at the team while the sweep waits at the run for its
        # marker: a deadlock, which the retry of the removal would hide from
        # the answers below.
        await _assert_one_wait(test_engine, removal, holder=hook.pid, table="runs")
    finally:
        hook.release.set()
    anonymized, response = await asyncio.wait_for(asyncio.gather(sweep, removal), timeout=30)

    assert response.status_code == 204, response.text
    assert len(attempts) == 1, "the removal lost a deadlock and ran again"
    assert anonymized >= 1
    await _assert_dissolved(db_session, team.id)
    assert await _member_count(db_session, team.id) == 0
    assert (await _team_row(db_session, team.id)).name == "Team 1"


async def _hold_team_and_start(test_engine: Any, org_id: UUID, team_id: UUID, task: Any) -> None:
    """Hold the team row in one transaction, start ``task`` in another: it must wait for that team first.

    Two things are true at the wait: it is the wait for the team row the
    holder has, and the task has no member row of the team yet. A path that
    writes its member rows first also ends up waiting for the team, at its
    rename, but by then it holds the member rows.
    """
    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as holder:
        assert await TeamRepository(holder, organization_id=org_id).lock_row(team_id)
        members = (
            await holder.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team_id))
        ).scalar_one()
        assert members > 0
        running = asyncio.create_task(_in_own_transaction(test_engine, task))
        try:
            await _assert_one_wait(test_engine, running, holder=await _backend_pid(holder), table="teams")
            assert await _free_member_rows(test_engine, team_id) == members, (
                "the path holds a member row without the lock of its team"
            )
        finally:
            await holder.rollback()
    await asyncio.wait_for(running, timeout=30)


@pytest.mark.asyncio
async def test_anonymize_sweep_takes_the_team_before_its_member_rows(db_session: AsyncSession, test_engine: Any):
    org_id, _run_row, team, _guest_user = await _ended_run_with_guest_team(db_session, "order-sweep-anon")

    await _hold_team_and_start(test_engine, org_id, team.id, retention_tasks.sweep_run_anonymize)

    assert await _names_in(db_session, team.id) == [ANONYMIZED_MEMBER_NAME]


@pytest.mark.asyncio
async def test_anonymize_sweep_takes_the_run_before_its_teams(db_session: AsyncSession, test_engine: Any):
    """Run, then team: the order of the dissolving acts, so the sweep and an act cannot wait in a ring."""
    org_id, run, team, _guest_user = await _ended_run_with_guest_team(db_session, "order-sweep-anon-run")

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as holder:
        await RunRepository(holder, organization_id=org_id).lock_row(run.id)
        sweep = asyncio.create_task(_in_own_transaction(test_engine, retention_tasks.sweep_run_anonymize))
        try:
            await _assert_one_wait(test_engine, sweep, holder=await _backend_pid(holder), table="runs")
            async with test_engine.connect() as connection:
                free = await connection.execute(
                    select(Team.id).where(Team.id == team.id).with_for_update(skip_locked=True)
                )
                assert free.all(), "the sweep holds a team without the lock of its run"
                await connection.rollback()
        finally:
            await holder.rollback()
    assert await asyncio.wait_for(sweep, timeout=30) >= 1

    assert await _names_in(db_session, team.id) == [ANONYMIZED_MEMBER_NAME]


@pytest.mark.asyncio
async def test_anonymized_member_names_sweep_takes_the_team_before_its_member_rows(
    db_session: AsyncSession, test_engine: Any
):
    """The catch-up for accounts an earlier version anonymized and left with their roster name."""
    org_id, user = await _bare_org_and_user(db_session, "order-sweep-names")
    run = await _run(db_session, org_id, RunState.ENDED)
    team = await _team(db_session, org_id, run)
    await _member(db_session, org_id, team, user, name="Anna")
    user.email = f"{gdpr.ANONYMIZED_EMAIL_PREFIX}{user.id}{gdpr.ANONYMIZED_EMAIL_SUFFIX}"
    await db_session.commit()

    await _hold_team_and_start(test_engine, org_id, team.id, retention_tasks.sweep_anonymized_member_names)

    assert await _names_in(db_session, team.id) == [ANONYMIZED_MEMBER_NAME]


@pytest.mark.asyncio
async def test_erasure_takes_the_team_of_an_ended_run_before_it_renames_the_member(
    db_session: AsyncSession, test_engine: Any
):
    org_id, user = await _bare_org_and_user(db_session, "order-erase-ended")
    run = await _run(db_session, org_id, RunState.ENDED)
    team = await _team(db_session, org_id, run)
    await _member(db_session, org_id, team, user, name="Anna")
    await db_session.commit()

    async def erase(session: AsyncSession) -> bool:
        return await anonymize_user(session, user.id, {"system": "test"})

    await _hold_team_and_start(test_engine, org_id, team.id, erase)

    await _assert_live(db_session, team.id)
    assert await _names_in(db_session, team.id) == [ANONYMIZED_MEMBER_NAME]


@pytest.mark.asyncio
async def test_guest_name_sweep_leaves_a_team_another_transaction_holds(db_session: AsyncSession, test_engine: Any):
    """The guest sweep waits for no lock: the names of a held team stay for the next pass."""
    org_id, run, team, _guest_user = await _ended_run_with_guest_team(db_session, "order-guest-names")
    free_team = await _team(db_session, org_id, run, "Free")
    await _member(db_session, org_id, free_team, None, name="Bela")
    await db_session.commit()

    async def blank(session: AsyncSession) -> int:
        return await blank_guest_member_names(session, [run.id])

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as holder:
        assert await TeamRepository(holder, organization_id=org_id).lock_row(team.id)
        assert await asyncio.wait_for(_in_own_transaction(test_engine, blank), timeout=10) == 1
        await holder.rollback()
    assert await _names_in(db_session, team.id) == ["Guest"]
    assert await _names_in(db_session, free_team.id) == [ANONYMIZED_MEMBER_NAME]

    assert await _in_own_transaction(test_engine, blank) == 1
    assert await _names_in(db_session, team.id) == [ANONYMIZED_MEMBER_NAME]


@pytest.mark.asyncio
async def test_guest_cleanup_leaves_a_guest_that_a_write_in_flight_points_to(
    db_session: AsyncSession, test_engine: Any
):
    """A row being written for the guest holds a key-share lock on the account: the cleanup does not wait for it."""
    org_id, run, team, guest = await _ended_run_with_guest_team(db_session, "cleanup-key-share")

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as writer:
        await writer.execute(select(User.id).where(User.id == guest.id).with_for_update(read=True, key_share=True))
        # The writer holds its lock until the rollback below, so a cleanup that waited for it would not end.
        assert await asyncio.wait_for(_clean_guests(test_engine, run.id, org_id), timeout=10) == 0
        await writer.rollback()

    assert await _account_exists(db_session, guest.id)
    assert await _clean_guests(test_engine, run.id, org_id) == 1
    await _assert_live(db_session, team.id)


# ---- the backstop: an act that loses a deadlock runs again -------------------


class _LockFailure(Exception):
    """Stands in for the driver error of a deadlock or a serialization failure."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"simulated lock failure {sqlstate}")
        self.sqlstate = sqlstate


def _fail_team_locks(
    monkeypatch: pytest.MonkeyPatch,
    times: int,
    sqlstate: str = "40P01",
    between_attempts: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, int]:
    """Let the next ``times`` team locks of any session fail as PostgreSQL reports a lock failure.

    ``between_attempts`` runs once, right after the rollback that follows the
    first failure: the failed attempt holds nothing any more and the next one
    has not started, so what it commits lies between the two.
    """
    state = {"left": times, "raised": 0, "between": 0}
    original = AsyncSession.execute

    async def execute(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            state["left"] > 0
            and getattr(statement, "_for_update_arg", None) is not None
            and "FROM teams" in str(statement)
        ):
            state["left"] -= 1
            state["raised"] += 1
            raise DBAPIError("SELECT teams FOR UPDATE", {}, _LockFailure(sqlstate))
        return await original(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", execute)

    if between_attempts is not None:
        original_rollback = AsyncSession.rollback

        async def rollback(session: AsyncSession) -> None:
            await original_rollback(session)
            if state["raised"] > 0 and state["between"] == 0:
                state["between"] = 1
                await between_attempts()

        monkeypatch.setattr(AsyncSession, "rollback", rollback)
    return state


async def _act_on_last_member(
    db: AsyncSession, client: AsyncClient, concurrent_client: AsyncClient, slug: str, act: str
) -> tuple[Any, Run, Team, Team, Any, User]:
    """A team with one member and the request that takes that member, or the team, out.

    The last element is the account that sends the request.
    """
    env = await setup_journey_environment(db, client, org_slug=slug)
    run = await _run(db, env.org.id)
    team = await _team(db, env.org.id, run, "Source")
    target = await _team(db, env.org.id, run, "Target")
    await _member(db, env.org.id, target, None, name="Stays")
    participant = env.users["participant1"]
    seat = await _member(db, env.org.id, team, participant)
    await db.commit()
    staff = env.auth_headers["coordinator"]
    url = _team_url(env, run, team)

    def send() -> Any:
        if act == "leave":
            return concurrent_client.post(f"{url}/leave", headers=env.auth_headers["participant1"], json=_CONFIRM_BODY)
        if act == "remove_member":
            return concurrent_client.delete(f"{url}/members/{seat.id}", headers=staff, params=_CONFIRM)
        if act == "move":
            return concurrent_client.post(
                f"{url}/members/{seat.id}/move", headers=staff, json={"target_team_id": str(target.id), **_CONFIRM_BODY}
            )
        return concurrent_client.delete(url, headers=staff, params=_CONFIRM)

    # The token the request is sent with, for a test that ends its session.
    sender = env.auth_headers["participant1"] if act == "leave" else staff
    send.token = sender["Authorization"].split(" ", 1)[1]  # type: ignore[attr-defined]
    return send, run, team, target, participant, participant if act == "leave" else env.users["coordinator"]


_ACTS = ["leave", "remove_member", "remove_team", "move"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
@pytest.mark.parametrize("act", _ACTS)
async def test_an_act_that_loses_a_deadlock_once_runs_again_and_succeeds(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    audit_spy,
    act: str,
    sqlstate: str,
):
    send, run, team, target, participant, _actor = await _act_on_last_member(
        db_session, client, concurrent_client, f"retry-{act.replace('_', '-')}-{sqlstate.lower()}", act
    )
    failures = _fail_team_locks(monkeypatch, times=1, sqlstate=sqlstate)

    response = await send()

    assert failures["raised"] == 1
    assert response.status_code == 204, response.text
    await _assert_dissolved(db_session, team.id)
    assert await _member_count(db_session, team.id) == 0
    assert await _member_count(db_session, target.id) == (2 if act == "move" else 1)
    assert len(audit_spy.named("team.dissolved")) == 1
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("act", _ACTS)
async def test_an_act_that_loses_three_deadlocks_answers_a_conflict_and_changes_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    audit_spy,
    act: str,
):
    send, _run_row, team, target, _participant, _actor = await _act_on_last_member(
        db_session, client, concurrent_client, f"retry3-{act.replace('_', '-')}", act
    )
    failures = _fail_team_locks(monkeypatch, times=99)

    response = await send()

    assert failures["raised"] == 3
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "TEAM_BUSY"
    failures["left"] = 0
    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1
    assert await _member_count(db_session, target.id) == 1
    assert audit_spy.named("team.dissolved") == []


@pytest.mark.asyncio
async def test_another_database_error_is_not_retried(
    client: AsyncClient, db_session: AsyncSession, concurrent_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    send, _run_row, team, _target, _participant, _actor = await _act_on_last_member(
        db_session, client, concurrent_client, "retry-other-error", "remove_team"
    )
    failures = _fail_team_locks(monkeypatch, times=99, sqlstate="23505")

    with pytest.raises(DBAPIError):
        await send()

    assert failures["raised"] == 1
    failures["left"] = 0
    await _assert_live(db_session, team.id)


# ---- the retry authenticates again -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["password_reset", "lockout"])
@pytest.mark.parametrize("act", _ACTS)
async def test_an_act_runs_again_only_while_its_session_still_holds(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    audit_spy,
    act: str,
    change: str,
):
    """A password reset or a lockout commits between two attempts: the next attempt answers as the authentication."""
    send, _run_row, team, target, _participant, actor = await _act_on_last_member(
        db_session, client, concurrent_client, f"reauth-{act.replace('_', '-')}-{change.replace('_', '-')}", act
    )

    async def change_account() -> None:
        values: dict[str, Any] = (
            {"session_version": User.session_version + 1}
            if change == "password_reset"
            else {"locked_until": datetime.now(UTC) + timedelta(minutes=15), "failed_login_attempts": 5}
        )
        async with test_engine.begin() as connection:
            await connection.execute(update(User).where(User.id == actor.id).values(**values))

    failures = _fail_team_locks(monkeypatch, times=1, between_attempts=change_account)

    response = await send()

    assert failures["raised"] == 1
    assert failures["between"] == 1
    if change == "password_reset":
        assert response.status_code == 401, response.text
        assert response.json()["error_code"] == "SESSION_REVOKED"
    else:
        assert response.status_code == 423, response.text
    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1
    assert await _member_count(db_session, target.id) == 1
    assert audit_spy.named("team.dissolved") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["sign_out", "expiry"])
@pytest.mark.parametrize("act", _ACTS)
async def test_an_act_does_not_run_again_for_a_session_that_ended(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    audit_spy,
    act: str,
    ending: str,
):
    """A sign-out, or the token's expiry, between two attempts: the next attempt answers 401."""
    send, _run_row, team, target, _participant, _actor = await _act_on_last_member(
        db_session, client, concurrent_client, f"ended-{act.replace('_', '-')}-{ending.replace('_', '-')}", act
    )
    token: str = send.token
    claims = auth_tokens.decode_token(token)

    class _ClockPastExpiry(datetime):
        """The clock of ``auth/tokens``, one second past the token's ``exp``."""

        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return datetime.fromtimestamp(claims["exp"] + 1, tz=tz)

    async def end_session() -> None:
        if ending == "sign_out":
            auth_tokens.sign_out_session(
                auth_tokens.token_session_id(claims, token), datetime.now(UTC) + timedelta(minutes=5)
            )
        else:
            monkeypatch.setattr(auth_tokens, "datetime", _ClockPastExpiry)

    failures = _fail_team_locks(monkeypatch, times=1, between_attempts=end_session)

    response = await send()

    assert failures["raised"] == 1
    assert failures["between"] == 1
    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    if ending == "sign_out":
        assert response.json()["error_code"] == "SESSION_REVOKED"
    else:
        assert "expired" in response.json()["detail"]
    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1
    assert await _member_count(db_session, target.id) == 1
    assert audit_spy.named("team.dissolved") == []


# ---- the authentication write: the account before any team -------------------


async def _lock_state(db: AsyncSession, user_id: UUID) -> tuple[Any, int]:
    row = (
        await db.execute(
            select(User.locked_until, User.failed_login_attempts)
            .where(User.id == user_id)
            .execution_options(populate_existing=True)
        )
    ).one()
    return row.locked_until, row.failed_login_attempts


@pytest.mark.asyncio
async def test_team_removal_by_a_coordinator_with_an_expired_lockout_beside_their_erasure(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The removal holds team and member rows, the erasure starts then.

    The authentication cleared the expired lockout of the coordinator. That
    write is done and committed before the removal takes its team, so the
    removal asks for no account row while it holds the team: the erasure waits
    for the run row, its first lock, which the removal holds; the removal
    never waits, and both end well in one attempt.
    """
    env = await setup_journey_environment(db_session, client, org_slug="race-lockout-erasure")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    coordinator = env.users["coordinator"]
    await _member(db_session, env.org.id, team, coordinator)
    coordinator.locked_until = datetime.now(UTC) - timedelta(minutes=1)
    coordinator.failed_login_attempts = 5
    await db_session.commit()

    attempts = _count_attempts(monkeypatch)
    hook = _PauseAfter(monkeypatch, _locks_rows_of("team_members"))
    removal = asyncio.create_task(
        concurrent_client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=_CONFIRM)
    )
    await hook.wait_reached()
    try:
        # Read by another transaction, with the row lock an update takes and
        # without waiting: the write of the authentication is committed, and
        # the removal holds no lock on the account.
        async with test_engine.connect() as connection:
            row = (
                await connection.execute(
                    select(User.locked_until, User.failed_login_attempts)
                    .where(User.id == coordinator.id)
                    .with_for_update(nowait=True, key_share=True)
                )
            ).one()
            await connection.rollback()
        assert tuple(row) == (None, 0), "the authentication left its write of the account pending"

        erasure = asyncio.create_task(_erase(test_engine, coordinator.id))
        await _assert_one_wait(test_engine, erasure, holder=hook.pid, table="runs")
    finally:
        hook.release.set()
    await _never_waits(test_engine, removal, hook.pid)
    response = await removal
    erased = await asyncio.wait_for(erasure, timeout=30)

    assert response.status_code == 204, response.text
    assert erased is True
    assert len(attempts) == 1, "the removal lost a deadlock and ran again"
    await _assert_dissolved(db_session, team.id)
    account = (
        await db_session.execute(
            select(User).where(User.id == coordinator.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert account.is_deleted and not account.is_active
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
async def test_authentication_clears_an_expired_lockout_at_once(client: AsyncClient, db_session: AsyncSession):
    """Also a request that writes nothing itself leaves the lockout cleared: the write does not wait for a later flush."""
    env = await setup_journey_environment(db_session, client, org_slug="lockout-cleared")
    run = await _run(db_session, env.org.id)
    coordinator = env.users["coordinator"]
    coordinator.locked_until = datetime.now(UTC) - timedelta(minutes=1)
    coordinator.failed_login_attempts = 5
    await db_session.commit()
    coordinator_id = coordinator.id

    response = await client.get(f"/api/orgs/{env.slug}/runs/{run.id}/teams/", headers=env.auth_headers["coordinator"])

    assert response.status_code == 200, response.text
    assert await _lock_state(db_session, coordinator_id) == (None, 0)


@pytest.mark.asyncio
async def test_authentication_keeps_a_lockout_that_still_holds(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="lockout-kept")
    run = await _run(db_session, env.org.id)
    coordinator = env.users["coordinator"]
    until = datetime.now(UTC) + timedelta(minutes=10)
    coordinator.locked_until = until
    coordinator.failed_login_attempts = 5
    await db_session.commit()
    coordinator_id = coordinator.id

    response = await client.get(f"/api/orgs/{env.slug}/runs/{run.id}/teams/", headers=env.auth_headers["coordinator"])

    assert response.status_code == 423, response.text
    assert await _lock_state(db_session, coordinator_id) == (until, 5)


# ---- member rows that keep changing teams answer a conflict ------------------


@pytest.mark.asyncio
async def test_guest_replay_answers_team_busy_when_the_team_locks_give_up(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """``lock_member_teams`` gives up after its rounds: the guest join answers 409 TEAM_BUSY, not 500."""
    env = await setup_journey_environment(db_session, client, org_slug="busy-guest-replay")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    guest = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, guest)
    await db_session.commit()
    team_id = team.id
    monkeypatch.setattr(team_dissolution, "_MEMBER_TEAM_LOCK_ATTEMPTS", 0)

    response = await client.post(
        f"/api/join/{run.join_code}", json={"mode": "create", "team_name": "Nochmal"}, headers=_session_cookie(guest)
    )

    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "TEAM_BUSY"
    await _assert_live(db_session, team_id)
    assert await _member_count(db_session, team_id) == 1


@pytest.mark.asyncio
async def test_the_grace_sweep_fails_when_the_team_locks_give_up(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """The sweep is no request: it fails, and ``retention._run_task`` logs it and runs it again."""
    org_id, user = await _bare_org_and_user(db_session, "busy-sweep")
    run = await _run(db_session, org_id)
    team = await _team(db_session, org_id, run)
    await _member(db_session, org_id, team, user)
    user.is_deleted = True
    user.deleted_at = datetime.now(UTC) - timedelta(days=40)
    user.is_active = False
    await db_session.commit()
    team_id = team.id
    monkeypatch.setattr(team_dissolution, "_MEMBER_TEAM_LOCK_ATTEMPTS", 0)

    with pytest.raises(RuntimeError) as raised:
        await gdpr.sweep_expired_grace_deletions(db_session)

    assert not isinstance(raised.value, HTTPException)
    await db_session.rollback()
    await _assert_live(db_session, team_id)


# ---- the server refuses a dissolution the client did not confirm -------------
#
# The page asks "dissolve this team?" on what it read before the click. Between
# that read and the click another coordinator can act, so the page can be wrong
# in both directions. Only the server decides, under the locks of the act.


async def _result_link(db: AsyncSession, run: Run, *, revoked: bool = False, expired: bool = False) -> RunResultLink:
    now = datetime.now(UTC)
    link = RunResultLink(
        run_id=run.id,
        slug_hash=uuid4().hex,
        snapshot={},
        published_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1) if expired else now + timedelta(days=30),
        revoked_at=now - timedelta(hours=1) if revoked else None,
    )
    db.add(link)
    await db.flush()
    return link


def _assert_would_dissolve(
    response: Any, *, members: int, answers: int, published: bool, run_ended: bool = False
) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error_code"] == "TEAM_WOULD_DISSOLVE"
    assert body["member_count"] == members
    assert body["answer_count"] == answers
    assert body["result_published"] is published
    assert body["run_ended"] is run_ended


_MEMBER_ACTS = ["remove_member", "move", "leave"]


@pytest.mark.asyncio
@pytest.mark.parametrize("act", _MEMBER_ACTS)
async def test_an_act_that_became_a_dissolution_before_its_lock_is_refused_with_the_numbers(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    audit_spy,
    act: str,
):
    """The page showed two members and sent the plain act. Before its lock the other member is removed.

    The act now takes the last member out. It was not confirmed as a
    dissolution: nothing changes, and the answer carries the numbers as they
    are now. Sent again with the confirmation, it dissolves the team.
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"confirm-{act.replace('_', '-')}")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run, "Source")
    target = await _team(db_session, env.org.id, run, "Target")
    await _member(db_session, env.org.id, target, None, name="Stays")
    guest = await _guest(db_session, env.org.id)
    seat = await _member(db_session, env.org.id, team, guest)
    other = await _member(db_session, env.org.id, team, env.users["participant2"])
    await _answer(db_session, env.org.id, run, team)
    await db_session.commit()
    staff = env.auth_headers["coordinator"]
    url = _team_url(env, run, team)
    attempts = _count_attempts(monkeypatch)

    def send(confirmed: bool) -> Any:
        if act == "leave":
            body = _CONFIRM_BODY if confirmed else None
            return concurrent_client.post(f"{url}/leave", headers=get_auth_headers(guest), json=body)
        if act == "remove_member":
            return concurrent_client.delete(
                f"{url}/members/{seat.id}", headers=staff, params=_CONFIRM if confirmed else None
            )
        body = {"target_team_id": str(target.id), **(_CONFIRM_BODY if confirmed else {})}
        return concurrent_client.post(f"{url}/members/{seat.id}/move", headers=staff, json=body)

    # Held before its first lock, the run row: under it no other act of the run goes through.
    hook = _PauseAtFirstLock(monkeypatch, table="runs")
    held = asyncio.create_task(send(confirmed=False))
    await hook.wait_reached()
    # The other coordinator: a removal that leaves a member behind needs no confirmation.
    removed = await concurrent_client.delete(f"{url}/members/{other.id}", headers=env.auth_headers["org_admin"])
    assert removed.status_code == 204, removed.text
    hook.release.set()
    response = await held

    _assert_would_dissolve(response, members=1, answers=1, published=False)
    assert attempts == [1, 1], "one attempt per request: the refusal is no lock failure and is not repeated"
    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1
    assert await _member_count(db_session, target.id) == 1
    assert [str(team_id) for team_id, _name in await _rows_of(db_session, guest.id)] == [str(team.id)]
    assert not await RunDepartureRepository(db_session, organization_id=env.org.id).has_departed(
        user_id=guest.id, run_id=run.id
    )
    assert audit_spy.named("team.dissolved") == []
    assert audit_spy.named("team.left") == []
    assert audit_spy.named("team.member_moved") == []
    assert [e["target"]["id"] for e in audit_spy.named("team.member_removed")] == [str(other.id)]

    confirmed = await send(confirmed=True)

    assert confirmed.status_code == 204, confirmed.text
    await _assert_dissolved(db_session, team.id)
    assert len(audit_spy.named("team.dissolved")) == 1
    assert await _member_count(db_session, target.id) == (2 if act == "move" else 1)
    await _assert_no_member_in_dissolved_and_no_empty_live_team(db_session, run.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("act", "state"),
    [
        *[(act, RunState.ACTIVE) for act in _MEMBER_ACTS],
        # A move in an ended run is refused before the confirmation is asked (RUN_ENDED).
        ("remove_member", RunState.ENDED),
        ("leave", RunState.ENDED),
    ],
)
async def test_taking_out_the_last_member_without_the_confirmation_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, audit_spy, act: str, state: RunState
):
    env = await setup_journey_environment(
        db_session, client, org_slug=f"unconfirmed-{act.replace('_', '-')}-{state.value}"
    )
    run = await _run(db_session, env.org.id, state)
    team = await _team(db_session, env.org.id, run, "Source")
    target = await _team(db_session, env.org.id, run, "Target")
    seat = await _member(db_session, env.org.id, team, env.users["participant1"])
    await _answer(db_session, env.org.id, run, team)
    await _answer(db_session, env.org.id, run, team, points=5)
    await _result_link(db_session, run)
    await db_session.commit()
    staff = env.auth_headers["coordinator"]
    url = _team_url(env, run, team)

    if act == "leave":
        response = await client.post(f"{url}/leave", headers=env.auth_headers["participant1"])
    elif act == "remove_member":
        response = await client.delete(f"{url}/members/{seat.id}", headers=staff)
    else:
        response = await client.post(
            f"{url}/members/{seat.id}/move", headers=staff, json={"target_team_id": str(target.id)}
        )

    _assert_would_dissolve(response, members=1, answers=2, published=True, run_ended=state is RunState.ENDED)
    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1
    assert await _member_count(db_session, target.id) == 0
    assert audit_spy.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [None, {"confirm_dissolution": "false"}])
async def test_removing_a_team_without_the_confirmation_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, audit_spy, sent: dict[str, str] | None
):
    """The DELETE of a team always dissolves, so it always needs the confirmation."""
    env = await setup_journey_environment(db_session, client, org_slug=f"unconfirmed-team-{'no' if sent else 'absent'}")
    run = await _run(db_session, env.org.id, RunState.ENDED)
    team = await _team(db_session, env.org.id, run)
    guest = await _guest(db_session, env.org.id)
    await _member(db_session, env.org.id, team, guest)
    await _member(db_session, env.org.id, team, env.users["participant1"])
    await _answer(db_session, env.org.id, run, team)
    await db_session.commit()

    response = await client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"], params=sent)

    _assert_would_dissolve(response, members=2, answers=1, published=False, run_ended=True)
    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 2
    assert not await RunDepartureRepository(db_session, organization_id=env.org.id).has_departed(
        user_id=guest.id, run_id=run.id
    )
    assert audit_spy.events == []


@pytest.mark.asyncio
async def test_removing_a_team_without_members_needs_the_confirmation_too(
    client: AsyncClient, db_session: AsyncSession
):
    env = await setup_journey_environment(db_session, client, org_slug="unconfirmed-empty")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run)
    await db_session.commit()
    url, staff = _team_url(env, run, team), env.auth_headers["coordinator"]

    _assert_would_dissolve(await client.delete(url, headers=staff), members=0, answers=0, published=False)
    await _assert_live(db_session, team.id)

    assert (await client.delete(url, headers=staff, params=_CONFIRM)).status_code == 204
    await _assert_dissolved(db_session, team.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(("link", "published"), [("revoked", False), ("expired", False), ("live", True)])
async def test_the_refusal_names_a_result_only_while_its_link_is_live(
    client: AsyncClient, db_session: AsyncSession, link: str, published: bool
):
    env = await setup_journey_environment(db_session, client, org_slug=f"unconfirmed-link-{link}")
    run = await _run(db_session, env.org.id, RunState.ENDED)
    other_run = await _run(db_session, env.org.id, RunState.ENDED)
    team = await _team(db_session, env.org.id, run)
    await _member(db_session, env.org.id, team, env.users["participant1"])
    await _result_link(db_session, run, revoked=link == "revoked", expired=link == "expired")
    await _result_link(db_session, other_run)
    await db_session.commit()

    response = await client.delete(_team_url(env, run, team), headers=env.auth_headers["coordinator"])

    _assert_would_dissolve(response, members=1, answers=0, published=published, run_ended=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("act", _MEMBER_ACTS)
async def test_an_act_that_leaves_members_behind_ignores_the_confirmation(
    client: AsyncClient, db_session: AsyncSession, audit_spy, act: str
):
    env = await setup_journey_environment(db_session, client, org_slug=f"confirm-ignored-{act.replace('_', '-')}")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run, "Source")
    target = await _team(db_session, env.org.id, run, "Target")
    seat = await _member(db_session, env.org.id, team, env.users["participant1"])
    await _member(db_session, env.org.id, team, env.users["participant2"])
    await db_session.commit()
    staff = env.auth_headers["coordinator"]
    url = _team_url(env, run, team)

    if act == "leave":
        response = await client.post(f"{url}/leave", headers=env.auth_headers["participant1"], json=_CONFIRM_BODY)
    elif act == "remove_member":
        response = await client.delete(f"{url}/members/{seat.id}", headers=staff, params=_CONFIRM)
    else:
        response = await client.post(
            f"{url}/members/{seat.id}/move", headers=staff, json={"target_team_id": str(target.id), **_CONFIRM_BODY}
        )

    assert response.status_code == 204, response.text
    await _assert_live(db_session, team.id)
    assert await _member_count(db_session, team.id) == 1
    assert audit_spy.named("team.dissolved") == []


@pytest.mark.asyncio
async def test_a_full_target_is_refused_before_the_confirmation_is_asked(client: AsyncClient, db_session: AsyncSession):
    """The confirmation is asked last: a move that cannot happen is not offered as a dissolution."""
    env = await setup_journey_environment(db_session, client, org_slug="confirm-after-checks")
    run = await _run(db_session, env.org.id)
    run.settings = {"max_members_per_team": 1}
    source = await _team(db_session, env.org.id, run, "Source")
    target = await _team(db_session, env.org.id, run, "Target")
    seat = await _member(db_session, env.org.id, source, env.users["participant1"])
    await _member(db_session, env.org.id, target, env.users["participant2"])
    await db_session.commit()

    response = await client.post(
        f"{_team_url(env, run, source)}/members/{seat.id}/move",
        headers=env.auth_headers["coordinator"],
        json={"target_team_id": str(target.id)},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error_code"] == "TEAM_FULL"


def test_the_routes_document_the_confirmation_and_the_refusal():
    from app.main import app

    paths = app.openapi()["paths"]
    team = "/api/orgs/{slug}/runs/{run_id}/teams/{team_id}"
    for path, method in (
        (team, "delete"),
        (f"{team}/members/{{member_id}}", "delete"),
        (f"{team}/members/{{member_id}}/move", "post"),
        (f"{team}/leave", "post"),
    ):
        operation = paths[path][method]
        conflict = operation["responses"]["409"]
        assert "TEAM_WOULD_DISSOLVE" in conflict["description"], path
        assert conflict["content"]["application/json"]["schema"]["$ref"].endswith("/TeamConflictResponse")
        assert "confirm_dissolution" in operation["description"], path
        if method == "delete":
            assert "confirm_dissolution" in [p["name"] for p in operation["parameters"] if p["in"] == "query"]
    schemas = app.openapi()["components"]["schemas"]
    assert "confirm_dissolution" in schemas["TeamMemberMoveRequest"]["properties"]
    assert "confirm_dissolution" in schemas["TeamLeaveRequest"]["properties"]
    assert {"member_count", "answer_count", "result_published", "run_ended"} <= schemas["TeamConflictResponse"][
        "properties"
    ].keys()


# ---- the run row is the first lock of every act that can dissolve ------------
#
# The publish of a result builds and writes its snapshot under the row lock on
# the run. A dissolution rewrites the stored snapshots of the run, so every act
# that can dissolve a team takes the same lock, and takes it first: the order
# is run, account, team, member, departure.


def _record_row_locks(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every statement that asks for a row lock, of any session, in the order sent."""
    locks: list[Any] = []
    original = AsyncSession.execute

    async def execute(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(statement, "_for_update_arg", None) is not None:
            locks.append(statement)
        return await original(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", execute)
    return locks


def _locked_tables(locks: list[Any]) -> list[str]:
    """The table each recorded lock is taken in."""
    tables = ("runs", "users", "team_members", "teams", "run_result_links")
    return [next((table for table in tables if f"FROM {table}" in str(lock)), "other") for lock in locks]


@pytest.mark.asyncio
@pytest.mark.parametrize("act", _ACTS)
async def test_every_dissolving_act_takes_the_run_row_first(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    act: str,
):
    send, _run_row, team, _target, _participant, _actor = await _act_on_last_member(
        db_session, client, concurrent_client, f"run-first-{act.replace('_', '-')}", act
    )
    locks = _record_row_locks(monkeypatch)

    response = await send()

    assert response.status_code == 204, response.text
    await _assert_dissolved(db_session, team.id)
    tables = _locked_tables(locks)
    assert tables[0] == "runs", tables
    # FOR NO KEY UPDATE: an answer or a new team of the run does not wait for it.
    assert locks[0]._for_update_arg.key_share is True
    assert tables.count("runs") == 1, tables
    # Run, then the account (only leaving takes one), then the teams.
    order = [table for table in tables if table in ("runs", "users", "teams")]
    assert order == sorted(order, key=("runs", "users", "teams").index), tables


@pytest.mark.asyncio
async def test_creating_a_team_under_a_limit_takes_the_run_row_before_the_account(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    env = await setup_journey_environment(db_session, client, org_slug="run-first-create")
    run = await _run(db_session, env.org.id)
    run.max_teams = 2
    await db_session.commit()
    locks = _record_row_locks(monkeypatch)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "Neu", "color": "#123456"},
    )

    assert response.status_code == 201, response.text
    assert _locked_tables(locks)[:2] == ["runs", "users"]


@pytest.mark.asyncio
async def test_the_erasure_takes_the_runs_of_its_member_rows_before_the_accounts(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Several accounts in several runs: every run row in ascending order, then every account, then the teams."""
    org_id, first = await _bare_org_and_user(db_session, "run-first-erase")
    second = await _guest(db_session, org_id)
    runs = [
        await _run(db_session, org_id),
        await _run(db_session, org_id),
        await _run(db_session, org_id, RunState.ENDED),
    ]
    teams = [await _team(db_session, org_id, run) for run in runs]
    await _member(db_session, org_id, teams[0], first)
    await _member(db_session, org_id, teams[1], second)
    await _member(db_session, org_id, teams[2], first)
    await db_session.commit()
    locks = _record_row_locks(monkeypatch)

    account_ids = [UUID(str(second.id)), UUID(str(first.id))]
    assert await gdpr.anonymize_users(db_session, account_ids, {"system": "test"}) == 2
    await db_session.commit()

    tables = _locked_tables(locks)
    assert tables[:5] == ["runs", "runs", "runs", "users", "users"], tables
    assert "teams" in tables[5:] and "runs" not in tables[5:] and "users" not in tables[5:], tables
    await _assert_dissolved(db_session, teams[0].id)
    await _assert_dissolved(db_session, teams[1].id)
    await _assert_live(db_session, teams[2].id)
    asked = [str(next(iter(lock.compile().params.values()))) for lock in locks[:3]]
    assert asked == sorted(str(run.id) for run in runs)


@pytest.mark.asyncio
async def test_the_erasure_starts_over_when_a_member_row_appears_in_a_run_it_did_not_lock(
    db_session: AsyncSession, test_engine: Any, monkeypatch: pytest.MonkeyPatch
):
    """A join committed between the read of the runs and the account lock: the round is given back and taken again."""
    org_id, user = await _bare_org_and_user(db_session, "run-first-retry")
    known = await _run(db_session, org_id)
    late = await _run(db_session, org_id)
    known_team = await _team(db_session, org_id, known)
    late_team = await _team(db_session, org_id, late)
    await _member(db_session, org_id, known_team, user)
    await db_session.commit()
    user_id, late_team_id = user.id, late_team.id
    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    locks = _record_row_locks(monkeypatch)
    hook = _PauseAtFirstLock(monkeypatch, table="users")
    erasure = asyncio.create_task(_erase(test_engine, user_id))
    await hook.wait_reached()
    assert _locked_tables(locks) == ["runs"]
    async with sessions() as other:
        other.add(
            TeamMember(
                organization_id=org_id,
                team_id=late_team_id,
                user_id=user_id,
                display_name="Late",
                is_guest=False,
                joined_at=datetime.now(UTC),
            )
        )
        await other.commit()
    hook.release.set()
    assert await asyncio.wait_for(erasure, timeout=30) is True

    # Round one: the known run, the account. Round two: both runs, the account again. Then the teams.
    assert _locked_tables(locks)[:5] == ["runs", "users", "runs", "runs", "users"], _locked_tables(locks)
    assert await _rows_of(db_session, user_id) == []
    await _assert_dissolved(db_session, known_team.id)
    await _assert_dissolved(db_session, late_team_id)
