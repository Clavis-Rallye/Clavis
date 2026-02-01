"""
Integration tests for feedback endpoints.

Tests cover:
- Submit feedback (POST /) returns 201 with no user_id in response
- Feedback only allowed after run ended (return 400 when run active)
- List feedback for run (coordinator+)
- Feedback summary returns correct average and distribution
- Rating must be between 1 and 5
- Cross-tenant isolation
"""

from uuid import UUID

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


@pytest.mark.asyncio
async def test_submit_feedback_success(client: AsyncClient, db_session: AsyncSession):
    """Anonymous feedback is accepted once the run ended, and the response carries no user_id."""
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

    end_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)
    assert end_response.status_code == 200

    # Submit feedback (no auth required — anonymous)
    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={
            "rating": 5,
            "comment": "Great experience!",
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert UUID(data["id"])
    assert data["run_id"] == run_id
    assert data["rating"] == 5
    assert data["comment"] == "Great experience!"
    assert "created_at" in data
    # CRITICAL: no user_id in response (anonymous)
    assert "user_id" not in data


@pytest.mark.asyncio
async def test_submit_feedback_no_comment(client: AsyncClient, db_session: AsyncSession):
    """Feedback may be submitted without a comment, which is stored as None."""
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
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 3},
    )

    assert response.status_code == 201
    data = response.json()

    assert data["rating"] == 3
    assert data["comment"] is None


@pytest.mark.asyncio
async def test_submit_feedback_run_not_ended(client: AsyncClient, db_session: AsyncSession):
    """Feedback on an active run is refused with 400, and the message says the run must have ended."""
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

    # Start run (but do NOT end it)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={
            "rating": 5,
            "comment": "Premature feedback",
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "ended" in detail.lower()
    assert "active" in detail.lower()


@pytest.mark.asyncio
async def test_submit_feedback_run_configured(client: AsyncClient, db_session: AsyncSession):
    """Feedback on a configured run, one that has not started, is refused with 400."""
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

    # Create run (but do NOT start it)
    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={
            "rating": 4,
            "comment": "Too early",
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "ended" in detail.lower()


@pytest.mark.asyncio
async def test_submit_feedback_rating_below_min(client: AsyncClient, db_session: AsyncSession):
    """A rating below 1 is refused with 422."""
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

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={
            "rating": 0,
            "comment": "Invalid rating",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_feedback_rating_above_max(client: AsyncClient, db_session: AsyncSession):
    """A rating above 5 is refused with 422."""
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
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={
            "rating": 6,
            "comment": "Invalid rating",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_feedback_success(client: AsyncClient, db_session: AsyncSession):
    """A coordinator lists all feedback entries of a run, and no entry carries a user_id."""
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
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 5, "comment": "Excellent!"},
    )

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 4, "comment": "Good"},
    )

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 3, "comment": "Average"},
    )

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 3
    assert len(data["items"]) == 3

    # Verify no user_id in any feedback entry
    for item in data["items"]:
        assert "user_id" not in item
        assert "rating" in item
        assert "comment" in item


@pytest.mark.asyncio
async def test_list_feedback_participant_denied(client: AsyncClient, db_session: AsyncSession):
    """A participant cannot list feedback (403)."""
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
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=coord_headers)

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 5},
    )

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", headers=participant_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_feedback_summary_success(client: AsyncClient, db_session: AsyncSession):
    """The summary gives the total count, the average rating and the count per rating."""
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
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    # Rating 5: 3 entries
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", json={"rating": 5})
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", json={"rating": 5})
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", json={"rating": 5})

    # Rating 4: 2 entries
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", json={"rating": 4})
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", json={"rating": 4})

    # Rating 3: 1 entry
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", json={"rating": 3})

    # Rating 2: 1 entry
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", json={"rating": 2})

    # Rating 1: 0 entries

    # Total: 7 entries, sum = 5*3 + 4*2 + 3*1 + 2*1 = 15 + 8 + 3 + 2 = 28
    # Average = 28 / 7 = 4.0

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/summary", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total_count"] == 7
    assert data["average_rating"] == 4.0

    # The distribution is sparse: only ratings that were submitted.
    # Note: JSON serializes integer keys as strings
    distribution = data["distribution"]
    assert distribution.get("1", 0) == 0  # No rating 1
    assert distribution.get("2", 0) == 1
    assert distribution.get("3", 0) == 1
    assert distribution.get("4", 0) == 2
    assert distribution.get("5", 0) == 3


@pytest.mark.asyncio
async def test_feedback_summary_no_feedback(client: AsyncClient, db_session: AsyncSession):
    """The summary of a run without feedback has a total of 0, an average of 0.0 and a count of 0 for every rating."""
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
    run_id = run_response.json()["id"]

    # Start and end run (but no feedback submitted)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/summary", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total_count"] == 0
    assert data["average_rating"] == 0.0

    # The distribution is empty or all zeros.
    # Note: JSON serializes integer keys as strings
    distribution = data["distribution"]
    assert distribution.get("1", 0) == 0
    assert distribution.get("2", 0) == 0
    assert distribution.get("3", 0) == 0
    assert distribution.get("4", 0) == 0
    assert distribution.get("5", 0) == 0


@pytest.mark.asyncio
async def test_feedback_summary_participant_denied(client: AsyncClient, db_session: AsyncSession):
    """A participant cannot view the feedback summary (403)."""
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
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=coord_headers)

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/summary", headers=participant_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_feedback_cross_tenant_isolation(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404, not 403, when it submits feedback to or lists the feedback of org B's run, which has feedback."""
    user_a, org_a = await create_test_user_with_org(
        db_session, email="coord-a@test.com", role=UserRole.COORDINATOR, org_slug="org-a"
    )
    headers_a = get_auth_headers(user_a)

    user_b, org_b = await create_test_user_with_org(
        db_session, email="coord-b@test.com", role=UserRole.COORDINATOR, org_slug="org-b"
    )
    headers_b = get_auth_headers(user_b)

    rallye_repo_b = RallyeRepository(db_session, org_b.id)
    rallye_b = await rallye_repo_b.create(
        title={"de": "Rallye B"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo_b = StationRepository(db_session, org_b.id)
    station_b = await station_repo_b.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs_station_b = RallyeStation(organization_id=org_b.id, rallye_id=rallye_b.id, station_id=station_b.id, sort_order=0)
    db_session.add(rs_station_b)
    await db_session.flush()
    await db_session.flush()

    challenge_repo_b = ChallengeRepository(db_session, org_b.id)
    _challenge_12 = await challenge_repo_b.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_b.id, rallye_station_id=rs_station_b.id, challenge_id=_challenge_12.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response_b = await client.post(
        f"/api/orgs/{org_b.slug}/runs/",
        headers=headers_b,
        json={"rallye_id": str(rallye_b.id), "name": "Run B"},
    )
    assert run_response_b.status_code == 201
    run_id_b = run_response_b.json()["id"]

    assert (await client.post(f"/api/orgs/{org_b.slug}/runs/{run_id_b}/start", headers=headers_b)).status_code == 200
    assert (await client.post(f"/api/orgs/{org_b.slug}/runs/{run_id_b}/end", headers=headers_b)).status_code == 200

    # Submit feedback to org B's run (no auth required, but wrong org slug)
    # Try to submit via org A's slug with org B's run_id - should fail (404)
    response = await client.post(
        f"/api/orgs/{org_a.slug}/runs/{run_id_b}/feedback/",
        json={"rating": 5, "comment": "Cross-tenant attempt"},
    )
    assert response.status_code == 404

    # Submit feedback successfully to org B's run, and org B sees it
    submitted = await client.post(
        f"/api/orgs/{org_b.slug}/runs/{run_id_b}/feedback/",
        json={"rating": 5, "comment": "Legitimate feedback"},
    )
    assert submitted.status_code == 201
    own_list = await client.get(f"/api/orgs/{org_b.slug}/runs/{run_id_b}/feedback/", headers=headers_b)
    assert own_list.status_code == 200
    assert [fb["id"] for fb in own_list.json()["items"]] == [submitted.json()["id"]]

    # User A tries to list feedback from org B's run - should fail (404)
    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_b}/feedback/", headers=headers_a)
    assert response.status_code == 404
    assert "Legitimate feedback" not in response.text


@pytest.mark.asyncio
async def test_anonymous_feedback_is_limited_per_address_and_run(client: AsyncClient, db_session: AsyncSession):
    """
    The anonymous endpoint takes 300 per hour from one address for one run.

    The browser keeps one feedback per device, but a script could send
    any number. The limit stores nothing about the sender beyond the count in
    memory, and another run counts on its own.
    """
    from app.routers.feedback_router import FEEDBACK_LIMIT_PER_HOUR
    from tests.journey_helpers import activate_run, setup_journey_environment

    assert FEEDBACK_LIMIT_PER_HOUR == 300

    env = await setup_journey_environment(db_session, client, org_slug="feedback-limit")
    coordinator = env.auth_headers["coordinator"]
    run_ids = []
    for name in ("Erster Lauf", "Zweiter Lauf"):
        created = await client.post(
            f"/api/orgs/{env.slug}/runs/", headers=coordinator, json={"rallye_id": str(env.rallye_id), "name": name}
        )
        run_id = created.json()["id"]
        await activate_run(client, env.slug, run_id, coordinator)
        assert (await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coordinator)).status_code == 200
        run_ids.append(run_id)

    first_url = f"/api/orgs/{env.slug}/runs/{run_ids[0]}/feedback/"
    for _ in range(FEEDBACK_LIMIT_PER_HOUR):
        assert (await client.post(first_url, json={"rating": 4})).status_code == 201
    assert (await client.post(first_url, json={"rating": 1})).status_code == 429

    second_url = f"/api/orgs/{env.slug}/runs/{run_ids[1]}/feedback/"
    assert (await client.post(second_url, json={"rating": 5})).status_code == 201
