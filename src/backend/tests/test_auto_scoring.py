"""Integration tests for the auto-scoring of quiz and QR_SCAN submissions.

They cover when a submission is auto-scored, the points and scored_by, the feed line,
the rejection of a QUIZ with auto_approve and no correct_option, and the manual override.

"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.feed_event import FeedEventType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.feed_event import FeedEventRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.score import ScoreRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_user_with_org, get_auth_headers


async def _setup_quiz_run(
    client: AsyncClient,
    db_session: AsyncSession,
    headers: dict,
    org,
    challenge_type: ChallengeType = ChallengeType.QUIZ,
    scoring_rules: dict | None = None,
):
    """Set up a started run with one challenge and a joined team.

    Creates rallye -> station -> challenge -> run (started) -> team (joined).
    Returns run_id, run_challenge_id and team_id.

    """
    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Quiz Rallye"},
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
    default_scoring_rules = {
        "max_points": 10,
        "auto_approve": True,
        "correct_option": "A",
    }
    _challenge_1 = await challenge_repo.create(
        challenge_type=challenge_type,
        title={"de": "Challenge 1"},
        instructions={"de": "Answer the quiz"},
        scoring_rules=scoring_rules if scoring_rules is not None else default_scoring_rules,
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
        json={"name": "Quiz Team"},
    )
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    join_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)
    assert join_response.status_code == 204

    return run_id, run_challenge_id, team_id


@pytest.mark.asyncio
async def test_correct_quiz_answer_auto_scored(client: AsyncClient, db_session: AsyncSession):
    """A correct quiz answer is auto-scored with full points, and the feed line names the quiz scoring in both languages for the answering team only."""
    user, org = await create_test_user_with_org(
        db_session, email="quiz@test.com", role=UserRole.COORDINATOR, org_slug="quiz-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        scoring_rules={"max_points": 10, "auto_approve": True, "correct_option": "A"},
    )

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"selected_option": "A"},
        },
    )

    assert response.status_code == 201
    submission_id = response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    score = await score_repo.get_by_submission(UUID(submission_id))

    assert score is not None
    assert score.points == 10
    assert score.max_points == 10

    feed_events = await FeedEventRepository(db_session, org.id).list_by_run(UUID(run_id))
    system_events = [e for e in feed_events if e.event_type == FeedEventType.SYSTEM]
    assert len(system_events) == 1
    assert system_events[0].content == {
        "de": "Quiz automatisch bewertet: 10/10 Punkte",
        "en": "Quiz auto-scored: 10/10 points",
    }
    # The line is the business of the team that answered: other teams of the
    # run must not read how this team's quiz was scored.
    assert system_events[0].target_team_id == UUID(team_id)


@pytest.mark.asyncio
async def test_qr_scan_auto_scored_feed_line_names_qr_check(client: AsyncClient, db_session: AsyncSession):
    """A QR_SCAN auto-score names the QR check in the feed line, not the quiz."""
    user, org = await create_test_user_with_org(
        db_session, email="qr-feed@test.com", role=UserRole.COORDINATOR, org_slug="qr-feed-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        challenge_type=ChallengeType.QR_SCAN,
        scoring_rules={"max_points": 10, "auto_approve": True, "expected_value": "clavis-station-7"},
    )

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"scanned_value": "clavis-station-7"},
        },
    )

    assert response.status_code == 201
    submission_id = response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    score = await score_repo.get_by_submission(UUID(submission_id))

    assert score is not None
    assert score.points == 10
    assert score.max_points == 10

    feed_events = await FeedEventRepository(db_session, org.id).list_by_run(UUID(run_id))
    system_events = [e for e in feed_events if e.event_type == FeedEventType.SYSTEM]
    assert len(system_events) == 1
    assert system_events[0].content == {
        "de": "QR-Code automatisch geprüft: 10/10 Punkte",
        "en": "QR code checked automatically: 10/10 points",
    }


@pytest.mark.asyncio
async def test_manual_override_of_quiz_autoscore_persists(client: AsyncClient, db_session: AsyncSession):
    """A coordinator's manual override of a quiz auto-score replaces it and survives.

    The submission stays scored, so it cannot be replaced. Nothing can re-run
    auto-scoring over the override unless a coordinator re-opens the answer.

    """
    user, org = await create_test_user_with_org(
        db_session, email="override-quiz@test.com", role=UserRole.COORDINATOR, org_slug="override-quiz-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        scoring_rules={"max_points": 10, "auto_approve": True, "correct_option": "A"},
    )

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"selected_option": "A"}},
    )
    assert submission_response.status_code == 201
    submission_id = submission_response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    auto_score = await score_repo.get_by_submission(UUID(submission_id))
    assert auto_score is not None
    assert auto_score.points == 10

    # Coordinator overrides the automatic score by hand
    override_response = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/{auto_score.id}",
        headers=headers,
        json={"points": 6, "comment": "Answer was late, deducting points"},
    )
    assert override_response.status_code == 200
    assert override_response.json()["points"] == 6

    # The submission stays scored, so it cannot be replaced — nothing can re-run
    # auto-scoring and overwrite the manual override
    replace_response = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/{submission_id}",
        headers=headers,
        json={"content": {"selected_option": "A"}},
    )
    assert replace_response.status_code == 422

    # The override is still in place, unchanged
    current_score = await score_repo.get_by_submission(UUID(submission_id))
    assert current_score is not None
    assert current_score.id == auto_score.id
    assert current_score.points == 6


@pytest.mark.asyncio
async def test_manual_override_of_qr_autoscore_persists(client: AsyncClient, db_session: AsyncSession):
    """A coordinator's manual override of a QR_SCAN auto-score replaces it and survives.

    The submission stays scored, so a replace attempt is rejected.

    """
    user, org = await create_test_user_with_org(
        db_session, email="override-qr@test.com", role=UserRole.COORDINATOR, org_slug="override-qr-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        challenge_type=ChallengeType.QR_SCAN,
        scoring_rules={"max_points": 10, "auto_approve": True, "expected_value": "clavis-station-7"},
    )

    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"scanned_value": "clavis-station-7"}},
    )
    assert submission_response.status_code == 201
    submission_id = submission_response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    auto_score = await score_repo.get_by_submission(UUID(submission_id))
    assert auto_score is not None
    assert auto_score.points == 10

    # Coordinator overrides the automatic score by hand
    override_response = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/{auto_score.id}",
        headers=headers,
        json={"points": 4, "comment": "Scan took several attempts, deducting points"},
    )
    assert override_response.status_code == 200
    assert override_response.json()["points"] == 4

    # The submission stays scored — a replace attempt is rejected outright
    replace_response = await client.put(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/{submission_id}",
        headers=headers,
        json={"content": {"scanned_value": "clavis-station-7"}},
    )
    assert replace_response.status_code == 422

    # The override is still in place, unchanged
    current_score = await score_repo.get_by_submission(UUID(submission_id))
    assert current_score is not None
    assert current_score.id == auto_score.id
    assert current_score.points == 4


@pytest.mark.asyncio
async def test_wrong_quiz_answer_auto_scored_zero(client: AsyncClient, db_session: AsyncSession):
    """A wrong quiz answer is auto-scored with 0 points."""
    user, org = await create_test_user_with_org(
        db_session, email="wrong@test.com", role=UserRole.COORDINATOR, org_slug="wrong-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        scoring_rules={"max_points": 10, "auto_approve": True, "correct_option": "A"},
    )

    # Submit wrong answer (correct is "A", we submit "B")
    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"selected_option": "B"},
        },
    )

    assert response.status_code == 201
    submission_id = response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    score = await score_repo.get_by_submission(UUID(submission_id))

    assert score is not None
    assert score.points == 0
    assert score.max_points == 10


@pytest.mark.asyncio
async def test_quiz_without_auto_approve_skips_auto_scoring(client: AsyncClient, db_session: AsyncSession):
    """A quiz without auto_approve creates no score."""
    user, org = await create_test_user_with_org(
        db_session, email="manual@test.com", role=UserRole.COORDINATOR, org_slug="manual-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        scoring_rules={"max_points": 10, "correct_option": "A"},
    )

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"selected_option": "A"},
        },
    )

    assert response.status_code == 201
    submission_id = response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    score = await score_repo.get_by_submission(UUID(submission_id))

    assert score is None


@pytest.mark.asyncio
async def test_non_quiz_challenge_skips_auto_scoring(client: AsyncClient, db_session: AsyncSession):
    """A TEXT challenge creates no score, even with auto_approve."""
    user, org = await create_test_user_with_org(
        db_session, email="text@test.com", role=UserRole.COORDINATOR, org_slug="text-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        challenge_type=ChallengeType.TEXT,
        scoring_rules={"max_points": 10, "auto_approve": True},
    )

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My text answer"},
        },
    )

    assert response.status_code == 201
    submission_id = response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    score = await score_repo.get_by_submission(UUID(submission_id))

    assert score is None


@pytest.mark.asyncio
async def test_auto_scored_submission_has_scored_by_none(client: AsyncClient, db_session: AsyncSession):
    """An auto-score has scored_by None, which tells it from a human score."""
    user, org = await create_test_user_with_org(
        db_session, email="system@test.com", role=UserRole.COORDINATOR, org_slug="system-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        scoring_rules={"max_points": 5, "auto_approve": True, "correct_option": "C"},
    )

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"selected_option": "C"},
        },
    )

    assert response.status_code == 201
    submission_id = response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    score = await score_repo.get_by_submission(UUID(submission_id))

    assert score is not None
    assert score.scored_by is None


@pytest.mark.asyncio
async def test_auto_score_points_match_max_points_for_correct_answer(client: AsyncClient, db_session: AsyncSession):
    """A correct answer earns exactly max_points, for a max other than 10."""
    user, org = await create_test_user_with_org(
        db_session, email="points@test.com", role=UserRole.COORDINATOR, org_slug="points-org"
    )
    headers = get_auth_headers(user)

    # Use 25 points as max to verify it's not hardcoded
    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        scoring_rules={"max_points": 25, "auto_approve": True, "correct_option": "D"},
    )

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"selected_option": "D"},
        },
    )

    assert response.status_code == 201
    submission_id = response.json()["id"]

    score_repo = ScoreRepository(db_session, org.id)
    score = await score_repo.get_by_submission(UUID(submission_id))

    assert score is not None
    assert score.points == 25
    assert score.max_points == 25


@pytest.mark.asyncio
async def test_quiz_with_auto_approve_but_no_correct_option_rejected(client: AsyncClient, db_session: AsyncSession):
    """Creating a QUIZ with auto_approve but without correct_option is rejected with 422."""
    user, org = await create_test_user_with_org(
        db_session, email="invalid@test.com", role=UserRole.CREATOR, org_slug="invalid-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Invalid Rallye"},
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

    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{rs_station.id}/challenges",
        headers=headers,
        json={
            "challenge_type": "quiz",  # lowercase as expected by API
            "title": {"de": "Invalid Quiz"},
            "instructions": {"de": "This should fail"},
            "scoring_rules": {"max_points": 10, "auto_approve": True},
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    # detail can be a string or list depending on error type
    detail_str = str(detail) if isinstance(detail, list) else detail
    assert "correct_option" in detail_str.lower()
