"""
Integration tests for score endpoints.

Tests cover:
- Create score (POST /)
- Update score (PUT /{score_id})
- List scores (GET /)
- Points validation (cannot exceed max_points)
- Role-based access control (coordinator+ required, participant denied)
- Scoreboard (GET /scoreboard)
- Scoreboard ranking by total points
- Scoreboard with teams without scores
- Scoreboard point aggregation across multiple submissions
- Scoreboard accessibility by participants
- Cross-tenant isolation
"""

from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.team_member import TeamMember
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from app.repositories.team import TeamRepository
from app.storage import ensure_bucket, get_minio_client
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers
from tests.test_image_variants import plain_image


async def _seat_staying_member(db_session: AsyncSession, org, team_id: str, tag: str) -> None:
    """Give a team a participant who never leaves, so it outlives the coordinator moving on.

    A team dissolves with its last member; a test that walks one coordinator through several
    teams keeps the earlier ones alive this way.
    """
    stayer = await create_test_user(
        db_session,
        email=f"stayer-{tag}@test.com",
        display_name=f"Stayer {tag}",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    db_session.add(
        TeamMember(
            organization_id=org.id,
            team_id=UUID(team_id),
            user_id=stayer.id,
            display_name=stayer.display_name,
            is_guest=False,
            joined_at=datetime.now(UTC),
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_create_score_success(client: AsyncClient, db_session: AsyncSession):
    """A coordinator creates a score with its points, max_points, comment and scorer."""
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

    # Start run (creates snapshots)
    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    join_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)
    assert join_response.status_code == 204

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer"},
        },
    )
    assert submission_response.status_code == 201
    submission_id = submission_response.json()["id"]

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission_id,
            "points": 8,
            "comment": "Good work!",
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert UUID(data["id"])
    assert data["organization_id"] == str(org.id)
    assert data["submission_id"] == submission_id
    assert data["points"] == 8
    assert data["max_points"] == 10
    assert data["comment"] == "Good work!"
    assert data["scored_by"] == str(user.id)
    assert "scored_at" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_update_score_success(client: AsyncClient, db_session: AsyncSession):
    """A coordinator updates the points and the comment of a score."""
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
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer"},
        },
    )
    submission_id = submission_response.json()["id"]

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission_id,
            "points": 5,
            "comment": "Initial comment",
        },
    )
    score_id = create_response.json()["id"]

    response = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/{score_id}",
        headers=headers,
        json={
            "points": 9,
            "comment": "Updated comment",
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == score_id
    assert data["points"] == 9
    assert data["comment"] == "Updated comment"
    assert data["max_points"] == 10


@pytest.mark.asyncio
async def test_create_score_points_exceed_max_points(client: AsyncClient, db_session: AsyncSession):
    """A score above max_points is refused with 422."""
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
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer"},
        },
    )
    submission_id = submission_response.json()["id"]

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission_id,
            "points": 15,  # > max_points (10)
            "comment": "Too many points",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "cannot exceed max_points" in detail.lower()


@pytest.mark.asyncio
async def test_update_score_points_exceed_max_points(client: AsyncClient, db_session: AsyncSession):
    """An update above max_points is refused with 422."""
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
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer"},
        },
    )
    submission_id = submission_response.json()["id"]

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission_id,
            "points": 5,
            "comment": "Initial comment",
        },
    )
    score_id = create_response.json()["id"]

    response = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/{score_id}",
        headers=headers,
        json={
            "points": 12,  # > max_points (10)
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "cannot exceed max_points" in detail.lower()


@pytest.mark.asyncio
async def test_create_score_participant_denied(client: AsyncClient, db_session: AsyncSession):
    """A participant cannot create a score. The answer is 403."""
    coord_user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coord_user)

    participant_user = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant_user)

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

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=coord_headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=coord_headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=participant_headers)

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer"},
        },
    )
    submission_id = submission_response.json()["id"]

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=participant_headers,
        json={
            "submission_id": submission_id,
            "points": 8,
            "comment": "Self-scoring attempt",
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_scores(client: AsyncClient, db_session: AsyncSession):
    """The score list holds every score of the run."""
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

    _challenge_7 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 2"},
        instructions={"de": "Do something else"},
        scoring_rules={"max_points": 15},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_7.id, sort_order=1
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_ids = [c["id"] for c in challenges_response.json()["items"]]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    submission1_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[0],
            "content": {"text": "Answer 1"},
        },
    )
    submission1_id = submission1_response.json()["id"]

    submission2_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[1],
            "content": {"text": "Answer 2"},
        },
    )
    submission2_id = submission2_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission1_id,
            "points": 8,
            "comment": "Good",
        },
    )

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission2_id,
            "points": 12,
            "comment": "Excellent",
        },
    )

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert len(data["items"]) == 2

    submission_ids = {item["submission_id"] for item in data["items"]}
    assert submission1_id in submission_ids
    assert submission2_id in submission_ids


@pytest.mark.asyncio
async def test_list_scores_participant_denied(client: AsyncClient, db_session: AsyncSession):
    """A participant cannot list scores. The answer is 403."""
    coord_user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coord_user)

    participant_user = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant_user)

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
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/", headers=participant_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_scoreboard_ranking_by_total_points(client: AsyncClient, db_session: AsyncSession):
    """The scoreboard ranks teams by total points, highest first, and lists a team without scores last with 0."""
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

    _challenge_10 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 2"},
        instructions={"de": "Do something else"},
        scoring_rules={"max_points": 15},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_10.id, sort_order=1
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_ids = [c["id"] for c in challenges_response.json()["items"]]

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

    team3_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Gamma"},
    )
    team3_id = team3_response.json()["id"]

    # Join teams (coordinator joins each)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/join", headers=headers)
    await _seat_staying_member(db_session, org, team1_id, "points-team1")

    # Create submissions and scores for Team Alpha (total: 18 points)
    submission1_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[0],
            "content": {"text": "Answer 1"},
        },
    )
    submission1_id = submission1_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission1_id,
            "points": 8,
            "comment": "Good",
        },
    )

    submission2_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[1],
            "content": {"text": "Answer 2"},
        },
    )
    submission2_id = submission2_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission2_id,
            "points": 10,
            "comment": "Excellent",
        },
    )

    # Leave team1 and join team2
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/leave", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/join", headers=headers)

    # Create submissions and scores for Team Beta (total: 22 points - should rank first)
    submission3_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[0],
            "content": {"text": "Answer 3"},
        },
    )
    submission3_id = submission3_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission3_id,
            "points": 9,
            "comment": "Very good",
        },
    )

    submission4_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[1],
            "content": {"text": "Answer 4"},
        },
    )
    submission4_id = submission4_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission4_id,
            "points": 13,
            "comment": "Outstanding",
        },
    )

    # Team Gamma has no submissions/scores

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert len(data["entries"]) == 3  # All teams appear (including team with no scores)
    assert data["is_final"] is False
    assert data["submission_count"] == 4
    assert data["scored_count"] == 4

    # Verify ranking (Team Beta first with 22 points, Team Alpha second with 18 points, Team Gamma third with 0)
    assert data["entries"][0]["team_id"] == team2_id
    assert data["entries"][0]["total_points"] == 22
    assert data["entries"][0]["scored_count"] == 2
    assert data["entries"][0]["last_scored_at"] is not None

    assert data["entries"][1]["team_id"] == team1_id
    assert data["entries"][1]["total_points"] == 18
    assert data["entries"][1]["scored_count"] == 2
    assert data["entries"][1]["last_scored_at"] is not None

    assert data["entries"][2]["team_id"] == team3_id
    assert data["entries"][2]["total_points"] == 0
    assert data["entries"][2]["scored_count"] == 0
    assert data["entries"][2]["last_scored_at"] is None

    # Equal totals share a rank; final standings mark every rank-one team.
    assert [entry["rank"] for entry in data["entries"]] == [1, 2, 3]
    assert all(entry["is_winner"] is False for entry in data["entries"])


@pytest.mark.asyncio
async def test_scoreboard_equal_points_share_rank(client: AsyncClient, db_session: AsyncSession):
    """
    Teams with equal totals share a rank (1, 1, 3) and are ordered by team name.

    The rank counts the teams ahead of a team. An active run is never final, even with every submission scored.
    """
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    # Create rallye with two challenges worth 10 and 5 points
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
    _challenge_11 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_11.id, sort_order=0
        )
    )
    await db_session.flush()

    _challenge_12 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 2"},
        instructions={"de": "Do something else"},
        scoring_rules={"max_points": 5},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_12.id, sort_order=1
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_ids = [c["id"] for c in challenges_response.json()["items"]]

    team1_id = (
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"})
    ).json()["id"]
    team2_id = (
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Beta"})
    ).json()["id"]
    team3_id = (
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Gamma"})
    ).json()["id"]

    # Team Alpha: 10 points on challenge 1
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/join", headers=headers)
    await _seat_staying_member(db_session, org, team1_id, "e_rank-team1")
    alpha_submission_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
            headers=headers,
            json={"run_challenge_id": run_challenge_ids[0], "content": {"text": "Alpha answer"}},
        )
    ).json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": alpha_submission_id, "points": 10, "comment": "Perfect"},
    )

    # Team Beta: 10 points on challenge 1 as well — a tie for first place
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/leave", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/join", headers=headers)
    await _seat_staying_member(db_session, org, team2_id, "e_rank-team2")
    beta_submission_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
            headers=headers,
            json={"run_challenge_id": run_challenge_ids[0], "content": {"text": "Beta answer"}},
        )
    ).json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": beta_submission_id, "points": 10, "comment": "Perfect too"},
    )

    # Team Gamma: 5 points on challenge 2
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/leave", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team3_id}/join", headers=headers)
    gamma_submission_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
            headers=headers,
            json={"run_challenge_id": run_challenge_ids[1], "content": {"text": "Gamma answer"}},
        )
    ).json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": gamma_submission_id, "points": 5, "comment": "Full points"},
    )

    # Get scoreboard (the run is still active)
    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)

    assert response.status_code == 200
    data = response.json()

    # Alpha and Beta tie at 10 points and share rank 1; Gamma ranks third, not second
    assert [entry["team_id"] for entry in data["entries"]] == [team1_id, team2_id, team3_id]
    assert [entry["rank"] for entry in data["entries"]] == [1, 1, 3]
    assert data["is_final"] is False


@pytest.mark.asyncio
async def test_scoreboard_is_final_follows_grading(client: AsyncClient, db_session: AsyncSession):
    """
    The scoreboard of an ended run is final only once grading is finished.

    One unscored submission keeps `is_final` false. Scoring the last one makes it true and marks the
    rank-1 entry as winner. Re-opening a score (`reopen_score`, deleting a score) makes the standing
    preliminary again.
    """
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    # Create rallye with two challenges worth 10 and 5 points
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
    _challenge_13 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_13.id, sort_order=0
        )
    )
    await db_session.flush()

    _challenge_14 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 2"},
        instructions={"de": "Do something else"},
        scoring_rules={"max_points": 5},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_14.id, sort_order=1
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_ids = [c["id"] for c in challenges_response.json()["items"]]

    # Create one team and submit on both challenges; only the first is scored
    team_id = (
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"})
    ).json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    submission1_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
            headers=headers,
            json={"run_challenge_id": run_challenge_ids[0], "content": {"text": "Answer 1"}},
        )
    ).json()["id"]

    score1_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
            headers=headers,
            json={"submission_id": submission1_id, "points": 8, "comment": "Good"},
        )
    ).json()["id"]

    submission2_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
            headers=headers,
            json={"run_challenge_id": run_challenge_ids[1], "content": {"text": "Answer 2"}},
        )
    ).json()["id"]

    # End the run — grading continues after the end
    end_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)
    assert end_response.status_code == 200
    assert end_response.json()["state"] == "ended"

    # Preliminary standing: one submission has no score yet
    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["is_final"] is False
    assert data["entries"][0]["is_winner"] is False

    # Score the remaining submission — grading is finished
    score2_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": submission2_id, "points": 4, "comment": "Almost"},
    )
    assert score2_response.status_code == 201

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)
    data = response.json()
    assert data["is_final"] is True
    assert data["entries"][0]["rank"] == 1
    assert data["entries"][0]["is_winner"] is True

    # Re-open the first score — the standing is preliminary again
    reopen_response = await client.delete(f"/api/orgs/{org.slug}/runs/{run_id}/scores/{score1_id}", headers=headers)
    assert reopen_response.status_code == 204

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)
    data = response.json()
    assert data["is_final"] is False
    assert data["entries"][0]["is_winner"] is False


@pytest.mark.asyncio
async def test_reopen_and_rescore_after_run_ended_updates_leaderboard(client: AsyncClient, db_session: AsyncSession):
    """A coordinator can re-open and re-score a submission after the run ended, and the scores list and the scoreboard follow the new points."""
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
    _challenge = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]
    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "My answer"}},
    )
    submission_id = submission_response.json()["id"]

    score_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": submission_id, "points": 6, "comment": "First pass"},
    )
    score_id = score_response.json()["id"]

    end_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)
    assert end_response.status_code == 200
    assert end_response.json()["state"] == "ended"

    # Re-open the scored submission on the ended run
    reopen_response = await client.delete(f"/api/orgs/{org.slug}/runs/{run_id}/scores/{score_id}", headers=headers)
    assert reopen_response.status_code == 204

    # Score it again with different points, still on the ended run
    rescore_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": submission_id, "points": 9, "comment": "Second pass"},
    )
    assert rescore_response.status_code == 201
    new_score = rescore_response.json()
    assert new_score["id"] != score_id
    assert new_score["points"] == 9

    # The scores list reflects the new score, not the original
    scores_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/", headers=headers)
    assert scores_response.json()["total"] == 1
    assert scores_response.json()["items"][0]["points"] == 9

    # The scoreboard and the team's total follow the new points
    scoreboard_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)
    scoreboard = scoreboard_response.json()
    team_entry = next(e for e in scoreboard["entries"] if e["team_id"] == team_id)
    assert team_entry["total_points"] == 9
    assert team_entry["scored_count"] == 1
    assert scoreboard["is_final"] is True


@pytest.mark.asyncio
async def test_score_photo_challenge_without_reference_solution(client: AsyncClient, db_session: AsyncSession):
    """A photo challenge without a reference solution can still be scored, and the coordinator gets its task text."""
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
    _challenge = await challenge_repo.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Rathaus fotografieren"},
        instructions={"de": "Mache ein Foto vom Rathaus"},
        scoring_rules={"max_points": 10},
        # suggested_solution intentionally omitted — no reference answer
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]
    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge = challenges_response.json()["items"][0]
    run_challenge_id = run_challenge["id"]

    # No reference solution, but the task text is there for the coordinator's scoring view
    assert run_challenge["suggested_solution"] is None
    assert run_challenge["title"] == {"de": "Rathaus fotografieren"}
    assert run_challenge["instructions"] == {"de": "Mache ein Foto vom Rathaus"}

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    ensure_bucket()
    upload_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files={"file": ("rathaus.jpg", BytesIO(plain_image((8, 8), "JPEG")), "image/jpeg")},
    )
    assert upload_response.status_code == 201
    photo_key = upload_response.json()["object_key"]

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"photo_url": photo_key}},
    )
    assert submission_response.status_code == 201
    submission_id = submission_response.json()["id"]

    score_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": submission_id, "points": 7, "comment": "Nice angle"},
    )
    assert score_response.status_code == 201
    assert score_response.json()["points"] == 7
    assert score_response.json()["max_points"] == 10

    get_minio_client().remove_object(settings.storage.bucket, photo_key)


@pytest.mark.asyncio
async def test_scored_count_ignores_soft_deleted_team(client: AsyncClient, db_session: AsyncSession):
    """
    A soft-deleted team's unscored submission does not keep an ended run preliminary.

    `submission_count` and `scored_count` exclude that submission, and the team has no scoreboard entry.
    """
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
    _challenge_15 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_15.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    # Create two teams; both submit, only Team Alpha's submission is scored
    team1_id = (
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"})
    ).json()["id"]
    team2_id = (
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Beta"})
    ).json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/join", headers=headers)
    await _seat_staying_member(db_session, org, team1_id, "d_team-team1")

    submission1_id = (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
            headers=headers,
            json={"run_challenge_id": run_challenge_id, "content": {"text": "Alpha answer"}},
        )
    ).json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": submission1_id, "points": 8, "comment": "Good"},
    )

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/leave", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/join", headers=headers)

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Beta answer"}},
    )

    # Soft-delete Team Beta — its unscored submission must stop counting
    team_repo = TeamRepository(db_session, org.id)
    assert await team_repo.soft_delete(team2_id) is True
    await db_session.commit()

    end_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)
    assert end_response.status_code == 200

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert [entry["team_id"] for entry in data["entries"]] == [team1_id]
    assert data["submission_count"] == 1
    assert data["scored_count"] == 1
    assert data["is_final"] is True


@pytest.mark.asyncio
async def test_scoreboard_team_with_no_scores(client: AsyncClient, db_session: AsyncSession):
    """A team without scores appears with 0 points, below the teams with points."""
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
    _challenge_11 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_11.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

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

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/join", headers=headers)

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer"},
        },
    )
    submission_id = submission_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission_id,
            "points": 7,
            "comment": "Good",
        },
    )

    # Team2 has no submissions/scores

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert len(data["entries"]) == 2

    assert data["entries"][0]["team_id"] == team1_id
    assert data["entries"][0]["total_points"] == 7
    assert data["entries"][0]["scored_count"] == 1

    assert data["entries"][1]["team_id"] == team2_id
    assert data["entries"][1]["total_points"] == 0
    assert data["entries"][1]["scored_count"] == 0


@pytest.mark.asyncio
async def test_scoreboard_multiple_submissions_sum_correctly(client: AsyncClient, db_session: AsyncSession):
    """The scoreboard sums the points and the max points of all scored submissions of a team."""
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
    _challenge_12 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_12.id, sort_order=0
        )
    )
    await db_session.flush()

    _challenge_13 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 2"},
        instructions={"de": "Do something else"},
        scoring_rules={"max_points": 15},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_13.id, sort_order=1
        )
    )
    await db_session.flush()

    _challenge_14 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 3"},
        instructions={"de": "Do another thing"},
        scoring_rules={"max_points": 20},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_14.id, sort_order=2
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_ids = [c["id"] for c in challenges_response.json()["items"]]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    # Create three submissions with different point values
    submission1_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[0],
            "content": {"text": "Answer 1"},
        },
    )
    submission1_id = submission1_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission1_id,
            "points": 8,  # 8 points from challenge 1
            "comment": "Good",
        },
    )

    submission2_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[1],
            "content": {"text": "Answer 2"},
        },
    )
    submission2_id = submission2_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission2_id,
            "points": 12,  # 12 points from challenge 2
            "comment": "Very good",
        },
    )

    submission3_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_ids[2],
            "content": {"text": "Answer 3"},
        },
    )
    submission3_id = submission3_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission3_id,
            "points": 18,  # 18 points from challenge 3
            "comment": "Excellent",
        },
    )

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert len(data["entries"]) == 1
    assert data["entries"][0]["team_id"] == team_id
    assert data["entries"][0]["total_points"] == 38  # 8 + 12 + 18 = 38
    assert data["entries"][0]["scored_count"] == 3
    assert data["entries"][0]["total_max_points"] == 45  # 10 + 15 + 20 = 45


@pytest.mark.asyncio
async def test_scoreboard_accessible_by_participants(client: AsyncClient, db_session: AsyncSession):
    """A participant can read the scoreboard."""
    coord_user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coord_user)

    participant_user = await create_test_user(
        db_session, email="participant@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant_user)

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
    _challenge_15 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_15.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=coord_headers)
    run_station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=coord_headers
    )
    run_challenge_id = challenges_response.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Team Alpha"},
    )
    team_id = team_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=coord_headers)

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=coord_headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer"},
        },
    )
    submission_id = submission_response.json()["id"]

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=coord_headers,
        json={
            "submission_id": submission_id,
            "points": 7,
            "comment": "Good",
        },
    )

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=participant_headers)

    assert response.status_code == 200
    data = response.json()

    assert len(data["entries"]) == 1
    assert data["entries"][0]["team_id"] == team_id
    assert data["entries"][0]["total_points"] == 7


# ---------------------------------------------------------------------------
# A dissolved team leaves the scores
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_list_and_count_leave_a_dissolved_team_out(client: AsyncClient, db_session: AsyncSession):
    """The list of scores and its total hold no score of the dissolved team."""
    from tests.test_submissions import make_dissolved_team_world

    w = await make_dissolved_team_world(client, db_session, "dissolved-scores")
    response = await client.get(f"/api/orgs/{w.slug}/runs/{w.run_id}/scores/", headers=w.coord)
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [w.live_score_id]
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_grading_an_answer_of_a_dissolved_team_answers_404(client: AsyncClient, db_session: AsyncSession):
    """Grading, regrading and re-opening an answer of a dissolved team answer 404."""
    from tests.test_submissions import make_dissolved_team_world

    w = await make_dissolved_team_world(client, db_session, "dissolved-grade")
    base = f"/api/orgs/{w.slug}/runs/{w.run_id}/scores"

    graded = await client.post(
        f"{base}/", json={"submission_id": w.dead_open_submission_id, "points": 3, "comment": "x"}, headers=w.coord
    )
    assert graded.status_code == 404
    regraded = await client.put(f"{base}/{w.dead_score_id}", json={"points": 4}, headers=w.coord)
    assert regraded.status_code == 404
    reopened = await client.delete(f"{base}/{w.dead_score_id}", headers=w.coord)
    assert reopened.status_code == 404

    # The live team is still graded.
    assert (await client.put(f"{base}/{w.live_score_id}", json={"points": 4}, headers=w.coord)).status_code == 200
