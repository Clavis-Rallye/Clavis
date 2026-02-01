"""
Tests for run state machine and state transitions.

Tests cover:
- RunStateService unit tests (pure domain logic):
  - Valid transitions succeed
  - Invalid transitions raise ValueError
  - Timestamp management (started_at, paused_at, ended_at)
  - Terminal state enforcement (ended cannot transition)
- State transition API integration tests:
  - POST /start creates snapshots and transitions to active
  - POST /pause from active state
  - POST /resume from paused state
  - POST /end from active/paused state
  - Cannot start already-ended run
  - Cannot transition from invalid states
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run import Run, RunState
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.run_station import RunStationRepository
from app.repositories.station import StationRepository
from app.services.run_state import VALID_TRANSITIONS, RunStateService
from tests.factories import create_test_user_with_org, get_auth_headers

# ============================================================================
# Unit Tests - RunStateService (Pure Domain Logic)
# ============================================================================


def test_valid_transitions_configured_to_scheduled():
    """Test transition from configured to scheduled state."""
    run = Run(state=RunState.CONFIGURED, started_at=None, paused_at=None, ended_at=None)
    RunStateService.transition(run, RunState.SCHEDULED)
    assert run.state == RunState.SCHEDULED
    assert run.started_at is None
    assert run.paused_at is None
    assert run.ended_at is None


def test_valid_transitions_configured_to_active():
    """Test transition from configured to active state sets started_at."""
    run = Run(state=RunState.CONFIGURED, started_at=None, paused_at=None, ended_at=None)
    before = datetime.now(UTC)
    RunStateService.transition(run, RunState.ACTIVE)
    after = datetime.now(UTC)

    assert run.state == RunState.ACTIVE
    assert run.started_at is not None
    assert before <= run.started_at <= after
    assert run.paused_at is None
    assert run.ended_at is None


def test_valid_transitions_scheduled_to_active():
    """Test transition from scheduled to active state sets started_at."""
    run = Run(state=RunState.SCHEDULED, started_at=None, paused_at=None, ended_at=None)
    before = datetime.now(UTC)
    RunStateService.transition(run, RunState.ACTIVE)
    after = datetime.now(UTC)

    assert run.state == RunState.ACTIVE
    assert run.started_at is not None
    assert before <= run.started_at <= after
    assert run.paused_at is None
    assert run.ended_at is None


def test_valid_transitions_scheduled_to_configured():
    """Test transition from scheduled back to configured (cancel schedule)."""
    run = Run(state=RunState.SCHEDULED, started_at=None, paused_at=None, ended_at=None)
    RunStateService.transition(run, RunState.CONFIGURED)
    assert run.state == RunState.CONFIGURED
    assert run.started_at is None
    assert run.paused_at is None
    assert run.ended_at is None


def test_valid_transitions_active_to_paused():
    """Test transition from active to paused state sets paused_at."""
    run = Run(
        state=RunState.ACTIVE,
        started_at=datetime.now(UTC),
        paused_at=None,
        ended_at=None,
    )
    before = datetime.now(UTC)
    RunStateService.transition(run, RunState.PAUSED)
    after = datetime.now(UTC)

    assert run.state == RunState.PAUSED
    assert run.paused_at is not None
    assert before <= run.paused_at <= after
    assert run.ended_at is None


def test_valid_transitions_active_to_ended():
    """Test transition from active to ended state sets ended_at."""
    run = Run(
        state=RunState.ACTIVE,
        started_at=datetime.now(UTC),
        paused_at=None,
        ended_at=None,
    )
    before = datetime.now(UTC)
    RunStateService.transition(run, RunState.ENDED)
    after = datetime.now(UTC)

    assert run.state == RunState.ENDED
    assert run.ended_at is not None
    assert before <= run.ended_at <= after


def test_valid_transitions_paused_to_active():
    """Test transition from paused to active clears paused_at and preserves started_at."""
    started_time = datetime.now(UTC)
    paused_time = datetime.now(UTC)
    run = Run(
        state=RunState.PAUSED,
        started_at=started_time,
        paused_at=paused_time,
        ended_at=None,
    )

    RunStateService.transition(run, RunState.ACTIVE)

    assert run.state == RunState.ACTIVE
    assert run.started_at == started_time  # Preserve original started_at
    assert run.paused_at is None  # Cleared on resume
    assert run.ended_at is None


def test_valid_transitions_paused_to_ended():
    """Test transition from paused to ended state sets ended_at."""
    run = Run(
        state=RunState.PAUSED,
        started_at=datetime.now(UTC),
        paused_at=datetime.now(UTC),
        ended_at=None,
    )
    before = datetime.now(UTC)
    RunStateService.transition(run, RunState.ENDED)
    after = datetime.now(UTC)

    assert run.state == RunState.ENDED
    assert run.ended_at is not None
    assert before <= run.ended_at <= after


def test_started_at_only_set_on_first_activation():
    """Test that started_at is only set on first transition to active."""
    run = Run(state=RunState.CONFIGURED, started_at=None, paused_at=None, ended_at=None)

    RunStateService.transition(run, RunState.ACTIVE)
    first_started = run.started_at
    assert first_started is not None

    RunStateService.transition(run, RunState.PAUSED)
    RunStateService.transition(run, RunState.ACTIVE)

    assert run.started_at == first_started


def test_invalid_transition_configured_to_paused():
    """Test invalid transition from configured to paused raises ValueError."""
    run = Run(state=RunState.CONFIGURED, started_at=None, paused_at=None, ended_at=None)
    with pytest.raises(ValueError, match="Invalid state transition from configured to paused"):
        RunStateService.transition(run, RunState.PAUSED)


def test_invalid_transition_configured_to_ended():
    """Test invalid transition from configured to ended raises ValueError."""
    run = Run(state=RunState.CONFIGURED, started_at=None, paused_at=None, ended_at=None)
    with pytest.raises(ValueError, match="Invalid state transition from configured to ended"):
        RunStateService.transition(run, RunState.ENDED)


def test_invalid_transition_scheduled_to_paused():
    """Test invalid transition from scheduled to paused raises ValueError."""
    run = Run(state=RunState.SCHEDULED, started_at=None, paused_at=None, ended_at=None)
    with pytest.raises(ValueError, match="Invalid state transition from scheduled to paused"):
        RunStateService.transition(run, RunState.PAUSED)


def test_invalid_transition_scheduled_to_ended():
    """Test invalid transition from scheduled to ended raises ValueError."""
    run = Run(state=RunState.SCHEDULED, started_at=None, paused_at=None, ended_at=None)
    with pytest.raises(ValueError, match="Invalid state transition from scheduled to ended"):
        RunStateService.transition(run, RunState.ENDED)


def test_invalid_transition_active_to_configured():
    """Test invalid transition from active to configured raises ValueError."""
    run = Run(
        state=RunState.ACTIVE,
        started_at=datetime.now(UTC),
        paused_at=None,
        ended_at=None,
    )
    with pytest.raises(ValueError, match="Invalid state transition from active to configured"):
        RunStateService.transition(run, RunState.CONFIGURED)


def test_invalid_transition_active_to_scheduled():
    """Test invalid transition from active to scheduled raises ValueError."""
    run = Run(
        state=RunState.ACTIVE,
        started_at=datetime.now(UTC),
        paused_at=None,
        ended_at=None,
    )
    with pytest.raises(ValueError, match="Invalid state transition from active to scheduled"):
        RunStateService.transition(run, RunState.SCHEDULED)


def test_invalid_transition_paused_to_configured():
    """Test invalid transition from paused to configured raises ValueError."""
    run = Run(
        state=RunState.PAUSED,
        started_at=datetime.now(UTC),
        paused_at=datetime.now(UTC),
        ended_at=None,
    )
    with pytest.raises(ValueError, match="Invalid state transition from paused to configured"):
        RunStateService.transition(run, RunState.CONFIGURED)


def test_invalid_transition_paused_to_scheduled():
    """Test invalid transition from paused to scheduled raises ValueError."""
    run = Run(
        state=RunState.PAUSED,
        started_at=datetime.now(UTC),
        paused_at=datetime.now(UTC),
        ended_at=None,
    )
    with pytest.raises(ValueError, match="Invalid state transition from paused to scheduled"):
        RunStateService.transition(run, RunState.SCHEDULED)


def test_invalid_transition_ended_is_terminal():
    """Test that ended state is terminal — no transitions allowed."""
    run = Run(
        state=RunState.ENDED,
        started_at=datetime.now(UTC),
        paused_at=None,
        ended_at=datetime.now(UTC),
    )

    for target_state in [
        RunState.CONFIGURED,
        RunState.SCHEDULED,
        RunState.ACTIVE,
        RunState.PAUSED,
    ]:
        with pytest.raises(ValueError, match=f"Invalid state transition from ended to {target_state.value}"):
            RunStateService.transition(run, target_state)


def test_can_transition_returns_true_for_valid():
    """Test can_transition returns True for valid transitions."""
    assert RunStateService.can_transition(RunState.CONFIGURED, RunState.SCHEDULED)
    assert RunStateService.can_transition(RunState.CONFIGURED, RunState.ACTIVE)
    assert RunStateService.can_transition(RunState.SCHEDULED, RunState.ACTIVE)
    assert RunStateService.can_transition(RunState.SCHEDULED, RunState.CONFIGURED)
    assert RunStateService.can_transition(RunState.ACTIVE, RunState.PAUSED)
    assert RunStateService.can_transition(RunState.ACTIVE, RunState.ENDED)
    assert RunStateService.can_transition(RunState.PAUSED, RunState.ACTIVE)
    assert RunStateService.can_transition(RunState.PAUSED, RunState.ENDED)


def test_can_transition_returns_false_for_invalid():
    """Test can_transition returns False for invalid transitions."""
    assert not RunStateService.can_transition(RunState.CONFIGURED, RunState.PAUSED)
    assert not RunStateService.can_transition(RunState.CONFIGURED, RunState.ENDED)
    assert not RunStateService.can_transition(RunState.SCHEDULED, RunState.PAUSED)
    assert not RunStateService.can_transition(RunState.SCHEDULED, RunState.ENDED)
    assert not RunStateService.can_transition(RunState.ACTIVE, RunState.CONFIGURED)
    assert not RunStateService.can_transition(RunState.ACTIVE, RunState.SCHEDULED)
    assert not RunStateService.can_transition(RunState.PAUSED, RunState.CONFIGURED)
    assert not RunStateService.can_transition(RunState.PAUSED, RunState.SCHEDULED)
    # Ended is terminal — no transitions allowed
    assert not RunStateService.can_transition(RunState.ENDED, RunState.CONFIGURED)
    assert not RunStateService.can_transition(RunState.ENDED, RunState.SCHEDULED)
    assert not RunStateService.can_transition(RunState.ENDED, RunState.ACTIVE)
    assert not RunStateService.can_transition(RunState.ENDED, RunState.PAUSED)


def test_valid_transitions_dict_completeness():
    """VALID_TRANSITIONS has an entry for every state, and the ended state has no target."""
    all_states = {RunState.CONFIGURED, RunState.SCHEDULED, RunState.ACTIVE, RunState.PAUSED, RunState.ENDED}
    assert set(VALID_TRANSITIONS.keys()) == all_states

    assert len(VALID_TRANSITIONS[RunState.ENDED]) == 0


# ============================================================================
# API Integration Tests - State Transitions
# ============================================================================


@pytest.mark.asyncio
async def test_start_run_creates_snapshots_and_transitions_to_active(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Starting a configured run makes it active, sets `started_at` and creates the run station snapshots."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test Description"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
        location_lat=48.3705,
        location_lng=10.8978,
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

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert response.status_code == 201
    run_data = response.json()
    run_id = run_data["id"]
    assert run_data["state"] == "configured"
    assert run_data["started_at"] is None

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["state"] == "active"
    assert data["started_at"] is not None
    assert data["ended_at"] is None
    assert data["grading_complete"] is False

    run_station_repo = RunStationRepository(db_session, org.id)
    run_stations = await run_station_repo.list_by_run(UUID(run_id))
    assert len(run_stations) == 1
    assert run_stations[0].title == {"de": "Station 1"}


@pytest.mark.asyncio
async def test_pause_run_from_active_state(client: AsyncClient, db_session: AsyncSession):
    """Pausing an active run sets `paused_at` and keeps `started_at`."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test Description"},
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

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/pause",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["state"] == "paused"
    assert data["paused_at"] is not None
    assert data["started_at"] is not None  # Preserved from start
    assert data["ended_at"] is None


@pytest.mark.asyncio
async def test_resume_run_from_paused_state(client: AsyncClient, db_session: AsyncSession):
    """Resuming a paused run clears `paused_at` and keeps the original `started_at`."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test Description"},
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

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = response.json()["id"]

    start_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=headers,
    )
    original_started_at = start_response.json()["started_at"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/resume",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["state"] == "active"
    assert data["paused_at"] is None  # Cleared on resume
    assert data["started_at"] == original_started_at  # Preserved
    assert data["ended_at"] is None


@pytest.mark.asyncio
async def test_end_run_from_active_state(client: AsyncClient, db_session: AsyncSession):
    """Ending an active run sets `ended_at` and keeps `started_at`."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test Description"},
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

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/end",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["state"] == "ended"
    assert data["ended_at"] is not None
    assert data["started_at"] is not None  # Preserved from start


@pytest.mark.asyncio
async def test_cannot_start_already_ended_run(client: AsyncClient, db_session: AsyncSession):
    """An ended run cannot be started again. The answer is 422."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test Description"},
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

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=headers,
    )
    assert response.status_code == 422
    data = response.json()
    assert "Invalid state transition" in data["detail"]
    assert "ended" in data["detail"]


@pytest.mark.asyncio
async def test_cannot_pause_configured_run(client: AsyncClient, db_session: AsyncSession):
    """A configured run cannot be paused. The answer is 422."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test Description"},
        is_published=True,
    )
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = response.json()["id"]

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/pause",
        headers=headers,
    )
    assert response.status_code == 422
    data = response.json()
    assert "Invalid state transition" in data["detail"]
    assert "configured" in data["detail"]


@pytest.mark.asyncio
async def test_cannot_resume_active_run(client: AsyncClient, db_session: AsyncSession):
    """An active run cannot be resumed. The answer is 422."""
    user, org = await create_test_user_with_org(
        db_session,
        email="coord@example.com",
        role=UserRole.COORDINATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test Description"},
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

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/resume",
        headers=headers,
    )
    assert response.status_code == 422
    data = response.json()
    assert "Invalid state transition" in data["detail"]
    assert "active" in data["detail"]
