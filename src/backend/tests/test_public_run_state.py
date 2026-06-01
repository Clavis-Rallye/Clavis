"""Tests for GET /api/public/runs/{run_id}/state.

The endpoint exposes a run's lifecycle (name + state-machine timestamps) to a
caller who is a member of a team in that run — authorized by team membership,
not organization role, so guests get run-state without the coordinator-scoped
/api/orgs endpoints that 403 them.

Tenant isolation is the load-bearing property: a non-member,
a cross-org caller, or a member of a *different* run must learn nothing — they
all receive 404. An anonymous caller gets 401.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from app.models.challenge import ChallengeType
from app.models.organization import Organization
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run import Run, RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_organization, create_test_user, create_test_user_with_org, get_auth_headers


async def _make_run(
    session: AsyncSession,
    *,
    org: Organization,
    join_code: str,
    name: str = "City Rally",
    state: RunState = RunState.ACTIVE,
    started_at: datetime | None = None,
    paused_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> Run:
    run = Run(
        organization_id=org.id,
        rallye_id=None,
        name=name,
        state=state,
        join_code=join_code,
        settings={},
        content_languages=["de", "en"],
        started_at=started_at,
        paused_at=paused_at,
        ended_at=ended_at,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _add_member(session: AsyncSession, *, run: Run, user_id, share_code: str = "TC0001AB") -> Team:
    team = Team(
        organization_id=run.organization_id,
        run_id=run.id,
        name="The Explorers",
        color="#FF5733",
        share_code=share_code,
    )
    session.add(team)
    await session.flush()
    session.add(
        TeamMember(
            organization_id=run.organization_id,
            team_id=team.id,
            user_id=user_id,
            display_name="Cara",
            is_guest=True,
        )
    )
    await session.commit()
    await session.refresh(team)
    return team


@pytest.mark.asyncio
async def test_state_returns_lifecycle_for_member(client: AsyncClient, db_session: AsyncSession) -> None:
    """A team member gets the run's name + state + timestamps."""
    org = await create_test_organization(db_session, name="Org A", slug="rs-member")
    started = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    run = await _make_run(db_session, org=org, join_code="RSM001", name="Augsburg Tour", started_at=started)
    user = await create_test_user(db_session, email="member@example.com", organization_id=org.id)
    await _add_member(db_session, run=run, user_id=user.id)
    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    resp = await client.get(f"/api/public/runs/{run.id}/state", cookies={"access_token": token})

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == str(run.id)
    assert body["name"] == "Augsburg Tour"
    assert body["state"] == "active"
    assert body["started_at"] is not None
    assert body["paused_at"] is None
    assert body["ended_at"] is None
    # Projection MUST NOT leak org/coordinator fields.
    assert "organization_id" not in body
    assert "settings" not in body
    assert "join_code" not in body


@pytest.mark.asyncio
async def test_state_reflects_ended_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """An ended run reports state=ended + ended_at (drives the feedback redirect)."""
    org = await create_test_organization(db_session, name="Org End", slug="rs-ended")
    ended = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    run = await _make_run(db_session, org=org, join_code="RSE001", state=RunState.ENDED, ended_at=ended)
    user = await create_test_user(db_session, email="ended@example.com", organization_id=org.id)
    await _add_member(db_session, run=run, user_id=user.id)
    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    resp = await client.get(f"/api/public/runs/{run.id}/state", cookies={"access_token": token})

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "ended"
    assert body["ended_at"] is not None


@pytest.mark.asyncio
async def test_state_grading_complete_follows_scoring(client: AsyncClient, db_session: AsyncSession) -> None:
    """On an ended run, grading_complete is false while a submission is unscored
    and flips to true once everything is scored (drives the "Zwischenstand" banner)."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    challenge = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=challenge.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    # Create and start the run through the API, so the coordinator's team membership
    # authorizes the public endpoint.
    run_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/",
            headers=headers,
            json={"rallye_id": str(rallye.id), "name": "Test Run"},
        )
    ).json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    team_id = (
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"})
    ).json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]
    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    submission_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
            headers=headers,
            json={"run_challenge_id": run_challenge_id, "content": {"text": "Our answer"}},
        )
    ).json()["id"]

    end_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)
    assert end_response.status_code == 200

    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    # Ended, but grading continues: the submission has no score yet
    resp = await client.get(f"/api/public/runs/{run_id}/state", cookies={"access_token": token})
    assert resp.status_code == 200
    assert resp.json()["state"] == "ended"
    assert resp.json()["grading_complete"] is False

    # Once the submission is scored, the standing is final
    score_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": submission_id, "points": 8, "comment": "Good"},
    )
    assert score_response.status_code == 201

    resp = await client.get(f"/api/public/runs/{run_id}/state", cookies={"access_token": token})
    assert resp.status_code == 200
    assert resp.json()["grading_complete"] is True


@pytest.mark.asyncio
async def test_state_401_without_token(client: AsyncClient, db_session: AsyncSession) -> None:
    """Anonymous caller → 401."""
    org = await create_test_organization(db_session, name="Org Anon", slug="rs-anon")
    run = await _make_run(db_session, org=org, join_code="RSA001")

    resp = await client.get(f"/api/public/runs/{run.id}/state")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_state_404_for_non_member_same_org(client: AsyncClient, db_session: AsyncSession) -> None:
    """Authenticated user in the run's org but not a team member → 404 (no disclosure)."""
    org = await create_test_organization(db_session, name="Org NM", slug="rs-nonmember")
    run = await _make_run(db_session, org=org, join_code="RSN001")
    outsider = await create_test_user(db_session, email="outsider@example.com", organization_id=org.id)
    token = create_access_token(user_id=outsider.id, role=outsider.role.value, organization_id=outsider.organization_id)

    resp = await client.get(f"/api/public/runs/{run.id}/state", cookies={"access_token": token})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_state_404_for_cross_org_user(client: AsyncClient, db_session: AsyncSession) -> None:
    """A user from a different org cannot read the run's state → 404."""
    org_a = await create_test_organization(db_session, name="Org A", slug="rs-xorg-a")
    org_b = await create_test_organization(db_session, name="Org B", slug="rs-xorg-b")
    run = await _make_run(db_session, org=org_a, join_code="RSX001")
    other = await create_test_user(db_session, email="other-org@example.com", organization_id=org_b.id)
    token = create_access_token(user_id=other.id, role=other.role.value, organization_id=other.organization_id)

    resp = await client.get(f"/api/public/runs/{run.id}/state", cookies={"access_token": token})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_state_404_for_member_of_different_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """Cross-run isolation: a member of run A cannot read run B's state → 404."""
    org = await create_test_organization(db_session, name="Org Iso", slug="rs-iso")
    run_a = await _make_run(db_session, org=org, join_code="RSIA01", name="Run A")
    run_b = await _make_run(db_session, org=org, join_code="RSIB01", name="Run B")
    user = await create_test_user(db_session, email="run-a-member@example.com", organization_id=org.id)
    await _add_member(db_session, run=run_a, user_id=user.id, share_code="AAA-001")
    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    resp_a = await client.get(f"/api/public/runs/{run_a.id}/state", cookies={"access_token": token})
    assert resp_a.status_code == 200
    resp_b = await client.get(f"/api/public/runs/{run_b.id}/state", cookies={"access_token": token})
    assert resp_b.status_code == 404


@pytest.mark.asyncio
async def test_state_404_for_unknown_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """A random run id → 404."""
    org = await create_test_organization(db_session, name="Org U", slug="rs-unknown")
    user = await create_test_user(db_session, email="u@example.com", organization_id=org.id)
    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    resp = await client.get(
        "/api/public/runs/00000000-0000-0000-0000-000000000000/state",
        cookies={"access_token": token},
    )

    assert resp.status_code == 404
