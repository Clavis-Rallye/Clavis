"""
Integration tests for feed event endpoints.

Tests cover:
- Create announcement (POST /) - coordinator can create
- Create announcement - participant denied (403), other event types refused (400)
- List feed events (GET /) - ordered chronologically, newest window, participants may read
- Feed event contains correct content and event_type
- The NOTIFY payload carries the target team
- The LK_C sweep rewrites a help request entry in place
- Who reads a run's feed without a team
- Entries aimed at a dissolved team are refused (404)
"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.team_member import TeamMember
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers
from tests.journey_helpers import (
    JourneyEnv,
    create_team_and_join,
    leave_as_last_member,
    remove_last_member,
    setup_journey_environment,
)


@pytest.mark.asyncio
async def test_create_announcement_success(client: AsyncClient, db_session: AsyncSession):
    """A coordinator creates an announcement, and the response carries its type, content and author."""
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
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Wichtige Ankündigung", "en": "Important announcement"},
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert UUID(data["id"])
    assert data["organization_id"] == str(org.id)
    assert data["run_id"] == run_id
    assert data["event_type"] == "announcement"
    assert data["content"] == {"de": "Wichtige Ankündigung", "en": "Important announcement"}
    assert data["author_id"] == str(user.id)
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_announcement_participant_denied(client: AsyncClient, db_session: AsyncSession):
    """A participant cannot create an announcement (403)."""
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
        headers=coord_headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=participant_headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Unauthorized announcement", "en": "Unauthorized announcement"},
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_feed_events_chronological_order(client: AsyncClient, db_session: AsyncSession):
    """Feed events are listed oldest first, and all of them are included."""
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

    announcement1 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Erste Ankündigung", "en": "First announcement"},
        },
    )
    assert announcement1.status_code == 201

    announcement2 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Zweite Ankündigung", "en": "Second announcement"},
        },
    )
    assert announcement2.status_code == 201

    announcement3 = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Dritte Ankündigung", "en": "Third announcement"},
        },
    )
    assert announcement3.status_code == 201

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feed/", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 3
    assert len(data["items"]) == 3

    assert data["items"][0]["content"]["de"] == "Erste Ankündigung"
    assert data["items"][1]["content"]["de"] == "Zweite Ankündigung"
    assert data["items"][2]["content"]["de"] == "Dritte Ankündigung"

    timestamps = [item["created_at"] for item in data["items"]]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_list_feed_events_latest_window(client: AsyncClient, db_session: AsyncSession):
    """
    latest=true returns the newest events, still oldest first.

    A feed is read at its end. Without latest=true, a client that takes
    `skip=0, limit=N` off the ascending query opens a long run on its own beginning.
    The default window is the oldest events, and skip pages back from the newest.
    """
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)
    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(title={"de": "Station 1"}, description={"de": "First station"})
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

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/", headers=headers, json={"rallye_id": str(rallye.id), "name": "Test Run"}
    )
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    for i in range(5):
        resp = await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
            headers=headers,
            json={"event_type": "announcement", "content": {"de": f"A{i}", "en": f"A{i}"}},
        )
        assert resp.status_code == 201

    def texts(response):
        return [i["content"]["de"] for i in response.json()["items"]]

    oldest = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feed/?limit=2", headers=headers)
    assert oldest.status_code == 200
    assert texts(oldest) == ["A0", "A1"]

    latest = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feed/?limit=2&latest=true", headers=headers)
    assert latest.status_code == 200
    assert texts(latest) == ["A3", "A4"]

    latest_prev_page = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/?skip=2&limit=2&latest=true", headers=headers
    )
    assert latest_prev_page.status_code == 200
    assert texts(latest_prev_page) == ["A1", "A2"]


async def test_feed_event_contains_correct_content_and_event_type(client: AsyncClient, db_session: AsyncSession):
    """An announcement keeps its event_type and its content, including nested JSON, on create and on list."""
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

    complex_content = {
        "de": "Die Rallye wird um 15:00 Uhr fortgesetzt",
        "en": "The rally will continue at 3:00 PM",
        "metadata": {
            "priority": "high",
            "category": "schedule_change",
        },
    }

    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={
            "event_type": "announcement",
            "content": complex_content,
        },
    )
    assert create_response.status_code == 201
    created_event = create_response.json()

    assert created_event["event_type"] == "announcement"
    assert created_event["content"] == complex_content

    list_response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feed/", headers=headers)
    assert list_response.status_code == 200
    data = list_response.json()

    assert data["total"] == 1
    event = data["items"][0]

    assert event["event_type"] == "announcement"
    assert event["content"] == complex_content
    assert event["content"]["metadata"]["priority"] == "high"
    assert event["content"]["metadata"]["category"] == "schedule_change"


@pytest.mark.asyncio
async def test_list_feed_events_participant_access(client: AsyncClient, db_session: AsyncSession):
    """A participant can list feed events and sees the announcements."""
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

    await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=coord_headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Wichtige Info", "en": "Important info"},
        },
    )

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/feed/", headers=participant_headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["event_type"] == "announcement"
    assert data["items"][0]["content"]["de"] == "Wichtige Info"


@pytest.mark.asyncio
async def test_create_announcement_invalid_event_type(client: AsyncClient, db_session: AsyncSession):
    """An event of type system cannot be created by hand (400), because system and milestone events are generated."""
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

    response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={
            "event_type": "system",
            "content": {"de": "System event", "en": "System event"},
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "cannot be created manually" in detail.lower()
    assert "system and milestone events are auto-generated" in detail.lower()


@pytest.mark.asyncio
async def test_notify_feed_event_payload_carries_the_target_team():
    """
    The NOTIFY payload names the target team.

    The socket channel is per run, so every client of the run receives the live
    event. A participant client can only drop an event of another team when
    the payload says whose it is.
    """
    import json
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.services.notify import notify_feed_event

    async def payload_of(target_team_id):
        db = AsyncMock()
        event = SimpleNamespace(
            id=uuid4(),
            run_id=uuid4(),
            organization_id=uuid4(),
            event_type="system",
            content={"de": "x"},
            author_id=None,
            target_team_id=target_team_id,
            created_at=datetime.now(UTC),
        )
        await notify_feed_event(db, event)  # type: ignore[arg-type]
        return json.loads(db.execute.await_args.args[1]["payload"])

    team_id = uuid4()
    assert (await payload_of(team_id))["target_team_id"] == str(team_id)
    assert (await payload_of(None))["target_team_id"] is None


@pytest.mark.asyncio
async def test_help_request_entry_loses_the_team_name_with_the_lkc_sweep(client: AsyncClient, db_session: AsyncSession):
    """The LK_C sweep rewrites the help request entry in place: the team name goes, created_at and the target team stay."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models.feed_event import FeedEvent
    from app.models.run import Run
    from app.services.gdpr import DeletionClass
    from app.services.retention_tasks import sweep_run_anonymize
    from tests.test_coordinator_request import _setup_run

    coordinator, org = await create_test_user_with_org(
        db_session, email="coord-feed-sweep@test.com", role=UserRole.COORDINATOR, org_slug="feed-sweep"
    )
    participant = await create_test_user(
        db_session, email="part-feed-sweep@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}"
    await client.post(f"{base}/join", headers=get_auth_headers(participant))
    resp = await client.post(f"{base}/chat/request-coordinator", headers=get_auth_headers(participant))
    assert resp.status_code == 200

    before = (await db_session.execute(select(FeedEvent).where(FeedEvent.run_id == UUID(run_id)))).scalar_one()
    assert before.content["team_name"] == "Team Alpha"
    created_at, target = before.created_at, before.target_team_id

    run = (await db_session.execute(select(Run).where(Run.id == UUID(run_id)))).scalar_one()
    run.ended_at = datetime.now(UTC) - timedelta(days=DeletionClass.LK_C.retention_days + 1)
    run.state = "ended"
    await db_session.flush()
    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))

    await db_session.refresh(before)
    assert before.content == {
        "message_key": "help_request",
        "team_id": team_id,
        "team_name": "Team 1",
        "de": "Team Team 1 benötigt Hilfe.",
        "en": "Team Team 1 needs help.",
    }
    assert before.created_at == created_at
    assert before.target_team_id == target


# ============================================================================
# Who reads a run's feed without a team
#
# The rule (routers/feed.py::list_feed_events): a registered participant
# without a team in the run reads the broadcasts only, and a guest without a
# team in the run reads nothing.
# ============================================================================


async def _run_with_code(client: AsyncClient, env: JourneyEnv, name: str) -> tuple[str, str]:
    """An active run: its id and its join code."""
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": name},
    )
    assert created.status_code == 201, created.text
    run = created.json()
    started = await client.post(f"/api/orgs/{env.slug}/runs/{run['id']}/start", headers=env.auth_headers["coordinator"])
    assert started.status_code == 200, started.text
    return run["id"], run["join_code"]


async def _guest_joins(client: AsyncClient, join_code: str) -> tuple[str, dict[str, str]]:
    """A guest joins with a new team: the team's id and the guest's headers."""
    joined = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Gaeste"},
    )
    assert joined.status_code == 201, joined.text
    # The join answers with the session cookie; the tests authenticate by header
    client.cookies.clear()
    return joined.json()["team_id"], {"Authorization": f"Bearer {joined.json()['token']}"}


async def _post_feed(client: AsyncClient, env: JourneyEnv, run_id: str, text: str, team_id: str | None = None) -> None:
    body: dict[str, object] = {"event_type": "announcement", "content": {"de": text}}
    if team_id is not None:
        body["target_team_id"] = team_id
    posted = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=env.auth_headers["coordinator"], json=body
    )
    assert posted.status_code == 201, posted.text


def _texts(response) -> list[str]:  # noqa: ANN001
    return [item["content"]["de"] for item in response.json()["items"]]


@pytest.mark.asyncio
async def test_a_guest_who_left_the_team_does_not_read_the_runs_feed(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="feed-guest-left")
    run_id, join_code = await _run_with_code(client, env, "Feed Run")
    team_id, guest_headers = await _guest_joins(client, join_code)
    await _post_feed(client, env, run_id, "An alle")
    feed_url = f"/api/orgs/{env.slug}/runs/{run_id}/feed/"

    as_member = await client.get(feed_url, headers=guest_headers)
    assert as_member.status_code == 200, as_member.text
    assert "An alle" in _texts(as_member)

    left = await leave_as_last_member(client, env.slug, run_id, team_id, guest_headers)
    assert left.status_code == 204, left.text

    after = await client.get(feed_url, headers=guest_headers)
    assert after.status_code == 403, after.text
    assert "items" not in after.json()


@pytest.mark.asyncio
async def test_a_guest_removed_from_the_team_does_not_read_the_runs_feed(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="feed-guest-removed")
    run_id, join_code = await _run_with_code(client, env, "Feed Run")
    team_id, guest_headers = await _guest_joins(client, join_code)
    await _post_feed(client, env, run_id, "An alle")
    member_id = (
        await db_session.execute(select(TeamMember.id).where(TeamMember.team_id == UUID(team_id)))
    ).scalar_one()

    removed = await remove_last_member(client, env.slug, run_id, team_id, member_id, env.auth_headers["coordinator"])
    assert removed.status_code == 204, removed.text

    after = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=guest_headers)
    assert after.status_code == 403, after.text


@pytest.mark.asyncio
async def test_a_guest_does_not_read_the_feed_of_a_run_it_has_no_team_in(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="feed-guest-other-run")
    _own_run_id, join_code = await _run_with_code(client, env, "Own Run")
    other_run_id, _ = await _run_with_code(client, env, "Other Run")
    _team_id, guest_headers = await _guest_joins(client, join_code)
    await _post_feed(client, env, other_run_id, "Fuer den anderen Lauf")

    response = await client.get(f"/api/orgs/{env.slug}/runs/{other_run_id}/feed/", headers=guest_headers)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_a_registered_former_member_of_a_team_that_lives_on_reads_the_broadcasts_and_nothing_of_the_team(
    client: AsyncClient, db_session: AsyncSession
):
    env = await setup_journey_environment(db_session, client, org_slug="feed-former-member")
    run_id, _ = await _run_with_code(client, env, "Feed Run")
    headers = env.auth_headers["participant1"]
    team = await create_team_and_join(client, env.slug, run_id, headers, "Team Alpha", "#FF0000")
    # A second member keeps the team alive after the leave
    second = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/by-code",
        headers=env.auth_headers["participant2"],
        json={"team_code": team["share_code"]},
    )
    assert second.status_code == 204, second.text
    await _post_feed(client, env, run_id, "An alle")
    await _post_feed(client, env, run_id, "Nur fuer Alpha", team_id=team["id"])
    feed_url = f"/api/orgs/{env.slug}/runs/{run_id}/feed/"

    as_member = await client.get(feed_url, headers=headers)
    assert as_member.status_code == 200, as_member.text
    assert {"An alle", "Nur fuer Alpha"} <= set(_texts(as_member))

    left = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}/leave", headers=headers)
    assert left.status_code == 204, left.text
    await _post_feed(client, env, run_id, "Alpha nach dem Austritt", team_id=team["id"])
    await _post_feed(client, env, run_id, "An alle, spaeter")

    # A registered account without a team: the broadcasts, old and new, and
    # no event for the team that was theirs, old or new
    after = await client.get(feed_url, headers=headers)
    assert after.status_code == 200, after.text
    texts = _texts(after)
    assert "An alle" in texts
    assert "An alle, spaeter" in texts
    assert "Nur fuer Alpha" not in texts
    assert "Alpha nach dem Austritt" not in texts


@pytest.mark.asyncio
async def test_a_registered_former_last_member_of_a_dissolved_team_reads_the_broadcasts_and_nothing_of_the_team(
    client: AsyncClient, db_session: AsyncSession
):
    env = await setup_journey_environment(db_session, client, org_slug="feed-former-last-member")
    run_id, _ = await _run_with_code(client, env, "Feed Run")
    headers = env.auth_headers["participant1"]
    team = await create_team_and_join(client, env.slug, run_id, headers, "Team Alpha", "#FF0000")
    await _post_feed(client, env, run_id, "An alle")
    await _post_feed(client, env, run_id, "Nur fuer Alpha", team_id=team["id"])

    left = await leave_as_last_member(client, env.slug, run_id, team["id"], headers)
    assert left.status_code == 204, left.text
    await _post_feed(client, env, run_id, "An alle, spaeter")

    after = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=headers)
    assert after.status_code == 200, after.text
    texts = _texts(after)
    assert "An alle" in texts
    assert "An alle, spaeter" in texts
    assert "Nur fuer Alpha" not in texts


@pytest.mark.asyncio
async def test_feed_entry_for_a_dissolved_team_is_refused(client: AsyncClient, db_session: AsyncSession):
    """A new entry aimed at a dissolved team answers 404. An entry for all teams or for a live team works."""
    from tests.test_submissions import make_dissolved_team_world

    w = await make_dissolved_team_world(client, db_session, "feed-dissolve")
    url = f"/api/orgs/{w.slug}/runs/{w.run_id}/feed/"
    body = {"event_type": "announcement", "content": {"de": "Hallo", "en": "Hello"}}

    refused = await client.post(url, json={**body, "target_team_id": w.dead_team_id}, headers=w.coord)
    assert refused.status_code == 404

    for target in (None, w.live_team_id):
        accepted = await client.post(url, json={**body, "target_team_id": target}, headers=w.coord)
        assert accepted.status_code == 201, accepted.text


@pytest.mark.asyncio
@pytest.mark.parametrize("run_action", ["pause", "end"])
async def test_feed_entry_for_a_dissolved_team_is_404_in_a_paused_or_ended_run(
    client: AsyncClient, db_session: AsyncSession, run_action: str
):
    """The 404 holds in every run state. The ended-run check does not answer first."""
    from tests.test_submissions import make_dissolved_team_world

    w = await make_dissolved_team_world(client, db_session, f"feed-dissolve-{run_action}")
    moved = await client.post(f"/api/orgs/{w.slug}/runs/{w.run_id}/{run_action}", headers=w.coord)
    assert moved.status_code == 200, moved.text
    url = f"/api/orgs/{w.slug}/runs/{w.run_id}/feed/"
    body = {"event_type": "announcement", "content": {"de": "Hallo", "en": "Hello"}}

    refused = await client.post(url, json={**body, "target_team_id": w.dead_team_id}, headers=w.coord)
    assert refused.status_code == 404
