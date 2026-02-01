"""
Feed events of the rich types COUNTDOWN, REMINDER and ALERT.

Covers the validation of `target_time`, the system-only types that coordination cannot create by hand,
and the feed listing.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.journey_helpers import JourneyEnv, setup_journey_environment

# =============================================================================
# COUNTDOWN with valid target_time
# =============================================================================


@pytest.mark.asyncio
async def test_countdown_with_valid_target_time(client: AsyncClient, db_session: AsyncSession) -> None:
    """A COUNTDOWN with a valid ISO 8601 `target_time` is created and keeps the time in its content."""
    env: JourneyEnv = await setup_journey_environment(db_session, client, org_slug="countdown-org")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Test Run",
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    start_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_resp.status_code == 200

    target_time = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    countdown_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "countdown",
            "content": {
                "target_time": target_time,
                "de": "Pause endet in",
                "en": "Break ends in",
            },
        },
    )

    assert countdown_resp.status_code == 201
    countdown_data = countdown_resp.json()
    assert countdown_data["event_type"] == "countdown"
    assert countdown_data["content"]["target_time"] == target_time
    assert "de" in countdown_data["content"]
    assert "en" in countdown_data["content"]
    assert countdown_data["author_id"] == str(env.users["coordinator"].id)


# =============================================================================
# COUNTDOWN without target_time
# =============================================================================


@pytest.mark.asyncio
async def test_countdown_without_target_time(client: AsyncClient, db_session: AsyncSession) -> None:
    """A COUNTDOWN without `target_time` is refused with 422."""
    env: JourneyEnv = await setup_journey_environment(db_session, client, org_slug="countdown-no-time-org")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Test Run",
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    start_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_resp.status_code == 200

    countdown_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "countdown",
            "content": {
                "de": "Pause endet bald",
                "en": "Break ends soon",
            },
        },
    )

    assert countdown_resp.status_code == 422
    error = countdown_resp.json()
    assert "target_time" in error["detail"].lower()


# =============================================================================
# COUNTDOWN with invalid target_time
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_target_time",
    [
        "not-a-datetime",
        "2026-13-45T99:99:99Z",
        12345,
        None,
        "",
    ],
)
async def test_countdown_with_invalid_target_time(
    client: AsyncClient, db_session: AsyncSession, invalid_target_time
) -> None:
    """A COUNTDOWN with a `target_time` that is not an ISO 8601 string is refused with 422."""
    env: JourneyEnv = await setup_journey_environment(
        db_session, client, org_slug=f"countdown-invalid-{str(invalid_target_time)[:10]}"
    )

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Test Run",
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    start_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_resp.status_code == 200

    countdown_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "countdown",
            "content": {
                "target_time": invalid_target_time,
                "de": "Pause endet",
                "en": "Break ends",
            },
        },
    )

    assert countdown_resp.status_code == 422
    error = countdown_resp.json()
    assert "target_time" in error["detail"].lower()


# =============================================================================
# REMINDER creation
# =============================================================================


@pytest.mark.asyncio
async def test_reminder_creation(client: AsyncClient, db_session: AsyncSession) -> None:
    """A REMINDER is created with its multilingual content."""
    env: JourneyEnv = await setup_journey_environment(db_session, client, org_slug="reminder-org")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Test Run",
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    start_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_resp.status_code == 200

    reminder_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "reminder",
            "content": {
                "de": "Vergesst nicht, Wasser mitzubringen!",
                "en": "Don't forget to bring water!",
            },
        },
    )

    assert reminder_resp.status_code == 201
    reminder_data = reminder_resp.json()
    assert reminder_data["event_type"] == "reminder"
    assert reminder_data["content"]["de"] == "Vergesst nicht, Wasser mitzubringen!"
    assert reminder_data["content"]["en"] == "Don't forget to bring water!"
    assert reminder_data["author_id"] == str(env.users["coordinator"].id)


# =============================================================================
# ALERT creation
# =============================================================================


@pytest.mark.asyncio
async def test_alert_creation(client: AsyncClient, db_session: AsyncSession) -> None:
    """An ALERT is created with its multilingual content."""
    env: JourneyEnv = await setup_journey_environment(db_session, client, org_slug="alert-org")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Test Run",
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    start_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_resp.status_code == 200

    alert_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "alert",
            "content": {
                "de": "ACHTUNG: Gewitter im Anmarsch!",
                "en": "WARNING: Thunderstorm approaching!",
            },
        },
    )

    assert alert_resp.status_code == 201
    alert_data = alert_resp.json()
    assert alert_data["event_type"] == "alert"
    assert alert_data["content"]["de"] == "ACHTUNG: Gewitter im Anmarsch!"
    assert alert_data["content"]["en"] == "WARNING: Thunderstorm approaching!"
    assert alert_data["author_id"] == str(env.users["coordinator"].id)


# =============================================================================
# SYSTEM type manually rejected
# =============================================================================


@pytest.mark.asyncio
async def test_system_type_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """A SYSTEM event cannot be created by hand. The endpoint answers 400."""
    env: JourneyEnv = await setup_journey_environment(db_session, client, org_slug="system-reject-org")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Test Run",
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    start_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_resp.status_code == 200

    system_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "system",
            "content": {
                "de": "System-Event",
                "en": "System event",
            },
        },
    )

    assert system_resp.status_code == 400
    error = system_resp.json()
    assert "system" in error["detail"].lower()
    assert "cannot be created manually" in error["detail"].lower() or "auto-generated" in error["detail"].lower()


# =============================================================================
# MILESTONE type manually rejected
# =============================================================================


@pytest.mark.asyncio
async def test_milestone_type_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    """A MILESTONE event cannot be created by hand. The endpoint answers 400."""
    env: JourneyEnv = await setup_journey_environment(db_session, client, org_slug="milestone-reject-org")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Test Run",
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    start_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_resp.status_code == 200

    milestone_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "milestone",
            "content": {
                "de": "Meilenstein",
                "en": "Milestone",
            },
        },
    )

    assert milestone_resp.status_code == 400
    error = milestone_resp.json()
    assert "milestone" in error["detail"].lower()
    assert "cannot be created manually" in error["detail"].lower() or "auto-generated" in error["detail"].lower()


# =============================================================================
# List feed includes new types
# =============================================================================


@pytest.mark.asyncio
async def test_list_feed_includes_new_types(client: AsyncClient, db_session: AsyncSession) -> None:
    """The feed lists the new event types with their content, oldest first."""
    env: JourneyEnv = await setup_journey_environment(db_session, client, org_slug="list-feed-org")

    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Test Run",
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    start_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    assert start_resp.status_code == 200

    target_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    countdown_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "countdown",
            "content": {
                "target_time": target_time,
                "de": "Countdown",
                "en": "Countdown",
            },
        },
    )
    assert countdown_resp.status_code == 201
    countdown_id = countdown_resp.json()["id"]

    reminder_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "reminder",
            "content": {
                "de": "Erinnerung",
                "en": "Reminder",
            },
        },
    )
    assert reminder_resp.status_code == 201
    reminder_id = reminder_resp.json()["id"]

    alert_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
        json={
            "event_type": "alert",
            "content": {
                "de": "Alarm",
                "en": "Alert",
            },
        },
    )
    assert alert_resp.status_code == 201
    alert_id = alert_resp.json()["id"]

    list_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=env.auth_headers["coordinator"],
    )
    assert list_resp.status_code == 200
    feed_data = list_resp.json()

    event_types = [event["event_type"] for event in feed_data["items"]]
    assert "countdown" in event_types
    assert "reminder" in event_types
    assert "alert" in event_types

    countdown_event = next(e for e in feed_data["items"] if e["id"] == countdown_id)
    assert countdown_event["content"]["target_time"] == target_time
    assert countdown_event["content"]["de"] == "Countdown"

    reminder_event = next(e for e in feed_data["items"] if e["id"] == reminder_id)
    assert reminder_event["content"]["de"] == "Erinnerung"
    assert reminder_event["content"]["en"] == "Reminder"

    alert_event = next(e for e in feed_data["items"] if e["id"] == alert_id)
    assert alert_event["content"]["de"] == "Alarm"
    assert alert_event["content"]["en"] == "Alert"

    # The feed lists the oldest event first, so the order is the creation order.
    event_ids = [e["id"] for e in feed_data["items"]]
    countdown_index = event_ids.index(countdown_id)
    reminder_index = event_ids.index(reminder_id)
    alert_index = event_ids.index(alert_id)
    assert countdown_index < reminder_index < alert_index
