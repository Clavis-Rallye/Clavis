"""
Guest journey: joining a run without an account, leaving, and what a guest is stored as.

Guests authenticate through POST /api/join/{code}, which returns a JWT.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from tests.journey_helpers import (
    activate_run,
    setup_journey_environment,
    submit_challenge,
)

# =============================================================================
# Scenario: Guest Joins and Participates
# =============================================================================


@pytest.mark.asyncio
async def test_guest_joins_and_participates(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest takes part in a run without an account, from joining to feedback."""
    env = await setup_journey_environment(db_session, client, org_slug="gu1-guest-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Guest Journey Test Run"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    run_id = run_data["id"]
    join_code = run_data["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    guest_join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast Benutzer", "team_name": "Gast Team"},
    )
    assert guest_join_response.status_code == 201
    join_data = guest_join_response.json()

    guest_token = join_data["token"]
    team_id = join_data["team_id"]
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    # Guest submits challenge
    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=guest_headers,
    )
    assert stations_response.status_code == 200
    station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
        headers=guest_headers,
    )
    assert challenges_response.status_code == 200
    challenge_id = challenges_response.json()["items"][0]["id"]

    submission = await submit_challenge(
        client,
        env.slug,
        run_id,
        guest_headers,
        challenge_id,
        {"text": "Guest answer"},
    )
    assert submission["content"]["text"] == "Guest answer"

    # Guest sends chat message
    chat_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=guest_headers,
        json={"content": "Hallo vom Gast!"},
    )
    assert chat_response.status_code == 201

    # Guest views feed
    feed_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=guest_headers,
    )
    assert feed_response.status_code == 200

    # Guest views scoreboard
    scoreboard_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=guest_headers,
    )
    assert scoreboard_response.status_code == 200

    # Coordinator ends run
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=coord_headers,
    )

    # Guest submits feedback
    feedback_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=guest_headers,
        json={"rating": 5, "comment": "Sehr gut!"},
    )
    assert feedback_response.status_code == 201


# =============================================================================
# Scenario: Guest Cannot Rejoin After Leaving
# =============================================================================


@pytest.mark.asyncio
async def test_guest_cannot_rejoin_after_leaving(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest who left a team cannot rejoin the run by any path."""
    env = await setup_journey_environment(db_session, client, org_slug="gu2-rejoin-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Guest Rejoin Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    guest_join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Gast Team"},
    )
    assert guest_join_response.status_code == 201
    join_data = guest_join_response.json()
    guest_token = join_data["token"]
    team_id = join_data["team_id"]
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    # Guest leaves team
    leave_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/leave",
        headers=guest_headers,
        json={"confirm_dissolution": True},
    )
    assert leave_response.status_code == 204

    # Direct team re-join is blocked (a guest who left may not join the run again)
    rejoin_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=guest_headers,
    )
    assert rejoin_response.status_code == 403

    # guest_join mode='join' with the old team's share code is blocked too
    # (the client still carries the guest auth cookie from the first join).
    team_share_code = join_data["team_share_code"]
    rejoin_by_code = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "join", "team_code": team_share_code, "display_name": "Gast"},
    )
    assert rejoin_by_code.status_code == 403
    # The error code lets the join screen say why instead of inviting a retry.
    assert rejoin_by_code.json()["error_code"] == "RUN_DEPARTED"

    # guest_join mode='create' (new team in the same run) is blocked as well
    rejoin_by_create = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Zweites Team"},
    )
    assert rejoin_by_create.status_code == 403
    assert rejoin_by_create.json()["error_code"] == "RUN_DEPARTED"

    # Creating a team directly in the same run is blocked
    create_team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=guest_headers,
        json={"name": "Drittes Team", "color": "#FF0000"},
    )
    assert create_team_response.status_code == 403


# =============================================================================
# Scenario: Guest Data is Ephemeral
# =============================================================================


@pytest.mark.asyncio
async def test_guest_is_stored_as_guest_user_without_email(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest is stored as a User with role GUEST and no email, and as a TeamMember with is_guest set."""
    env = await setup_journey_environment(db_session, client, org_slug="gu3-ephemeral-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Guest Ephemeral Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    guest_display_name = "Ephemerer Gast"
    guest_join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": guest_display_name, "team_name": "Gast Team"},
    )
    assert guest_join_response.status_code == 201
    join_data = guest_join_response.json()
    guest_token = join_data["token"]
    user_id = join_data["user_id"]
    team_id = join_data["team_id"]
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    # The user is a guest without an email
    result = await db_session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    assert user is not None
    assert user.role == UserRole.GUEST
    assert user.email is None
    assert user.display_name == guest_display_name

    # The team member is marked as guest
    result = await db_session.execute(select(TeamMember).where(TeamMember.team_id == team_id))
    member = result.scalar_one_or_none()
    assert member is not None
    assert member.is_guest is True
    assert member.display_name == guest_display_name

    # Guest submits challenge
    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=guest_headers,
    )
    assert stations_response.status_code == 200
    station_id = stations_response.json()["items"][0]["id"]

    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/",
        headers=guest_headers,
    )
    assert challenges_response.status_code == 200
    challenge_id = challenges_response.json()["items"][0]["id"]

    submission = await submit_challenge(
        client,
        env.slug,
        run_id,
        guest_headers,
        challenge_id,
        {"text": "Guest ephemeral answer"},
    )
    submission_id = submission["id"]

    # The coordinator can see the guest's submission
    coord_view_submission = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/{submission_id}",
        headers=coord_headers,
    )
    assert coord_view_submission.status_code == 200
    submission_data = coord_view_submission.json()
    assert submission_data["id"] == submission_id
    assert "team_id" in submission_data or "team" in submission_data


# =============================================================================
# Guest Join Endpoint Tests
# =============================================================================


@pytest.mark.asyncio
async def test_guest_join_valid_code_returns_201(client: AsyncClient, db_session: AsyncSession) -> None:
    """A valid join code returns 201 with the token and the ids of user, team and run."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-valid-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Valid Code Test Run"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    run_id = run_data["id"]
    join_code = run_data["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create"},
    )
    assert join_response.status_code == 201
    join_data = join_response.json()

    assert "token" in join_data
    assert "user_id" in join_data
    assert "team_id" in join_data
    assert "run_id" in join_data
    assert "display_name" in join_data
    assert "team_name" in join_data
    assert join_data["run_id"] == run_id


@pytest.mark.asyncio
async def test_guest_join_invalid_code_returns_404(client: AsyncClient, db_session: AsyncSession) -> None:
    """An unknown join code returns 404 with a not-found message."""
    join_response = await client.post(
        "/api/join/XXXXXX",
        json={"mode": "create"},
    )
    assert join_response.status_code == 404
    assert "not found" in join_response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_guest_join_non_active_run_returns_422(client: AsyncClient, db_session: AsyncSession) -> None:
    """Joining a run that is configured, paused or ended returns 422 with the state in the message."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-nonactive-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Non-Active Test Run"},
    )
    assert create_response.status_code == 201
    run_data = create_response.json()
    run_id = run_data["id"]
    join_code = run_data["join_code"]

    # A configured run is not open yet
    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create"},
    )
    assert join_response.status_code == 422
    assert "not started" in join_response.json()["detail"].lower()

    # Start and then pause the run
    await activate_run(client, env.slug, run_id, coord_headers)

    pause_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/pause",
        headers=coord_headers,
    )
    assert pause_response.status_code == 200

    # A paused run is closed
    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create"},
    )
    assert join_response.status_code == 422
    assert "paused" in join_response.json()["detail"].lower()

    # Resume and end the run
    resume_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/resume",
        headers=coord_headers,
    )
    assert resume_response.status_code == 200

    end_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=coord_headers,
    )
    assert end_response.status_code == 200

    # An ended run is closed
    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create"},
    )
    assert join_response.status_code == 422
    assert "ended" in join_response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_guest_join_with_display_name(client: AsyncClient, db_session: AsyncSession) -> None:
    """The display name of the request is used and returned."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-displayname-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Display Name Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    custom_name = "Mein Anzeigename"
    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": custom_name},
    )
    assert join_response.status_code == 201
    join_data = join_response.json()

    assert join_data["display_name"] == custom_name


@pytest.mark.asyncio
async def test_guest_join_creates_team_when_none_exist(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest join in mode create makes a new team when the run has none."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-createteam-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Create Team Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    # The run starts without teams
    teams_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_response.status_code == 200
    assert teams_response.json()["total"] == 0

    # The join creates the team
    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "team_name": "Auto Created Team"},
    )
    assert join_response.status_code == 201
    join_data = join_response.json()

    assert "team_id" in join_data
    assert join_data["team_name"] == "Auto Created Team"

    # The team exists now
    teams_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_response.status_code == 200
    assert teams_response.json()["total"] == 1


@pytest.mark.asyncio
async def test_guest_join_respects_max_teams(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest cannot open a new team once max_teams is reached and the team is full."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-maxteams-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Max Teams Test Run",
            "max_teams": 1,
            "settings": {"max_members_per_team": 1},
        },
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    # First guest joins (creates team, fills max_teams)
    join_response1 = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Guest 1", "team_name": "Team 1"},
    )
    assert join_response1.status_code == 201

    # Clear cookies so the second request is a fresh guest. Otherwise the join is idempotent
    # and returns the team of the first guest.
    client.cookies.clear()

    # Second guest tries to join with a new team - should fail
    join_response2 = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Guest 2", "team_name": "Team 2"},
    )
    assert join_response2.status_code == 422
    assert "maximum" in join_response2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_guest_jwt_claims_correct(client: AsyncClient, db_session: AsyncSession) -> None:
    """The guest token carries the role guest, the user id as sub, the org id and the times of issue and expiry."""
    import jwt

    from app.config import get_settings

    env = await setup_journey_environment(db_session, client, org_slug="gj-jwt-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "JWT Claims Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "JWT Test Guest"},
    )
    assert join_response.status_code == 201
    join_data = join_response.json()

    token = join_data["token"]
    settings = get_settings()
    payload = jwt.decode(token, settings.auth.jwt_secret_key, algorithms=["HS256"])

    assert payload["role"] == "guest"
    assert payload["sub"] == join_data["user_id"]
    assert payload["org_id"] == str(env.org.id)
    assert "exp" in payload
    assert "iat" in payload


@pytest.mark.asyncio
async def test_guest_can_access_run_endpoints(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest can read stations, feed and scoreboard of the run."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-access-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Access Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Access Test Guest"},
    )
    assert join_response.status_code == 201
    guest_token = join_response.json()["token"]
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    stations_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=guest_headers,
    )
    assert stations_response.status_code == 200

    feed_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=guest_headers,
    )
    assert feed_response.status_code == 200

    scoreboard_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=guest_headers,
    )
    assert scoreboard_response.status_code == 200


@pytest.mark.asyncio
async def test_guest_cannot_access_admin_endpoints(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest gets 403 on user listing, user creation and rallye creation."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-noadmin-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "No Admin Access Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "No Admin Guest"},
    )
    assert join_response.status_code == 201
    guest_token = join_response.json()["token"]
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    # Listing users needs org_admin
    users_list_response = await client.get(
        f"/api/orgs/{env.slug}/users/",
        headers=guest_headers,
    )
    assert users_list_response.status_code == 403

    # Creating users needs org_admin
    users_create_response = await client.post(
        f"/api/orgs/{env.slug}/users/",
        headers=guest_headers,
        json={
            "email": "newuser@test.com",
            "display_name": "New User",
            "password": "SecurePass123!",
            "role": "participant",
        },
    )
    assert users_create_response.status_code == 403

    # Creating rallyes needs the creator role
    rallye_create_response = await client.post(
        f"/api/orgs/{env.slug}/rallyes/",
        headers=guest_headers,
        json={
            "title": {"de": "Test Rallye", "en": "Test Rally"},
            "description": {"de": "Test", "en": "Test"},
        },
    )
    assert rallye_create_response.status_code == 403


@pytest.mark.asyncio
async def test_guest_join_case_insensitive_code(client: AsyncClient, db_session: AsyncSession) -> None:
    """Join codes match regardless of letter case."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-case-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Case Insensitive Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    join_response_lower = await client.post(
        f"/api/join/{join_code.lower()}",
        json={"mode": "create", "display_name": "Lowercase Guest"},
    )
    assert join_response_lower.status_code == 201

    join_response_upper = await client.post(
        f"/api/join/{join_code.upper()}",
        json={"mode": "create", "display_name": "Uppercase Guest"},
    )
    assert join_response_upper.status_code == 201


@pytest.mark.asyncio
async def test_guest_join_without_body(client: AsyncClient, db_session: AsyncSession) -> None:
    """A join with only mode create generates a default display name and leaves the team name unset."""
    env = await setup_journey_environment(db_session, client, org_slug="gj-nobody-org")
    coord_headers = env.auth_headers["coordinator"]

    create_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "No Body Test Run"},
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["id"]
    join_code = create_response.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    join_response = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create"},
    )
    assert join_response.status_code == 201
    join_data = join_response.json()

    assert join_data["display_name"].startswith("Guest")
    assert len(join_data["display_name"]) == 9  # "Guest" + 4 digits

    # The team is name-pending: team_name is present but null
    assert "team_name" in join_data
    assert join_data["team_name"] is None
