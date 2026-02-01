"""
Journey tests of the coordinator workflow, from run creation to the data of an ended run.

The scenarios cover:
- run creation and configuration
- run start and the frozen snapshots
- snapshot immutability when templates are modified
- valid and invalid state transitions
- scheduling
- monitoring a live run: teams, submissions, pagination
- scoring with edge cases, and the scoreboard
- feed announcements
- pause and resume, and what a paused run refuses
- ending a run and the post-run data
- which run states allow update and deletion
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers
from tests.journey_helpers import (
    activate_run,
    create_team_and_join,
    setup_journey_environment,
    submit_challenge,
)

# =============================================================================
# Run Creation and Configuration
# =============================================================================


@pytest.mark.asyncio
async def test_run_creation_and_configuration(client: AsyncClient, db_session: AsyncSession) -> None:
    """A created run is configured, with a well-formed join code and no timestamps. GET and the list return it."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@runconfig.test",
        role=UserRole.COORDINATOR,
        org_slug="runconfig-org",
        org_name="Run Configuration Org",
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye", "en": "Test Rallye"},
        description={"de": "Test", "en": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    challenge_repo = ChallengeRepository(db_session, org.id)

    for i in range(3):
        station = await station_repo.create(
            title={"de": f"Station {i + 1}", "en": f"Station {i + 1}"},
            description={"de": "Test", "en": "Test"},
            location_lat=48.3705 + i * 0.001,
            location_lng=10.8978 + i * 0.001,
        )
        rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=i)
        db_session.add(rs_station)
        await db_session.flush()

        for j in range(2):
            _challenge_1 = await challenge_repo.create(
                challenge_type=ChallengeType.TEXT if j == 0 else ChallengeType.PHOTO,
                title={"de": f"Challenge {j + 1}", "en": f"Challenge {j + 1}"},
                instructions={"de": "Test", "en": "Test"},
                scoring_rules={"max_points": 10},
            )
            db_session.add(
                RallyeStationChallenge(
                    organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_1.id, sort_order=j
                )
            )
            await db_session.flush()

    await db_session.commit()

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Frühlingslauf 2026"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()

    assert run_data["state"] == "configured"
    assert run_data["name"] == "Frühlingslauf 2026"
    assert run_data["rallye_id"] == str(rallye.id)

    # Join code: 6 characters from uppercase letters and the digits 2-9, without ambiguous characters.
    join_code = run_data["join_code"]
    assert len(join_code) == 6
    assert join_code.isalnum()
    assert all(c in "ABCDEFGHJKMNPQRSTUVWXYZ23456789" for c in join_code)
    ambiguous_chars = set("0OI1L")
    assert not any(char in ambiguous_chars for char in join_code)

    assert run_data["started_at"] is None
    assert run_data["ended_at"] is None
    assert run_data["paused_at"] is None

    run_id = run_data["id"]

    get_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=coord_headers,
    )
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["id"] == run_id
    assert get_data["state"] == "configured"
    assert get_data["name"] == "Frühlingslauf 2026"

    list_response = await client.get(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
    )
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert "items" in list_data
    run_ids = [run["id"] for run in list_data["items"]]
    assert run_id in run_ids


# =============================================================================
# Run Start and Snapshot Verification
# =============================================================================


@pytest.mark.asyncio
async def test_run_start_freezes_stations_and_challenges_into_a_snapshot(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Starting a run freezes every station and challenge into a snapshot that matches the template."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@snapshot.test",
        role=UserRole.COORDINATOR,
        org_slug="snapshot-org",
        org_name="Snapshot Verification Org",
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Snapshot Test Rallye", "en": "Snapshot Test Rallye"},
        description={"de": "Für Snapshot-Tests", "en": "For snapshot tests"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    challenge_repo = ChallengeRepository(db_session, org.id)

    template_stations: list[dict[str, Any]] = []
    template_challenges: list[list[dict[str, Any]]] = []

    station_coords = [
        (48.3705, 10.8978),
        (48.3713, 10.8976),
        (48.3685, 10.9020),
    ]

    challenge_types = [
        ChallengeType.TEXT,
        ChallengeType.PHOTO,
        ChallengeType.QR_SCAN,
        ChallengeType.QUIZ,
        ChallengeType.CREATIVE,
        ChallengeType.TEXT,
    ]

    max_points = [10, 20, 15, 5, 25, 10]
    challenge_idx = 0

    for i, (lat, lng) in enumerate(station_coords):
        station = await station_repo.create(
            title={"de": f"Station {i + 1} DE", "en": f"Station {i + 1} EN"},
            description={"de": f"Beschreibung {i + 1}", "en": f"Description {i + 1}"},
            location_lat=lat,
            location_lng=lng,
        )
        rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=i)
        db_session.add(rs_station)
        await db_session.flush()
        template_stations.append(
            {
                "id": station.id,
                "title": {"de": f"Station {i + 1} DE", "en": f"Station {i + 1} EN"},
                "description": {"de": f"Beschreibung {i + 1}", "en": f"Description {i + 1}"},
                "location_lat": lat,
                "location_lng": lng,
                "sort_order": i,
            }
        )

        station_challenges = []
        for j in range(2):
            challenge = await challenge_repo.create(
                challenge_type=challenge_types[challenge_idx],
                title={"de": f"Aufgabe {challenge_idx + 1} DE", "en": f"Challenge {challenge_idx + 1} EN"},
                instructions={"de": f"Anleitung {challenge_idx + 1}", "en": f"Instructions {challenge_idx + 1}"},
                scoring_rules={"max_points": max_points[challenge_idx]},
            )
            db_session.add(
                RallyeStationChallenge(
                    organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=challenge.id, sort_order=j
                )
            )
            await db_session.flush()
            station_challenges.append(
                {
                    "id": challenge.id,
                    "challenge_type": challenge_types[challenge_idx].value,
                    "title": {"de": f"Aufgabe {challenge_idx + 1} DE", "en": f"Challenge {challenge_idx + 1} EN"},
                    "instructions": {"de": f"Anleitung {challenge_idx + 1}", "en": f"Instructions {challenge_idx + 1}"},
                    "scoring_rules": {"max_points": max_points[challenge_idx]},
                    "sort_order": j,
                }
            )
            challenge_idx += 1

        template_challenges.append(station_challenges)

    await db_session.commit()

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Snapshot Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    start_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=coord_headers,
    )
    assert start_response.status_code == 200
    start_data = start_response.json()

    assert start_data["state"] == "active"
    assert start_data["started_at"] is not None
    started_at = datetime.fromisoformat(start_data["started_at"].replace("Z", "+00:00"))
    now = datetime.now(UTC)
    assert (now - started_at).total_seconds() < 10

    stations_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/",
        headers=coord_headers,
    )
    assert stations_response.status_code == 200
    stations_data = stations_response.json()
    assert "items" in stations_data
    run_stations = stations_data["items"]
    assert len(run_stations) == 3

    for idx, run_station in enumerate(sorted(run_stations, key=lambda s: s["sort_order"])):
        template = template_stations[idx]
        assert run_station["title"] == template["title"]
        assert run_station["description"] == template["description"]
        assert run_station["location_lat"] == template["location_lat"]
        assert run_station["location_lng"] == template["location_lng"]
        assert run_station["sort_order"] == template["sort_order"]
        assert run_station["source_station_id"] == str(template["id"])

    for station_idx, run_station in enumerate(sorted(run_stations, key=lambda s: s["sort_order"])):
        challenges_response = await client.get(
            f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station['id']}/challenges/",
            headers=coord_headers,
        )
        assert challenges_response.status_code == 200
        challenges_data = challenges_response.json()
        assert "items" in challenges_data
        run_challenges = challenges_data["items"]
        assert len(run_challenges) == 2

        for challenge_idx, run_challenge in enumerate(sorted(run_challenges, key=lambda c: c["sort_order"])):
            template = template_challenges[station_idx][challenge_idx]
            assert run_challenge["challenge_type"] == template["challenge_type"]
            assert run_challenge["title"] == template["title"]
            assert run_challenge["instructions"] == template["instructions"]
            assert run_challenge["scoring_rules"] == template["scoring_rules"]
            assert run_challenge["sort_order"] == template["sort_order"]
            assert run_challenge["source_challenge_id"] == str(template["id"])


# =============================================================================
# Snapshot Immutability
# =============================================================================


@pytest.mark.asyncio
async def test_snapshot_keeps_its_content_when_the_template_changes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Snapshots keep their content when the template station and challenge are modified after the start."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@immutable.test",
        role=UserRole.COORDINATOR,
        org_slug="immutable-org",
        org_name="Immutability Test Org",
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Immutability Test", "en": "Immutability Test"},
        description={"de": "Test", "en": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    challenge_repo = ChallengeRepository(db_session, org.id)

    station = await station_repo.create(
        title={"de": "Original Station Title", "en": "Original Station Title"},
        description={"de": "Original Description", "en": "Original Description"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()
    station_id = station.id

    challenge = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Original Challenge Title", "en": "Original Challenge Title"},
        instructions={"de": "Original Instructions", "en": "Original Instructions"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=challenge.id, sort_order=0
        )
    )
    await db_session.flush()
    challenge_id = challenge.id

    await db_session.commit()

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Immutability Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    start_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=coord_headers,
    )
    assert start_response.status_code == 200

    stations_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/",
        headers=coord_headers,
    )
    assert stations_response.status_code == 200
    original_stations = stations_response.json()["items"]
    assert len(original_stations) == 1
    original_station = original_stations[0]
    run_station_id = original_station["id"]

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=coord_headers,
    )
    assert challenges_response.status_code == 200
    original_challenges = challenges_response.json()["items"]
    assert len(original_challenges) == 1
    original_challenge = original_challenges[0]

    assert original_station["title"]["de"] == "Original Station Title"
    assert original_challenge["instructions"]["de"] == "Original Instructions"

    # Edit the template station (needs the creator role)
    creator = await create_test_user(
        db_session,
        email="creator@immutable.test",
        role=UserRole.CREATOR,
        organization=org,
    )
    creator_headers = get_auth_headers(creator)

    station_update_response = await client.put(
        f"/api/orgs/{org.slug}/stations/{station_id}",
        headers=creator_headers,
        json={
            "title": {"de": "MODIFIED Station Title", "en": "MODIFIED Station Title"},
            "description": {"de": "MODIFIED Description", "en": "MODIFIED Description"},
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
    )
    assert station_update_response.status_code == 200

    # Edit the template challenge
    challenge_update_response = await client.put(
        f"/api/orgs/{org.slug}/challenges/{challenge_id}",
        headers=creator_headers,
        json={
            "challenge_type": "text",
            "title": {"de": "MODIFIED Challenge Title", "en": "MODIFIED Challenge Title"},
            "instructions": {"de": "MODIFIED Instructions", "en": "MODIFIED Instructions"},
            "scoring_rules": {"max_points": 10},
        },
    )
    assert challenge_update_response.status_code == 200

    # The frozen stations stay as they were
    frozen_stations_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/",
        headers=coord_headers,
    )
    assert frozen_stations_response.status_code == 200
    frozen_stations = frozen_stations_response.json()["items"]
    assert len(frozen_stations) == 1
    frozen_station = frozen_stations[0]

    assert frozen_station["title"]["de"] == "Original Station Title"
    assert frozen_station["description"]["de"] == "Original Description"

    # The frozen challenges stay as they were
    frozen_challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=coord_headers,
    )
    assert frozen_challenges_response.status_code == 200
    frozen_challenges = frozen_challenges_response.json()["items"]
    assert len(frozen_challenges) == 1
    frozen_challenge = frozen_challenges[0]

    assert frozen_challenge["title"]["de"] == "Original Challenge Title"
    assert frozen_challenge["instructions"]["de"] == "Original Instructions"


# =============================================================================
# State Machine — All Valid Transitions
# =============================================================================


@pytest.mark.asyncio
async def test_valid_state_transitions_keep_started_at_across_a_resume(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A run follows the valid transitions and keeps its started_at across a resume."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@statetrans.test",
        role=UserRole.COORDINATOR,
        org_slug="statetrans-org",
        org_name="State Transitions Org",
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "State Test", "en": "State Test"},
        description={"de": "Test", "en": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1", "en": "Station 1"},
        description={"de": "Test", "en": "Test"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_2 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1", "en": "Challenge 1"},
        instructions={"de": "Test", "en": "Test"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_2.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "State Transitions Test"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    assert create_response.json()["state"] == "configured"

    # configured → active
    start_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=coord_headers,
    )
    assert start_response.status_code == 200
    start_data = start_response.json()
    assert start_data["state"] == "active"
    assert start_data["started_at"] is not None
    original_started_at = start_data["started_at"]

    # active → paused
    pause_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/pause",
        headers=coord_headers,
    )
    assert pause_response.status_code == 200
    pause_data = pause_response.json()
    assert pause_data["state"] == "paused"
    assert pause_data["paused_at"] is not None

    # paused → active (resume)
    resume_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/resume",
        headers=coord_headers,
    )
    assert resume_response.status_code == 200
    resume_data = resume_response.json()
    assert resume_data["state"] == "active"
    # A resume keeps the original started_at
    assert resume_data["started_at"] == original_started_at

    # active → ended
    end_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/end",
        headers=coord_headers,
    )
    assert end_response.status_code == 200
    end_data = end_response.json()
    assert end_data["state"] == "ended"
    assert end_data["ended_at"] is not None

    started_at = datetime.fromisoformat(end_data["started_at"].replace("Z", "+00:00"))
    ended_at = datetime.fromisoformat(end_data["ended_at"].replace("Z", "+00:00"))
    assert ended_at > started_at

    # paused → ended, on a second run
    create_response2 = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "State Test 2"},
    )
    assert create_response2.status_code == 201
    run_id2 = create_response2.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id2}/start", headers=coord_headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id2}/pause", headers=coord_headers)

    end_response2 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id2}/end",
        headers=coord_headers,
    )
    assert end_response2.status_code == 200
    assert end_response2.json()["state"] == "ended"


# =============================================================================
# State Machine — All Invalid Transitions (Parameterized)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_state,action,expected_status",
    [
        # From CONFIGURED
        ("configured", "pause", 422),
        ("configured", "end", 422),
        # From ACTIVE
        ("active", "start", 422),
        ("active", "schedule", 422),
        # From PAUSED
        ("paused", "start", 422),
        ("paused", "pause", 422),
        ("paused", "schedule", 422),
        # From ENDED (terminal state)
        ("ended", "start", 422),
        ("ended", "pause", 422),
        ("ended", "end", 422),
        ("ended", "schedule", 422),
        # From SCHEDULED
        ("scheduled", "pause", 422),
        ("scheduled", "end", 422),
    ],
)
async def test_invalid_state_transitions_answer_422(
    client: AsyncClient,
    db_session: AsyncSession,
    initial_state: str,
    action: str,
    expected_status: int,
) -> None:
    """Each invalid transition answers 422."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email=f"coord-{initial_state}-{action}@invalid.test",
        role=UserRole.COORDINATOR,
        org_slug=f"invalid-{initial_state}-{action}",
        org_name=f"Invalid {initial_state} {action}",
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Invalid Test", "en": "Invalid Test"},
        description={"de": "Test", "en": "Test"},
        is_published=True,
    )
    await db_session.commit()

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Invalid Transition Test"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    if initial_state == "active":
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)
    elif initial_state == "paused":
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=coord_headers)
    elif initial_state == "ended":
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=coord_headers)
    elif initial_state == "scheduled":
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/schedule",
            headers=coord_headers,
            json={"scheduled_for": "2026-06-15T09:00:00Z"},
        )
    # configured is the default state, no action needed

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/{action}",
        headers=coord_headers,
    )

    assert response.status_code == expected_status


# =============================================================================
# Scheduling a Run
# =============================================================================


@pytest.mark.asyncio
async def test_scheduled_run_reports_scheduled_for_and_can_be_started(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A scheduled run reports its scheduled_for and can be started."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@schedule.test",
        role=UserRole.COORDINATOR,
        org_slug="schedule-org",
        org_name="Schedule Test Org",
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Schedule Test", "en": "Schedule Test"},
        description={"de": "Test", "en": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1", "en": "Station 1"},
        description={"de": "Test", "en": "Test"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_3 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1", "en": "Challenge 1"},
        instructions={"de": "Test", "en": "Test"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_3.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Scheduled Run Test"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    assert create_response.json()["state"] == "configured"

    future_time = "2026-06-15T09:00:00Z"
    schedule_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/schedule",
        headers=coord_headers,
        json={"scheduled_for": future_time},
    )
    assert schedule_response.status_code == 200
    schedule_data = schedule_response.json()

    assert schedule_data["state"] == "scheduled"
    assert schedule_data["scheduled_for"] is not None
    # Verify timestamp matches (accounting for possible timezone differences)
    assert future_time in schedule_data["scheduled_for"] or "2026-06-15T09:00:00" in schedule_data["scheduled_for"]

    # A scheduled run can be started. In production an automated scheduler calls /start at the scheduled_for time.
    start_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=coord_headers,
    )
    assert start_response.status_code == 200
    start_data = start_response.json()
    assert start_data["state"] == "active"
    assert start_data["started_at"] is not None


# =============================================================================
# Coordinator Monitors Live Run
# =============================================================================


@pytest.mark.asyncio
async def test_coordinator_sees_teams_submissions_and_feed_of_a_live_run(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The coordinator sees the teams with their member counts, all submissions with pagination, and the feed."""
    # TEXT-only challenges keep the submission validation simple
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="monitor-org",
        num_stations=3,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )

    coordinator_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Monitoring Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    run_data = await activate_run(client, env.slug, run_id, coordinator_headers)
    assert run_data["state"] == "active"

    stations_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coordinator_headers,
    )
    run_stations = stations_resp.json()["items"]
    assert len(run_stations) == 3

    challenges_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[0]['id']}/challenges/",
        headers=coordinator_headers,
    )
    run_challenges = challenges_resp.json()["items"]
    assert len(run_challenges) == 2
    run_challenge_id = run_challenges[0]["id"]

    team_alpha = await create_team_and_join(
        client, env.slug, run_id, env.auth_headers["participant1"], "Team Alpha", "#FF0000"
    )
    team_alpha_id = team_alpha["id"]
    assert team_alpha["member_count"] == 1  # Creator has joined

    alpha_member2 = await create_test_user(
        db_session,
        email="alpha2@monitor.test",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    alpha2_headers = get_auth_headers(alpha_member2)
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_alpha_id}/join",
        headers=alpha2_headers,
    )

    alpha_member3 = await create_test_user(
        db_session,
        email="alpha3@monitor.test",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    alpha3_headers = get_auth_headers(alpha_member3)
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_alpha_id}/join",
        headers=alpha3_headers,
    )

    team_beta = await create_team_and_join(
        client, env.slug, run_id, env.auth_headers["participant2"], "Team Beta", "#00FF00"
    )
    team_beta_id = team_beta["id"]
    assert team_beta["member_count"] == 1  # Creator has joined

    beta_member2 = await create_test_user(
        db_session,
        email="beta2@monitor.test",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    beta2_headers = get_auth_headers(beta_member2)
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_beta_id}/join",
        headers=beta2_headers,
    )

    # The coordinator sees both teams with their member counts
    teams_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coordinator_headers,
    )
    assert teams_resp.status_code == 200
    teams_data = teams_resp.json()
    assert "items" in teams_data
    teams = teams_data["items"]
    assert len(teams) == 2

    alpha_team = next(t for t in teams if t["name"] == "Team Alpha")
    beta_team = next(t for t in teams if t["name"] == "Team Beta")
    assert alpha_team["member_count"] == 3
    assert beta_team["member_count"] == 2

    # Team Alpha submits 3 answers, Team Beta 2
    # All challenges are TEXT type due to challenge_type_override
    await submit_challenge(
        client, env.slug, run_id, env.auth_headers["participant1"], run_challenge_id, {"text": "Alpha submission 1"}
    )
    await submit_challenge(
        client,
        env.slug,
        run_id,
        env.auth_headers["participant1"],
        run_challenges[1]["id"],
        {"text": "Alpha submission 2"},
    )

    challenges_resp2 = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[1]['id']}/challenges/",
        headers=coordinator_headers,
    )
    run_challenges2 = challenges_resp2.json()["items"]
    await submit_challenge(
        client,
        env.slug,
        run_id,
        env.auth_headers["participant1"],
        run_challenges2[0]["id"],
        {"text": "Alpha submission 3"},
    )

    await submit_challenge(
        client, env.slug, run_id, env.auth_headers["participant2"], run_challenge_id, {"text": "Beta submission 1"}
    )
    await submit_challenge(
        client,
        env.slug,
        run_id,
        env.auth_headers["participant2"],
        run_challenges[1]["id"],
        {"text": "Beta submission 2"},
    )

    # The coordinator sees the submissions of all teams
    submissions_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=coordinator_headers,
    )
    assert submissions_resp.status_code == 200
    submissions_data = submissions_resp.json()
    assert "items" in submissions_data
    submissions = submissions_data["items"]
    assert len(submissions) == 5  # 3 from Alpha, 2 from Beta
    assert submissions_data["total"] == 5

    # Pagination
    page1_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/?skip=0&limit=2",
        headers=coordinator_headers,
    )
    assert page1_resp.status_code == 200
    page1_data = page1_resp.json()
    assert len(page1_data["items"]) == 2
    assert page1_data["total"] == 5

    page2_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/?skip=2&limit=2",
        headers=coordinator_headers,
    )
    assert page2_resp.status_code == 200
    page2_data = page2_resp.json()
    assert len(page2_data["items"]) == 2
    assert page2_data["total"] == 5

    page3_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/?skip=4&limit=2",
        headers=coordinator_headers,
    )
    assert page3_resp.status_code == 200
    page3_data = page3_resp.json()
    assert len(page3_data["items"]) == 1
    assert page3_data["total"] == 5

    # The coordinator sees the feed
    feed_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=coordinator_headers,
    )
    assert feed_resp.status_code == 200
    feed_data = feed_resp.json()
    assert "items" in feed_data
    # Feed should contain system events (run started, teams joined, submissions)


# =============================================================================
# Full Scoring Workflow
# =============================================================================


@pytest.mark.asyncio
async def test_coordinator_creates_and_updates_a_score(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    A coordinator creates and updates a score.

    A second score, too many points, negative points and a submission of another run are refused.
    """
    # TEXT-only challenges keep the submission validation simple
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="scoring-org",
        num_stations=1,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )

    coordinator_headers = env.auth_headers["coordinator"]
    participant_headers = env.auth_headers["participant1"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Scoring Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    await activate_run(client, env.slug, run_id, coordinator_headers)

    stations_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coordinator_headers,
    )
    run_station_id = stations_resp.json()["items"][0]["id"]

    challenges_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=coordinator_headers,
    )
    run_challenges = challenges_resp.json()["items"]
    run_challenge_id = run_challenges[0]["id"]

    team = await create_team_and_join(client, env.slug, run_id, participant_headers, "Team Scoring", "#FF0000")
    team_id = team["id"]

    # Join the team explicitly (team creation doesn't auto-join)
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "Good answer"}
    )
    submission_id = submission["id"]

    # The coordinator scores the submission
    score_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={
            "submission_id": submission_id,
            "points": 8,
            "comment": "Gut gemacht!",
        },
    )
    assert score_response.status_code == 201
    score_data = score_response.json()

    assert score_data["points"] == 8
    assert score_data["max_points"] == 10
    assert score_data["comment"] == "Gut gemacht!"
    assert score_data["scored_by"] == str(env.users["coordinator"].id)
    assert score_data["scored_at"] is not None
    score_id = score_data["id"]

    scores_list_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
    )
    assert scores_list_resp.status_code == 200
    scores_list = scores_list_resp.json()["items"]
    assert len(scores_list) >= 1
    assert any(s["id"] == score_id for s in scores_list)

    update_response = await client.put(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/{score_id}",
        headers=coordinator_headers,
        json={
            "points": 9,
            "comment": "Nachkorrektur: bessere Antwort als gedacht",
        },
    )
    assert update_response.status_code == 200
    updated_score = update_response.json()

    assert updated_score["points"] == 9
    assert updated_score["comment"] == "Nachkorrektur: bessere Antwort als gedacht"

    # A second score for the same submission is refused
    duplicate_score_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={
            "submission_id": submission_id,
            "points": 7,
        },
    )
    assert duplicate_score_resp.status_code == 422

    # Points above max_points are refused
    submission2 = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenges[1]["id"], {"text": "Another good answer"}
    )

    exceed_points_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={
            "submission_id": submission2["id"],
            "points": 25,  # max_points is 20
        },
    )
    assert exceed_points_resp.status_code == 422

    # Negative points are refused
    negative_points_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={
            "submission_id": submission2["id"],
            "points": -1,
        },
    )
    assert negative_points_resp.status_code == 422

    # A submission of another run cannot be scored (submission2 belongs to the first run)
    create_response2 = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Different Run"},
    )
    run2_id = create_response2.json()["id"]
    await activate_run(client, env.slug, run2_id, coordinator_headers)

    wrong_run_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run2_id}/scores/",
        headers=coordinator_headers,
        json={
            "submission_id": submission2["id"],
            "points": 10,
        },
    )
    assert wrong_run_resp.status_code == 422


# =============================================================================
# Scoreboard Verification
# =============================================================================


@pytest.mark.asyncio
async def test_scoreboard_ranks_teams_by_total_points(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    The scoreboard ranks teams by total points.

    Setup: Team Alpha has 8/10 + 18/20 = 26, Team Beta has 5/10 = 5, Team Gamma has 10/10 + 15/15 = 25.
    """
    # TEXT-only challenges keep the submission validation simple
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="scoreboard-org",
        num_stations=2,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )

    coordinator_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Scoreboard Test Run"},
    )
    run_id = create_response.json()["id"]
    await activate_run(client, env.slug, run_id, coordinator_headers)

    stations_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coordinator_headers,
    )
    run_stations = stations_resp.json()["items"]

    challenges1_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[0]['id']}/challenges/",
        headers=coordinator_headers,
    )
    challenges1 = challenges1_resp.json()["items"]

    challenges2_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[1]['id']}/challenges/",
        headers=coordinator_headers,
    )
    challenges2 = challenges2_resp.json()["items"]

    alpha_user = await create_test_user(
        db_session, email="alpha@scoreboard.test", role=UserRole.PARTICIPANT, organization=env.org
    )
    alpha_headers = get_auth_headers(alpha_user)
    team_alpha = await create_team_and_join(client, env.slug, run_id, alpha_headers, "Team Alpha", "#FF0000")
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_alpha['id']}/join",
        headers=alpha_headers,
    )

    beta_user = await create_test_user(
        db_session, email="beta@scoreboard.test", role=UserRole.PARTICIPANT, organization=env.org
    )
    beta_headers = get_auth_headers(beta_user)
    team_beta = await create_team_and_join(client, env.slug, run_id, beta_headers, "Team Beta", "#00FF00")
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_beta['id']}/join",
        headers=beta_headers,
    )

    gamma_user = await create_test_user(
        db_session, email="gamma@scoreboard.test", role=UserRole.PARTICIPANT, organization=env.org
    )
    gamma_headers = get_auth_headers(gamma_user)
    team_gamma = await create_team_and_join(client, env.slug, run_id, gamma_headers, "Team Gamma", "#0000FF")
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_gamma['id']}/join",
        headers=gamma_headers,
    )

    # Team Alpha submissions (max_points: 10 + 20 = 30 total)
    alpha_sub1 = await submit_challenge(
        client, env.slug, run_id, alpha_headers, challenges1[0]["id"], {"text": "Alpha answer 1"}
    )
    alpha_sub2 = await submit_challenge(
        client, env.slug, run_id, alpha_headers, challenges1[1]["id"], {"text": "Alpha answer 2"}
    )

    # Team Beta submission (max_points: 10)
    beta_sub1 = await submit_challenge(
        client, env.slug, run_id, beta_headers, challenges1[0]["id"], {"text": "Beta answer 1"}
    )

    # Team Gamma submissions (max_points: 10 + 15 = 25 total)
    gamma_sub1 = await submit_challenge(
        client, env.slug, run_id, gamma_headers, challenges1[0]["id"], {"text": "Gamma answer 1"}
    )
    gamma_sub2 = await submit_challenge(
        client, env.slug, run_id, gamma_headers, challenges2[0]["id"], {"text": "Gamma answer 2"}
    )

    # Team Alpha: 8/10 + 18/20 = 26 total
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={"submission_id": alpha_sub1["id"], "points": 8},
    )
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={"submission_id": alpha_sub2["id"], "points": 18},
    )

    # Team Beta: 5/10 = 5 total
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={"submission_id": beta_sub1["id"], "points": 5},
    )

    # Team Gamma: 10/10 + 15/15 = 25 total
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={"submission_id": gamma_sub1["id"], "points": 10},
    )
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={"submission_id": gamma_sub2["id"], "points": 15},
    )

    scoreboard_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=coordinator_headers,
    )
    assert scoreboard_resp.status_code == 200
    scoreboard_data = scoreboard_resp.json()
    assert "entries" in scoreboard_data

    entries = scoreboard_data["entries"]
    assert len(entries) == 3

    # Sorted by total_points, highest first
    assert entries[0]["team_name"] == "Team Alpha"
    assert entries[0]["total_points"] == 26
    assert entries[0]["total_max_points"] == 30
    assert entries[0]["scored_count"] == 2
    assert entries[0]["rank"] == 1

    assert entries[1]["team_name"] == "Team Gamma"
    assert entries[1]["total_points"] == 25
    assert entries[1]["total_max_points"] == 25
    assert entries[1]["scored_count"] == 2
    assert entries[1]["rank"] == 2

    assert entries[2]["team_name"] == "Team Beta"
    assert entries[2]["total_points"] == 5
    assert entries[2]["total_max_points"] == 10
    assert entries[2]["scored_count"] == 1
    assert entries[2]["rank"] == 3

    for entry in entries:
        assert "team_id" in entry
        assert "team_name" in entry
        assert "total_points" in entry
        assert "total_max_points" in entry
        assert "scored_count" in entry
        assert "rank" in entry


# =============================================================================
# Feed Event Management
# =============================================================================


@pytest.mark.asyncio
async def test_announcement_shows_in_the_feed_ordered_by_created_at(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A coordinator announcement shows in the feed for the coordinator and a participant, ordered by created_at."""
    env = await setup_journey_environment(
        db_session, client, org_slug="feed-org", num_stations=1, challenges_per_station=1
    )

    coordinator_headers = env.auth_headers["coordinator"]
    participant_headers = env.auth_headers["participant1"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Feed Test Run"},
    )
    run_id = create_response.json()["id"]
    await activate_run(client, env.slug, run_id, coordinator_headers)

    announcement_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=coordinator_headers,
        json={
            "event_type": "announcement",
            "content": {
                "de": "Mittagspause bis 13:00 Uhr!",
                "en": "Lunch break until 1:00 PM!",
            },
        },
    )
    assert announcement_response.status_code == 201
    announcement_data = announcement_response.json()

    assert announcement_data["event_type"] == "announcement"
    assert announcement_data["author_id"] == str(env.users["coordinator"].id)
    assert announcement_data["created_at"] is not None
    assert announcement_data["content"]["de"] == "Mittagspause bis 13:00 Uhr!"
    assert announcement_data["content"]["en"] == "Lunch break until 1:00 PM!"

    feed_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=coordinator_headers,
    )
    assert feed_response.status_code == 200
    feed_data = feed_response.json()
    assert "items" in feed_data
    feed_items = feed_data["items"]

    announcement_in_feed = next(
        (item for item in feed_items if item["event_type"] == "announcement"),
        None,
    )
    assert announcement_in_feed is not None
    assert announcement_in_feed["content"]["de"] == "Mittagspause bis 13:00 Uhr!"

    # A participant sees the announcement too
    participant_feed_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=participant_headers,
    )
    assert participant_feed_resp.status_code == 200
    participant_feed_data = participant_feed_resp.json()
    participant_feed_items = participant_feed_data["items"]

    participant_announcement = next(
        (item for item in participant_feed_items if item["event_type"] == "announcement"),
        None,
    )
    assert participant_announcement is not None
    assert participant_announcement["content"]["de"] == "Mittagspause bis 13:00 Uhr!"

    # Announcements come in the order of created_at
    announcement2_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=coordinator_headers,
        json={
            "event_type": "announcement",
            "content": {
                "de": "Noch 30 Minuten!",
                "en": "30 minutes remaining!",
            },
        },
    )
    assert announcement2_response.status_code == 201

    feed_response2 = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=coordinator_headers,
    )
    feed_items2 = feed_response2.json()["items"]

    announcements = [item for item in feed_items2 if item["event_type"] == "announcement"]
    if len(announcements) >= 2:
        first_created = datetime.fromisoformat(announcements[0]["created_at"].replace("Z", "+00:00"))
        second_created = datetime.fromisoformat(announcements[1]["created_at"].replace("Z", "+00:00"))
        assert first_created <= second_created


# =============================================================================
# Pause/Resume Cycle with Impact Verification
# =============================================================================


@pytest.mark.asyncio
async def test_paused_run_refuses_submissions_teams_joins_and_chat_but_takes_announcements(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """
    A paused run refuses submissions, new teams, joins and chat, but accepts announcements.

    A resume restores submissions and keeps started_at.
    """
    # TEXT-only challenges keep the submission validation simple
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="pause-org",
        num_stations=2,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )

    coordinator_headers = env.auth_headers["coordinator"]
    participant_headers = env.auth_headers["participant1"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Pause Test Run"},
    )
    run_id = create_response.json()["id"]
    run_data = await activate_run(client, env.slug, run_id, coordinator_headers)
    original_started_at = run_data["started_at"]

    stations_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coordinator_headers,
    )
    run_stations = stations_resp.json()["items"]

    challenges_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[0]['id']}/challenges/",
        headers=coordinator_headers,
    )
    run_challenges = challenges_resp.json()["items"]

    team = await create_team_and_join(client, env.slug, run_id, participant_headers, "Team Pause", "#FF0000")
    team_id = team["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )

    await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenges[0]["id"], {"text": "First submission"}
    )

    pause_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/pause",
        headers=coordinator_headers,
    )
    assert pause_response.status_code == 200
    pause_data = pause_response.json()
    assert pause_data["state"] == "paused"
    assert pause_data["paused_at"] is not None

    # While paused, the team can neither submit, create a team nor join one
    submit_while_paused_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": run_challenges[1]["id"],
            "content": {"text": "Submission while paused"},
        },
    )
    assert submit_while_paused_resp.status_code in [400, 422]

    new_participant = await create_test_user(
        db_session, email="newuser@pause.test", role=UserRole.PARTICIPANT, organization=env.org
    )
    new_participant_headers = get_auth_headers(new_participant)

    create_team_paused_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=new_participant_headers,
        json={"name": "Team Should Fail", "color": "#00FF00"},
    )
    assert create_team_paused_resp.status_code in [400, 422]

    join_team_paused_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=new_participant_headers,
    )
    assert join_team_paused_resp.status_code in [400, 422]

    # The coordinator can still post to the feed
    announcement_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=coordinator_headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Pause angekündigt", "en": "Pause announced"},
        },
    )
    assert announcement_resp.status_code == 201

    # Participant chat is blocked while the run is paused.
    # A pause halts participant interaction, like submissions and uploads.
    chat_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=participant_headers,
        json={"content": "Wann geht es weiter?"},
    )
    assert chat_resp.status_code in [400, 422]

    # After the resume the run is active again
    resume_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/resume",
        headers=coordinator_headers,
    )
    assert resume_response.status_code == 200
    resume_data = resume_response.json()
    assert resume_data["state"] == "active"

    assert resume_data["started_at"] == original_started_at

    submit_after_resume_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": run_challenges[1]["id"],
            "content": {"text": "Submission after resume"},
        },
    )
    assert submit_after_resume_resp.status_code == 201


# =============================================================================
# End Run and Post-Run State
# =============================================================================


@pytest.mark.asyncio
async def test_ended_run_refuses_submissions_new_teams_and_joins(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    An ended run refuses submissions, new teams and joins.

    Scoreboard, submissions and feed stay readable, and feedback is still accepted.
    """
    # TEXT-only challenges keep the submission validation simple
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="endrun-org",
        num_stations=1,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )

    coordinator_headers = env.auth_headers["coordinator"]
    participant_headers = env.auth_headers["participant1"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json={"rallye_id": str(env.rallye_id), "name": "End Run Test"},
    )
    run_id = create_response.json()["id"]
    await activate_run(client, env.slug, run_id, coordinator_headers)

    stations_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=coordinator_headers,
    )
    run_station_id = stations_resp.json()["items"][0]["id"]

    challenges_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=coordinator_headers,
    )
    run_challenges = challenges_resp.json()["items"]

    team = await create_team_and_join(client, env.slug, run_id, participant_headers, "Team End", "#FF0000")
    team_id = team["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=participant_headers,
    )

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenges[0]["id"], {"text": "Final submission"}
    )

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={"submission_id": submission["id"], "points": 8},
    )

    end_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=coordinator_headers,
    )
    assert end_response.status_code == 200
    end_data = end_response.json()
    assert end_data["state"] == "ended"

    assert end_data["ended_at"] is not None
    ended_at = datetime.fromisoformat(end_data["ended_at"].replace("Z", "+00:00"))
    started_at = datetime.fromisoformat(end_data["started_at"].replace("Z", "+00:00"))
    assert ended_at > started_at

    submit_ended_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={
            "run_challenge_id": run_challenges[1]["id"],
            "content": {"text": "Submission after ended"},
        },
    )
    assert submit_ended_resp.status_code in [400, 422]

    new_participant = await create_test_user(
        db_session, email="newuser@endrun.test", role=UserRole.PARTICIPANT, organization=env.org
    )
    new_participant_headers = get_auth_headers(new_participant)

    create_team_ended_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=new_participant_headers,
        json={"name": "Team After End", "color": "#00FF00"},
    )
    assert create_team_ended_resp.status_code in [400, 422]

    join_team_ended_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=new_participant_headers,
    )
    assert join_team_ended_resp.status_code in [400, 422]

    scoreboard_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=coordinator_headers,
    )
    assert scoreboard_resp.status_code == 200

    submissions_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=coordinator_headers,
    )
    assert submissions_resp.status_code == 200

    feed_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=coordinator_headers,
    )
    assert feed_resp.status_code == 200

    # Feedback is allowed after the end
    feedback_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=participant_headers,
        json={"rating": 5, "comment": "Great experience!"},
    )
    assert feedback_resp.status_code == 201

    feedback_list_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=coordinator_headers,
    )
    assert feedback_list_resp.status_code == 200


# =============================================================================
# Run Update Restrictions by State
# =============================================================================


@pytest.mark.asyncio
async def test_run_update_restrictions_by_state(client: AsyncClient, db_session: AsyncSession) -> None:
    """A configured run takes an update. An active, a paused and an ended run refuse it with 422 and keep the name."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@updaterestrict.test",
        role=UserRole.COORDINATOR,
        org_slug="updaterestrict-org",
        org_name="Update Restriction Org",
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Update Test", "en": "Update Test"},
        description={"de": "Test", "en": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1", "en": "Station 1"},
        description={"de": "Test", "en": "Test"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_4 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1", "en": "Challenge 1"},
        instructions={"de": "Test", "en": "Test"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_4.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Original Name"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]

    update_configured_resp = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=coord_headers,
        json={"name": "Updated in Configured", "settings": {}},
    )
    assert update_configured_resp.status_code == 200

    assert (await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)).status_code == 200

    update_active_resp = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=coord_headers,
        json={"name": "Updated in Active", "settings": {}},
    )
    assert update_active_resp.status_code == 422

    assert (await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=coord_headers)).status_code == 200

    update_paused_resp = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=coord_headers,
        json={"name": "Updated in Paused", "settings": {}},
    )
    assert update_paused_resp.status_code == 422

    assert (await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=coord_headers)).status_code == 200

    update_ended_resp = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=coord_headers,
        json={"name": "Updated in Ended", "settings": {}},
    )
    assert update_ended_resp.status_code == 422

    run_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}", headers=coord_headers)
    assert run_resp.status_code == 200
    assert run_resp.json()["name"] == "Updated in Configured"


# =============================================================================
# Run Deletion Restrictions by State
# =============================================================================


@pytest.mark.asyncio
async def test_run_deletion_restrictions_by_state(client: AsyncClient, db_session: AsyncSession) -> None:
    """A configured run can be deleted. An active and an ended run cannot."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@deleterestrict.test",
        role=UserRole.COORDINATOR,
        org_slug="deleterestrict-org",
        org_name="Delete Restriction Org",
    )
    coord_headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Delete Test", "en": "Delete Test"},
        description={"de": "Test", "en": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1", "en": "Station 1"},
        description={"de": "Test", "en": "Test"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_5 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1", "en": "Challenge 1"},
        instructions={"de": "Test", "en": "Test"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_5.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    create_response1 = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Run to Delete in Configured"},
    )
    assert create_response1.status_code == 201
    run_id1 = create_response1.json()["id"]

    delete_configured_resp = await client.delete(
        f"/api/orgs/{org.slug}/runs/{run_id1}",
        headers=coord_headers,
    )
    assert delete_configured_resp.status_code == 204

    create_response2 = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Run to Delete in Active"},
    )
    run_id2 = create_response2.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id2}/start", headers=coord_headers)

    delete_active_resp = await client.delete(
        f"/api/orgs/{org.slug}/runs/{run_id2}",
        headers=coord_headers,
    )
    assert delete_active_resp.status_code in [400, 422]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id2}/end", headers=coord_headers)

    # An ended run holds historical data, so it cannot be deleted.
    delete_ended_resp = await client.delete(
        f"/api/orgs/{org.slug}/runs/{run_id2}",
        headers=coord_headers,
    )
    assert delete_ended_resp.status_code in [400, 422]
