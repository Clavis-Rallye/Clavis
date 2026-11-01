"""POST /teams/{team_id}/join is off unless a test setting turns it on.

The endpoint creates a membership from the team id alone, without the team
code, so with the default settings it must answer 404 for every role: a
participant or guest cannot enter a team without its code. The suite turns it
on with ALLOW_TEAM_JOIN_BY_ID (tests/conftest.py); these tests turn it back
off to prove the default, and check the capacity under the setting.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from tests.journey_helpers import activate_run, setup_journey_environment


@pytest.fixture
def join_by_id_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production default: the endpoint is closed."""
    monkeypatch.setattr(settings.auth, "allow_team_join_by_id", False)


async def _team_ready_for_join(
    db_session: AsyncSession,
    client: AsyncClient,
    org_slug: str,
    max_members_per_team: int | None = None,
) -> tuple[dict[str, dict[str, str]], str, str]:
    """An active run with one team; returns (auth_headers, run_id, team_id)."""
    env = await setup_journey_environment(db_session, client, org_slug=org_slug)
    coord_headers = env.auth_headers["coordinator"]
    await db_session.commit()

    run_settings = {} if max_members_per_team is None else {"max_members_per_team": max_members_per_team}
    create_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Join By Id Run", "settings": run_settings},
    )
    assert create_resp.status_code == 201, create_resp.text
    run_id = create_resp.json()["id"]
    await activate_run(client, env.slug, run_id, coord_headers)

    team_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
        json={"name": "Ziel-Team"},
    )
    assert team_resp.status_code == 201, team_resp.text
    team_id = team_resp.json()["id"]
    return env.auth_headers, run_id, team_id


@pytest.mark.asyncio
async def test_join_by_id_answers_404_for_every_role_when_off(
    db_session: AsyncSession,
    client: AsyncClient,
    join_by_id_off: None,
) -> None:
    """With the default settings no role, a guest session included, may join by team id."""
    auth_headers, run_id, team_id = await _team_ready_for_join(db_session, client, "join-by-id-closed")

    # A guest session is a role too: it joins the run by its join
    # code, then tries the team id.
    join_code = (
        await client.get("/api/orgs/join-by-id-closed/runs/" + run_id, headers=auth_headers["coordinator"])
    ).json()["join_code"]
    guest_join = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Gast-Team"},
    )
    assert guest_join.status_code == 201, guest_join.text
    auth_headers["guest"] = {"Authorization": f"Bearer {guest_join.json()['token']}"}

    for role in ("participant1", "participant2", "coordinator", "creator", "org_admin", "guest"):
        response = await client.post(
            f"/api/orgs/join-by-id-closed/runs/{run_id}/teams/{team_id}/join",
            headers=auth_headers[role],
        )
        assert response.status_code == 404, f"{role} must get 404, got {response.status_code}"


@pytest.mark.asyncio
async def test_join_by_id_checks_capacity_when_on(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    """With the setting on the endpoint still enforces the team capacity."""
    auth_headers, run_id, team_id = await _team_ready_for_join(
        db_session, client, "join-by-id-capacity", max_members_per_team=1
    )

    first = await client.post(
        f"/api/orgs/join-by-id-capacity/runs/{run_id}/teams/{team_id}/join",
        headers=auth_headers["participant1"],
    )
    assert first.status_code == 204, first.text

    second = await client.post(
        f"/api/orgs/join-by-id-capacity/runs/{run_id}/teams/{team_id}/join",
        headers=auth_headers["participant2"],
    )
    assert second.status_code == 422, second.text
    assert second.json()["error_code"] == "TEAM_FULL"


@pytest.mark.asyncio
async def test_join_by_id_holds_a_guest_session_to_one_team_when_on(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    """With the setting on a guest session is held to the rules too.

    The guest entered the run with its own team, so the team id of another
    team is refused with ALREADY_IN_OTHER_TEAM and that team gains no member.
    """
    auth_headers, run_id, team_id = await _team_ready_for_join(db_session, client, "join-by-id-guest-on")
    base = f"/api/orgs/join-by-id-guest-on/runs/{run_id}"
    join_code = (await client.get(base, headers=auth_headers["coordinator"])).json()["join_code"]
    guest_join = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Gast-Team"},
    )
    assert guest_join.status_code == 201, guest_join.text
    client.cookies.clear()
    guest_headers = {"Authorization": f"Bearer {guest_join.json()['token']}"}

    response = await client.post(f"{base}/teams/{team_id}/join", headers=guest_headers)
    assert response.status_code == 422, response.text
    assert response.json()["error_code"] == "ALREADY_IN_OTHER_TEAM"

    team = await client.get(f"{base}/teams/{team_id}", headers=auth_headers["coordinator"])
    assert team.status_code == 200, team.text
    assert team.json()["member_count"] == 0
