"""
Join-capacity race tests.

The join paths take a row lock before their capacity count — the guest join
into an existing team locks the team's row, a team creation locks the run's
row — so the limits hold under genuinely concurrent requests. These tests
race two requests against the one free place and assert the limit holds:
exactly one winner, the loser gets the coded refusal, and the capacity ends
at its maximum.

Like test_concurrency.py, these use `concurrent_client` (a session per
request) because asyncio.gather() over the shared-session `client` raises
SQLAlchemy "concurrent operations not permitted".
"""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.journey_helpers import activate_run, setup_journey_environment


@pytest.mark.asyncio
async def test_two_joins_for_last_team_place_exactly_one_succeeds(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """Two joins into a team with one free place.

    The team allows 2 members and holds 1, so of two guests joining by the
    team's share-code at the same moment, exactly one may succeed. The other
    gets TEAM_FULL, and the team ends at its maximum of 2 members.
    """
    env = await setup_journey_environment(db_session, concurrent_client, org_slug="join-capacity-race")
    coord_headers = env.auth_headers["coordinator"]
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Team Capacity Race Run",
            "settings": {"max_members_per_team": 2},
        },
    )
    assert create_resp.status_code == 201
    run_data = create_resp.json()
    join_code = run_data["join_code"]
    run_id = run_data["id"]
    await activate_run(concurrent_client, env.slug, run_id, coord_headers)

    # Guest one creates the team — 1 of 2 places taken, one free place left.
    first = await concurrent_client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Fast Voll"},
    )
    assert first.status_code == 201, first.text
    team_id = first.json()["team_id"]
    team_share_code = first.json()["team_share_code"]

    # Fresh guests for the race: drop the creator's cookie, otherwise the
    # idempotent join would recognize the creator and no-op both joins.
    concurrent_client.cookies.clear()

    async def join_by_code(display_name: str):
        return await concurrent_client.post(
            f"/api/join/{join_code}",
            json={"mode": "join", "display_name": display_name, "team_code": team_share_code},
        )

    results = await asyncio.gather(
        join_by_code("Zweite"),
        join_by_code("Dritte"),
    )
    status_codes = [r.status_code for r in results]

    success = [r for r in results if r.status_code == 201]
    refused = [r for r in results if r.status_code == 422]
    assert len(success) == 1, f"Exactly one join may take the last place, got {status_codes}"
    assert len(refused) == 1, f"The other join must be refused with 422, got {status_codes}"
    assert refused[0].json()["error_code"] == "TEAM_FULL"

    # The team ends at its maximum.
    teams_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_resp.status_code == 200
    team = next(t for t in teams_resp.json()["items"] if t["id"] == team_id)
    assert team["member_count"] == 2, f"Team must end at its maximum of 2, has {team['member_count']}"


@pytest.mark.asyncio
async def test_two_registered_joins_by_code_for_last_place_exactly_one_succeeds(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """The registered by-code join locks the team row.

    The team allows 2 members and holds 1, so of two signed-in participants
    joining by the team's share-code at the same moment, exactly one may
    succeed. The other gets TEAM_FULL, and the team ends at its maximum.
    """
    env = await setup_journey_environment(db_session, concurrent_client, org_slug="registered-race")
    coord_headers = env.auth_headers["coordinator"]
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Registered Capacity Race Run",
            "settings": {"max_members_per_team": 2},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    run_data = create_resp.json()
    join_code = run_data["join_code"]
    run_id = run_data["id"]
    await activate_run(concurrent_client, env.slug, run_id, coord_headers)

    # A guest creates the team — 1 of 2 places taken, one free place left.
    first = await concurrent_client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Fast Voll R"},
    )
    assert first.status_code == 201, first.text
    team_id = first.json()["team_id"]
    team_share_code = first.json()["team_share_code"]

    # The two registered participants race for the last place. Distinct
    # headers (distinct users), same client address — the rate limiter only
    # records lookup failures, and these lookups succeed.
    async def join_by_code(headers: dict[str, str]):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/by-code",
            headers=headers,
            json={"team_code": team_share_code},
        )

    results = await asyncio.gather(
        join_by_code(env.auth_headers["participant1"]),
        join_by_code(env.auth_headers["participant2"]),
    )
    status_codes = [r.status_code for r in results]

    success = [r for r in results if r.status_code == 204]
    refused = [r for r in results if r.status_code == 422]
    assert len(success) == 1, f"Exactly one join may take the last place, got {status_codes}"
    assert len(refused) == 1, f"The other join must be refused with 422, got {status_codes}"
    assert refused[0].json()["error_code"] == "TEAM_FULL"

    # The team ends at its maximum.
    teams_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_resp.status_code == 200
    team = next(t for t in teams_resp.json()["items"] if t["id"] == team_id)
    assert team["member_count"] == 2, f"Team must end at its maximum of 2, has {team['member_count']}"


@pytest.mark.asyncio
async def test_eight_joins_at_once_with_the_correct_code_are_not_limited(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """Eight joins at once with the correct code get no 429.

    All eight come from one client address. A correct code is no failure, so
    the limiter stays out of it and the capacity rule decides: the team allows
    3 members and holds 1 (the guest), two participants send four joins each,
    and exactly two places are taken.
    """
    env = await setup_journey_environment(db_session, concurrent_client, org_slug="bycode-eight")
    coord_headers = env.auth_headers["coordinator"]
    await db_session.commit()

    create_resp = await concurrent_client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coord_headers,
        json={
            "rallye_id": str(env.rallye_id),
            "name": "Eight Joins Run",
            "settings": {"max_members_per_team": 3},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    run_data = create_resp.json()
    run_id = run_data["id"]
    await activate_run(concurrent_client, env.slug, run_id, coord_headers)

    first = await concurrent_client.post(
        f"/api/join/{run_data['join_code']}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Acht Joins"},
    )
    assert first.status_code == 201, first.text
    team_id = first.json()["team_id"]
    team_share_code = first.json()["team_share_code"]

    async def join_by_code(headers: dict[str, str]):
        return await concurrent_client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/by-code",
            headers=headers,
            json={"team_code": team_share_code},
        )

    results = await asyncio.gather(
        *[join_by_code(env.auth_headers[role]) for role in ("participant1", "participant2") for _ in range(4)]
    )
    status_codes = [r.status_code for r in results]

    assert 429 not in status_codes, f"A correct code must not be limited, got {status_codes}"
    assert status_codes.count(204) == 2, f"Each participant takes one place, got {status_codes}"
    assert all(code in (204, 422) for code in status_codes), status_codes

    teams_resp = await concurrent_client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/",
        headers=coord_headers,
    )
    assert teams_resp.status_code == 200
    team = next(t for t in teams_resp.json()["items"] if t["id"] == team_id)
    assert team["member_count"] == 3
