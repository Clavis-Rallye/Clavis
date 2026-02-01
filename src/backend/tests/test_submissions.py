"""
Integration tests for submission endpoints.

- create, list and get a submission
- duplicate rule: one submission per team and challenge
- state rule: submissions only in active runs
- JSONB content storage
- role-based access: coordinator sees all, participant sees own team
- cross-tenant isolation
- photo URL verification: the file must exist in the S3 store
- content validation per challenge type
- a dissolved team leaves the readers of answers
"""

from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import AsyncClient
from minio import Minio
from minio.error import S3Error
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
from app.storage import ensure_bucket, get_minio_client
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers
from tests.test_image_variants import jpeg_with_gps


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


@pytest_asyncio.fixture(scope="function")
async def storage_bucket() -> Minio:
    """Ensure the S3 bucket exists for each test and clean up after."""
    ensure_bucket()
    client = get_minio_client()
    yield client

    try:
        objects = client.list_objects(settings.storage.bucket, recursive=True)
        for obj in objects:
            client.remove_object(settings.storage.bucket, obj.object_name)
    except S3Error:
        pass  # Cleanup is best-effort


@pytest.mark.asyncio
async def test_create_submission_success(client: AsyncClient, db_session: AsyncSession):
    """A member of a team creates a submission and gets it back with its fields."""
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

    # A TEXT challenge requires the 'text' field.
    submission_content = {"text": "This is my answer"}
    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": submission_content,
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert UUID(data["id"])
    assert data["organization_id"] == str(org.id)
    assert data["run_id"] == run_id
    assert data["team_id"] == team_id
    assert data["run_challenge_id"] == run_challenge_id
    assert data["content"] == submission_content
    assert "submitted_at" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_submission_duplicate(client: AsyncClient, db_session: AsyncSession):
    """A second submission of a team for the same challenge is refused with 422 and TEAM_ALREADY_SUBMITTED."""
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

    # A TEXT challenge requires the 'text' field.
    response1 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "First answer"},
        },
    )
    assert response1.status_code == 201

    response2 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "Second answer"},
        },
    )

    assert response2.status_code == 422
    assert response2.json()["error_code"] == "TEAM_ALREADY_SUBMITTED"


@pytest.mark.asyncio
async def test_create_submission_run_not_active(client: AsyncClient, db_session: AsyncSession):
    """A submission is refused with 422 while the run is paused or ended."""
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

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=headers)

    # Validation runs after the state check, so any content is fine.
    response_paused = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "Answer while paused"},
        },
    )

    assert response_paused.status_code == 422
    detail = response_paused.json()["detail"]
    assert "paused" in detail.lower()
    assert "active" in detail.lower()
    # The code lets the participant read the refusal in German.
    assert response_paused.json()["error_code"] == "RUN_PAUSED"

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/resume", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    response_ended = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "Answer while ended"},
        },
    )

    assert response_ended.status_code == 422
    detail = response_ended.json()["detail"]
    assert "ended" in detail.lower()
    assert "active" in detail.lower()
    assert response_ended.json()["error_code"] == "RUN_ENDED"


@pytest.mark.asyncio
async def test_list_submissions_coordinator_sees_all(client: AsyncClient, db_session: AsyncSession):
    """A coordinator lists the submissions of all teams of the run."""
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

    # Join team1 and create submission
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/join", headers=headers)
    await _seat_staying_member(db_session, org, team1_id, "es_all-team1")
    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "Team 1 answer"},
        },
    )

    # Leave team1, join team2, and create submission
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/leave", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/join", headers=headers)
    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "Team 2 answer"},
        },
    )

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/submissions/", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert len(data["items"]) == 2

    team_ids = {item["team_id"] for item in data["items"]}
    assert team1_id in team_ids
    assert team2_id in team_ids


@pytest.mark.asyncio
async def test_list_submissions_participant_sees_own_team(client: AsyncClient, db_session: AsyncSession):
    """A participant lists only the submissions of their own team."""
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

    team1_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Team Alpha"},
    )
    team1_id = team1_response.json()["id"]

    team2_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Team Beta"},
    )
    team2_id = team2_response.json()["id"]

    # Coordinator joins team1 and creates submission
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/join", headers=coord_headers)
    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=coord_headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "Team 1 answer"},
        },
    )

    # Participant joins team2 and creates submission
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/join", headers=participant_headers)
    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "Team 2 answer"},
        },
    )

    # List submissions as participant (should only see team2)
    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/submissions/", headers=participant_headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["team_id"] == team2_id


@pytest.mark.asyncio
async def test_get_submission_success(client: AsyncClient, db_session: AsyncSession):
    """A submission is fetched by its ID with its content."""
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

    # A TEXT challenge requires the 'text' field. Extra fields are allowed.
    submission_content = {"text": "Complex answer", "nested": {"field": "value"}, "list": [1, 2, 3]}
    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": submission_content,
        },
    )
    submission_id = create_response.json()["id"]

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/submissions/{submission_id}", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == submission_id
    assert data["run_id"] == run_id
    assert data["team_id"] == team_id
    assert data["run_challenge_id"] == run_challenge_id
    assert data["content"] == submission_content


@pytest.mark.asyncio
async def test_get_submission_participant_access_control(client: AsyncClient, db_session: AsyncSession):
    """A participant is refused the submission of another team with 403."""
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

    team1_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Team Alpha"},
    )
    team1_id = team1_response.json()["id"]

    team2_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Team Beta"},
    )
    team2_id = team2_response.json()["id"]

    # Coordinator joins team1 and creates submission
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team1_id}/join", headers=coord_headers)
    team1_submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=coord_headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "Team 1 answer"},
        },
    )
    team1_submission_id = team1_submission_response.json()["id"]

    # Participant joins team2
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/join", headers=participant_headers)

    # Participant tries to get team1's submission - should fail
    response_forbidden = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/{team1_submission_id}", headers=participant_headers
    )
    assert response_forbidden.status_code == 403
    detail = response_forbidden.json()["detail"]
    assert "access denied" in detail.lower()


@pytest.mark.asyncio
async def test_submission_content_jsonb_storage(client: AsyncClient, db_session: AsyncSession):
    """Nested objects, lists, numbers, booleans and null in the content come back exactly as submitted."""
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

    # A TEXT challenge requires the 'text' field.
    complex_content = {
        "text": "This is the main text answer",  # Required for TEXT challenge
        "numeric_answer": 42,
        "boolean_flag": True,
        "list_of_items": ["item1", "item2", "item3"],
        "nested_object": {"key1": "value1", "key2": {"deep_key": "deep_value"}},
        "null_value": None,
    }

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": complex_content,
        },
    )

    assert create_response.status_code == 201
    submission_id = create_response.json()["id"]

    get_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/submissions/{submission_id}", headers=headers)

    assert get_response.status_code == 200
    retrieved_content = get_response.json()["content"]

    assert retrieved_content == complex_content
    assert retrieved_content["nested_object"]["key2"]["deep_key"] == "deep_value"
    assert retrieved_content["list_of_items"] == ["item1", "item2", "item3"]
    assert retrieved_content["null_value"] is None


# ============================================================================
# Content Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_text_submission_with_text_field(client: AsyncClient, db_session: AsyncSession):
    """Test TEXT challenge submission with valid 'text' field succeeds."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_9 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Text Challenge"},
        instructions={"de": "Write something"},
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
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "This is my answer"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_text_submission_without_text_field(client: AsyncClient, db_session: AsyncSession):
    """Test TEXT challenge submission without 'text' field returns 422."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_10 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Text Challenge"},
        instructions={"de": "Write something"},
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
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"answer": "wrong field name"}},
    )

    assert response.status_code == 422
    assert "text" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_photo_submission_with_photo_url(client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio):
    """Test PHOTO challenge submission with valid 'photo_url' field succeeds."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_11 = await challenge_repo.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Photo Challenge"},
        instructions={"de": "Take a photo"},
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
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    # First upload a file to the S3 store via the upload endpoint
    file_content = jpeg_with_gps((40, 40))
    files = {"file": ("test_image.jpg", BytesIO(file_content), "image/jpeg")}
    upload_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    object_key = upload_response.json()["object_key"]

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"photo_url": object_key}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_photo_submission_without_photo_url(client: AsyncClient, db_session: AsyncSession):
    """Test PHOTO challenge submission without 'photo_url' field returns 422."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_12 = await challenge_repo.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Photo Challenge"},
        instructions={"de": "Take a photo"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_12.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"image": "wrong-field.jpg"}},
    )

    assert response.status_code == 422
    assert "photo_url" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_photo_submission_with_nonexistent_file(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """Test PHOTO challenge submission with non-existent photo_url returns 422."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_13 = await challenge_repo.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Photo Challenge"},
        instructions={"de": "Take a photo"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_13.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    # A photo_url that does not exist in the S3 store but does sit under the team's own
    # upload prefix. So this exercises the existence check, not the ownership check.
    nonexistent_key = f"{org.id}/{run_id}/{team_id}/nonexistent.jpg"
    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"photo_url": nonexistent_key}},
    )

    assert response.status_code == 422
    assert "file not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_quiz_submission_with_selected_option(client: AsyncClient, db_session: AsyncSession):
    """Test QUIZ challenge submission with valid 'selected_option' field succeeds."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_14 = await challenge_repo.create(
        challenge_type=ChallengeType.QUIZ,
        title={"de": "Quiz Challenge"},
        instructions={"de": "Select the correct option"},
        scoring_rules={"max_points": 10, "correct_option": "A"},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_14.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"selected_option": "A"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_quiz_submission_without_selected_option(client: AsyncClient, db_session: AsyncSession):
    """Test QUIZ challenge submission without 'selected_option' field returns 422."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_15 = await challenge_repo.create(
        challenge_type=ChallengeType.QUIZ,
        title={"de": "Quiz Challenge"},
        instructions={"de": "Select the correct option"},
        scoring_rules={"max_points": 10, "correct_option": "A"},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_15.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"answer": "A"}},
    )

    assert response.status_code == 422
    assert "selected_option" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_qr_scan_submission_with_scanned_value(client: AsyncClient, db_session: AsyncSession):
    """Test QR_SCAN challenge submission with valid 'scanned_value' field succeeds."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_16 = await challenge_repo.create(
        challenge_type=ChallengeType.QR_SCAN,
        title={"de": "QR Challenge"},
        instructions={"de": "Scan the QR code"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_16.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"scanned_value": "QR_CODE_12345"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_qr_scan_submission_without_scanned_value(client: AsyncClient, db_session: AsyncSession):
    """Test QR_SCAN challenge submission without 'scanned_value' field returns 422."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_17 = await challenge_repo.create(
        challenge_type=ChallengeType.QR_SCAN,
        title={"de": "QR Challenge"},
        instructions={"de": "Scan the QR code"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_17.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"code": "QR_CODE_12345"}},
    )

    assert response.status_code == 422
    assert "scanned_value" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_creative_submission_with_text_only(client: AsyncClient, db_session: AsyncSession):
    """Test CREATIVE challenge submission with only 'text' field succeeds."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_18 = await challenge_repo.create(
        challenge_type=ChallengeType.CREATIVE,
        title={"de": "Creative Challenge"},
        instructions={"de": "Be creative"},
        scoring_rules={"max_points": 20},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_18.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "My creative story"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_creative_submission_with_nothing(client: AsyncClient, db_session: AsyncSession):
    """Test CREATIVE challenge submission with no content returns 422."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_19 = await challenge_repo.create(
        challenge_type=ChallengeType.CREATIVE,
        title={"de": "Creative Challenge"},
        instructions={"de": "Be creative"},
        scoring_rules={"max_points": 20},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_19.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"irrelevant": "data"}},
    )

    assert response.status_code == 422
    assert "creative" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_text_submission_exceeds_max_length(client: AsyncClient, db_session: AsyncSession):
    """Test TEXT challenge submission with text exceeding 5000 chars returns 422."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_20 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Text Challenge"},
        instructions={"de": "Write something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_20.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    long_text = "x" * 5001
    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": long_text}},
    )

    assert response.status_code == 422
    assert "5000" in response.json()["detail"]


# ---------------------------------------------------------------------------
# A dissolved team leaves the readers of answers
# ---------------------------------------------------------------------------


class DissolvedTeamWorld:
    """A run with one live and one dissolved team; both have answers, one score each."""

    def __init__(self, **fields: object) -> None:
        self.__dict__.update(fields)

    slug: str
    run_id: str
    coord: dict[str, str]
    participant_dead: dict[str, str]
    participant_live: dict[str, str]
    live_team_id: str
    dead_team_id: str
    live_submission_id: str
    live_score_id: str
    dead_scored_submission_id: str
    dead_open_submission_id: str
    dead_score_id: str


async def make_dissolved_team_world(
    client: AsyncClient, db_session: AsyncSession, org_slug: str, *, dissolve: bool = True
) -> DissolvedTeamWorld:
    """
    Two teams answer; the coordinator grades one answer each; the second team is then dissolved.

    A team is dissolved by setting is_deleted and deleted_at and removing the member rows.
    The team keeps its answers and scores.
    """
    from datetime import UTC, datetime

    from sqlalchemy import delete, update

    from app.models.team import Team
    from app.models.team_member import TeamMember
    from tests.journey_helpers import setup_journey_environment

    env = await setup_journey_environment(
        db_session,
        client,
        org_slug=org_slug,
        num_stations=1,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )
    slug = env.slug
    coord = env.auth_headers["coordinator"]
    run = await client.post(
        f"/api/orgs/{slug}/runs/", json={"name": "Dissolve run", "rallye_id": str(env.rallye_id)}, headers=coord
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    assert (await client.post(f"/api/orgs/{slug}/runs/{run_id}/start", headers=coord)).status_code == 200

    stations = await client.get(f"/api/orgs/{slug}/runs/{run_id}/stations/", headers=coord)
    station_id = stations.json()["items"][0]["id"]
    challenges = await client.get(f"/api/orgs/{slug}/runs/{run_id}/stations/{station_id}/challenges/", headers=coord)
    run_challenge_ids = [item["id"] for item in challenges.json()["items"]]
    assert len(run_challenge_ids) == 2

    team_ids: dict[str, str] = {}
    submissions: dict[str, list[str]] = {}
    for key, name in (("live", "Team Live"), ("dead", "Team Dead")):
        headers = env.auth_headers["participant1" if key == "live" else "participant2"]
        team = await client.post(f"/api/orgs/{slug}/runs/{run_id}/teams/", json={"name": name}, headers=headers)
        assert team.status_code == 201, team.text
        team_ids[key] = team.json()["id"]
        joined = await client.post(f"/api/orgs/{slug}/runs/{run_id}/teams/{team_ids[key]}/join", headers=headers)
        assert joined.status_code == 204, joined.text
        submissions[key] = []
        for rc_id in run_challenge_ids if key == "dead" else run_challenge_ids[:1]:
            answer = await client.post(
                f"/api/orgs/{slug}/runs/{run_id}/submissions/",
                json={"run_challenge_id": rc_id, "content": {"text": f"{name} answer"}},
                headers=headers,
            )
            assert answer.status_code == 201, answer.text
            submissions[key].append(answer.json()["id"])

    score_ids: dict[str, str] = {}
    for key in ("live", "dead"):
        graded = await client.post(
            f"/api/orgs/{slug}/runs/{run_id}/scores/",
            json={"submission_id": submissions[key][0], "points": 5, "comment": "ok"},
            headers=coord,
        )
        assert graded.status_code == 201, graded.text
        score_ids[key] = graded.json()["id"]

    if dissolve:
        await db_session.execute(delete(TeamMember).where(TeamMember.team_id == UUID(team_ids["dead"])))
        await db_session.execute(
            update(Team).where(Team.id == UUID(team_ids["dead"])).values(is_deleted=True, deleted_at=datetime.now(UTC))
        )
        await db_session.commit()

    return DissolvedTeamWorld(
        slug=slug,
        run_id=run_id,
        coord=coord,
        participant_dead=env.auth_headers["participant2"],
        participant_live=env.auth_headers["participant1"],
        live_team_id=team_ids["live"],
        dead_team_id=team_ids["dead"],
        live_submission_id=submissions["live"][0],
        live_score_id=score_ids["live"],
        dead_scored_submission_id=submissions["dead"][0],
        dead_open_submission_id=submissions["dead"][1],
        dead_score_id=score_ids["dead"],
    )


@pytest.mark.asyncio
async def test_review_list_leaves_a_dissolved_teams_answers_out(client: AsyncClient, db_session: AsyncSession) -> None:
    """The list of answers holds no answer of the dissolved team and agrees with its total."""
    w = await make_dissolved_team_world(client, db_session, "dissolved-list")
    response = await client.get(f"/api/orgs/{w.slug}/runs/{w.run_id}/submissions/", headers=w.coord)
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [w.live_submission_id]
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_a_dissolved_teams_answer_by_id_answers_404(client: AsyncClient, db_session: AsyncSession) -> None:
    """Reading or changing an answer of a dissolved team answers 404; a live answer still reads."""
    w = await make_dissolved_team_world(client, db_session, "dissolved-byid")
    base = f"/api/orgs/{w.slug}/runs/{w.run_id}/submissions"

    assert (await client.get(f"{base}/{w.live_submission_id}", headers=w.coord)).status_code == 200
    for submission_id in (w.dead_scored_submission_id, w.dead_open_submission_id):
        assert (await client.get(f"{base}/{submission_id}", headers=w.coord)).status_code == 404
        replaced = await client.put(
            f"{base}/{submission_id}", json={"content": {"text": "new"}}, headers=w.participant_dead
        )
        assert replaced.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("run_action", ["pause", "end"])
async def test_a_dissolved_teams_answer_answers_404_in_a_paused_or_ended_run(
    client: AsyncClient, db_session: AsyncSession, run_action: str
) -> None:
    """The 404 holds in every run state; the state check does not answer first (422)."""
    w = await make_dissolved_team_world(client, db_session, f"dissolved-state-{run_action}")
    base = f"/api/orgs/{w.slug}/runs/{w.run_id}"
    moved = await client.post(f"{base}/{run_action}", headers=w.coord)
    assert moved.status_code == 200, moved.text

    for submission_id in (w.dead_scored_submission_id, w.dead_open_submission_id):
        assert (await client.get(f"{base}/submissions/{submission_id}", headers=w.coord)).status_code == 404
        replaced = await client.put(
            f"{base}/submissions/{submission_id}", json={"content": {"text": "new"}}, headers=w.participant_dead
        )
        assert replaced.status_code == 404
        graded = await client.post(
            f"{base}/scores/", json={"submission_id": submission_id, "points": 3, "comment": "x"}, headers=w.coord
        )
        assert graded.status_code == 404
    assert (
        await client.put(f"{base}/scores/{w.dead_score_id}", json={"points": 4}, headers=w.coord)
    ).status_code == 404
    assert (await client.delete(f"{base}/scores/{w.dead_score_id}", headers=w.coord)).status_code == 404

    # An answer of a live team keeps the state check: 422 for a change in a paused or ended run.
    live = await client.put(
        f"{base}/submissions/{w.live_submission_id}", json={"content": {"text": "new"}}, headers=w.participant_live
    )
    assert live.status_code == 422


# ---------------------------------------------------------------------------
# An answer in flight beside a dissolution
# ---------------------------------------------------------------------------


class _HeldOnce:
    """Holds the first caller of ``pause`` until ``release`` is set; ``pid`` is its database backend."""

    def __init__(self) -> None:
        import asyncio

        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.pid = 0
        self._armed = True

    async def pause(self, session: AsyncSession) -> None:
        from sqlalchemy import text

        if not self._armed:
            return
        self._armed = False
        self.pid = int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        self.reached.set()
        await self.release.wait()


async def _open_challenge_of_the_live_team(db_session: AsyncSession, w: DissolvedTeamWorld) -> str:
    """The challenge the live team has not answered yet: the second answer of the other team names it."""
    from app.models.submission import Submission

    other = await db_session.get(Submission, UUID(w.dead_open_submission_id))
    assert other is not None
    return str(other.run_challenge_id)


async def _answers_of(db_session: AsyncSession, team_id: str, run_challenge_id: str) -> int:
    from sqlalchemy import func, select

    from app.models.submission import Submission

    return (
        await db_session.execute(
            select(func.count(Submission.id)).where(
                Submission.team_id == UUID(team_id), Submission.run_challenge_id == UUID(run_challenge_id)
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_an_answer_in_flight_is_refused_when_its_team_dissolved_before_the_team_lock(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer read its membership, the team is removed and committed, then the answer goes on.

    The answer locks the team row before it inserts and reads again under the
    lock: the team is dissolved, so no answer is written and the caller gets
    the answer the join gives for a dissolved team.
    """
    import asyncio

    from app.repositories.team_member import TeamMemberRepository

    w = await make_dissolved_team_world(client, db_session, "live-answer-late", dissolve=False)
    run_challenge_id = await _open_challenge_of_the_live_team(db_session, w)

    held = _HeldOnce()
    original = TeamMemberRepository.get_by_user_and_run

    async def get_by_user_and_run(self: TeamMemberRepository, *args: object, **kwargs: object) -> object:
        membership = await original(self, *args, **kwargs)  # type: ignore[arg-type]
        await held.pause(self.session)
        return membership

    monkeypatch.setattr(TeamMemberRepository, "get_by_user_and_run", get_by_user_and_run)

    answer = asyncio.create_task(
        concurrent_client.post(
            f"/api/orgs/{w.slug}/runs/{w.run_id}/submissions/",
            json={"run_challenge_id": run_challenge_id, "content": {"text": "too late"}},
            headers=w.participant_live,
        )
    )
    try:
        await asyncio.wait_for(held.reached.wait(), timeout=10)
        # The answer holds no lock yet, so the removal ends on its own.
        removed = await asyncio.wait_for(
            concurrent_client.delete(
                f"/api/orgs/{w.slug}/runs/{w.run_id}/teams/{w.live_team_id}",
                headers=w.coord,
                params={"confirm_dissolution": "true"},
            ),
            timeout=10,
        )
    finally:
        held.release.set()
    response = await asyncio.wait_for(answer, timeout=30)

    assert removed.status_code == 204, removed.text
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Team not found in this run"
    assert await _answers_of(db_session, w.live_team_id, run_challenge_id) == 0


@pytest.mark.asyncio
async def test_a_dissolution_waits_for_an_answer_that_holds_the_team(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other way round: the answer holds its locks, so the removal waits for that backend.

    The answer is held right after its team lock. By then it holds the shared
    lock of the run row as well, its first row lock. The removal asks for the
    run row before the team (lock order run, account, team, member). So the
    removal waits at the run row, for the backend of the answer, and never
    reaches the team row while the answer is open.

    The answer was first: it is written with its 201, and the team dissolves
    after it and keeps the answer, as it keeps every answer.
    """
    import asyncio

    from sqlalchemy import text

    w = await make_dissolved_team_world(client, db_session, "live-answer-first", dissolve=False)
    run_challenge_id = await _open_challenge_of_the_live_team(db_session, w)

    held = _HeldOnce()
    original = AsyncSession.execute

    async def execute(session: AsyncSession, statement: object, *args: object, **kwargs: object) -> object:
        result = await original(session, statement, *args, **kwargs)  # type: ignore[arg-type,call-overload]
        if getattr(statement, "_for_update_arg", None) is not None and "FROM teams" in str(statement):
            await held.pause(session)
        return result

    monkeypatch.setattr(AsyncSession, "execute", execute)

    answer = asyncio.create_task(
        concurrent_client.post(
            f"/api/orgs/{w.slug}/runs/{w.run_id}/submissions/",
            json={"run_challenge_id": run_challenge_id, "content": {"text": "in time"}},
            headers=w.participant_live,
        )
    )
    reached = asyncio.create_task(held.reached.wait())
    await asyncio.wait({answer, reached}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
    reached.cancel()
    assert not answer.done(), "the answer was written without the lock of its team"

    removal = asyncio.create_task(
        concurrent_client.delete(
            f"/api/orgs/{w.slug}/runs/{w.run_id}/teams/{w.live_team_id}",
            headers=w.coord,
            params={"confirm_dissolution": "true"},
        )
    )
    waits_query = text(
        """
        SELECT pg_blocking_pids(a.pid) AS blocked_by,
               (SELECT min(l.relation::regclass::text) FROM pg_locks l
                 WHERE l.pid = a.pid AND l.locktype = 'tuple') AS waits_in
          FROM pg_stat_activity a
         WHERE a.datname = current_database() AND a.wait_event_type = 'Lock'
        """
    )

    async def lock_waits() -> list[tuple[tuple[int, ...], str | None]]:
        async with test_engine.connect() as connection:  # type: ignore[attr-defined]
            while not removal.done():
                rows = (await connection.execute(waits_query)).all()
                if rows:
                    return [(tuple(row.blocked_by), row.waits_in) for row in rows]
                await asyncio.sleep(0.01)
        return []

    try:
        waits = await asyncio.wait_for(lock_waits(), timeout=10)
        # Exactly this edge: the removal waits for the backend of the answer, at the run row.
        assert waits == [((held.pid,), "runs")], waits
    finally:
        held.release.set()
    response, removed = await asyncio.wait_for(asyncio.gather(answer, removal), timeout=30)

    assert response.status_code == 201, response.text
    assert removed.status_code == 204, removed.text
    assert await _answers_of(db_session, w.live_team_id, run_challenge_id) == 1


@pytest.mark.asyncio
async def test_an_answer_of_a_member_removed_before_the_team_lock_is_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The team stays, the member went: the membership read again under the team lock decides."""
    import asyncio

    from sqlalchemy import select

    from app.repositories.team_member import TeamMemberRepository

    w = await make_dissolved_team_world(client, db_session, "live-answer-removed", dissolve=False)
    run_challenge_id = await _open_challenge_of_the_live_team(db_session, w)
    (seat,) = (
        await db_session.execute(
            select(TeamMember.id, TeamMember.organization_id).where(TeamMember.team_id == UUID(w.live_team_id))
        )
    ).all()
    leaving_seat = seat.id
    db_session.add(
        TeamMember(
            organization_id=seat.organization_id,
            team_id=UUID(w.live_team_id),
            user_id=None,
            display_name="Stays",
            is_guest=False,
            joined_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    held = _HeldOnce()
    original = TeamMemberRepository.get_by_user_and_run

    async def get_by_user_and_run(self: TeamMemberRepository, *args: object, **kwargs: object) -> object:
        membership = await original(self, *args, **kwargs)  # type: ignore[arg-type]
        await held.pause(self.session)
        return membership

    monkeypatch.setattr(TeamMemberRepository, "get_by_user_and_run", get_by_user_and_run)

    answer = asyncio.create_task(
        concurrent_client.post(
            f"/api/orgs/{w.slug}/runs/{w.run_id}/submissions/",
            json={"run_challenge_id": run_challenge_id, "content": {"text": "too late"}},
            headers=w.participant_live,
        )
    )
    try:
        await asyncio.wait_for(held.reached.wait(), timeout=10)
        removed = await asyncio.wait_for(
            concurrent_client.delete(
                f"/api/orgs/{w.slug}/runs/{w.run_id}/teams/{w.live_team_id}/members/{leaving_seat}", headers=w.coord
            ),
            timeout=10,
        )
    finally:
        held.release.set()
    response = await asyncio.wait_for(answer, timeout=30)

    assert removed.status_code == 204, removed.text
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "NOT_TEAM_MEMBER"
    assert await _answers_of(db_session, w.live_team_id, run_challenge_id) == 0
