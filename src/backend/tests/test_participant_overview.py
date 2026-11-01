"""
What a team may see of a run's challenges, and the station list in one answer.

The per-station challenge list answers a participant without the complete scoring rules and without
the reference answer. A team cannot read the right quiz option or the expected QR value in the
browser's network tab. The participant station list takes one request per refresh, with the same
participant-safe serialization.

The rallye editor's composition reads are creator-only. The rallye list and detail are
coordinator-only. An account participant never receives the library challenge with its answers.
"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import Challenge, ChallengeType
from tests.journey_helpers import activate_run, create_team_and_join, setup_journey_environment, submit_challenge

SECRET_RULES = {"correct_option", "expected_value"}


async def _run_with_secrets(client: AsyncClient, db_session: AsyncSession, slug: str):
    """A started run whose quiz and QR challenge carry answers, plus a reference answer."""
    env = await setup_journey_environment(db_session, client, org_slug=slug)
    for challenge_id, challenge_type in env.challenge_types.items():
        rules: dict = {"max_points": 10}
        if challenge_type == ChallengeType.QUIZ:
            rules = {"max_points": 5, "options": ["Lech", "Wertach"], "correct_option": 0, "auto_approve": True}
        elif challenge_type == ChallengeType.QR_SCAN:
            rules = {"max_points": 15, "expected_value": "CLAVIS-GEHEIM", "auto_approve": True}
        await db_session.execute(
            update(Challenge)
            .where(Challenge.id == challenge_id)
            .values(scoring_rules=rules, suggested_solution="Die Referenzantwort")
        )
    await db_session.commit()

    coordinator = env.auth_headers["coordinator"]
    created = await client.post(
        f"/api/orgs/{slug}/runs/", headers=coordinator, json={"rallye_id": str(env.rallye_id), "name": "Lauf"}
    )
    run_id = created.json()["id"]
    await activate_run(client, slug, run_id, coordinator)
    return env, run_id


def _assert_safe(challenge: dict) -> None:
    assert SECRET_RULES.isdisjoint(challenge["scoring_rules"]), challenge["scoring_rules"]
    assert challenge["suggested_solution"] is None


@pytest.mark.asyncio
async def test_a_participant_never_receives_answers_and_coordination_does(
    client: AsyncClient, db_session: AsyncSession
):
    env, run_id = await _run_with_secrets(client, db_session, "safe-challenges")
    stations = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/", headers=env.auth_headers["participant1"]
    )
    station_ids = [s["id"] for s in stations.json()["items"]]

    seen_types: set[str] = set()
    for station_id in station_ids:
        url = f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/"
        participant = (await client.get(url, headers=env.auth_headers["participant1"])).json()["items"]
        coordination = (await client.get(url, headers=env.auth_headers["coordinator"])).json()["items"]
        for safe, full in zip(participant, coordination, strict=True):
            seen_types.add(safe["challenge_type"])
            _assert_safe(safe)
            # What a team needs stays: the options to pick from and the points.
            assert safe["scoring_rules"]["max_points"] == full["scoring_rules"]["max_points"]
            if safe["challenge_type"] == "quiz":
                assert safe["scoring_rules"]["options"] == ["Lech", "Wertach"]
                assert full["scoring_rules"]["correct_option"] == 0
            if safe["challenge_type"] == "qr_scan":
                assert full["scoring_rules"]["expected_value"] == "CLAVIS-GEHEIM"
            assert full["suggested_solution"] == "Die Referenzantwort"
    assert {"quiz", "qr_scan"} <= seen_types


@pytest.mark.asyncio
async def test_the_overview_answers_in_one_request_with_the_own_team_only(
    client: AsyncClient, db_session: AsyncSession
):
    env, run_id = await _run_with_secrets(client, db_session, "overview-one-request")
    own_team = await create_team_and_join(
        client, env.slug, run_id, env.auth_headers["participant1"], "Team Eins", "#FF0000"
    )
    other_team = await create_team_and_join(
        client, env.slug, run_id, env.auth_headers["participant2"], "Team Zwei", "#00FF00"
    )
    assert own_team["id"] != other_team["id"]

    overview_url = f"/api/orgs/{env.slug}/runs/{run_id}/participant-overview"
    first = (await client.get(overview_url, headers=env.auth_headers["participant1"])).json()
    stations = first["stations"]
    assert [s["sort_order"] for s in stations] == sorted(s["sort_order"] for s in stations)
    text_challenges = [c for s in stations for c in s["challenges"] if c["challenge_type"] == "text"]
    for station in stations:
        for challenge in station["challenges"]:
            _assert_safe(challenge)

    await submit_challenge(
        client, env.slug, run_id, env.auth_headers["participant1"], text_challenges[0]["id"], {"text": "Eins"}
    )
    await submit_challenge(
        client, env.slug, run_id, env.auth_headers["participant2"], text_challenges[1]["id"], {"text": "Zwei"}
    )

    mine = (await client.get(overview_url, headers=env.auth_headers["participant1"])).json()
    assert [UUID(i) for i in mine["answered_challenge_ids"]] == [UUID(text_challenges[0]["id"])]

    outsider = (await client.get(overview_url, headers=env.auth_headers["coordinator"])).json()
    assert outsider["answered_challenge_ids"] == []
    assert len(outsider["stations"]) == len(stations)


@pytest.mark.asyncio
async def test_the_rallye_editor_reads_are_closed_to_participants_and_coordinators(
    client: AsyncClient, db_session: AsyncSession
):
    """Participants are refused on every rallye read. Coordinators may read the rallye and its list, not its stations or challenges."""
    env, _run_id = await _run_with_secrets(client, db_session, "rallye-reads-closed")
    rallye_url = f"/api/orgs/{env.slug}/rallyes/{env.rallye_id}"
    link_id = env.rallye_station_ids[0]
    challenges_url = f"{rallye_url}/stations/{link_id}/challenges"

    participant = env.auth_headers["participant1"]
    for url in (f"/api/orgs/{env.slug}/rallyes/", rallye_url, f"{rallye_url}/stations", challenges_url):
        assert (await client.get(url, headers=participant)).status_code == 403, url

    coordinator = env.auth_headers["coordinator"]
    assert (await client.get(f"/api/orgs/{env.slug}/rallyes/", headers=coordinator)).status_code == 200
    assert (await client.get(rallye_url, headers=coordinator)).status_code == 200
    for url in (f"{rallye_url}/stations", challenges_url):
        assert (await client.get(url, headers=coordinator)).status_code == 403, url

    editor = (await client.get(challenges_url, headers=env.auth_headers["creator"])).json()["items"]
    assert editor and all(item["challenge"]["suggested_solution"] == "Die Referenzantwort" for item in editor)


@pytest.mark.asyncio
async def test_a_removed_member_learns_it_from_the_overview_and_the_submissions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """After the Spielleitung removes a member, the overview says is_team_member false.

    The submissions read answers 404 NOT_TEAM_MEMBER.
    """
    env, run_id = await _run_with_secrets(client, db_session, "overview-removed")
    participant = env.auth_headers["participant1"]
    team = await create_team_and_join(client, env.slug, run_id, participant, "Team Eins", "#FF0000")
    overview_url = f"/api/orgs/{env.slug}/runs/{run_id}/participant-overview"
    assert (await client.get(overview_url, headers=participant)).json()["is_team_member"] is True

    coordinator = env.auth_headers["coordinator"]
    detail = (await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}", headers=coordinator)).json()
    member_id = detail["members"][0]["id"]
    removed = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}/members/{member_id}",
        headers=coordinator,
        params={"confirm_dissolution": "true"},
    )
    assert removed.status_code == 204, removed.text

    after = await client.get(overview_url, headers=participant)
    assert after.status_code == 200
    assert after.json()["is_team_member"] is False
    assert after.json()["answered_challenge_ids"] == []
    reads = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/submissions/", headers=participant)
    assert reads.status_code == 404
    assert reads.json()["error_code"] == "NOT_TEAM_MEMBER"
