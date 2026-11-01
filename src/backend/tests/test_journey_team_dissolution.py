"""
Three teams, the middle one dissolves.

One run holds three teams with graded answers on ranks 1, 2 and 3. The team on
rank 2 dissolves, and every reader of the run is read before and after through
the calls a client makes: the team list, the team limit, the scoreboard and the
leaderboard of a member, the progress view, the review list with its counts,
the score list, the statistics with station funnel and task table and their
exports, the statistics of the period, the comparison of runs, the feed, the
chat of each team, the run history of a member, the state of the result link
and the public result. The run itself is read too: whether its grading is complete.
Each test asserts every number of the two other teams, not only that the
dissolved team is gone.

Two readers need different states of the run. A team is created in an active
run only, so the team limit is read there. A result is published for an ended
run only, so the stored result is read there. The journey therefore runs in
both states: ``test_dj1`` in the active run with the team limit and a new team
under the old name and share code, ``test_dj5`` after the end with a live
result link. Both run once per way a team dissolves.

The numbers of the test data, with the points of the tasks 10, 20, 15, 5, 25, 10:

- Team Rathaus: one account and one guest, six answers, 10+18+15+5+22+9 = 79 of 85
- Team Dom: one guest, answers to tasks 1, 2, 3 and 5, 6+15+12+20 = 53 of 70
- Team Fuggerei: one guest, answers to tasks 1, 3 and 4, 5+12+4 = 21 of 30
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.user import User, UserRole
from app.services import team_share_code
from app.services.retention_tasks import sweep_run_guests
from tests.factories import create_test_user, get_auth_headers
from tests.journey_helpers import (
    activate_run,
    join_as_guest,
    leave_as_last_member,
    read_run_numbers,
    remove_last_member,
    remove_team,
    setup_journey_environment,
    submit_challenge,
)

RATHAUS = "Team Rathaus"
DOM = "Team Dom"
FUGGEREI = "Team Fuggerei"

# Points per team: index of the task (station by station, two tasks each) to the points given.
POINTS: dict[str, dict[int, int]] = {
    RATHAUS: {0: 10, 1: 18, 2: 15, 3: 5, 4: 22, 5: 9},
    DOM: {0: 6, 1: 15, 2: 12, 4: 20},
    FUGGEREI: {0: 5, 2: 12, 3: 4},
}

# The three ways a team loses its last member or goes as a whole.
WAYS = ["last_member_removed", "last_member_left", "team_removed"]


# =============================================================================
# The numbers every reader must show
# =============================================================================
#
# Scoreboard entry: (rank, name, points, highest possible points, graded answers).
# Progress: (members, stations done, stations, tasks answered, tasks, answers graded, points, highest possible).
# Statistics, team: (members, guests, points, tasks answered, tasks graded).
# Statistics, station: (teams with an answer, teams with a graded answer).
# Statistics, task: (answers, graded, mean points, highest possible points, teams with a measured time).
# Chat: messages of the team as the Spielleitung reads them, its own one and one of the system.

_ALL_THREE: dict[str, Any] = {
    "team_list": {"total": 3, "teams": {RATHAUS: (2, 1), DOM: (1, 1), FUGGEREI: (1, 1)}},
    "board": [(1, RATHAUS, 79, 85, 6), (2, DOM, 53, 70, 4), (3, FUGGEREI, 21, 30, 3)],
    "answers": 13,
    "progress": {
        RATHAUS: (2, 3, 3, 6, 6, 6, 79, 85),
        DOM: (1, 1, 3, 4, 6, 4, 53, 70),
        FUGGEREI: (1, 1, 3, 3, 6, 3, 21, 30),
    },
    "review_by_team": {RATHAUS: (6, 79), DOM: (4, 53), FUGGEREI: (3, 21)},
    "statistics": {
        "participation": (3, 4, 3, 1),
        "teams": {RATHAUS: (2, 1, 79, 6, 6), DOM: (1, 1, 53, 4, 4), FUGGEREI: (1, 1, 21, 3, 3)},
        "stations": [(3, 3), (3, 3), (2, 2)],
        "challenges": [
            (3, 3, 7.0, 10, 3),
            (2, 2, 16.5, 20, 2),
            (3, 3, 13.0, 15, 3),
            (2, 2, 4.5, 5, 2),
            (2, 2, 21.0, 25, 2),
            (1, 1, 9.0, 10, 1),
        ],
        "timeline_submissions": 13,
        "teams_measured": 3,
    },
    "chat": {RATHAUS: 2, DOM: 2, FUGGEREI: 2},
    "public_result": [(1, RATHAUS, 79, True), (2, DOM, 53, False), (3, FUGGEREI, 21, False)],
}

# Team Dom is gone: Team Fuggerei is on rank 2, and no number of the two others moved.
_WITHOUT_DOM: dict[str, Any] = {
    "team_list": {"total": 2, "teams": {RATHAUS: (2, 1), FUGGEREI: (1, 1)}},
    "board": [(1, RATHAUS, 79, 85, 6), (2, FUGGEREI, 21, 30, 3)],
    "answers": 9,
    "progress": {
        RATHAUS: (2, 3, 3, 6, 6, 6, 79, 85),
        FUGGEREI: (1, 1, 3, 3, 6, 3, 21, 30),
    },
    "review_by_team": {RATHAUS: (6, 79), FUGGEREI: (3, 21)},
    "statistics": {
        "participation": (2, 3, 2, 1),
        "teams": {RATHAUS: (2, 1, 79, 6, 6), FUGGEREI: (1, 1, 21, 3, 3)},
        "stations": [(2, 2), (2, 2), (1, 1)],
        "challenges": [
            (2, 2, 7.5, 10, 2),
            (1, 1, 18.0, 20, 1),
            (2, 2, 13.5, 15, 2),
            (2, 2, 4.5, 5, 2),
            (1, 1, 22.0, 25, 1),
            (1, 1, 9.0, 10, 1),
        ],
        "timeline_submissions": 9,
        "teams_measured": 2,
    },
    "chat": {RATHAUS: 2, DOM: "no team", FUGGEREI: 2},
    "public_result": [(1, RATHAUS, 79, True), (2, FUGGEREI, 21, False)],
}

# A new team took the name of Team Dom: one account, no answer, no point of the old team.
_WITH_NEW_DOM: dict[str, Any] = {
    "team_list": {"total": 3, "teams": {RATHAUS: (2, 1), FUGGEREI: (1, 1), DOM: (1, 0)}},
    "board": [(1, RATHAUS, 79, 85, 6), (2, FUGGEREI, 21, 30, 3), (3, DOM, 0, 0, 0)],
    "answers": 9,
    "progress": {
        RATHAUS: (2, 3, 3, 6, 6, 6, 79, 85),
        FUGGEREI: (1, 1, 3, 3, 6, 3, 21, 30),
        DOM: (1, 0, 3, 0, 6, 0, 0, 0),
    },
    "review_by_team": {RATHAUS: (6, 79), FUGGEREI: (3, 21)},
    "statistics": {
        **_WITHOUT_DOM["statistics"],
        "participation": (3, 4, 2, 2),
        "teams": {RATHAUS: (2, 1, 79, 6, 6), FUGGEREI: (1, 1, 21, 3, 3), DOM: (1, 0, 0, 0, 0)},
    },
    # The new team did not ask for help: its chat is closed, the open chat of the old team is not its own.
    "chat": {RATHAUS: 2, FUGGEREI: 2, DOM: "closed"},
    "public_result": [(1, RATHAUS, 79, True), (2, FUGGEREI, 21, False), (3, DOM, 0, False)],
}


def _expected(stand: dict[str, Any], *, ended: bool, published: bool = False) -> dict[str, Any]:
    """What ``read_run_numbers`` must return for a run in which every answer is graded.

    The ranking is final once the run has ended, and only then the team on
    rank 1 is the winner. The run reports its grading as complete once it has
    ended, and false before that, whatever is graded.
    """
    board = {
        "entries": [(*entry, ended and entry[0] == 1 and entry[2] > 0) for entry in stand["board"]],
        "submission_count": stand["answers"],
        "scored_count": stand["answers"],
        "is_final": ended,
    }
    expected: dict[str, Any] = {
        "grading_complete": ended,
        "team_list": stand["team_list"],
        "scoreboard": board,
        "leaderboard": board,
        "progress": stand["progress"],
        "review": {
            "total": stand["answers"],
            "listed": stand["answers"],
            "ungraded": 0,
            "by_team": stand["review_by_team"],
        },
        "scores": {"total": stand["answers"], "listed": stand["answers"], "by_team": stand["review_by_team"]},
        "statistics": stand["statistics"],
        **_derived(stand),
        "chat": stand["chat"],
        # The history of Alice of Team Rathaus: place and number of teams once the ranking is final.
        "run_history": [(RATHAUS, 1, len(stand["board"]), 79) if ended else (RATHAUS, None, None, 79)],
    }
    if published:
        expected["public_result"] = stand["public_result"]
    return expected


def _derived(stand: dict[str, Any]) -> dict[str, Any]:
    """The readers that show the numbers of the statistics a second time, each with its own query.

    The exports of the statistics, the statistics of the period (the organization has this
    run and one that never started) and the comparison of the two runs.
    """
    statistics = stand["statistics"]
    team_count, members, guests, registered = statistics["participation"]
    answers = stand["answers"]
    points = sum(entry[2] for entry in stand["board"])
    measured = statistics["teams_measured"]
    finished = sum(1 for team in statistics["teams"].values() if team[3] == len(statistics["challenges"]))
    return {
        "statistics_export": {
            "teams": statistics["teams"],
            "challenges": [task[:3] for task in statistics["challenges"]],
            "working_time": {name: (team[2], team[3]) for name, team in statistics["teams"].items()},
        },
        "period": {
            "totals": (team_count, members, guests, registered, answers, answers, 0, points, measured),
            "buckets": (team_count, answers, points),
            "teams": {name: (team[2], team[3], team[4]) for name, team in statistics["teams"].items()},
            "tasks": sorted(task[:3] for task in statistics["challenges"]),
        },
        "comparison": {
            "run": (
                team_count,
                members,
                guests,
                registered,
                answers,
                answers,
                finished,
                points,
                round(points / team_count, 2) if team_count else None,
                measured,
            ),
            "stations": [(team_count, station[0]) for station in statistics["stations"]],
            "tasks": [(team_count, *task[:3]) for task in statistics["challenges"]],
        },
    }


def _assert_numbers(numbers: dict[str, Any], expected: dict[str, Any], moment: str) -> None:
    """Compare reader by reader and name every reader that differs with both values."""
    wrong = [
        f"{reader}:\n    expected {expected[reader]!r}\n    read     {numbers.get(reader)!r}"
        for reader in expected
        if numbers.get(reader) != expected[reader]
    ]
    assert not wrong, f"{moment}: {len(wrong)} reader(s) show wrong numbers\n" + "\n".join(wrong)


# =============================================================================
# The stand: the run the journeys start from
# =============================================================================


@dataclass
class _Team:
    id: str
    name: str
    share_code: str
    member_headers: list[dict[str, str]]


@dataclass
class _Stand:
    slug: str
    org_id: Any
    run_id: str
    join_code: str
    coord: dict[str, str]
    challenges: list[str]
    spare: list[dict[str, str]]
    compare_run_id: str | None = None
    teams: dict[str, _Team] = field(default_factory=dict)
    feed_ids: dict[str, str] = field(default_factory=dict)
    result_slug: str | None = None

    @property
    def base(self) -> str:
        return f"/api/orgs/{self.slug}/runs/{self.run_id}"

    def team_names(self) -> dict[str, str]:
        return {team.id: team.name for team in self.teams.values()}


async def _build_stand(
    client: AsyncClient,
    db_session: AsyncSession,
    slug: str,
    *,
    team_names: tuple[str, ...] = (RATHAUS, DOM, FUGGEREI),
    max_teams: int = 3,
) -> _Stand:
    """An active run with the named teams, their answers graded, and three feed entries."""
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug=slug,
        num_stations=3,
        challenges_per_station=2,
        challenge_type_override=ChallengeType.TEXT,
    )
    alice = await create_test_user(
        db_session,
        email=f"alice@{slug}.test",
        password="AlicePass123!",
        display_name="Alice",
        role=UserRole.PARTICIPANT,
        organization=env.org,
    )
    coord = env.auth_headers["coordinator"]

    created = await client.post(
        f"/api/orgs/{slug}/runs/",
        headers=coord,
        json={"rallye_id": str(env.rallye_id), "name": "Augsburg Entdecken", "max_teams": max_teams},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    await activate_run(client, slug, run_id, coord)

    challenges: list[str] = []
    stations = await client.get(f"/api/orgs/{slug}/runs/{run_id}/stations/", headers=coord)
    assert stations.status_code == 200
    for station in stations.json()["items"]:
        listed = await client.get(f"/api/orgs/{slug}/runs/{run_id}/stations/{station['id']}/challenges/", headers=coord)
        assert listed.status_code == 200
        challenges.extend(challenge["id"] for challenge in listed.json()["items"])
    assert len(challenges) == 6

    stand = _Stand(
        slug=slug,
        org_id=env.org.id,
        run_id=run_id,
        join_code=created.json()["join_code"],
        coord=coord,
        challenges=challenges,
        spare=[env.auth_headers["participant1"], env.auth_headers["participant2"]],
    )

    for name in team_names:
        if name == RATHAUS:
            # An account creates the team and a guest joins it by its code.
            alice_headers = get_auth_headers(alice)
            team = await client.post(
                f"{stand.base}/teams/", headers=alice_headers, json={"name": name, "color": "#FF0000"}
            )
            assert team.status_code == 201, team.text
            team_id = team.json()["id"]
            joined = await client.post(f"{stand.base}/teams/{team_id}/join", headers=alice_headers)
            assert joined.status_code == 204, joined.text
            share_code = await _share_code(client, stand, team_id)
            anna = await join_as_guest(client, stand.join_code, display_name="Anna", team_code=share_code)
            assert anna["team_id"] == team_id
            stand.teams[name] = _Team(team_id, name, share_code, [alice_headers, anna["headers"]])
        else:
            guest = await join_as_guest(client, stand.join_code, display_name=f"Gast {name}", team_name=name)
            stand.teams[name] = _Team(guest["team_id"], name, guest["team_share_code"], [guest["headers"]])

    # Every team answers and the Spielleitung grades every answer.
    for name in team_names:
        team = stand.teams[name]
        for index, points in POINTS[name].items():
            answer = await submit_challenge(
                client, slug, run_id, team.member_headers[0], challenges[index], {"text": f"{name} {index}"}
            )
            graded = await client.post(
                f"{stand.base}/scores/", headers=coord, json={"submission_id": answer["id"], "points": points}
            )
            assert graded.status_code == 201, graded.text

    # Every team writes one chat message and asks the Spielleitung for help, which opens
    # its chat to the Spielleitung and adds one message of the system.
    for name in team_names:
        team = stand.teams[name]
        message = await client.post(
            f"{stand.base}/teams/{team.id}/chat/", headers=team.member_headers[0], json={"content": f"Hallo von {name}"}
        )
        assert message.status_code == 201, message.text
        asked = await client.post(
            f"{stand.base}/teams/{team.id}/chat/request-coordinator", headers=team.member_headers[0]
        )
        assert asked.status_code == 200, asked.text

    # A second run of the same rallye, never started: the comparison needs two runs.
    second = await client.post(
        f"/api/orgs/{slug}/runs/", headers=coord, json={"rallye_id": str(env.rallye_id), "name": "Vergleichslauf"}
    )
    assert second.status_code == 201, second.text
    stand.compare_run_id = second.json()["id"]

    # One entry for all teams and one for each of the first two teams.
    targets: dict[str, str | None] = {"all": None}
    targets.update({name: stand.teams[name].id for name in team_names[:2]})
    for key, target in targets.items():
        entry = await client.post(
            f"{stand.base}/feed/",
            headers=coord,
            json={
                "event_type": "announcement",
                "content": {"de": f"Nachricht {key}", "en": f"Message {key}"},
                "target_team_id": target,
            },
        )
        assert entry.status_code == 201, entry.text
        stand.feed_ids[key] = entry.json()["id"]

    return stand


async def _share_code(client: AsyncClient, stand: _Stand, team_id: str) -> str:
    """The share code of a team as the team list shows it to the Spielleitung."""
    listed = await client.get(f"{stand.base}/teams/", headers=stand.coord)
    assert listed.status_code == 200
    return next(str(team["share_code"]) for team in listed.json()["items"] if team["id"] == team_id)


async def _read(client: AsyncClient, stand: _Stand) -> dict[str, Any]:
    """Every reader, with the leaderboard and the feed as Alice of Team Rathaus sees them."""
    rathaus = stand.teams.get(RATHAUS)
    return await read_run_numbers(
        client,
        stand.slug,
        stand.run_id,
        stand.coord,
        team_names=stand.team_names(),
        member_headers=rathaus.member_headers[0] if rathaus else None,
        result_slug=stand.result_slug,
        compare_run_id=stand.compare_run_id,
    )


async def _dissolve(client: AsyncClient, stand: _Stand, team: _Team, way: str) -> None:
    """Dissolve ``team`` in one of the three ways, and see that the team is gone by its id.

    The calls are the ones of ``journey_helpers``, which send the confirmation of the dissolution.
    """
    if way == "last_member_removed":
        listed = await client.get(f"{stand.base}/teams/", headers=stand.coord)
        assert listed.status_code == 200
        members = next(item["members"] for item in listed.json()["items"] if item["id"] == team.id)
        assert len(members) == 1
        response = await remove_last_member(client, stand.slug, stand.run_id, team.id, members[0]["id"], stand.coord)
    elif way == "last_member_left":
        response = await leave_as_last_member(client, stand.slug, stand.run_id, team.id, team.member_headers[0])
    else:
        response = await remove_team(client, stand.slug, stand.run_id, team.id, stand.coord)
    assert response.status_code == 204, f"{way}: {response.status_code} {response.text}"

    gone = await client.get(f"{stand.base}/teams/{team.id}", headers=stand.coord)
    assert gone.status_code == 404


async def _end(client: AsyncClient, stand: _Stand) -> None:
    ended = await client.post(f"{stand.base}/end", headers=stand.coord)
    assert ended.status_code == 200, ended.text
    assert ended.json()["state"] == "ended"


async def _publish(client: AsyncClient, stand: _Stand) -> None:
    published = await client.post(f"{stand.base}/results/publish", headers=stand.coord)
    assert published.status_code == 200, published.text
    stand.result_slug = published.json()["slug"]


async def _create_team(client: AsyncClient, stand: _Stand, headers: dict[str, str], name: str) -> Any:
    return await client.post(f"{stand.base}/teams/", headers=headers, json={"name": name, "color": "#00FF00"})


def _assert_feed_kept(stand: _Stand, before: dict[str, Any], after: dict[str, Any]) -> None:
    """The feed is the record of the run: every entry stays, also the one for the dissolved team."""
    assert after["feed"] == before["feed"]
    assert set(stand.feed_ids.values()) <= set(after["feed"]["entries"])


# =============================================================================
# The journey in an active run
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("way", WAYS)
async def test_middle_team_dissolves_in_an_active_run(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, way: str
) -> None:
    """
    The team on rank 2 dissolves while the run is active.

    Every reader shows two teams, Team Fuggerei on rank 2, all other numbers as before.
    The share code of Team Dom is unknown. A new team takes its name and share code, and the
    limit has one free slot, not two. A result published after the end holds the new team
    and not the old one.
    """
    stand = await _build_stand(client, db_session, f"dj1-{way.replace('_', '-')}")
    dom = stand.teams[DOM]

    before = await _read(client, stand)
    _assert_numbers(before, _expected(_ALL_THREE, ended=False), "before the dissolution")
    assert before["result_link"][0] == "unpublished"
    assert stand.feed_ids["all"] in before["feed"]["member_entries"]
    assert stand.feed_ids[RATHAUS] in before["feed"]["member_entries"]
    assert stand.feed_ids[DOM] not in before["feed"]["member_entries"]
    assert stand.feed_ids[DOM] in before["feed"]["entries"]

    refused = await _create_team(client, stand, stand.spare[0], "Team Vier")
    assert refused.status_code == 422, refused.text
    assert refused.json()["error_code"] == "TEAM_LIMIT_REACHED"

    await _dissolve(client, stand, dom, way)

    after = await _read(client, stand)
    _assert_numbers(after, _expected(_WITHOUT_DOM, ended=False), f"after the dissolution ({way})")
    _assert_feed_kept(stand, before, after)
    assert after["result_link"] == before["result_link"]

    # The share code leads nowhere
    unknown = await client.post(
        f"{stand.base}/teams/by-code", headers=stand.spare[1], json={"team_code": dom.share_code}
    )
    assert unknown.status_code == 404, unknown.text

    # A new team with the name and the share code of the dissolved one
    monkeypatch.setattr(team_share_code, "generate", lambda: dom.share_code)
    new_team = await _create_team(client, stand, stand.spare[0], DOM)
    assert new_team.status_code == 201, new_team.text
    new_team_id = new_team.json()["id"]
    assert new_team_id != dom.id
    assert await _share_code(client, stand, new_team_id) == dom.share_code
    by_code = await client.post(
        f"{stand.base}/teams/by-code", headers=stand.spare[0], json={"team_code": dom.share_code}
    )
    assert by_code.status_code == 204, by_code.text
    monkeypatch.undo()

    full_again = await _create_team(client, stand, stand.spare[1], "Team Vier")
    assert full_again.status_code == 422, full_again.text
    assert full_again.json()["error_code"] == "TEAM_LIMIT_REACHED"

    # The new team carries nothing of the old one
    stand.teams[DOM] = _Team(new_team_id, DOM, dom.share_code, [stand.spare[0]])
    with_new_team = await _read(client, stand)
    _assert_numbers(with_new_team, _expected(_WITH_NEW_DOM, ended=False), "after a new team took the name")

    # A result published after the end is built without the dissolved team
    await _end(client, stand)
    await _publish(client, stand)
    published = await _read(client, stand)
    _assert_numbers(published, _expected(_WITH_NEW_DOM, ended=True, published=True), "after the end and the publish")


# =============================================================================
# The last team of a run
# =============================================================================


@pytest.mark.asyncio
async def test_last_team_of_a_run_dissolves(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    A run with one team loses it: every reader shows a run without teams, and none fails.

    No team, no answer, no point. The result published after the end has no entry.
    """
    stand = await _build_stand(client, db_session, "dj2-last-team", team_names=(DOM,), max_teams=1)

    before = await _read(client, stand)
    assert before["team_list"] == {"total": 1, "teams": {DOM: (1, 1)}}
    assert before["scoreboard"]["entries"] == [(1, DOM, 53, 70, 4, False)]
    assert before["review"] == {"total": 4, "listed": 4, "ungraded": 0, "by_team": {DOM: (4, 53)}}
    assert before["statistics"]["participation"] == (1, 1, 1, 0)
    assert before["scores"] == {"total": 4, "listed": 4, "by_team": {DOM: (4, 53)}}
    assert before["period"]["teams"] == {DOM: (53, 4, 4)}
    assert before["comparison"]["run"][:6] == (1, 1, 1, 0, 4, 4)
    assert before["chat"] == {DOM: 2}
    assert before["grading_complete"] is False

    await _dissolve(client, stand, stand.teams[DOM], "last_member_left")

    empty_board = {"entries": [], "submission_count": 0, "scored_count": 0, "is_final": False}
    no_team: dict[str, Any] = {
        # The run is active: its grading is not complete, with or without an answer.
        "grading_complete": False,
        "team_list": {"total": 0, "teams": {}},
        "scoreboard": empty_board,
        "progress": {},
        "review": {"total": 0, "listed": 0, "ungraded": 0, "by_team": {}},
        "statistics": {
            "participation": (0, 0, 0, 0),
            "teams": {},
            "stations": [(0, 0), (0, 0), (0, 0)],
            "challenges": [(0, 0, None, points, 0) for points in (10, 20, 15, 5, 25, 10)],
            "timeline_submissions": 0,
            "teams_measured": 0,
        },
        "scores": {"total": 0, "listed": 0, "by_team": {}},
        "chat": {DOM: "no team"},
    }
    no_team.update(_derived({"statistics": no_team["statistics"], "answers": 0, "board": []}))
    after = await _read(client, stand)
    _assert_numbers(after, no_team, "after the last team dissolved")
    _assert_feed_kept(stand, before, after)

    await _end(client, stand)
    await _publish(client, stand)
    published = await _read(client, stand)
    _assert_numbers(
        published,
        {
            **no_team,
            # Ended and no answer is open: the grading of a run without teams is complete.
            "grading_complete": True,
            "scoreboard": {**empty_board, "is_final": True},
            "public_result": [],
        },
        "after the end and the publish",
    )


# =============================================================================
# The dissolution completes the grading
# =============================================================================


@pytest.mark.asyncio
async def test_dissolution_of_the_team_with_the_ungraded_answer_completes_the_grading(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """
    The only ungraded answer of an ended run belongs to the team that dissolves.

    Before the removal the grading is not complete (14 answers, 13 graded), the ranking is not
    final and a publish is refused. After it the grading is complete (9 answers, 9 graded),
    Team Rathaus is the winner, and the publish goes through with the two other teams.
    """
    stand = await _build_stand(client, db_session, "dj3-ungraded")
    dom = stand.teams[DOM]
    await submit_challenge(
        client, stand.slug, stand.run_id, dom.member_headers[0], stand.challenges[5], {"text": "offen"}
    )
    await _end(client, stand)

    before = await _read(client, stand)
    assert before["grading_complete"] is False
    assert before["scoreboard"] == {
        "entries": [
            (1, RATHAUS, 79, 85, 6, False),
            (2, DOM, 53, 70, 4, False),
            (3, FUGGEREI, 21, 30, 3, False),
        ],
        "submission_count": 14,
        "scored_count": 13,
        "is_final": False,
    }
    assert before["review"] == {
        "total": 14,
        "listed": 14,
        "ungraded": 1,
        "by_team": {RATHAUS: (6, 79), DOM: (5, 53), FUGGEREI: (3, 21)},
    }
    assert before["progress"][DOM] == (1, 2, 3, 5, 6, 4, 53, 70)
    assert before["scores"] == {
        "total": 13,
        "listed": 13,
        "by_team": {RATHAUS: (6, 79), DOM: (4, 53), FUGGEREI: (3, 21)},
    }
    assert before["period"]["totals"] == (3, 4, 3, 1, 14, 13, 1, 153, 3)
    assert before["period"]["teams"] == {RATHAUS: (79, 6, 6), DOM: (53, 5, 4), FUGGEREI: (21, 3, 3)}
    assert before["comparison"]["run"] == (3, 4, 3, 1, 14, 13, 1, 153, 51.0, 3)
    assert before["chat"] == {RATHAUS: 2, DOM: 2, FUGGEREI: 2}
    assert before["run_history"] == [(RATHAUS, None, None, 79)]
    assert before["statistics"]["challenges"][5] == (2, 1, 9.0, 10, 2)
    assert before["result_link"][0] == "unpublished"
    refused = await client.post(f"{stand.base}/results/publish", headers=stand.coord)
    assert refused.status_code == 409, refused.text

    await _dissolve(client, stand, dom, "team_removed")

    after = await _read(client, stand)
    assert after["grading_complete"] is True
    _assert_numbers(after, _expected(_WITHOUT_DOM, ended=True), "after the dissolution completed the grading")
    _assert_feed_kept(stand, before, after)

    await _publish(client, stand)
    published = await _read(client, stand)
    assert published["result_link"][0] == "published"
    _assert_numbers(published, _expected(_WITHOUT_DOM, ended=True, published=True), "after the publish")


# =============================================================================
# A dissolution in a paused run
# =============================================================================


@pytest.mark.asyncio
async def test_middle_team_dissolves_in_a_paused_run(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    The rule holds while the run is paused, and the run stays paused.

    Every reader shows two teams after the last member of Team Dom is removed. After the
    resume a new team takes the name and the free slot.
    """
    stand = await _build_stand(client, db_session, "dj4-paused")
    dom = stand.teams[DOM]
    paused = await client.post(f"{stand.base}/pause", headers=stand.coord)
    assert paused.status_code == 200, paused.text

    before = await _read(client, stand)
    _assert_numbers(before, _expected(_ALL_THREE, ended=False), "before the dissolution")

    await _dissolve(client, stand, dom, "last_member_removed")

    after = await _read(client, stand)
    _assert_numbers(after, _expected(_WITHOUT_DOM, ended=False), "after the dissolution in the paused run")
    _assert_feed_kept(stand, before, after)
    run = await client.get(stand.base, headers=stand.coord)
    assert run.status_code == 200
    assert run.json()["state"] == "paused"

    resumed = await client.post(f"{stand.base}/resume", headers=stand.coord)
    assert resumed.status_code == 200, resumed.text
    new_team = await _create_team(client, stand, stand.spare[0], DOM)
    assert new_team.status_code == 201, new_team.text
    new_team_id = new_team.json()["id"]
    assert new_team_id != dom.id
    new_share_code = await _share_code(client, stand, new_team_id)
    by_code = await client.post(
        f"{stand.base}/teams/by-code", headers=stand.spare[0], json={"team_code": new_share_code}
    )
    assert by_code.status_code == 204, by_code.text
    full_again = await _create_team(client, stand, stand.spare[1], "Team Vier")
    assert full_again.status_code == 422, full_again.text
    assert full_again.json()["error_code"] == "TEAM_LIMIT_REACHED"

    # The new team carries nothing of the old one
    stand.teams[DOM] = _Team(new_team_id, DOM, new_share_code, [stand.spare[0]])
    with_new_team = await _read(client, stand)
    _assert_numbers(with_new_team, _expected(_WITH_NEW_DOM, ended=False), "after a new team took the name")


# =============================================================================
# A dissolution after the end with a live result link
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("way", WAYS)
async def test_middle_team_dissolves_after_the_end_with_a_live_result_link(
    client: AsyncClient, db_session: AsyncSession, way: str
) -> None:
    """
    The team on rank 2 dissolves after the end, while the result is public.

    The public result under the same slug shows the two other teams at once, without a new
    publish. The link is the same: still published, same publish time, same end of its period.
    """
    stand = await _build_stand(client, db_session, f"dj5-{way.replace('_', '-')}")
    await _end(client, stand)
    await _publish(client, stand)

    before = await _read(client, stand)
    _assert_numbers(before, _expected(_ALL_THREE, ended=True, published=True), "before the dissolution")
    assert before["result_link"][0] == "published"

    await _dissolve(client, stand, stand.teams[DOM], way)

    after = await _read(client, stand)
    _assert_numbers(after, _expected(_WITHOUT_DOM, ended=True, published=True), f"after the dissolution ({way})")
    _assert_feed_kept(stand, before, after)
    assert after["result_link"] == before["result_link"]


# =============================================================================
# The opposite direction: the guest cleanup dissolves nothing
# =============================================================================


@pytest.mark.asyncio
async def test_guest_cleanup_after_the_end_keeps_all_three_teams(client: AsyncClient, db_session: AsyncSession) -> None:
    """
    Member rows without an account are not a dissolution.

    After the guest cleanup deletes every guest account, every reader shows all three teams
    with the points, ranks and sizes from before.
    """
    stand = await _build_stand(client, db_session, "dj6-cleanup")
    await _end(client, stand)
    await _publish(client, stand)

    before = await _read(client, stand)
    _assert_numbers(before, _expected(_ALL_THREE, ended=True, published=True), "before the cleanup")

    guests = select(func.count(User.id)).where(User.organization_id == stand.org_id, User.role == UserRole.GUEST)
    assert (await db_session.execute(guests)).scalar_one() == 3
    assert await sweep_run_guests(db_session, reference_date=datetime.now(UTC) + timedelta(days=400)) == 3
    await db_session.commit()
    db_session.expire_all()
    assert (await db_session.execute(guests)).scalar_one() == 0

    after = await _read(client, stand)
    _assert_numbers(after, _expected(_ALL_THREE, ended=True, published=True), "after the cleanup")
    assert after == before
