"""Tests for GET /api/me/run-history: the placement of a team in its runs."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import create_test_user, get_auth_headers
from tests.journey_helpers import setup_journey_environment


@pytest.mark.asyncio
async def test_a_team_without_any_answer_still_has_a_place(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    Two teams that sent nothing share place 1 of 2 once the run has ended.

    A team without a submission still counts in the placement, so the history shows a place and not "?".
    While a run is in play, or an answer is ungraded, there is no place: it would not be final.
    """
    env = await setup_journey_environment(db_session, client, org_slug="run-history-placement")
    other = await create_test_user(
        db_session, email="other@run-history.test", role=UserRole.PARTICIPANT, organization=env.org
    )
    await db_session.commit()
    coord = env.auth_headers["coordinator"]
    mine = env.auth_headers["participant1"]
    theirs = get_auth_headers(other)

    run = await client.post(
        f"/api/orgs/{env.slug}/runs/", json={"name": "History run", "rallye_id": str(env.rallye_id)}, headers=coord
    )
    assert run.status_code == 201
    run_id = run.json()["id"]
    assert (await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/start", headers=coord)).status_code == 200

    for name, headers in (("Team Alpha", mine), ("Team Bravo", theirs)):
        team = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/teams/", json={"name": name}, headers=headers)
        assert team.status_code == 201
        joined = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team.json()['id']}/join", headers=headers
        )
        assert joined.status_code == 204

    async def my_entry() -> dict:
        history = await client.get("/api/me/run-history", headers=mine)
        assert history.status_code == 200
        return next(item for item in history.json()["items"] if item["run_id"] == run_id)

    # While the run is in play there is no place yet: the history shows the state.
    playing = await my_entry()
    assert playing["team_name"] == "Team Alpha"
    assert playing["state"] == "active"
    assert playing["placement"] is None

    assert (await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coord)).status_code == 200

    # Ended and nothing left to grade: both teams sent nothing and share place 1 of 2.
    final = await my_entry()
    assert final["total_points"] == 0
    assert final["placement"] == 1
    assert final["total_teams"] == 2


@pytest.mark.asyncio
async def test_the_end_notice_does_not_promise_results_while_an_answer_is_ungraded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """
    With an ungraded answer the end notice says that grading is under way.

    Without one it says that the results are there.
    """
    from app.models.challenge import ChallengeType

    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="end-notice",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.TEXT,
    )
    coord = env.auth_headers["coordinator"]
    player = env.auth_headers["participant1"]

    async def end_notice(with_open_answer: bool) -> str:
        run = await client.post(
            f"/api/orgs/{env.slug}/runs/",
            json={"name": "End notice run", "rallye_id": str(env.rallye_id)},
            headers=coord,
        )
        run_id = run.json()["id"]
        await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/start", headers=coord)
        if with_open_answer:
            team = await client.post(
                f"/api/orgs/{env.slug}/runs/{run_id}/teams/", json={"name": "Team Alpha"}, headers=player
            )
            await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team.json()['id']}/join", headers=player)
            stations = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/stations/", headers=player)
            station_id = stations.json()["items"][0]["id"]
            challenges = await client.get(
                f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/", headers=player
            )
            assert challenges.status_code == 200, challenges.text
            run_challenge_id = challenges.json()["items"][0]["id"]
            answer = await client.post(
                f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
                json={"run_challenge_id": run_challenge_id, "content": {"text": "Eine Antwort"}},
                headers=player,
            )
            assert answer.status_code == 201, answer.text
        ended = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coord)
        assert ended.status_code == 200, ended.text
        feed = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=coord)
        return next(i["content"]["de"] for i in feed.json()["items"] if i["event_type"] == "results_available")

    assert await end_notice(with_open_answer=False) == "Eure Ergebnisse sind verfügbar!"
    assert "Die Bewertung läuft noch" in await end_notice(with_open_answer=True)


@pytest.mark.asyncio
async def test_a_former_member_holds_no_row_of_a_dissolved_team(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    The run history and the data export of a former member hold no row of the dissolved team.

    Both read through the member rows, which dissolving removes.
    """
    from tests.test_submissions import make_dissolved_team_world

    w = await make_dissolved_team_world(client, db_session, "dissolved-history")
    former_member = w.participant_dead
    dissolved_name = "Team Dead"

    history = await client.get("/api/me/run-history", headers=former_member)
    assert history.status_code == 200
    assert history.json()["items"] == []

    export = await client.get("/api/me/export", headers=former_member)
    assert export.status_code == 200
    body = export.json()
    assert w.dead_team_id not in export.text
    assert dissolved_name not in export.text
    assert body["participations"] == []
    assert body["submissions"] == []
