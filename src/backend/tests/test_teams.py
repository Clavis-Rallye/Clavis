"""
Integration tests for team endpoints.

- create, list, get, join and leave a team
- state rule: teams only in active runs
- max teams limit and one team per run
- role enforcement (participant+) and cross-tenant isolation
- team names unique per run
- the join by team code: failure limiter and one request at a time
"""

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from app.repositories.team import TeamRepository
from app.services import team_code_rate_limit
from tests.factories import create_multiple_users, create_test_user_with_org, get_auth_headers
from tests.journey_helpers import activate_run, setup_journey_environment


@pytest.mark.asyncio
async def test_create_team_success(client: AsyncClient, db_session: AsyncSession):
    """A team is created in an active run and starts with no members."""
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
    _challenge_1 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_1.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha", "color": "#FF5733"},
    )

    assert response.status_code == 201
    data = response.json()

    assert data["name"] == "Team Alpha"
    assert data["color"] == "#FF5733"
    assert data["member_count"] == 0
    assert data["run_id"] == run_id
    assert UUID(data["id"])
    assert data["organization_id"] == str(org.id)


@pytest.mark.asyncio
async def test_create_team_requires_active_state(client: AsyncClient, db_session: AsyncSession):
    """Creating a team is refused with 422 while the run is not active."""
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
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    # Attempt to create team before starting run
    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "configured" in detail.lower()
    assert "active" in detail.lower()
    assert response.json()["error_code"] == "RUN_NOT_STARTED"


@pytest.mark.asyncio
async def test_create_team_max_limit(client: AsyncClient, db_session: AsyncSession):
    """Teams can be created up to max_teams, then creation is refused with 422."""
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
    _challenge_2 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_2.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run", "max_teams": 2},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    # Create first team - should succeed
    response1 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team 1"},
    )
    assert response1.status_code == 201

    # Create second team - should succeed
    response2 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team 2"},
    )
    assert response2.status_code == 201

    # Create third team - should fail
    response3 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team 3"},
    )
    assert response3.status_code == 422
    detail = response3.json()["detail"]
    assert "maximum team limit" in detail.lower()
    assert "2" in detail
    assert response3.json()["error_code"] == "TEAM_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_list_teams(client: AsyncClient, db_session: AsyncSession):
    """The team list holds every team of the run with its member count."""
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
    _challenge_3 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_3.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha", "color": "#FF5733"},
    )
    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Beta"},
    )

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert {team["name"] for team in data["items"]} == {"Team Alpha", "Team Beta"}
    # All teams should have member_count = 0 (no members yet)
    for team in data["items"]:
        assert team["member_count"] == 0


@pytest.mark.asyncio
async def test_get_team(client: AsyncClient, db_session: AsyncSession):
    """A team is fetched by its ID with its member count."""
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
    _challenge_4 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_4.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha", "color": "#FF5733"},
    )
    team_id = create_response.json()["id"]

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == team_id
    assert data["name"] == "Team Alpha"
    assert data["color"] == "#FF5733"
    assert data["member_count"] == 0
    assert data["run_id"] == run_id


@pytest.mark.asyncio
async def test_get_team_wrong_run(client: AsyncClient, db_session: AsyncSession):
    """A team is not found through a run it does not belong to."""
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
    _challenge_5 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_5.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run1_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Run 1"},
    )
    run1_id = run1_response.json()["id"]

    run2_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Run 2"},
    )
    run2_id = run2_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run1_id}/start", headers=headers)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run1_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team_id = create_response.json()["id"]

    # Try to get team from run2 (wrong run)
    response = await client.get(f"/api/orgs/{org.slug}/runs/{run2_id}/teams/{team_id}", headers=headers)

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "not found" in detail.lower()


@pytest.mark.asyncio
async def test_join_team_success(client: AsyncClient, db_session: AsyncSession):
    """Joining a team returns 204 and raises its member count."""
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
    _challenge_6 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_6.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    assert response.status_code == 204

    team_get_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}", headers=headers)
    assert team_get_response.status_code == 200
    assert team_get_response.json()["member_count"] == 1


@pytest.mark.asyncio
async def test_join_team_requires_active_state(client: AsyncClient, db_session: AsyncSession):
    """Joining a team is refused with 422 while the run is paused."""
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
    _challenge_7 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_7.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=headers)

    # Try to join team while paused
    response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "paused" in detail.lower()
    assert "active" in detail.lower()
    assert response.json()["error_code"] == "RUN_PAUSED"


@pytest.mark.asyncio
async def test_join_team_already_in_another_team(client: AsyncClient, db_session: AsyncSession):
    """A user already in a team of the run cannot join a second one."""
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
    _challenge_8 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_8.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    team1_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team1_id = team1_response.json()["id"]

    team2_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Beta"},
    )
    team2_id = team2_response.json()["id"]

    response1 = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/join", headers=headers)
    assert response1.status_code == 204

    # Try to join second team
    response2 = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/join", headers=headers)

    assert response2.status_code == 422
    detail = response2.json()["detail"]
    assert "already in a team" in detail.lower()
    assert response2.json()["error_code"] == "ALREADY_IN_OTHER_TEAM"


@pytest.mark.asyncio
async def test_leave_team_success(client: AsyncClient, db_session: AsyncSession):
    """The last member who leaves dissolves the team: it is gone and cannot be joined."""
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
    _challenge_9 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_9.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    team_get_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}", headers=headers)
    assert team_get_response.json()["member_count"] == 1

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/leave",
        headers=headers,
        json={"confirm_dissolution": True},
    )

    assert response.status_code == 204

    # The leaver was the last member: the team is dissolved with the leave,
    # so it no longer exists and cannot be joined again.
    team_get_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}", headers=headers)
    assert team_get_response.status_code == 404
    rejoin_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)
    assert rejoin_response.status_code == 404


@pytest.mark.asyncio
async def test_leave_team_not_a_member(client: AsyncClient, db_session: AsyncSession):
    """Leaving a team one is not a member of returns 404."""
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
    _challenge_10 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_10.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    # Try to leave team without joining first
    response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/leave", headers=headers)

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "not a member" in detail.lower()


# --- team names are unique per run -----------------------------------------


async def _active_run(client: AsyncClient, db_session: AsyncSession, org_slug: str) -> tuple[str, str, dict[str, str]]:
    """Set up an org with a published rallye and one ACTIVE run.

    Returns (slug, run_id, coordinator headers) — enough to POST teams.
    """
    env = await setup_journey_environment(db_session, client, org_slug=org_slug)
    headers = env.auth_headers["coordinator"]
    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(env.rallye_id), "name": "Unique Name Run"},
    )
    assert create_response.status_code == 201, create_response.text
    run_id = create_response.json()["id"]
    await activate_run(client, env.slug, run_id, headers)
    return env.slug, run_id, headers


@pytest.mark.asyncio
async def test_create_team_with_taken_name_409(client: AsyncClient, db_session: AsyncSession):
    """Creating a second team with a name the run already holds is a coded 409."""
    slug, run_id, headers = await _active_run(client, db_session, "create-taken")

    first = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Schnelle Füchse"},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "  schnelle füchse  "},
    )
    assert second.status_code == 409, second.text
    assert second.json()["error_code"] == "TEAM_NAME_TAKEN"


@pytest.mark.asyncio
async def test_create_team_trims_the_name(client: AsyncClient, db_session: AsyncSession):
    """The stored name is the trimmed one, so the uniqueness check sees what is stored."""
    slug, run_id, headers = await _active_run(client, db_session, "create-trim")

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "  Schnelle Füchse  "},
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Schnelle Füchse"


@pytest.mark.asyncio
async def test_join_by_code_limiter_blocks_even_a_correct_code(
    db_session: AsyncSession,
    client: AsyncClient,
):
    """The limiter is asked before the code is looked up.

    A client that burned its failure budget gets 429 for a correct code too,
    until the block ends. The team's share-code is fetched from the API as a
    coordinator (visibility rule), so the test never guesses it.
    """
    env = await setup_journey_environment(db_session, client, org_slug="bycode-limiter")
    coord_headers = env.auth_headers["coordinator"]
    participant_headers = env.auth_headers["participant1"]
    await db_session.commit()

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Limiter Run"},
    )
    assert create_resp.status_code == 201, create_resp.text
    run_id = create_resp.json()["id"]
    await activate_run(client, env.slug, run_id, coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Limiter-Team"},
    )
    assert team_resp.status_code == 201, team_resp.text
    team_id = team_resp.json()["id"]

    team_get = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        headers=coord_headers,
    )
    assert team_get.status_code == 200
    team_code = team_get.json()["share_code"]
    assert team_code

    # Burn the failure budget with wrong codes (MAX_FAILURES = 5).
    for _ in range(5):
        wrong = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/by-code",
            headers=participant_headers,
            json={"team_code": "nope"},
        )
        assert wrong.status_code == 404, wrong.text

    # The CORRECT code is now refused with 429: the block is checked before
    # the lookup, not only on the failure path.
    blocked = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/by-code",
        headers=participant_headers,
        json={"team_code": team_code},
    )
    assert blocked.status_code == 429, blocked.text
    assert blocked.headers.get("Retry-After") == "60"


@pytest.mark.asyncio
async def test_join_by_code_refusals_with_a_correct_code_use_no_budget(
    db_session: AsyncSession,
    client: AsyncClient,
):
    """Only a code that matches no team spends failure budget.

    The team holds its one place, so every join with its correct code ends in
    TEAM_FULL. Eight such refusals (the budget is 5) must neither block the
    client nor leave a trace: a wrong code afterwards is still a plain 404.
    """
    env = await setup_journey_environment(db_session, client, org_slug="bycode-nobudget")
    coord_headers = env.auth_headers["coordinator"]
    participant_headers = env.auth_headers["participant1"]
    await db_session.commit()

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={
            "rallye_id": str(env.rallye_id),
            "name": "No Budget Run",
            "settings": {"max_members_per_team": 1},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    run_id = create_resp.json()["id"]
    join_code = create_resp.json()["join_code"]
    await activate_run(client, env.slug, run_id, coord_headers)

    # A guest creates the team and takes its only place.
    first = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Voll-Team"},
    )
    assert first.status_code == 201, first.text
    team_code = first.json()["team_share_code"]

    for attempt in range(8):
        full = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/by-code",
            headers=participant_headers,
            json={"team_code": team_code},
        )
        assert full.status_code == 422, f"attempt {attempt}: {full.status_code} {full.text}"
        assert full.json()["error_code"] == "TEAM_FULL"

    wrong = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/by-code",
        headers=participant_headers,
        json={"team_code": "nope"},
    )
    assert wrong.status_code == 404, wrong.text


# --- the join by team code admits one request per client and run at a time ---

SIGNAL_TIMEOUT = 10.0  # a missing arrival must fail the test, never pass it


def _client_from(address: str) -> AsyncClient:
    """A client on the App of ``concurrent_client`` whose connection comes from ``address``, the peer."""
    return AsyncClient(transport=ASGITransport(app=app, client=(address, 123)), base_url="http://testserver")


class _AdmissionProbe:
    """Wraps ``team_code_rate_limit.admission`` and acknowledges every request that reaches it.

    ``arrived`` counts requests that called the admission, ``entered`` those that
    got through it. ``arrived > entered`` means requests are waiting at the admission.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.arrived = 0
        self.entered = 0
        self._events: dict[int, asyncio.Event] = {}
        original = team_code_rate_limit.admission

        @asynccontextmanager
        async def probe(client_ip, run_id):  # noqa: ANN001, ANN202
            self.arrived += 1
            for count, event in self._events.items():
                if self.arrived >= count:
                    event.set()
            async with original(client_ip, run_id) as slot:
                self.entered += 1
                yield slot

        monkeypatch.setattr(team_code_rate_limit, "admission", probe)

    async def reached(self, count: int) -> None:
        """Wait until ``count`` requests have arrived at the admission; fail after the timeout."""
        event = self._events.setdefault(count, asyncio.Event())
        if self.arrived >= count:
            event.set()
        await asyncio.wait_for(event.wait(), SIGNAL_TIMEOUT)


async def _by_code_run(
    concurrent_client: AsyncClient,
    db_session: AsyncSession,
    slug: str,
    participants: int,
    max_members_per_team: int | None = None,
) -> tuple[str, str, list[dict[str, str]]]:
    """(by-code url, share_code, auth headers of ``participants`` accounts) of an active run with one empty team."""
    env = await setup_journey_environment(db_session, concurrent_client, org_slug=slug)
    coord_headers = env.auth_headers["coordinator"]
    users = await create_multiple_users(db_session, env.org, count=participants, email_prefix=f"{slug}-member")
    headers = [get_auth_headers(user) for user in users]
    await db_session.commit()

    run_settings = {} if max_members_per_team is None else {"max_members_per_team": max_members_per_team}
    created = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Admission Run", "settings": run_settings},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    await activate_run(concurrent_client, env.slug, run_id, coord_headers)

    team = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Admission-Team"},
    )
    assert team.status_code == 201, team.text
    shown = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team.json()['id']}",
        headers=coord_headers,
    )
    assert shown.status_code == 200, shown.text
    return f"/api/orgs/{env.slug}/runs/{run_id}/teams/by-code", shown.json()["share_code"], headers


@pytest.mark.asyncio
async def test_join_by_code_concurrent_wrong_codes_get_at_most_the_budget_of_lookups(
    db_session: AsyncSession, concurrent_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A burst of wrong codes from one address causes at most MAX_FAILURES lookups and the rest get 429.

    A lookup is held until every request of the burst has reached the admission.
    So the burst overlaps whatever the database does.
    """
    burst = team_code_rate_limit.MAX_FAILURES * 3
    url, _, headers = await _by_code_run(concurrent_client, db_session, "bycode-admission-burst", burst)
    probe = _AdmissionProbe(monkeypatch)
    lookups = 0
    release = asyncio.Event()
    original = TeamRepository.get_by_share_code

    async def counting(self: TeamRepository, run_id, code):  # noqa: ANN001, ANN202
        nonlocal lookups
        lookups += 1
        await asyncio.wait_for(release.wait(), SIGNAL_TIMEOUT)
        return await original(self, run_id, code)

    monkeypatch.setattr(TeamRepository, "get_by_share_code", counting)
    peer = _client_from("203.0.113.60")
    tasks = [
        asyncio.create_task(
            peer.post(
                url,
                headers=headers[i],
                json={"team_code": f"ZZZZ{i:02d}"},
            )
        )
        for i in range(burst)
    ]
    try:
        await probe.reached(burst)
        # Every request is at the admission. Only one may be through it.
        assert probe.entered == 1, f"{probe.entered} of {burst} requests got through the admission at once"
        release.set()
        responses = await asyncio.gather(*tasks)
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await peer.aclose()
        team_code_rate_limit._failures.clear()

    statuses = [r.status_code for r in responses]
    assert lookups <= team_code_rate_limit.MAX_FAILURES, f"{lookups} lookups for a budget of 5"
    assert statuses.count(404) == team_code_rate_limit.MAX_FAILURES
    assert statuses.count(429) == burst - team_code_rate_limit.MAX_FAILURES
    blocked = next(r for r in responses if r.status_code == 429)
    assert blocked.headers.get("Retry-After") == "60"


@pytest.mark.asyncio
async def test_join_by_code_six_correct_joins_at_once_reset_the_budget_and_get_no_429(
    db_session: AsyncSession, concurrent_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six correct joins at once from one address for three places: none answers 429 and the budget is cleared.

    The address has four failures on the books. Each correct code resets the
    budget inside its admission, before the capacity is known. So the joins
    that end in TEAM_FULL leave nothing behind either.
    """
    joins = 6
    places = 3
    url, share_code, headers = await _by_code_run(
        concurrent_client, db_session, "bycode-admission-full", joins, max_members_per_team=places
    )
    ip = "203.0.113.61"
    key = (ip, UUID(url.split("/")[5]))
    for _ in range(team_code_rate_limit.MAX_FAILURES - 1):
        assert await team_code_rate_limit.check_and_record_failure(*key)

    probe = _AdmissionProbe(monkeypatch)
    release = asyncio.Event()
    original = TeamRepository.get_by_share_code

    async def held(self: TeamRepository, run_id, code):  # noqa: ANN001, ANN202
        await asyncio.wait_for(release.wait(), SIGNAL_TIMEOUT)
        return await original(self, run_id, code)

    monkeypatch.setattr(TeamRepository, "get_by_share_code", held)
    peer = _client_from(ip)
    tasks = [
        asyncio.create_task(peer.post(url, headers=headers[i], json={"team_code": share_code})) for i in range(joins)
    ]
    try:
        await probe.reached(joins)
        assert probe.entered == 1, f"{probe.entered} of {joins} requests got through the admission at once"
        release.set()
        responses = await asyncio.gather(*tasks)
        assert key not in team_code_rate_limit._failures, "a correct code left failures on the books"
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await peer.aclose()
        team_code_rate_limit._failures.clear()

    statuses = [r.status_code for r in responses]
    assert 429 not in statuses, statuses
    assert statuses.count(204) == places, statuses
    full = [r for r in responses if r.status_code == 422]
    assert len(full) == joins - places, statuses
    assert all(r.json()["error_code"] == "TEAM_FULL" for r in full)


@pytest.mark.asyncio
async def test_join_by_code_correct_code_that_ends_in_team_full_resets_the_budget(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """The reset belongs to the resolution of the code, not to the finished join.

    Four wrong codes, then the correct code of a full team (TEAM_FULL), then
    the whole budget of wrong codes again: all of them are plain 404.
    """
    url, share_code, headers = await _by_code_run(client, db_session, "bycode-full-reset", 2, max_members_per_team=1)
    first = await client.post(url, headers=headers[0], json={"team_code": share_code})
    assert first.status_code == 204, first.text

    async def wrong() -> int:
        return (await client.post(url, headers=headers[1], json={"team_code": "nope"})).status_code

    try:
        for _ in range(team_code_rate_limit.MAX_FAILURES - 1):
            assert await wrong() == 404
        full = await client.post(url, headers=headers[1], json={"team_code": share_code})
        assert full.status_code == 422, full.text
        assert full.json()["error_code"] == "TEAM_FULL"
        for attempt in range(team_code_rate_limit.MAX_FAILURES):
            assert await wrong() == 404, f"wrong code {attempt} after the reset"
        assert await wrong() == 429
    finally:
        team_code_rate_limit._failures.clear()
