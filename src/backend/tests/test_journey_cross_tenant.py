"""
Cross-tenant isolation at the journey level.

Complete end-to-end scenarios cannot breach tenant boundaries. They extend the
repository-level cross-tenant tests.

The scenarios cover:
- runtime endpoints of another organization
- the correct slug with the resource IDs of another organization, which answers 404
- stable content of another organization
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.instance import create_instance_admin
from app.auth.tokens import create_instance_admin_token
from tests.journey_helpers import activate_run, create_team_and_join, setup_journey_environment

# =============================================================================
# Complete Cross-Tenant Runtime Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_run_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A user of org A gets 404 for every run, state-transition and snapshot endpoint of org B, through org A's slug."""
    # org-a in the slug keeps the emails unique
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    env_a.users["coordinator"]
    headers_a = env_a.auth_headers["coordinator"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=headers_a,
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    assert run_a_response.status_code == 201
    run_a_response.json()["id"]

    # org-b in the slug keeps the emails unique
    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    headers_b = env_b.auth_headers["coordinator"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=headers_b,
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    assert run_b_response.status_code == 201
    run_b_id = run_b_response.json()["id"]

    await activate_run(client, env_b.slug, run_b_id, headers_b)

    stations_b_response = await client.get(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/stations/",
        headers=headers_b,
    )
    assert stations_b_response.status_code == 200
    run_stations_b = stations_b_response.json()["items"]
    run_station_b_id = run_stations_b[0]["id"]

    # User A tries to GET org B's run (via org A's slug) → 404
    response = await client.get(f"/api/orgs/{env_a.slug}/runs/{run_b_id}", headers=headers_a)
    assert response.status_code == 404

    # User A tries to UPDATE org B's run → 404
    response = await client.put(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}",
        headers=headers_a,
        json={"name": "Hacked name"},
    )
    assert response.status_code == 404

    # User A tries to DELETE org B's run → 404
    response = await client.delete(f"/api/orgs/{env_a.slug}/runs/{run_b_id}", headers=headers_a)
    assert response.status_code == 404

    # User A tries to START org B's run → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/start",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to PAUSE org B's run → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/pause",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to RESUME org B's run → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/resume",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to END org B's run → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/end",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to GET org B's run stations (snapshots) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/stations/",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to GET org B's run challenges (snapshots) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/stations/{run_station_b_id}/challenges/",
        headers=headers_a,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_team_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A user of org A gets 404 when listing, reading, joining or leaving a team of org B."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    headers_a = env_a.auth_headers["participant1"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=env_a.auth_headers["coordinator"],
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]
    await activate_run(client, env_a.slug, run_a_id, env_a.auth_headers["coordinator"])

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    headers_b = env_b.auth_headers["participant1"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=env_b.auth_headers["coordinator"],
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]
    await activate_run(client, env_b.slug, run_b_id, env_b.auth_headers["coordinator"])

    team_b = await create_team_and_join(client, env_b.slug, run_b_id, headers_b, "Team B", "#00FF00")
    team_b_id = team_b["id"]

    # User A tries to LIST org B's teams (via org A's slug with org B's run_id) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/teams/",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to GET org B's team detail → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/teams/{team_b_id}",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to JOIN org B's team → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/teams/{team_b_id}/join",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to LEAVE org B's team → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/teams/{team_b_id}/leave",
        headers=headers_a,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_submission_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A user of org A gets 404 when reading or listing submissions of org B, and 422 when submitting to its challenge."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    headers_a = env_a.auth_headers["participant1"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=env_a.auth_headers["coordinator"],
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]
    await activate_run(client, env_a.slug, run_a_id, env_a.auth_headers["coordinator"])

    stations_a_response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/stations/",
        headers=headers_a,
    )
    run_stations_a = stations_a_response.json()["items"]
    challenges_a_response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/stations/{run_stations_a[0]['id']}/challenges/",
        headers=headers_a,
    )
    run_challenges_a = challenges_a_response.json()["items"]
    run_challenges_a[0]["id"]

    await create_team_and_join(client, env_a.slug, run_a_id, headers_a, "Team A", "#FF0000")

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    headers_b = env_b.auth_headers["participant1"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=env_b.auth_headers["coordinator"],
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]
    await activate_run(client, env_b.slug, run_b_id, env_b.auth_headers["coordinator"])

    stations_b_response = await client.get(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/stations/",
        headers=headers_b,
    )
    run_stations_b = stations_b_response.json()["items"]
    challenges_b_response = await client.get(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/stations/{run_stations_b[0]['id']}/challenges/",
        headers=headers_b,
    )
    run_challenges_b = challenges_b_response.json()["items"]
    run_challenge_b_id = run_challenges_b[0]["id"]

    await create_team_and_join(client, env_b.slug, run_b_id, headers_b, "Team B", "#00FF00")

    submission_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/submissions/",
        headers=headers_b,
        json={"run_challenge_id": run_challenge_b_id, "content": {"text": "Answer B"}},
    )
    assert submission_b_response.status_code == 201
    submission_b_id = submission_b_response.json()["id"]

    # User A tries to GET org B's submission detail (via org A's slug) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/submissions/{submission_b_id}",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to LIST org B's submissions (via org B's run_id) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/submissions/",
        headers=headers_a,
    )
    assert response.status_code == 404

    # User A tries to SUBMIT to org B's challenge (via org A's run_id) → 422
    # Repository scoping prevents finding the challenge
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/submissions/",
        headers=headers_a,
        json={"run_challenge_id": run_challenge_b_id, "content": {"text": "Hacked submission"}},
    )
    assert response.status_code == 422
    assert "challenge" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_score_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    A user of org A cannot reach the scores of org B.

    Listing scores, updating a score and reading the scoreboard answer 404.
    Creating a score for a submission of org B answers 422.
    """
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    coord_headers_a = env_a.auth_headers["coordinator"]
    participant_headers_a = env_a.auth_headers["participant1"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=coord_headers_a,
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]
    await activate_run(client, env_a.slug, run_a_id, coord_headers_a)

    stations_a_response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/stations/",
        headers=participant_headers_a,
    )
    run_stations_a = stations_a_response.json()["items"]
    challenges_a_response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/stations/{run_stations_a[0]['id']}/challenges/",
        headers=participant_headers_a,
    )
    run_challenges_a = challenges_a_response.json()["items"]
    run_challenge_a_id = run_challenges_a[0]["id"]

    await create_team_and_join(client, env_a.slug, run_a_id, participant_headers_a, "Team A", "#FF0000")
    submission_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/submissions/",
        headers=participant_headers_a,
        json={"run_challenge_id": run_challenge_a_id, "content": {"text": "Answer A"}},
    )
    submission_a_response.json()["id"]

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    coord_headers_b = env_b.auth_headers["coordinator"]
    participant_headers_b = env_b.auth_headers["participant1"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=coord_headers_b,
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]
    await activate_run(client, env_b.slug, run_b_id, coord_headers_b)

    stations_b_response = await client.get(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/stations/",
        headers=participant_headers_b,
    )
    run_stations_b = stations_b_response.json()["items"]
    challenges_b_response = await client.get(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/stations/{run_stations_b[0]['id']}/challenges/",
        headers=participant_headers_b,
    )
    run_challenges_b = challenges_b_response.json()["items"]
    run_challenge_b_id = run_challenges_b[0]["id"]

    await create_team_and_join(client, env_b.slug, run_b_id, participant_headers_b, "Team B", "#00FF00")
    submission_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/submissions/",
        headers=participant_headers_b,
        json={"run_challenge_id": run_challenge_b_id, "content": {"text": "Answer B"}},
    )
    submission_b_id = submission_b_response.json()["id"]

    score_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/scores/",
        headers=coord_headers_b,
        json={"submission_id": submission_b_id, "points": 8, "comment": "Good"},
    )
    assert score_b_response.status_code == 201
    score_b_id = score_b_response.json()["id"]

    # User A tries to LIST org B's scores (via org B's run_id) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/scores/",
        headers=coord_headers_a,
    )
    assert response.status_code == 404

    # User A tries to UPDATE org B's score → 404
    response = await client.put(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/scores/{score_b_id}",
        headers=coord_headers_a,
        json={"points": 0, "comment": "Hacked"},
    )
    assert response.status_code == 404

    # User A tries to CREATE score for org B's submission → 422
    # Repository scoping prevents finding the submission
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/scores/",
        headers=coord_headers_a,
        json={"submission_id": submission_b_id, "points": 10, "comment": "Hacked"},
    )
    assert response.status_code == 422
    assert "not found" in response.json()["detail"].lower()

    # User A tries to GET org B's scoreboard → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/scores/scoreboard",
        headers=coord_headers_a,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_feed_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A user of org A gets 404 when listing or posting feed events of org B."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    coord_headers_a = env_a.auth_headers["coordinator"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=coord_headers_a,
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]
    await activate_run(client, env_a.slug, run_a_id, coord_headers_a)

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    coord_headers_b = env_b.auth_headers["coordinator"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=coord_headers_b,
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]
    await activate_run(client, env_b.slug, run_b_id, coord_headers_b)

    feed_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/feed/",
        headers=coord_headers_b,
        json={
            "event_type": "announcement",
            "content": {"de": "Announcement B", "en": "Announcement B"},
        },
    )
    assert feed_b_response.status_code == 201

    # User A tries to LIST org B's feed events → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/feed/",
        headers=coord_headers_a,
    )
    assert response.status_code == 404

    # User A tries to CREATE feed event in org B's run → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/feed/",
        headers=coord_headers_a,
        json={
            "event_type": "announcement",
            "content": {"de": "Hacked announcement", "en": "Hacked announcement"},
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_chat_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A user of org A gets 404 when listing or sending chat messages of org B."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    participant_headers_a = env_a.auth_headers["participant1"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=env_a.auth_headers["coordinator"],
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]
    await activate_run(client, env_a.slug, run_a_id, env_a.auth_headers["coordinator"])

    team_a = await create_team_and_join(client, env_a.slug, run_a_id, participant_headers_a, "Team A", "#FF0000")
    team_a["id"]

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    participant_headers_b = env_b.auth_headers["participant1"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=env_b.auth_headers["coordinator"],
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]
    await activate_run(client, env_b.slug, run_b_id, env_b.auth_headers["coordinator"])

    team_b = await create_team_and_join(client, env_b.slug, run_b_id, participant_headers_b, "Team B", "#00FF00")
    team_b_id = team_b["id"]

    chat_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/teams/{team_b_id}/chat/",
        headers=participant_headers_b,
        json={"content": "Message B"},
    )
    assert chat_b_response.status_code == 201

    # User A tries to LIST org B's chat messages → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/teams/{team_b_id}/chat/",
        headers=participant_headers_a,
    )
    assert response.status_code == 404

    # User A tries to SEND chat message to org B's team → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/teams/{team_b_id}/chat/",
        headers=participant_headers_a,
        json={"content": "Hacked message"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_feedback_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A user of org A gets 404 when listing or submitting feedback of org B, or reading its summary."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    coord_headers_a = env_a.auth_headers["coordinator"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=coord_headers_a,
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]
    await activate_run(client, env_a.slug, run_a_id, coord_headers_a)

    await client.post(f"/api/orgs/{env_a.slug}/runs/{run_a_id}/end", headers=coord_headers_a)

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    coord_headers_b = env_b.auth_headers["coordinator"]
    participant_headers_b = env_b.auth_headers["participant1"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=coord_headers_b,
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]
    await activate_run(client, env_b.slug, run_b_id, coord_headers_b)

    await client.post(f"/api/orgs/{env_b.slug}/runs/{run_b_id}/end", headers=coord_headers_b)

    feedback_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/feedback/",
        headers=participant_headers_b,
        json={"rating": 5, "comment": "Feedback B"},
    )
    assert feedback_b_response.status_code == 201

    # User A tries to LIST org B's feedback → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/feedback/",
        headers=coord_headers_a,
    )
    assert response.status_code == 404

    # User A tries to GET org B's feedback summary → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/feedback/summary",
        headers=coord_headers_a,
    )
    assert response.status_code == 404

    # User A tries to SUBMIT feedback to org B's run → 404
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/feedback/",
        headers=coord_headers_a,
        json={"rating": 1, "comment": "Hacked feedback"},
    )
    assert response.status_code == 404


# =============================================================================
# Correct Slug but Wrong Resource IDs
# =============================================================================


@pytest.mark.asyncio
async def test_correct_slug_wrong_run_id_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    The correct slug with a run_id of another organization answers 404, not 403.

    The run is not found in the scope of the user's own organization.
    """
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    headers_a = env_a.auth_headers["coordinator"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=headers_a,
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    headers_b = env_b.auth_headers["coordinator"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=headers_b,
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]

    # User A uses CORRECT slug (org-a) but WRONG run_id (from org B) → 404
    response = await client.get(f"/api/orgs/{env_a.slug}/runs/{run_b_id}", headers=headers_a)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

    # Verify the reverse: user B cannot access run A
    response = await client.get(f"/api/orgs/{env_b.slug}/runs/{run_a_id}", headers=headers_b)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_correct_slug_wrong_team_id_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """The correct slug and run with a team_id of another organization answers 404."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    headers_a = env_a.auth_headers["participant1"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=env_a.auth_headers["coordinator"],
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]
    await activate_run(client, env_a.slug, run_a_id, env_a.auth_headers["coordinator"])

    await create_team_and_join(client, env_a.slug, run_a_id, headers_a, "Team A", "#FF0000")

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    headers_b = env_b.auth_headers["participant1"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=env_b.auth_headers["coordinator"],
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]
    await activate_run(client, env_b.slug, run_b_id, env_b.auth_headers["coordinator"])

    team_b = await create_team_and_join(client, env_b.slug, run_b_id, headers_b, "Team B", "#00FF00")
    team_b_id = team_b["id"]

    # User A uses CORRECT slug (org-a) with correct run_a_id but WRONG team_id (from org B) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/teams/{team_b_id}",
        headers=headers_a,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_correct_slug_wrong_submission_id_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """The correct slug and run with a submission_id of another organization answers 404."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    headers_a = env_a.auth_headers["participant1"]

    run_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/",
        headers=env_a.auth_headers["coordinator"],
        json={"rallye_id": str(env_a.rallye_id), "name": "Run A"},
    )
    run_a_id = run_a_response.json()["id"]
    await activate_run(client, env_a.slug, run_a_id, env_a.auth_headers["coordinator"])

    stations_a_response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/stations/",
        headers=headers_a,
    )
    run_stations_a = stations_a_response.json()["items"]
    challenges_a_response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/stations/{run_stations_a[0]['id']}/challenges/",
        headers=headers_a,
    )
    run_challenges_a = challenges_a_response.json()["items"]
    run_challenge_a_id = run_challenges_a[0]["id"]

    await create_team_and_join(client, env_a.slug, run_a_id, headers_a, "Team A", "#FF0000")
    submission_a_response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/submissions/",
        headers=headers_a,
        json={"run_challenge_id": run_challenge_a_id, "content": {"text": "Answer A"}},
    )
    submission_a_response.json()["id"]

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")
    headers_b = env_b.auth_headers["participant1"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=env_b.auth_headers["coordinator"],
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]
    await activate_run(client, env_b.slug, run_b_id, env_b.auth_headers["coordinator"])

    stations_b_response = await client.get(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/stations/",
        headers=headers_b,
    )
    run_stations_b = stations_b_response.json()["items"]
    challenges_b_response = await client.get(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/stations/{run_stations_b[0]['id']}/challenges/",
        headers=headers_b,
    )
    run_challenges_b = challenges_b_response.json()["items"]
    run_challenge_b_id = run_challenges_b[0]["id"]

    await create_team_and_join(client, env_b.slug, run_b_id, headers_b, "Team B", "#00FF00")
    submission_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/{run_b_id}/submissions/",
        headers=headers_b,
        json={"run_challenge_id": run_challenge_b_id, "content": {"text": "Answer B"}},
    )
    submission_b_id = submission_b_response.json()["id"]

    # User A uses CORRECT slug and run_id but WRONG submission_id (from org B) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_a_id}/submissions/{submission_b_id}",
        headers=headers_a,
    )
    assert response.status_code == 404


# =============================================================================
# Stable Content Cross-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_rallye_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    A user of org A cannot reach the rallyes of org B.

    Org B's slug answers 403. Org A's slug with a rallye_id of org B answers 404.
    """
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    creator_headers_a = env_a.auth_headers["creator"]

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")

    # User A tries to LIST org B's rallyes (via org B's slug) → 403
    response = await client.get(
        f"/api/orgs/{env_b.slug}/rallyes/",
        headers=creator_headers_a,
    )
    assert response.status_code == 403
    assert "access denied" in response.json()["detail"].lower()

    # User A tries to GET org B's rallye (via org A's slug with org B's rallye_id) → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/rallyes/{env_b.rallye_id}",
        headers=creator_headers_a,
    )
    assert response.status_code == 404

    # User A tries to UPDATE org B's rallye → 404
    response = await client.put(
        f"/api/orgs/{env_a.slug}/rallyes/{env_b.rallye_id}",
        headers=creator_headers_a,
        json={"title": {"de": "Hacked title", "en": "Hacked title"}},
    )
    assert response.status_code == 404

    # User A tries to DELETE org B's rallye → 404
    response = await client.delete(
        f"/api/orgs/{env_a.slug}/rallyes/{env_b.rallye_id}",
        headers=creator_headers_a,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_station_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A user of org A cannot reach the stations of org B: 403 through its slug, 404 through org A's slug."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    creator_headers_a = env_a.auth_headers["creator"]

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")

    # User A tries to LIST org B's stations (via org B's slug) → 403 (wrong tenant)
    response = await client.get(
        f"/api/orgs/{env_b.slug}/rallyes/{env_b.rallye_id}/stations",
        headers=creator_headers_a,
    )
    assert response.status_code == 403

    # User A tries to GET org B's library station via org A's slug → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/stations/{env_b.station_ids[0]}",
        headers=creator_headers_a,
    )
    assert response.status_code == 404

    # User A tries to CREATE station under org B's rallye (A's slug) → 404 (rallye not in org A)
    response = await client.post(
        f"/api/orgs/{env_a.slug}/rallyes/{env_b.rallye_id}/stations",
        headers=creator_headers_a,
        json={
            "title": {"de": "Hacked station", "en": "Hacked station"},
            "description": {"de": "Should not be created", "en": "Should not be created"},
        },
    )
    assert response.status_code == 404

    # User A tries to UPDATE org B's library station via org A's slug → 404
    response = await client.put(
        f"/api/orgs/{env_a.slug}/stations/{env_b.station_ids[0]}",
        headers=creator_headers_a,
        json={"title": {"de": "Hacked title", "en": "Hacked title"}},
    )
    assert response.status_code == 404

    # User A tries to DELETE org B's library station via org A's slug → 404
    response = await client.delete(
        f"/api/orgs/{env_a.slug}/stations/{env_b.station_ids[0]}",
        headers=creator_headers_a,
    )
    assert response.status_code == 404

    # User A tries to IMPORT org B's station into A's rallye → 404 (station not in org A)
    response = await client.post(
        f"/api/orgs/{env_a.slug}/rallyes/{env_a.rallye_id}/stations/import",
        headers=creator_headers_a,
        json={"station_id": str(env_b.station_ids[0])},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_org_a_cannot_access_org_b_challenge_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """A user of org A cannot reach the challenges of org B: 403 through its slug, 404 through org A's slug."""
    env_a = await setup_journey_environment(db_session, client, org_slug="org-a")
    creator_headers_a = env_a.auth_headers["creator"]

    env_b = await setup_journey_environment(db_session, client, org_slug="org-b")

    challenge_b_id = env_b.challenge_ids[0][0]

    # User A tries to LIST org B's challenges (via org B's rallye-station, A's auth) → 403 (wrong tenant)
    response = await client.get(
        f"/api/orgs/{env_b.slug}/rallyes/{env_b.rallye_id}/stations/{env_b.rallye_station_ids[0]}/challenges",
        headers=creator_headers_a,
    )
    assert response.status_code == 403

    # User A tries to GET org B's library challenge via org A's slug → 404
    response = await client.get(
        f"/api/orgs/{env_a.slug}/challenges/{challenge_b_id}",
        headers=creator_headers_a,
    )
    assert response.status_code == 404

    # User A tries to CREATE challenge under org B's rallye-station (A's slug) → 404 (rallye not in org A)
    response = await client.post(
        f"/api/orgs/{env_a.slug}/rallyes/{env_b.rallye_id}/stations/{env_b.rallye_station_ids[0]}/challenges",
        headers=creator_headers_a,
        json={
            "challenge_type": "text",
            "title": {"de": "Hacked challenge", "en": "Hacked challenge"},
            "instructions": {"de": "Should not be created", "en": "Should not be created"},
            "scoring_rules": {"max_points": 99},
        },
    )
    assert response.status_code == 404

    # User A tries to UPDATE org B's library challenge via org A's slug → 404
    response = await client.put(
        f"/api/orgs/{env_a.slug}/challenges/{challenge_b_id}",
        headers=creator_headers_a,
        json={"title": {"de": "Hacked title", "en": "Hacked title"}},
    )
    assert response.status_code == 404

    # User A tries to DELETE org B's library challenge via org A's slug → 404
    response = await client.delete(
        f"/api/orgs/{env_a.slug}/challenges/{challenge_b_id}",
        headers=creator_headers_a,
    )
    assert response.status_code == 404

    # User A tries to IMPORT org B's challenge into A's rallye-station → 404 (challenge not in org A)
    response = await client.post(
        f"/api/orgs/{env_a.slug}/rallyes/{env_a.rallye_id}/stations/{env_a.rallye_station_ids[0]}/challenges/import",
        headers=creator_headers_a,
        json={"challenge_id": str(challenge_b_id)},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_coordinator_gets_404_publishing_results_of_another_organizations_run(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    A coordinator of org A cannot publish results for a run of org B.

    This holds even with org A's own slug and the run_id of org B. The run lookup is
    org-scoped (``RunRepository``), so the run is not found in org A's scope. The
    answer is 404, not 403, like the other nested run endpoints.
    """
    env_a = await setup_journey_environment(db_session, client, org_slug="ct-results-a")
    headers_a = env_a.auth_headers["coordinator"]

    env_b = await setup_journey_environment(db_session, client, org_slug="ct-results-b")
    headers_b = env_b.auth_headers["coordinator"]

    run_b_response = await client.post(
        f"/api/orgs/{env_b.slug}/runs/",
        headers=headers_b,
        json={"rallye_id": str(env_b.rallye_id), "name": "Run B"},
    )
    run_b_id = run_b_response.json()["id"]

    # User A uses their OWN (correct) slug but org B's run_id → 404 on every
    # result-link endpoint, never 403.
    response = await client.post(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/results/publish",
        headers=headers_a,
    )
    assert response.status_code == 404

    response = await client.get(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/results/link",
        headers=headers_a,
    )
    assert response.status_code == 404

    response = await client.delete(
        f"/api/orgs/{env_a.slug}/runs/{run_b_id}/results/publish",
        headers=headers_a,
    )
    assert response.status_code == 404


# =============================================================================
# The operator's cross-tenant view
# =============================================================================


@pytest.mark.asyncio
async def test_instance_operator_lists_both_organizations_org_admin_cannot(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """
    The instance operator's list spans tenants. An org admin never gets one.

    An org admin is bound to their organization. The organization list at
    /api/instance/organizations belongs to the operator realm above all tenants,
    so the valid token of an org admin gets 403 there.
    """
    env_a = await setup_journey_environment(db_session, client, org_slug="operator-view-a")
    env_b = await setup_journey_environment(db_session, client, org_slug="operator-view-b")

    operator = await create_instance_admin(db_session, "operator@example.com", "OperatorPass123!")
    operator_headers = {"Authorization": f"Bearer {create_instance_admin_token(operator.id)}"}

    listing = await client.get("/api/instance/organizations", headers=operator_headers)
    assert listing.status_code == 200
    slugs = [item["slug"] for item in listing.json()["items"]]
    assert env_a.slug in slugs
    assert env_b.slug in slugs

    forbidden = await client.get("/api/instance/organizations", headers=env_a.auth_headers["org_admin"])
    assert forbidden.status_code == 403
