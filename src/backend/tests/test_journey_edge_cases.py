"""
Journey tests of edge cases and error conditions.

The scenarios cover:
- empty runs (no teams, no submissions)
- runs with no challenges
- Unicode and special characters in all text fields
- pagination boundaries
- join code properties
- scoring boundaries
- feedback constraints
- the max_teams setting
- one user in several teams
- submissions for a challenge that does not exist
"""

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
from tests.factories import (
    create_test_user,
    create_test_user_with_org,
    get_auth_headers,
)

# ============================================================================
# Empty Run (No Teams, Immediate End)
# ============================================================================


@pytest.mark.asyncio
async def test_empty_run_ends_at_once(client: AsyncClient, db_session: AsyncSession):
    """An empty run ends at once, has an empty scoreboard and still takes anonymous feedback."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="empty-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Test"},
        location_lat=48.3705,
        location_lng=10.8978,
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
        json={"rallye_id": str(rallye.id), "name": "Empty Run"},
    )
    assert response.status_code == 201
    run_id = response.json()["id"]

    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200
    assert start_response.json()["state"] == "active"

    # Immediately end the run: no teams, no submissions
    end_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)
    assert end_response.status_code == 200
    assert end_response.json()["state"] == "ended"
    assert end_response.json()["ended_at"] is not None

    # The scoreboard of an empty run has no entries
    scoreboard_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)
    assert scoreboard_response.status_code == 200
    data = scoreboard_response.json()
    assert data["entries"] == []
    assert data["run_id"] == run_id

    # Feedback needs no team membership and carries no user_id
    feedback_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 4, "comment": "Run ended too quickly"},
    )
    assert feedback_response.status_code == 201
    assert "user_id" not in feedback_response.json()


# ============================================================================
# Run With No Challenges
# ============================================================================


@pytest.mark.asyncio
async def test_run_with_no_challenges(client: AsyncClient, db_session: AsyncSession):
    """
    A run whose stations have no challenges starts and lists empty challenge lists.

    It still pauses, resumes and ends.
    """
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="no-challenge-org",
    )
    headers = get_auth_headers(coordinator)

    # Create rallye with 2 stations but NO challenges
    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Rallye Ohne Challenges"},
        description={"de": "Keine Challenges"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    _station_1 = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Erste Station"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs__station_1 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=_station_1.id, sort_order=0)
    db_session.add(rs__station_1)
    await db_session.flush()
    _station_2 = await station_repo.create(
        title={"de": "Station 2"},
        description={"de": "Zweite Station"},
        location_lat=48.3713,
        location_lng=10.8976,
    )
    rs__station_2 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=_station_2.id, sort_order=1)
    db_session.add(rs__station_2)
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Run Without Challenges"},
    )
    assert response.status_code == 201
    run_id = response.json()["id"]

    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200
    assert start_response.json()["state"] == "active"

    # The run has its 2 stations
    stations_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    assert stations_response.status_code == 200
    stations_data = stations_response.json()
    assert stations_data["total"] == 2
    assert len(stations_data["items"]) == 2

    # Each station has no challenges
    for station in stations_data["items"]:
        challenges_response = await client.get(
            f"/api/orgs/{org.slug}/runs/{run_id}/stations/{station['id']}/challenges/",
            headers=headers,
        )
        assert challenges_response.status_code == 200
        challenges_data = challenges_response.json()
        assert challenges_data["total"] == 0
        assert challenges_data["items"] == []

    # The scoreboard is empty
    scoreboard_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard", headers=headers)
    assert scoreboard_response.status_code == 200
    scoreboard_data = scoreboard_response.json()
    assert scoreboard_data["entries"] == []
    assert scoreboard_data["run_id"] == run_id

    # The run still pauses, resumes and ends
    pause_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=headers)
    assert pause_response.status_code == 200
    assert pause_response.json()["state"] == "paused"

    resume_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/resume", headers=headers)
    assert resume_response.status_code == 200
    assert resume_response.json()["state"] == "active"

    end_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)
    assert end_response.status_code == 200
    assert end_response.json()["state"] == "ended"


# ============================================================================
# Unicode and Special Characters
# ============================================================================


@pytest.mark.asyncio
async def test_unicode_team_name_comes_back_unchanged(client: AsyncClient, db_session: AsyncSession):
    """A team name with emoji comes back unchanged."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="unicode-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_4 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
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
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Unicode Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    unicode_name = "Die Münchner Kindl 🏰"
    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": unicode_name, "color": "#FF0000"},
    )
    assert team_response.status_code == 201
    team_data = team_response.json()
    assert team_data["name"] == unicode_name

    get_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_data['id']}",
        headers=p_headers,
    )
    assert get_response.status_code == 200
    assert get_response.json()["name"] == unicode_name


@pytest.mark.asyncio
async def test_unicode_submission_comes_back_unchanged(client: AsyncClient, db_session: AsyncSession):
    """Umlauts, ß and guillemets in a submission come back unchanged."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="unicode-sub-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_5 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
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
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Unicode Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Unicode", "color": "#00FF00"},
    )
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    # Join the team (required before submitting)
    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=p_headers,
    )
    assert join_response.status_code == 204

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=p_headers)
    run_station_id = stations_resp.json()["items"][0]["id"]

    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=p_headers,
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    unicode_text = "Ä Ö Ü ä ö ü ß — «Zitat»"
    submit_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=p_headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": unicode_text}},
    )
    assert submit_response.status_code == 201
    submission_id = submit_response.json()["id"]
    assert submit_response.json()["content"]["text"] == unicode_text

    get_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/{submission_id}",
        headers=p_headers,
    )
    assert get_response.status_code == 200
    assert get_response.json()["content"]["text"] == unicode_text


@pytest.mark.asyncio
async def test_unicode_chat_message_is_stored_unchanged(client: AsyncClient, db_session: AsyncSession):
    """An emoji chat message is stored and listed unchanged."""
    # The coordinator is the single user who creates the team and sends the message
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="unicode-chat-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_6 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
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
        json={"rallye_id": str(rallye.id), "name": "Chat Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    # Create team (coordinator auto-joins as member)
    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Chat", "color": "#0000FF"},
    )
    team_id = team_response.json()["id"]

    # Join the team: chat access is membership-based and coordinators have no standing access to team chats
    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=headers,
    )
    assert join_response.status_code == 204

    emoji_message = "Toll gemacht! 🎉🎊"
    chat_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=headers,
        json={"content": emoji_message},
    )
    assert chat_response.status_code == 201
    assert chat_response.json()["content"] == emoji_message

    get_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=headers,
    )
    assert get_response.status_code == 200
    messages = get_response.json()["items"]
    assert len(messages) >= 1
    assert any(msg["content"] == emoji_message for msg in messages)


@pytest.mark.asyncio
async def test_unicode_feed_announcement_comes_back_unchanged(client: AsyncClient, db_session: AsyncSession):
    """Special characters in a feed announcement come back unchanged."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="unicode-feed-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_7 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_7.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Feed Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    special_text = {
        "de": "Pause — bitte an der Sammelstelle «Rathausplatz» treffen",
        "en": "Break — please meet at the collection point «Town Hall Square»",
    }
    feed_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={"event_type": "announcement", "content": special_text},
    )
    assert feed_response.status_code == 201
    assert feed_response.json()["content"] == special_text

    get_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feed/", headers=headers)
    assert get_response.status_code == 200
    events = get_response.json()["items"]
    assert any(event["content"] == special_text for event in events if event["event_type"] == "announcement")


@pytest.mark.asyncio
async def test_unicode_feedback_comes_back_unchanged_to_the_coordinator(client: AsyncClient, db_session: AsyncSession):
    """Multilingual Unicode in feedback comes back unchanged to the coordinator."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="unicode-feedback-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_8 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_8.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Feedback Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    unicode_comment = "Très bien! Çok güzel! Очень хорошо!"
    feedback_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 5, "comment": unicode_comment},
    )
    assert feedback_response.status_code == 201
    assert feedback_response.json()["comment"] == unicode_comment

    get_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", headers=headers)
    assert get_response.status_code == 200
    feedbacks = get_response.json()["items"]
    assert any(fb["comment"] == unicode_comment for fb in feedbacks)


# ============================================================================
# Pagination Boundaries
# ============================================================================


@pytest.mark.asyncio
async def test_submission_list_pagination_boundaries(client: AsyncClient, db_session: AsyncSession):
    """
    Submission lists page correctly over 25 submissions.

    The test covers pages inside the list, a page past the end, the maximum limit,
    and limit=0 and a negative skip, which answer 422.
    """
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="pagination-org",
    )
    coord_headers = get_auth_headers(coordinator)

    # Create 5 participants (each will submit 5 challenges = 25 total)
    participants = []
    for i in range(5):
        participant = await create_test_user(
            db_session,
            email=f"p{i}@test.com",
            role=UserRole.PARTICIPANT,
            organization=org,
        )
        participants.append(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Pagination Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Test"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    challenges = []
    for i in range(5):
        challenge = await challenge_repo.create(
            challenge_type=ChallengeType.TEXT,
            title={"de": f"Challenge {i + 1}"},
            instructions={"de": "Do"},
            scoring_rules={"max_points": 10},
        )
        db_session.add(
            RallyeStationChallenge(
                organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=challenge.id, sort_order=i
            )
        )
        await db_session.flush()
        challenges.append(challenge)
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Pagination Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=coord_headers)
    run_station_id = stations_resp.json()["items"][0]["id"]

    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=coord_headers,
    )
    run_challenges = challenges_resp.json()["items"]
    assert len(run_challenges) == 5

    # Each participant creates team, joins, and submits to all 5 challenges = 25 total submissions
    for idx, participant in enumerate(participants):
        p_headers = get_auth_headers(participant)

        team_resp = await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
            headers=p_headers,
            json={"name": f"Team {idx + 1}", "color": "#FF0000"},
        )
        team_id = team_resp.json()["id"]

        # Join the team (required before submitting)
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
            headers=p_headers,
        )

        for run_challenge in run_challenges:
            await client.post(
                f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
                headers=p_headers,
                json={
                    "run_challenge_id": run_challenge["id"],
                    "content": {"text": f"Answer from Team {idx + 1}"},
                },
            )

    # skip=0, limit=10 → 10 items, total=25
    response1 = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/?skip=0&limit=10",
        headers=coord_headers,
    )
    assert response1.status_code == 200
    data1 = response1.json()
    assert len(data1["items"]) == 10
    assert data1["total"] == 25

    # skip=10, limit=10 → 10 items
    response2 = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/?skip=10&limit=10",
        headers=coord_headers,
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert len(data2["items"]) == 10
    assert data2["total"] == 25

    # skip=20, limit=10 → 5 items
    response3 = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/?skip=20&limit=10",
        headers=coord_headers,
    )
    assert response3.status_code == 200
    data3 = response3.json()
    assert len(data3["items"]) == 5
    assert data3["total"] == 25

    # skip=25, limit=10 → 0 items (empty list, still total=25)
    response4 = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/?skip=25&limit=10",
        headers=coord_headers,
    )
    assert response4.status_code == 200
    data4 = response4.json()
    assert len(data4["items"]) == 0
    assert data4["total"] == 25

    # skip=0, limit=1000 → 25 items (clamped to max limit, which is 1000)
    response5 = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/?skip=0&limit=1000",
        headers=coord_headers,
    )
    assert response5.status_code == 200
    data5 = response5.json()
    assert len(data5["items"]) == 25
    assert data5["total"] == 25

    # skip=0, limit=0 → 422 (validation error, limit must be >= 1)
    response6 = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/?skip=0&limit=0",
        headers=coord_headers,
    )
    assert response6.status_code == 422

    # skip=-1 → 422 (validation error)
    response7 = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/?skip=-1&limit=10",
        headers=coord_headers,
    )
    assert response7.status_code == 422


# ============================================================================
# Join Code Properties
# ============================================================================


@pytest.mark.asyncio
async def test_join_code_properties(client: AsyncClient, db_session: AsyncSession):
    """
    Fifty join codes are 6 characters, alphanumeric, uppercase and unique.

    None contains an ambiguous character (0, O, I, 1, L).
    """
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="joincode-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_9 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_9.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    join_codes = []
    for i in range(50):
        response = await client.post(
            f"/api/orgs/{org.slug}/runs/",
            headers=headers,
            json={"rallye_id": str(rallye.id), "name": f"Run {i + 1}"},
        )
        assert response.status_code == 201
        data = response.json()
        join_code = data["join_code"]
        join_codes.append(join_code)

        assert len(join_code) == 6, f"Code {join_code} is not 6 characters"
        assert join_code.isalnum(), f"Code {join_code} is not alphanumeric"
        # Use == instead of isupper() to handle all-digit codes
        assert join_code == join_code.upper(), f"Code {join_code} is not uppercase (or contains lowercase)"

        forbidden_chars = {"0", "O", "I", "1", "L"}
        assert not any(char in join_code for char in forbidden_chars), f"Code {join_code} contains forbidden characters"

    assert len(join_codes) == len(set(join_codes)), "Join codes are not unique"


# ============================================================================
# Scoring Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_scoring_zero_points(client: AsyncClient, db_session: AsyncSession):
    """A score of 0 points is valid and is recorded."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="score-zero-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_10 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_10.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Score Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Zero", "color": "#FF0000"},
    )
    team_id = team_resp.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=p_headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=p_headers)
    run_station_id = stations_resp.json()["items"][0]["id"]

    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=p_headers,
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    submit_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=p_headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Wrong answer"}},
    )
    submission_id = submit_resp.json()["id"]

    score_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=coord_headers,
        json={"submission_id": submission_id, "points": 0, "comment": "No points earned"},
    )
    assert score_resp.status_code == 201
    score_data = score_resp.json()
    assert score_data["points"] == 0
    assert score_data["max_points"] == 10
    assert score_data["comment"] == "No points earned"


@pytest.mark.asyncio
async def test_scoring_exact_boundary(client: AsyncClient, db_session: AsyncSession):
    """A score equal to max_points is valid and is recorded."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="score-boundary-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_11 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 15},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_11.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Boundary Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Perfect", "color": "#00FF00"},
    )
    team_id = team_resp.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=p_headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=p_headers)
    run_station_id = stations_resp.json()["items"][0]["id"]

    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=p_headers,
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    submit_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=p_headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Perfect answer"}},
    )
    submission_id = submit_resp.json()["id"]

    # Score with max_points (15)
    score_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=coord_headers,
        json={"submission_id": submission_id, "points": 15, "comment": "Perfect!"},
    )
    assert score_resp.status_code == 201
    score_data = score_resp.json()
    assert score_data["points"] == 15
    assert score_data["max_points"] == 15


@pytest.mark.asyncio
async def test_scoring_exceed_max(client: AsyncClient, db_session: AsyncSession):
    """A score above max_points is refused with 422."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="score-exceed-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_12 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_12.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Exceed Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Exceed", "color": "#0000FF"},
    )
    team_id = team_resp.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=p_headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=p_headers)
    run_station_id = stations_resp.json()["items"][0]["id"]

    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=p_headers,
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    submit_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=p_headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Answer"}},
    )
    submission_id = submit_resp.json()["id"]

    # Score with max_points+1 (11 > 10)
    score_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=coord_headers,
        json={"submission_id": submission_id, "points": 11},
    )
    assert score_resp.status_code == 422


@pytest.mark.asyncio
async def test_scoring_update_to_zero(client: AsyncClient, db_session: AsyncSession):
    """A score can be updated down to 0."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="score-update-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_13 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_13.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Update Run"},
    )
    run_id = response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Update", "color": "#FFFF00"},
    )
    team_id = team_resp.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=p_headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=p_headers)
    run_station_id = stations_resp.json()["items"][0]["id"]

    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=p_headers,
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    submit_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=p_headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Answer"}},
    )
    submission_id = submit_resp.json()["id"]

    score_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=coord_headers,
        json={"submission_id": submission_id, "points": 5},
    )
    assert score_resp.status_code == 201
    score_id = score_resp.json()["id"]

    update_resp = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/{score_id}",
        headers=coord_headers,
        json={"points": 0, "comment": "Revised to zero"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["points"] == 0


# ============================================================================
# Feedback Constraints
# ============================================================================


@pytest.mark.asyncio
async def test_feedback_only_after_end(client: AsyncClient, db_session: AsyncSession):
    """Feedback is refused while a run is active or paused and accepted after it ended."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="feedback-state-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_14 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_14.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Feedback Run"},
    )
    run_id = response.json()["id"]

    # Feedback is refused while the run is active
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    feedback1 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 4, "comment": "Too early"},
    )
    assert feedback1.status_code in [400, 422]  # API may return 400 or 422

    # Feedback is refused while the run is paused
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=headers)

    feedback2 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 4, "comment": "Still too early"},
    )
    assert feedback2.status_code in [400, 422]  # API may return 400 or 422

    # Feedback is accepted after the end
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/resume", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    feedback3 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 4, "comment": "Now it works!"},
    )
    assert feedback3.status_code == 201


@pytest.mark.asyncio
async def test_feedback_rating_bounds(client: AsyncClient, db_session: AsyncSession):
    """Ratings 1 and 5 are accepted. Ratings 0 and 6 are refused with 422."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="feedback-bounds-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_15 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_15.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Bounds Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    # rating=1 (minimum) → valid
    fb1 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 1, "comment": "Minimum rating"},
    )
    assert fb1.status_code == 201

    # rating=5 (maximum) → valid
    fb2 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 5, "comment": "Maximum rating"},
    )
    assert fb2.status_code == 201

    # rating=0 (below minimum) → invalid
    fb3 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 0, "comment": "Too low"},
    )
    assert fb3.status_code == 422

    # rating=6 (above maximum) → invalid
    fb4 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 6, "comment": "Too high"},
    )
    assert fb4.status_code == 422


@pytest.mark.asyncio
async def test_feedback_optional_comment(client: AsyncClient, db_session: AsyncSession):
    """A feedback with only a rating is accepted and has no comment."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="feedback-optional-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_16 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_16.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Optional Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    fb_resp = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/feedback/", json={"rating": 4})
    assert fb_resp.status_code == 201
    fb_data = fb_resp.json()
    assert fb_data["rating"] == 4
    # Comment should be null or absent
    assert fb_data.get("comment") is None or fb_data.get("comment") == ""


@pytest.mark.asyncio
async def test_feedback_anonymous(client: AsyncClient, db_session: AsyncSession):
    """A feedback response carries no user_id."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="feedback-anon-org",
    )
    headers = get_auth_headers(coordinator)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_17 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_17.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Anon Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/end", headers=headers)

    fb_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        json={"rating": 5, "comment": "Great!"},
    )
    assert fb_resp.status_code == 201
    assert "user_id" not in fb_resp.json()


# ============================================================================
# Max Teams Setting
# ============================================================================


@pytest.mark.asyncio
async def test_max_teams_enforcement(client: AsyncClient, db_session: AsyncSession):
    """With max_teams=2 a third team is refused with 422."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="max-teams-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participants = []
    for i in range(3):
        participant = await create_test_user(
            db_session,
            email=f"p{i}@test.com",
            role=UserRole.PARTICIPANT,
            organization=org,
        )
        participants.append(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_18 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_18.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Max Teams Run", "max_teams": 2},
    )
    assert response.status_code == 201
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    # Participant A creates team (1/2)
    p0_headers = get_auth_headers(participants[0])
    team1_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p0_headers,
        json={"name": "Team Alpha", "color": "#FF0000"},
    )
    assert team1_resp.status_code == 201

    # Participant B creates team (2/2)
    p1_headers = get_auth_headers(participants[1])
    team2_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p1_headers,
        json={"name": "Team Beta", "color": "#00FF00"},
    )
    assert team2_resp.status_code == 201

    # Participant C tries to create team → 422 (max reached)
    p2_headers = get_auth_headers(participants[2])
    team3_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p2_headers,
        json={"name": "Team Gamma", "color": "#0000FF"},
    )
    assert team3_resp.status_code == 422


@pytest.mark.asyncio
async def test_max_teams_unlimited(client: AsyncClient, db_session: AsyncSession):
    """With max_teams null any number of teams can be created."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="unlimited-teams-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participants = []
    for i in range(5):
        participant = await create_test_user(
            db_session,
            email=f"p{i}@test.com",
            role=UserRole.PARTICIPANT,
            organization=org,
        )
        participants.append(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_19 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_19.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Unlimited Run"},
    )
    assert response.status_code == 201
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    for idx, participant in enumerate(participants):
        p_headers = get_auth_headers(participant)
        team_resp = await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
            headers=p_headers,
            json={"name": f"Team {idx + 1}", "color": f"#00{idx:02x}00"},
        )
        assert team_resp.status_code == 201


# ============================================================================
# Concurrent Team Operations
# ============================================================================


@pytest.mark.asyncio
async def test_a_participant_who_creates_teams_without_joining_can_still_join_one(
    client: AsyncClient, db_session: AsyncSession
):
    """A participant who created two teams (201 each) can still join another participant's team (204)."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="concurrent-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    participant2 = await create_test_user(
        db_session,
        email="participant2@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p2_headers = get_auth_headers(participant2)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_20 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_20.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Concurrent Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    # User creates Team Alpha (auto-joined as creator)
    team1_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Alpha", "color": "#FF0000"},
    )
    assert team1_resp.status_code == 201

    # Another participant creates Team Beta
    team2_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p2_headers,
        json={"name": "Team Beta", "color": "#00FF00"},
    )
    assert team2_resp.status_code == 201
    team2_id = team2_resp.json()["id"]

    # The user creates another team (Team Gamma)
    team3_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Gamma", "color": "#0000FF"},
    )
    assert team3_resp.status_code == 201

    # The user joins Team Beta
    join_resp = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team2_id}/join", headers=p_headers)
    assert join_resp.status_code == 204


# ============================================================================
# Submission for Non-Existent Challenge
# ============================================================================


@pytest.mark.asyncio
async def test_submission_nonexistent_challenge(client: AsyncClient, db_session: AsyncSession):
    """A submission with a run_challenge_id that does not exist is refused with 404 or 422."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="nonexist-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_21 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_21.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Nonexist Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Test", "color": "#FF0000"},
    )
    team_id = team_resp.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=p_headers)

    fake_uuid = "00000000-0000-0000-0000-000000000000"
    submit_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=p_headers,
        json={"run_challenge_id": fake_uuid, "content": {"text": "Answer"}},
    )
    # API returns 422 for validation errors (including non-existent challenge)
    assert submit_resp.status_code in [404, 422]


@pytest.mark.asyncio
async def test_submission_wrong_run_challenge(client: AsyncClient, db_session: AsyncSession):
    """A participant of run A cannot submit to a challenge of run B: 404 or 422."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="wrong-run-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_22 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_22.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    responseA = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Run A"},
    )
    run_a_id = responseA.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_a_id}/start", headers=coord_headers)

    responseB = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Run B"},
    )
    run_b_id = responseB.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_b_id}/start", headers=coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_a_id}/teams/",
        headers=p_headers,
        json={"name": "Team A", "color": "#FF0000"},
    )
    team_id = team_resp.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_a_id}/teams/{team_id}/join", headers=p_headers)

    stations_resp_b = await client.get(f"/api/orgs/{org.slug}/runs/{run_b_id}/stations/", headers=coord_headers)
    run_station_b_id = stations_resp_b.json()["items"][0]["id"]

    challenges_resp_b = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_b_id}/stations/{run_station_b_id}/challenges/",
        headers=coord_headers,
    )
    run_challenge_b_id = challenges_resp_b.json()["items"][0]["id"]

    submit_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_a_id}/submissions/",
        headers=p_headers,
        json={"run_challenge_id": run_challenge_b_id, "content": {"text": "Answer"}},
    )
    # Should fail (404 or 422, depending on validation order)
    assert submit_resp.status_code in [404, 422]


@pytest.mark.asyncio
async def test_submission_malformed_uuid(client: AsyncClient, db_session: AsyncSession):
    """A malformed run_challenge_id is refused with 422."""
    coordinator, org = await create_test_user_with_org(
        db_session,
        email="coord@test.com",
        role=UserRole.COORDINATOR,
        org_slug="malformed-org",
    )
    coord_headers = get_auth_headers(coordinator)

    participant = await create_test_user(
        db_session,
        email="participant@test.com",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    p_headers = get_auth_headers(participant)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "S1"},
        description={"de": "S1"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_23 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "C1"},
        instructions={"de": "Do"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_23.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Malformed Run"},
    )
    run_id = response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=p_headers,
        json={"name": "Team Test", "color": "#00FF00"},
    )
    team_id = team_resp.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=p_headers)

    submit_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=p_headers,
        json={"run_challenge_id": "not-a-uuid", "content": {"text": "Answer"}},
    )
    assert submit_resp.status_code == 422
