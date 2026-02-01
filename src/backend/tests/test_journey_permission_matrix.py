"""
Permission matrix journey tests: every endpoint × every role.

Verifies correct permission enforcement. Parametrized tests give exhaustive coverage.

Sections, each headed by a banner:
- Run Management Endpoints × All Roles
- Team Endpoints × All Roles
- Submission Endpoints × All Roles
- Score Endpoints × All Roles
- Feed Endpoints × All Roles
- Chat Endpoints (Team Member vs Non-Member vs Coordinator)
- Feedback Endpoints × All Roles
- Stable Content CRUD × Roles
- Result Link Publish Endpoint × Role
- Instance Operator Tokens × Organization Endpoints
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import create_test_user, get_auth_headers
from tests.journey_helpers import setup_journey_environment

# ============================================================================
# Run Management Endpoints × All Roles
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 201),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_create_run_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST create run × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 201 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-create-{role.value}")

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_list_runs_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET list runs × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-list-{role.value}")

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 200),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_get_single_run_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET single run × all roles.

    Expected:
    - Participant+: 200 (all roles can view run details)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-get-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_update_run_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    PUT update run × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-update-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run", "settings": {}},
    )
    run_id = run_response.json()["id"]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    # The update must provide at least one field.
    response = await client.put(
        f"/api/orgs/{env.slug}/runs/{run_id}",
        headers=headers,
        json={"name": "Updated Name", "max_teams": 10, "settings": {}},
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 204),
        (UserRole.CREATOR, 204),
        (UserRole.ORG_ADMIN, 204),
    ],
)
async def test_delete_run_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    DELETE run × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 204 (allowed)

    Note: Each test creates its own run to delete.
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-delete-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_start_run_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST /start run × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-start-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_pause_run_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST /pause run × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-pause-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/pause",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_resume_run_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST /resume run × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-resume-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/pause",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/resume",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_end_run_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST /end run × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm1-end-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm1.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=headers,
    )

    assert response.status_code == expected_status


# ============================================================================
# Team Endpoints × All Roles
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 201),
        (UserRole.COORDINATOR, 201),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_create_team_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST create team × all roles.

    Expected:
    - All roles: 201 (all users can create teams)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm2-create-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm2.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Test Team", "color": "#FF0000"},
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 200),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_list_teams_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET list teams × all roles.

    Expected:
    - All roles: 200 (all users can view teams)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm2-list-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm2.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 204),
        (UserRole.COORDINATOR, 204),
        (UserRole.CREATOR, 204),
        (UserRole.ORG_ADMIN, 204),
    ],
)
async def test_join_team_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST join team × all roles.

    Expected:
    - All roles: 204 (all users can join teams)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm2-join-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    # participant1 creates the team and joins it as its creator.
    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "Test Team", "color": "#FF0000"},
    )
    team_id = team_response.json()["id"]

    # A different user than the team creator, who is already in the team.
    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm2.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 204),
        (UserRole.COORDINATOR, 204),
        (UserRole.CREATOR, 204),
        (UserRole.ORG_ADMIN, 204),
    ],
)
async def test_leave_team_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST leave team × all roles.

    Expected:
    - All roles: 204 (all users can leave teams)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm2-leave-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm2.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Test Team", "color": "#FF0000"},
    )
    team_id = team_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=headers,
    )

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/leave",
        headers=headers,
        json={"confirm_dissolution": True},
    )

    assert response.status_code == expected_status


# ============================================================================
# Submission Endpoints × All Roles
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 201),
        (UserRole.COORDINATOR, 201),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_create_submission_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST create submission × all roles.

    Expected:
    - All roles: 201 (all team members can submit)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm3-create-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    stations_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["coordinator"],
    )
    run_stations = stations_resp.json()["items"]
    challenges_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[0]['id']}/challenges/",
        headers=env.auth_headers["coordinator"],
    )
    run_challenges = challenges_resp.json()["items"]
    run_challenge_id = run_challenges[0]["id"]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm3.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Test Team", "color": "#FF0000"},
    )
    team_id = team_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=headers,
    )

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Answer"}},
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 200),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_list_submissions_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET list submissions × all roles.

    Expected:
    - Participant: 200 (sees only own team's submissions)
    - Coordinator+: 200 (sees all submissions)

    Note: This test only verifies status code, not filtering behavior.
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm3-list-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm3.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    # A participant needs a team to access submissions.
    if role == UserRole.PARTICIPANT:
        team_resp = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
            headers=headers,
            json={"name": "Test Team", "color": "#FF0000"},
        )
        team_id = team_resp.json()["id"]
        await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
            headers=headers,
        )

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=headers,
    )

    assert response.status_code == expected_status


# ============================================================================
# Score Endpoints × All Roles
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 201),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_create_score_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST create score × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 201 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm4-create-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    stations_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["coordinator"],
    )
    run_stations = stations_resp.json()["items"]
    challenges_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[0]['id']}/challenges/",
        headers=env.auth_headers["coordinator"],
    )
    run_challenges = challenges_resp.json()["items"]
    run_challenge_id = run_challenges[0]["id"]

    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "Test Team", "color": "#FF0000"},
    )
    team_id = team_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )

    submission_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=env.auth_headers["participant1"],
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Answer"}},
    )
    submission_id = submission_response.json()["id"]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm4.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": submission_id, "points": 8, "comment": "Good answer"},
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_update_score_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    PUT update score × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm4-update-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    stations_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/",
        headers=env.auth_headers["coordinator"],
    )
    run_stations = stations_resp.json()["items"]
    challenges_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_stations[0]['id']}/challenges/",
        headers=env.auth_headers["coordinator"],
    )
    run_challenges = challenges_resp.json()["items"]
    run_challenge_id = run_challenges[0]["id"]

    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "Test Team", "color": "#FF0000"},
    )
    team_id = team_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )

    submission_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=env.auth_headers["participant1"],
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Answer"}},
    )
    submission_id = submission_response.json()["id"]

    score_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=env.auth_headers["coordinator"],
        json={"submission_id": submission_id, "points": 8, "comment": "Good answer"},
    )
    score_id = score_response.json()["id"]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm4.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.put(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/{score_id}",
        headers=headers,
        json={"points": 9, "comment": "Updated comment"},
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_list_scores_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET list scores × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm4-list-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm4.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 200),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_get_scoreboard_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET scoreboard × all roles.

    Expected:
    - All roles: 200 (all users can view scoreboard)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm4-scoreboard-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm4.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard",
        headers=headers,
    )

    assert response.status_code == expected_status


# ============================================================================
# Feed Endpoints × All Roles
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 200),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_get_feed_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET feed events × all roles.

    Expected:
    - All roles: 200 (all users can view feed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm5-get-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm5.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 201),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_post_announcement_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST feed announcement × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 201 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm5-post-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm5.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feed/",
        headers=headers,
        json={
            "event_type": "announcement",
            "content": {"de": "Testankündigung", "en": "Test announcement"},
        },
    )

    assert response.status_code == expected_status


# ============================================================================
# Chat Endpoints (Team Member vs Non-Member vs Coordinator)
# ============================================================================


@pytest.mark.asyncio
async def test_chat_team_member_can_read_and_post(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Team member can read and post chat.

    Expected:
    - Team member: GET 200, POST 201
    """
    env = await setup_journey_environment(db_session, client, org_slug="pm6-member")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "Test Team", "color": "#FF0000"},
    )
    team_id = team_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )

    post_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=env.auth_headers["participant1"],
        json={"content": "Hello team!"},
    )
    assert post_response.status_code == 201

    get_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=env.auth_headers["participant1"],
    )
    assert get_response.status_code == 200


@pytest.mark.asyncio
async def test_chat_non_member_cannot_access(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Non-member participant cannot access team chat.

    Expected:
    - Non-member participant: GET 403/404, POST 403/404
    """
    env = await setup_journey_environment(db_session, client, org_slug="pm6-nonmember")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "Test Team", "color": "#FF0000"},
    )
    team_id = team_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )

    get_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=env.auth_headers["participant2"],
    )
    assert get_response.status_code in [403, 404]

    post_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=env.auth_headers["participant2"],
        json={"content": "Hello team!"},
    )
    assert post_response.status_code in [403, 404]


@pytest.mark.asyncio
async def test_chat_coordinator_gets_access_only_after_an_assistance_request(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Coordinator chat access is on request.

    Expected:
    - Coordinator (not in team), no assistance request: GET 403, POST 403
    - After the team requests assistance: GET 200, POST 201
    """
    env = await setup_journey_environment(db_session, client, org_slug="pm6-coordinator")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )

    team_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "Test Team", "color": "#FF0000"},
    )
    team_id = team_response.json()["id"]

    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=env.auth_headers["participant1"],
    )

    # Without an assistance request the coordinator has no access.
    assert (
        await client.get(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
            headers=env.auth_headers["coordinator"],
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
            headers=env.auth_headers["coordinator"],
            json={"content": "Coordinator message"},
        )
    ).status_code == 403

    # The team requests assistance. The assist window opens.
    request_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=env.auth_headers["participant1"],
    )
    assert request_response.status_code == 200

    # Now the coordinator can read and post
    get_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=env.auth_headers["coordinator"],
    )
    assert get_response.status_code == 200

    post_response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
        headers=env.auth_headers["coordinator"],
        json={"content": "Coordinator message"},
    )
    assert post_response.status_code == 201


# ============================================================================
# Feedback Endpoints × All Roles
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 201),
        (UserRole.COORDINATOR, 201),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_post_feedback_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST feedback × all roles.

    Expected:
    - All roles: 201 (all users can submit feedback after run ends)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm7-post-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm7.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=headers,
        json={"rating": 5, "comment": "Great experience!"},
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_get_feedback_list_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET feedback list × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm7-list-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm7.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_get_feedback_summary_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET feedback summary × all roles.

    Expected:
    - Participant: 403 (insufficient role)
    - Coordinator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm7-summary-{role.value}")

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/start",
        headers=env.auth_headers["coordinator"],
    )
    await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/end",
        headers=env.auth_headers["coordinator"],
    )

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm7.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/feedback/summary",
        headers=headers,
    )

    assert response.status_code == expected_status


# ============================================================================
# Stable Content CRUD × Roles
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 200),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_get_rallyes_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    GET rallyes × all roles.

    Expected:
    - Participant: 403. No participant screen lists rallyes
    - Coordinator+: 200
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm8-get-r-{role.value}")

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm8.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get(
        f"/api/orgs/{env.slug}/rallyes/",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 403),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_post_rallye_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST rallye × all roles.

    Expected:
    - Participant/Coordinator: 403 (insufficient role)
    - Creator+: 201 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm8-post-r-{role.value}")

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm8.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/rallyes/",
        headers=headers,
        json={
            "title": {"de": "Test Rallye", "en": "Test Rally"},
            "description": {"de": "Beschreibung", "en": "Description"},
        },
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 403),
        (UserRole.CREATOR, 200),
        (UserRole.ORG_ADMIN, 200),
    ],
)
async def test_put_rallye_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    PUT rallye × all roles.

    Expected:
    - Participant/Coordinator: 403 (insufficient role)
    - Creator+: 200 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm8-put-r-{role.value}")

    rallye_id = env.rallye_id

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm8.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    # Read the current rallye data first, because the API sets omitted fields to None.
    get_response = await client.get(
        f"/api/orgs/{env.slug}/rallyes/{rallye_id}",
        headers=env.auth_headers["creator"],  # Use creator to GET (guaranteed access)
    )
    current_data = get_response.json()

    # The update must include all fields.
    response = await client.put(
        f"/api/orgs/{env.slug}/rallyes/{rallye_id}",
        headers=headers,
        json={
            "title": {"de": "Updated Rallye", "en": "Updated Rally"},
            "description": {"de": "Updated Beschreibung", "en": "Updated Description"},
            "is_published": current_data.get("is_published", False),
        },
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 403),
        (UserRole.CREATOR, 204),
        (UserRole.ORG_ADMIN, 204),
    ],
)
async def test_delete_rallye_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    DELETE rallye × all roles.

    Expected:
    - Participant/Coordinator: 403 (insufficient role)
    - Creator+: 204 (allowed)

    Note: Each test creates its own rallye to delete.
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm8-del-r-{role.value}")

    rallye_response = await client.post(
        f"/api/orgs/{env.slug}/rallyes/",
        headers=env.auth_headers["creator"],
        json={
            "title": {"de": "Delete Me", "en": "Delete Me"},
            "description": {"de": "Beschreibung", "en": "Description"},
        },
    )
    rallye_id = rallye_response.json()["id"]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm8.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.delete(
        f"/api/orgs/{env.slug}/rallyes/{rallye_id}",
        headers=headers,
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 403),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_post_station_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST station × all roles.

    Expected:
    - Participant/Coordinator: 403 (insufficient role)
    - Creator+: 201 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm8-post-s-{role.value}")

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm8.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/rallyes/{env.rallye_id}/stations",
        headers=headers,
        json={
            "title": {"de": "Test Station", "en": "Test Station"},
            "description": {"de": "Beschreibung", "en": "Description"},
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
    )

    assert response.status_code == expected_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.PARTICIPANT, 403),
        (UserRole.COORDINATOR, 403),
        (UserRole.CREATOR, 201),
        (UserRole.ORG_ADMIN, 201),
    ],
)
async def test_post_challenge_by_role(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    expected_status: int,
):
    """
    POST challenge × all roles.

    Expected:
    - Participant/Coordinator: 403 (insufficient role)
    - Creator+: 201 (allowed)
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm8-post-c-{role.value}")

    rallye_station_id = env.rallye_station_ids[0]

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm8.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.post(
        f"/api/orgs/{env.slug}/rallyes/{env.rallye_id}/stations/{rallye_station_id}/challenges",
        headers=headers,
        json={
            "challenge_type": "text",
            "title": {"de": "Test Challenge", "en": "Test Challenge"},
            "instructions": {"de": "Antworten Sie", "en": "Answer"},
            "scoring_rules": {"max_points": 10},
        },
    )

    assert response.status_code == expected_status


# ============================================================================
# Result Link Publish Endpoint × Role
# ============================================================================


@pytest.mark.asyncio
async def test_publish_results_participant_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    POST publish results as a participant → 403.

    A participant may never publish a run's result link, regardless of the
    run's state — the role check must reject before the ended/graded gate
    is even reached.
    """
    env = await setup_journey_environment(db_session, client, org_slug="pm9-publish", num_stations=1)

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=env.auth_headers["coordinator"],
        json={"rallye_id": str(env.rallye_id), "name": "Test Run"},
    )
    run_id = run_response.json()["id"]

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/results/publish",
        headers=env.auth_headers["participant1"],
    )

    assert response.status_code == 403


# ============================================================================
# Instance Operator Tokens × Organization Endpoints
# ============================================================================


@pytest.mark.asyncio
async def test_instance_token_forbidden_on_run_list(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    GET run list with an instance operator token → 403.

    An operator token (scope "instance") belongs to no organization: every
    organization endpoint must reject it, whatever role-less power the
    operator has on the instance level.
    """
    from app.auth.instance import create_instance_admin
    from app.auth.tokens import create_instance_admin_token

    env = await setup_journey_environment(db_session, client, org_slug="pm10-run-list")
    admin = await create_instance_admin(db_session, "operator@pm10.test", "OperatorPass123!")

    response = await client.get(
        f"/api/orgs/{env.slug}/runs/",
        headers={"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_instance_token_forbidden_on_org_detail(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    GET org detail with an instance operator token → 403.

    The org-scoped dependency (get_org_from_slug) inherits the rejection
    through get_current_user: an operator is a member of no organization,
    not even their "own".
    """
    from app.auth.instance import create_instance_admin
    from app.auth.tokens import create_instance_admin_token

    env = await setup_journey_environment(db_session, client, org_slug="pm10-org-detail")
    admin = await create_instance_admin(db_session, "operator@pm10.test", "OperatorPass123!")

    response = await client.get(
        f"/api/orgs/{env.slug}",
        headers={"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        UserRole.PARTICIPANT,
        UserRole.COORDINATOR,
        UserRole.CREATOR,
        UserRole.ORG_ADMIN,
    ],
)
async def test_org_role_forbidden_on_instance_me(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
):
    """
    GET /api/instance/auth/me with an organization token → 403.

    Every organization role — even org_admin — is a member of an
    organization, not an operator of the instance: the instance dependency
    answers 403 (a valid token for the wrong realm, not a missing one).
    """
    env = await setup_journey_environment(db_session, client, org_slug=f"pm10-me-{role.value}")

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm10.test",
        role=role,
        organization=env.org,
    )
    headers = get_auth_headers(test_user)

    response = await client.get("/api/instance/auth/me", headers=headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_operator_cookie_forbidden_on_run_list(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    GET run list with only the operator session cookie → 403.

    The operator session lives in its own cookie (instance_token), set by
    POST /api/instance/auth/login. A browser holding only that cookie — no
    Bearer, no access_token — is a signed-in operator in the wrong realm:
    403, not the anonymous 401.
    """
    from app.auth.instance import create_instance_admin
    from app.auth.tokens import INSTANCE_ADMIN_COOKIE_NAME, create_instance_admin_token

    env = await setup_journey_environment(db_session, client, org_slug="pm10-cookie-run-list")
    admin = await create_instance_admin(db_session, "operator@pm10.test", "OperatorPass123!")
    client.cookies.set(INSTANCE_ADMIN_COOKIE_NAME, create_instance_admin_token(admin.id))

    response = await client.get(f"/api/orgs/{env.slug}/runs/")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_operator_cookie_forbidden_on_org_detail(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    GET org detail with only the operator session cookie → 403.

    Same realm rule as the previous test through get_org_from_slug: the operator
    cookie alone must read as wrong realm (403), never as anonymous (401).
    """
    from app.auth.instance import create_instance_admin
    from app.auth.tokens import INSTANCE_ADMIN_COOKIE_NAME, create_instance_admin_token

    env = await setup_journey_environment(db_session, client, org_slug="pm10-cookie-org-detail")
    admin = await create_instance_admin(db_session, "operator@pm10.test", "OperatorPass123!")
    client.cookies.set(INSTANCE_ADMIN_COOKIE_NAME, create_instance_admin_token(admin.id))

    response = await client.get(f"/api/orgs/{env.slug}")

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        UserRole.PARTICIPANT,
        UserRole.COORDINATOR,
        UserRole.CREATOR,
        UserRole.ORG_ADMIN,
    ],
)
async def test_org_role_cookie_forbidden_on_instance_me(
    client: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
):
    """
    GET /api/instance/auth/me with only the user session cookie → 403.

    Mirror of the two tests above: an organization member whose browser holds only the
    access_token cookie is a valid token in the wrong realm — 403 for every
    organization role, not the anonymous 401.
    """
    from tests.factories import get_auth_cookies

    env = await setup_journey_environment(db_session, client, org_slug=f"pm10-cookie-me-{role.value}")

    test_user = await create_test_user(
        db_session,
        email=f"test-{role.value}@pm10.test",
        role=role,
        organization=env.org,
    )
    client.cookies.set("access_token", get_auth_cookies(test_user)["access_token"])

    response = await client.get("/api/instance/auth/me")

    assert response.status_code == 403
