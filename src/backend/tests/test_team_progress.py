"""
Integration tests for the team-progress endpoint's requested_help field.

Tests cover the three states of the coordinator assist window:
- no request ever made: requested_help is False
- request made, coordinator has not yet written: requested_help is True
- request made, coordinator has joined the chat: requested_help is False again
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers
from tests.test_coordinator_request import _setup_run


@pytest.mark.asyncio
async def test_team_progress_reports_requested_help_for_three_states(client: AsyncClient, db_session: AsyncSession):
    """
    Team-progress computes requested_help per team, not a hardcoded False.

    Three teams, one per state:
    - Team Alpha: never requested help -> False
    - Team Beta: requested help, no coordinator has written yet -> True
    - Team Gamma: requested help, coordinator joined -> False
    """
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)

    # _setup_run creates an active run with one team ("Team Alpha").
    run_id, alpha_id = await _setup_run(client, db_session, org.slug, coordinator, org)

    beta_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Team Beta"},
    )
    assert beta_resp.status_code == 201
    beta_id = beta_resp.json()["id"]

    gamma_resp = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Team Gamma"},
    )
    assert gamma_resp.status_code == 201
    gamma_id = gamma_resp.json()["id"]

    # Join a participant onto each of Beta and Gamma so they may request help.
    beta_member = await create_test_user(
        db_session, email="beta-member@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    beta_member_headers = get_auth_headers(beta_member)
    assert (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{beta_id}/join",
            headers=beta_member_headers,
        )
    ).status_code == 204

    gamma_member = await create_test_user(
        db_session, email="gamma-member@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    gamma_member_headers = get_auth_headers(gamma_member)
    assert (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{gamma_id}/join",
            headers=gamma_member_headers,
        )
    ).status_code == 204

    # Beta requests help and no coordinator has written yet.
    assert (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{beta_id}/chat/request-coordinator",
            headers=beta_member_headers,
        )
    ).status_code == 200

    # Gamma requests help, then a coordinator writes into the chat, which
    # marks the assist window as joined.
    assert (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{gamma_id}/chat/request-coordinator",
            headers=gamma_member_headers,
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/{gamma_id}/chat/",
            headers=coord_headers,
            json={"content": "How can I help?"},
        )
    ).status_code == 201

    response = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/team-progress", headers=coord_headers)
    assert response.status_code == 200
    entries = {entry["team_id"]: entry for entry in response.json()["teams"]}

    assert entries[alpha_id]["requested_help"] is False
    assert entries[beta_id]["requested_help"] is True
    assert entries[gamma_id]["requested_help"] is False


@pytest.mark.asyncio
async def test_team_progress_leaves_a_dissolved_team_out(client: AsyncClient, db_session: AsyncSession):
    """The progress view of the run page holds no row of a dissolved team."""
    from tests.test_submissions import make_dissolved_team_world

    w = await make_dissolved_team_world(client, db_session, "dissolved-progress")
    response = await client.get(f"/api/orgs/{w.slug}/runs/{w.run_id}/team-progress", headers=w.coord)
    assert response.status_code == 200, response.text
    assert [team["team_id"] for team in response.json()["teams"]] == [w.live_team_id]
