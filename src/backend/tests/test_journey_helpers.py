"""The shared journey helpers build a working environment and drive a run."""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.user import UserRole
from tests.journey_helpers import (
    activate_run,
    create_team_and_join,
    setup_journey_environment,
    submit_challenge,
)


@pytest.mark.asyncio
async def test_setup_journey_environment_creates_complete_hierarchy(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The environment holds an org, its four users and a rallye with all five challenge types."""
    env = await setup_journey_environment(db_session, client, org_slug="test-journey")

    assert env.org is not None
    assert env.slug == "test-journey"

    assert "coordinator" in env.users
    assert "creator" in env.users
    assert "participant1" in env.users
    assert "participant2" in env.users

    assert env.users["coordinator"].role == UserRole.COORDINATOR
    assert env.users["creator"].role == UserRole.CREATOR
    assert env.users["participant1"].role == UserRole.PARTICIPANT
    assert env.users["participant2"].role == UserRole.PARTICIPANT

    assert "coordinator" in env.auth_headers
    assert "Authorization" in env.auth_headers["coordinator"]

    assert env.rallye_id is not None
    assert len(env.station_ids) == 3  # Default num_stations
    assert len(env.challenge_ids) == 3  # One list per station
    assert all(len(challenges) == 2 for challenges in env.challenge_ids)  # Default challenges_per_station

    challenge_types_used = set(env.challenge_types.values())
    expected_types = {
        ChallengeType.TEXT,
        ChallengeType.PHOTO,
        ChallengeType.QR_SCAN,
        ChallengeType.QUIZ,
        ChallengeType.CREATIVE,
    }
    assert expected_types.issubset(challenge_types_used), f"Missing types: {expected_types - challenge_types_used}"


@pytest.mark.asyncio
async def test_activate_run_helper_works(client: AsyncClient, db_session: AsyncSession) -> None:
    """activate_run moves a configured run to active."""
    env = await setup_journey_environment(db_session, client, org_slug="activate-test")

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    assert create_response.json()["state"] == "configured"

    activated = await activate_run(client, env.slug, run_id, env.auth_headers["coordinator"])

    assert activated["state"] == "active"
    assert activated["started_at"] is not None


@pytest.mark.asyncio
async def test_create_team_and_join_helper_works(client: AsyncClient, db_session: AsyncSession) -> None:
    """create_team_and_join creates a team with its creator as the only member."""
    env = await setup_journey_environment(db_session, client, org_slug="team-test")

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = create_response.json()["id"]
    await activate_run(client, env.slug, run_id, env.auth_headers["coordinator"])

    team = await create_team_and_join(
        client, env.slug, run_id, env.auth_headers["participant1"], "Team Alpha", "#FF0000"
    )

    assert team["name"] == "Team Alpha"
    assert team["color"] == "#FF0000"
    assert team["member_count"] == 1  # Creator auto-joins


@pytest.mark.asyncio
async def test_submit_challenge_helper_works(client: AsyncClient, db_session: AsyncSession) -> None:
    """submit_challenge stores the submitted content for the run challenge."""
    env = await setup_journey_environment(db_session, client, org_slug="submit-test")

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = create_response.json()["id"]
    await activate_run(client, env.slug, run_id, env.auth_headers["coordinator"])

    await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant1"], "Team Alpha", "#FF0000")

    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/", headers=env.auth_headers["participant1"]
    )
    run_stations = stations_response.json()["items"]
    assert len(run_stations) > 0

    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[0]['id']}/challenges/",
        headers=env.auth_headers["participant1"],
    )
    run_challenges = challenges_response.json()["items"]
    assert len(run_challenges) > 0

    run_challenge_id = run_challenges[0]["id"]
    challenge_type = run_challenges[0]["challenge_type"]

    content: dict[str, Any] = {"text": "Test answer"}
    if challenge_type == "photo":
        content = {"photo_url": "https://storage.example.com/photo.jpg"}
    elif challenge_type == "qr_scan":
        content = {"code": "TEST-QR-CODE"}
    elif challenge_type == "quiz":
        content = {"selected_option": 1}
    elif challenge_type == "creative":
        content = {"text": "Creative work", "media_urls": ["https://storage.example.com/video.mp4"]}

    submission = await submit_challenge(
        client, env.slug, run_id, env.auth_headers["participant1"], run_challenge_id, content
    )

    assert submission["run_challenge_id"] == run_challenge_id
    assert submission["content"] == content
    assert submission["submitted_at"] is not None
