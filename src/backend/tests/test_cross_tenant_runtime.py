"""
Cross-tenant isolation tests for runtime entities.

Organization A cannot access organization B's runtime data:
- Runs
- Teams (and joining teams)
- Submissions (and submitting to challenges)
- Scores
- Feed events
- Chat messages
- Feedback

Every organization gets a complete chain: rallye, run, team, submission, score, feed, chat, feedback.
A user from org A attempts to reach the resources of org B.
A request that addresses an org B resource by id returns 404, not 403.
A request that names an org B challenge or submission in its body returns 422,
because the repository scope cannot find it.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run import RunState
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.run import RunRepository
from app.repositories.station import StationRepository
from app.repositories.submission import SubmissionRepository
from app.repositories.team import TeamRepository
from tests.factories import create_test_user_with_org, get_auth_headers


async def setup_full_runtime_chain(
    db_session: AsyncSession,
    org_slug: str,
    org_name: str,
    user_email: str,
):
    """
    Create a complete runtime chain for testing cross-tenant isolation.

    The run is ended at the end, because feedback needs an ended run.

    Returns:
        tuple: (user, org, run_id, team_id, submission_id, score_id, feed_event_id, chat_message_id,
        feedback_id, run_station_id, run_challenge_id)
    """
    user, org = await create_test_user_with_org(
        db_session,
        email=user_email,
        role=UserRole.COORDINATOR,
        org_slug=org_slug,
        org_name=org_name,
    )

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": f"Rallye {org_name}"},
        description={"de": "Test rallye"},
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

    # Create run via repository (so we can get the ID directly)
    from app.services.join_code import generate_unique_join_code

    join_code = await generate_unique_join_code(db_session, org.id)

    run_repo = RunRepository(db_session, org.id)
    run = await run_repo.create(
        rallye_id=rallye.id,
        name=f"Run {org_name}",
        join_code=join_code,
    )
    await db_session.commit()
    await db_session.refresh(run)
    run_id = str(run.id)

    # Start run (creates snapshots)
    from app.services.run_state import RunStateService
    from app.services.snapshot import create_run_snapshot

    await create_run_snapshot(db_session, run, org.id)

    RunStateService.transition(run, RunState.ACTIVE)
    await db_session.commit()

    from app.repositories.run_challenge import RunChallengeRepository
    from app.repositories.run_station import RunStationRepository

    run_station_repo = RunStationRepository(db_session, org.id)
    run_stations = await run_station_repo.list_by_run(run.id)
    run_station_id = str(run_stations[0].id)

    run_challenge_repo = RunChallengeRepository(db_session, org.id)
    run_challenges = await run_challenge_repo.list_by_run_station(run_stations[0].id)
    run_challenge_id = str(run_challenges[0].id)

    team_repo = TeamRepository(db_session, org.id)
    team = await team_repo.create(
        run_id=run.id,
        name=f"Team {org_name}",
    )
    await db_session.commit()
    await db_session.refresh(team)
    team_id = str(team.id)

    from app.repositories.team_member import TeamMemberRepository

    team_member_repo = TeamMemberRepository(db_session, org.id)
    await team_member_repo.create(
        team_id=team.id,
        user_id=user.id,
        display_name=user.display_name,
        is_guest=False,
    )
    await db_session.commit()

    submission_repo = SubmissionRepository(db_session, org.id)
    submission = await submission_repo.create(
        run_id=run.id,
        team_id=team.id,
        run_challenge_id=run_challenges[0].id,
        content={"answer": "Test answer"},
    )
    await db_session.commit()
    await db_session.refresh(submission)
    submission_id = str(submission.id)

    from app.repositories.score import ScoreRepository

    score_repo = ScoreRepository(db_session, org.id)
    score = await score_repo.create(
        submission_id=submission.id,
        points=8,
        max_points=10,
        scored_by=user.id,
    )
    await db_session.commit()
    await db_session.refresh(score)
    score_id = str(score.id)

    from app.models.feed_event import FeedEventType
    from app.repositories.feed_event import FeedEventRepository

    feed_repo = FeedEventRepository(db_session, org.id)
    feed_event = await feed_repo.create(
        run_id=run.id,
        event_type=FeedEventType.ANNOUNCEMENT,
        content={"de": "Test announcement"},
        author_id=user.id,
    )
    await db_session.commit()
    await db_session.refresh(feed_event)
    feed_event_id = str(feed_event.id)

    from app.repositories.chat_message import ChatMessageRepository

    chat_repo = ChatMessageRepository(db_session, org.id)
    chat_message = await chat_repo.create(
        team_id=team.id,
        author_id=user.id,
        content="Test message",
        is_system_message=False,
    )
    await db_session.commit()
    await db_session.refresh(chat_message)
    chat_message_id = str(chat_message.id)

    # Create feedback (end run first)
    RunStateService.transition(run, RunState.ENDED)
    await db_session.commit()

    from app.repositories.feedback import FeedbackRepository

    feedback_repo = FeedbackRepository(db_session, org.id)
    feedback = await feedback_repo.create(
        run_id=run.id,
        rating=5,
        comment="Great run!",
    )
    await db_session.commit()
    await db_session.refresh(feedback)
    feedback_id = str(feedback.id)

    return (
        user,
        org,
        run_id,
        team_id,
        submission_id,
        score_id,
        feed_event_id,
        chat_message_id,
        feedback_id,
        run_station_id,
        run_challenge_id,
    )


# =============================================================================
# Run Cross-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_cannot_access_run(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it reads, updates or deletes the run of org B."""
    (
        user_a,
        org_a,
        run_id_a,
        _team_id_a,
        _submission_id_a,
        _score_id_a,
        _feed_event_id_a,
        _chat_message_id_a,
        _feedback_id_a,
        _run_station_id_a,
        _run_challenge_id_a,
    ) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_b}", headers=headers_a)
    assert response.status_code == 404

    response = await client.put(
        f"/api/orgs/{org_a.slug}/runs/{run_id_b}",
        headers=headers_a,
        json={"name": "Hacked name"},
    )
    assert response.status_code == 404

    response = await client.delete(f"/api/orgs/{org_a.slug}/runs/{run_id_b}", headers=headers_a)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_cannot_start_run(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it starts a configured run of org B."""
    (user_a, org_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    # Setup org B with a configured run (not started yet)
    user_b, org_b = await create_test_user_with_org(
        db_session,
        email="user-b@test.com",
        role=UserRole.COORDINATOR,
        org_slug="org-b",
        org_name="Org B",
    )

    rallye_repo = RallyeRepository(db_session, org_b.id)
    rallye = await rallye_repo.create(
        title={"de": "Rallye B"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org_b.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs_station = RallyeStation(organization_id=org_b.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org_b.id)
    _challenge_2 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_b.id, rallye_station_id=rs_station.id, challenge_id=_challenge_2.id, sort_order=0
        )
    )
    await db_session.flush()

    from app.services.join_code import generate_unique_join_code

    join_code_b = await generate_unique_join_code(db_session, org_b.id)

    run_repo = RunRepository(db_session, org_b.id)
    run_b = await run_repo.create(
        rallye_id=rallye.id,
        name="Run B",
        join_code=join_code_b,
    )
    await db_session.commit()
    await db_session.refresh(run_b)

    headers_a = get_auth_headers(user_a)

    response = await client.post(f"/api/orgs/{org_a.slug}/runs/{run_b.id}/start", headers=headers_a)
    assert response.status_code == 404


# =============================================================================
# Team Cross-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_cannot_access_team(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it lists the teams of org B's run (the run is not found) and when it reads org B's team."""
    (user_a, org_a, run_id_a, team_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, team_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_b}/teams/", headers=headers_a)
    assert response.status_code == 404

    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_a}/teams/{team_id_b}", headers=headers_a)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_cannot_join_team(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it joins a team of org B.

    Org A needs an active run, so the test builds a fresh one. setup_full_runtime_chain
    ends its run for the feedback."""
    user_a, org_a = await create_test_user_with_org(
        db_session,
        email="user-a@test.com",
        role=UserRole.COORDINATOR,
        org_slug="org-a",
        org_name="Org A",
    )

    rallye_repo = RallyeRepository(db_session, org_a.id)
    rallye = await rallye_repo.create(
        title={"de": "Rallye A"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org_a.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs_station = RallyeStation(organization_id=org_a.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org_a.id)
    _challenge_3 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_a.id, rallye_station_id=rs_station.id, challenge_id=_challenge_3.id, sort_order=0
        )
    )
    await db_session.flush()

    from app.services.join_code import generate_unique_join_code

    join_code_a = await generate_unique_join_code(db_session, org_a.id)

    run_repo = RunRepository(db_session, org_a.id)
    run_a = await run_repo.create(
        rallye_id=rallye.id,
        name="Run A",
        join_code=join_code_a,
    )
    await db_session.commit()
    await db_session.refresh(run_a)

    from app.services.run_state import RunStateService
    from app.services.snapshot import create_run_snapshot

    await create_run_snapshot(db_session, run_a, org_a.id)
    RunStateService.transition(run_a, RunState.ACTIVE)
    await db_session.commit()

    (_user_b, org_b, run_id_b, team_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.post(f"/api/orgs/{org_a.slug}/runs/{run_a.id}/teams/{team_id_b}/join", headers=headers_a)
    assert response.status_code == 404


# =============================================================================
# Submission Cross-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_cannot_access_submission(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it reads a submission of org B."""
    (user_a, org_a, run_id_a, _team_id_a, submission_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, _team_id_b, submission_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.get(
        f"/api/orgs/{org_a.slug}/runs/{run_id_a}/submissions/{submission_id_b}", headers=headers_a
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_cannot_submit_to_challenge(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 422 when it submits to a challenge of org B.

    The repository scope cannot find the challenge, so the validation fails.
    The first chain of org A has an ended run, so the test submits from a fresh organization with an active run."""
    (user_a, org_a, run_id_a, team_id_a, *_, run_challenge_id_a) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (*_, run_challenge_id_b) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    user_a2, org_a2 = await create_test_user_with_org(
        db_session,
        email="user-a2@test.com",
        role=UserRole.COORDINATOR,
        org_slug="org-a2",
        org_name="Org A2",
    )

    rallye_repo = RallyeRepository(db_session, org_a2.id)
    rallye = await rallye_repo.create(
        title={"de": "Rallye A2"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org_a2.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs_station = RallyeStation(organization_id=org_a2.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org_a2.id)
    _challenge_4 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1"},
        instructions={"de": "Do something"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org_a2.id, rallye_station_id=rs_station.id, challenge_id=_challenge_4.id, sort_order=0
        )
    )
    await db_session.flush()

    from app.services.join_code import generate_unique_join_code

    join_code_a2 = await generate_unique_join_code(db_session, org_a2.id)

    run_repo_a2 = RunRepository(db_session, org_a2.id)
    run_a2 = await run_repo_a2.create(
        rallye_id=rallye.id,
        name="Run A2",
        join_code=join_code_a2,
    )
    await db_session.commit()
    await db_session.refresh(run_a2)

    from app.services.run_state import RunStateService
    from app.services.snapshot import create_run_snapshot

    await create_run_snapshot(db_session, run_a2, org_a2.id)
    RunStateService.transition(run_a2, RunState.ACTIVE)
    await db_session.commit()

    from app.repositories.team import TeamRepository

    team_repo = TeamRepository(db_session, org_a2.id)
    team_a2 = await team_repo.create(
        run_id=run_a2.id,
        name="Team A2",
    )
    await db_session.commit()

    from app.repositories.team_member import TeamMemberRepository

    team_member_repo = TeamMemberRepository(db_session, org_a2.id)
    await team_member_repo.create(
        team_id=team_a2.id,
        user_id=user_a2.id,
        display_name=user_a2.display_name,
        is_guest=False,
    )
    await db_session.commit()

    headers_a2 = get_auth_headers(user_a2)

    response = await client.post(
        f"/api/orgs/{org_a2.slug}/runs/{run_a2.id}/submissions/",
        headers=headers_a2,
        json={
            "run_challenge_id": run_challenge_id_b,
            "content": {"answer": "Hacked submission"},
        },
    )
    assert response.status_code == 422
    assert "challenge" in response.json()["detail"].lower()


# =============================================================================
# Score Cross-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_cannot_access_score(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it lists the scores of org B's run (the run is not found) and when it updates org B's score."""
    (
        user_a,
        org_a,
        run_id_a,
        _team_id_a,
        _submission_id_a,
        score_id_a,
        *_,
    ) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    result_b = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )
    score_id_b = result_b[5]
    run_id_b = result_b[2]

    headers_a = get_auth_headers(user_a)

    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_b}/scores/", headers=headers_a)
    assert response.status_code == 404

    response = await client.put(
        f"/api/orgs/{org_a.slug}/runs/{run_id_a}/scores/{score_id_b}",
        headers=headers_a,
        json={"points": 0, "comment": "Hacked score"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_cannot_create_score_for_other_org_submission(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 422 when it scores a submission of org B.

    The submission exists, but the repository scope of org A cannot find it."""
    (user_a, org_a, run_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    result_b = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )
    submission_id_b = result_b[4]

    headers_a = get_auth_headers(user_a)

    response = await client.post(
        f"/api/orgs/{org_a.slug}/runs/{run_id_a}/scores/",
        headers=headers_a,
        json={
            "submission_id": submission_id_b,
            "points": 10,
            "comment": "Hacked score",
        },
    )
    assert response.status_code == 422
    assert "not found" in response.json()["detail"].lower()


# =============================================================================
# Feed Event Cross-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_cannot_access_feed_event(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it lists the feed of org B's run."""
    (user_a, org_a, run_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_b}/feed/", headers=headers_a)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_cannot_create_feed_announcement(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it posts an announcement to the feed of org B's run."""
    (user_a, org_a, run_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.post(
        f"/api/orgs/{org_a.slug}/runs/{run_id_b}/feed/",
        headers=headers_a,
        json={
            "event_type": "announcement",
            "content": {"de": "Hacked announcement"},
        },
    )
    assert response.status_code == 404


# =============================================================================
# Chat Message Cross-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_cannot_access_chat(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it reads the chat of a team of org B."""
    (user_a, org_a, run_id_a, team_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, team_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_a}/teams/{team_id_b}/chat/", headers=headers_a)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_cannot_send_chat_message(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it sends a message to the chat of a team of org B."""
    (user_a, org_a, run_id_a, team_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, team_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.post(
        f"/api/orgs/{org_a.slug}/runs/{run_id_a}/teams/{team_id_b}/chat/",
        headers=headers_a,
        json={"content": "Hacked message"},
    )
    assert response.status_code == 404


# =============================================================================
# Feedback Cross-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_cross_tenant_cannot_access_feedback(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it lists the feedback or reads the feedback summary of org B's run."""
    (user_a, org_a, run_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_b}/feedback/", headers=headers_a)
    assert response.status_code == 404

    response = await client.get(f"/api/orgs/{org_a.slug}/runs/{run_id_b}/feedback/summary", headers=headers_a)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_cannot_submit_feedback(client: AsyncClient, db_session: AsyncSession):
    """Org A gets 404 when it submits feedback to org B's run."""
    (user_a, org_a, run_id_a, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-a",
        org_name="Org A",
        user_email="user-a@test.com",
    )

    (_user_b, org_b, run_id_b, *_) = await setup_full_runtime_chain(
        db_session,
        org_slug="org-b",
        org_name="Org B",
        user_email="user-b@test.com",
    )

    headers_a = get_auth_headers(user_a)

    response = await client.post(
        f"/api/orgs/{org_a.slug}/runs/{run_id_b}/feedback/",
        headers=headers_a,
        json={
            "rating": 5,
            "comment": "Hacked feedback",
        },
    )
    assert response.status_code == 404


# =============================================================================
# End-to-End Integration Test
# =============================================================================


@pytest.mark.asyncio
async def test_full_run_lifecycle_integration(client: AsyncClient, db_session: AsyncSession):
    """A run goes through its whole lifecycle, from the rallye template to the ended run and its feedback."""
    # Create rallye with stations/challenges (templates)
    user, org = await create_test_user_with_org(
        db_session,
        email="creator@test.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
        org_name="Test Organization",
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Augsburg Stadtrallye", "en": "Augsburg City Rally"},
        description={"de": "Eine Tour durch Augsburg", "en": "A tour through Augsburg"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station1 = await station_repo.create(
        title={"de": "Rathausplatz", "en": "Town Hall Square"},
        description={"de": "Der historische Rathausplatz", "en": "The historic town hall square"},
    )
    rs_station1 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station1.id, sort_order=0)
    db_session.add(rs_station1)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_5 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Wann wurde das Rathaus gebaut?", "en": "When was the town hall built?"},
        instructions={"de": "Finde das Datum", "en": "Find the date"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station1.id, challenge_id=_challenge_5.id, sort_order=0
        )
    )
    await db_session.flush()

    station2 = await station_repo.create(
        title={"de": "Fuggerei", "en": "Fuggerei"},
        description={"de": "Die älteste Sozialsiedlung der Welt", "en": "The world's oldest social housing"},
    )
    rs_station2 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station2.id, sort_order=1)
    db_session.add(rs_station2)
    await db_session.flush()

    _challenge_6 = await challenge_repo.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Mache ein Foto vom Eingang", "en": "Take a photo of the entrance"},
        instructions={"de": "Fotografiere das Haupttor", "en": "Photograph the main gate"},
        scoring_rules={"max_points": 15},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station2.id, challenge_id=_challenge_6.id, sort_order=0
        )
    )
    await db_session.flush()

    # Commit all template data before creating the run
    await db_session.commit()

    # Create run from rallye
    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={
            "rallye_id": str(rallye.id),
            "name": "Sommer Tour 2026",
        },
    )
    assert run_response.status_code == 201
    run = run_response.json()
    run_id = run["id"]
    assert run["name"] == "Sommer Tour 2026"
    assert run["state"] == "configured"
    assert "join_code" in run
    assert len(run["join_code"]) == 6

    # Start run (creates snapshots)
    start_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=headers,
    )
    assert start_response.status_code == 200, start_response.json()
    run_after_start = start_response.json()
    assert run_after_start["state"] == "active"
    assert run_after_start["started_at"] is not None

    # Verify snapshots were created
    stations_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/",
        headers=headers,
    )
    assert stations_response.status_code == 200
    stations_data = stations_response.json()
    assert stations_data["total"] == 2
    run_station_1 = stations_data["items"][0]
    run_station_2 = stations_data["items"][1]
    assert run_station_1["title"]["de"] == "Rathausplatz"
    assert run_station_2["title"]["de"] == "Fuggerei"

    challenges_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_1['id']}/challenges/",
        headers=headers,
    )
    assert challenges_response.status_code == 200
    challenges_data = challenges_response.json()
    assert challenges_data["total"] == 1
    run_challenge_1 = challenges_data["items"][0]
    assert run_challenge_1["challenge_type"] == "text"
    assert run_challenge_1["scoring_rules"]["max_points"] == 10

    # Create team
    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={
            "name": "Die Stadtentdecker",
            "color": "#FF5733",
        },
    )
    assert team_response.status_code == 201
    team = team_response.json()
    team_id = team["id"]
    assert team["name"] == "Die Stadtentdecker"
    assert team["color"] == "#FF5733"

    # Join team
    join_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=headers,
    )
    assert join_response.status_code == 204

    # Submit to challenge
    submission_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_1["id"],
            "content": {"text": "1615"},
        },
    )
    assert submission_response.status_code == 201
    submission = submission_response.json()
    submission_id = submission["id"]
    assert submission["content"]["text"] == "1615"
    assert submission["run_challenge_id"] == run_challenge_1["id"]
    assert submission["team_id"] == team_id
    assert submission["submitted_at"] is not None

    # Score submission
    score_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={
            "submission_id": submission_id,
            "points": 8,
            "comment": "Richtige Antwort, aber Jahr statt vollständiges Datum",
        },
    )
    assert score_response.status_code == 201
    score = score_response.json()
    assert score["points"] == 8
    assert score["max_points"] == 10
    assert score["comment"] == "Richtige Antwort, aber Jahr statt vollständiges Datum"
    assert score["scored_by"] == str(user.id)
    assert score["scored_at"] is not None

    # Post announcement
    feed_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={
            "event_type": "announcement",
            "content": {
                "de": "Willkommen zur Stadtrallye! Viel Erfolg!",
                "en": "Welcome to the city rally! Good luck!",
            },
        },
    )
    assert feed_response.status_code == 201
    feed_event = feed_response.json()
    assert feed_event["event_type"] == "announcement"
    assert feed_event["content"]["de"] == "Willkommen zur Stadtrallye! Viel Erfolg!"
    assert feed_event["author_id"] == str(user.id)

    feed_list_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
    )
    assert feed_list_response.status_code == 200
    feed_list = feed_list_response.json()
    assert feed_list["total"] >= 1
    assert any(e["event_type"] == "announcement" for e in feed_list["items"])

    # Send chat message
    chat_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=headers,
        json={
            "content": "Wir sind jetzt am Rathausplatz!",
        },
    )
    assert chat_response.status_code == 201
    chat_message = chat_response.json()
    assert chat_message["content"] == "Wir sind jetzt am Rathausplatz!"
    assert chat_message["author_id"] == str(user.id)
    assert chat_message["is_system_message"] is False

    chat_list_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=headers,
    )
    assert chat_list_response.status_code == 200
    chat_list = chat_list_response.json()
    assert chat_list["total"] >= 1
    assert chat_list["items"][0]["content"] == "Wir sind jetzt am Rathausplatz!"

    # Get scoreboard
    scoreboard_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/scoreboard",
        headers=headers,
    )
    assert scoreboard_response.status_code == 200
    scoreboard = scoreboard_response.json()
    assert len(scoreboard["entries"]) == 1
    team_score = scoreboard["entries"][0]
    assert team_score["team_id"] == team_id
    assert team_score["team_name"] == "Die Stadtentdecker"
    assert team_score["total_points"] == 8
    assert team_score["total_max_points"] == 10
    assert team_score["scored_count"] == 1
    assert team_score["rank"] == 1

    # End run
    end_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/end",
        headers=headers,
    )
    assert end_response.status_code == 200
    run_after_end = end_response.json()
    assert run_after_end["state"] == "ended"
    assert run_after_end["ended_at"] is not None

    # Submit feedback
    feedback_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        headers=headers,
        json={
            "rating": 5,
            "comment": "Hervorragende Stadtrallye! Sehr gut organisiert und interessante Stationen.",
        },
    )
    assert feedback_response.status_code == 201
    feedback = feedback_response.json()
    assert feedback["rating"] == 5
    assert feedback["comment"] == "Hervorragende Stadtrallye! Sehr gut organisiert und interessante Stationen."
    assert "user_id" not in feedback  # Feedback is anonymous

    # List feedback (coordinator can see all feedback)
    feedback_list_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/",
        headers=headers,
    )
    assert feedback_list_response.status_code == 200
    feedback_list = feedback_list_response.json()
    assert feedback_list["total"] >= 1
    assert feedback_list["items"][0]["rating"] == 5

    summary_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/feedback/summary",
        headers=headers,
    )
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["average_rating"] == 5.0
    assert summary["total_count"] == 1
    assert summary["distribution"]["5"] == 1

    # The ended run cannot be restarted
    restart_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/start",
        headers=headers,
    )
    assert restart_response.status_code == 422  # Invalid state transition
    assert "transition" in restart_response.json()["detail"].lower()

    final_run_response = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}",
        headers=headers,
    )
    assert final_run_response.status_code == 200
    final_run = final_run_response.json()
    assert final_run["state"] == "ended"
    assert final_run["started_at"] is not None
    assert final_run["ended_at"] is not None
    assert final_run["name"] == "Sommer Tour 2026"
