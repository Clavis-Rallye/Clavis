"""A stored result snapshot loses a dissolved team and ranks the rest again.

The snapshot is the JSON in ``run_result_links.snapshot``. Its
entries of an older snapshot carry no team id, so the entry of a team is found
by its id or, there, by its name. The ranks come from the points the snapshot
holds, never from the live scores.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_result_link import RunResultLink
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.repositories.run_result_link import RunResultLinkRepository, compute_slug, hash_slug
from app.routers import runs as runs_router
from app.services import retention_tasks, team_dissolution
from app.services.gdpr import DeletionClass
from app.services.result_snapshot import rank_entries, without_team
from app.services.team_dissolution import dissolve_team
from tests.journey_helpers import JourneyEnv, setup_journey_environment

# ---- the two pure functions -------------------------------------------------


def test_rank_entries_shares_a_rank_for_equal_points():
    assert rank_entries([("A", 30), ("B", 20), ("C", 20), ("D", 5)]) == [
        {"team_name": "A", "points": 30, "rank": 1, "is_winner": True},
        {"team_name": "B", "points": 20, "rank": 2, "is_winner": False},
        {"team_name": "C", "points": 20, "rank": 2, "is_winner": False},
        {"team_name": "D", "points": 5, "rank": 4, "is_winner": False},
    ]


def test_rank_entries_names_every_team_on_rank_one_a_winner():
    assert [e["is_winner"] for e in rank_entries([("A", 7), ("B", 7), ("C", 1)])] == [True, True, False]


def test_rank_entries_has_no_winner_with_zero_points():
    assert rank_entries([("A", 0), ("B", 0)]) == [
        {"team_name": "A", "points": 0, "rank": 1, "is_winner": False},
        {"team_name": "B", "points": 0, "rank": 1, "is_winner": False},
    ]


def test_rank_entries_of_nothing_is_nothing():
    assert rank_entries([]) == []


def _snapshot(*teams: tuple[str, int]) -> dict[str, Any]:
    return {
        "run_name": "Lauf",
        "ended_at": "2026-11-01T10:00:00+00:00",
        "language": "de",
        "entries": rank_entries(list(teams)),
    }


def test_without_team_removes_the_entry_and_ranks_the_rest_again():
    """The winner goes, the two behind it share rank 1 and both win."""
    before = _snapshot(("A", 30), ("B", 20), ("C", 20), ("D", 5))

    after = without_team(before, "A")

    assert after["entries"] == [
        {"team_name": "B", "points": 20, "rank": 1, "is_winner": True},
        {"team_name": "C", "points": 20, "rank": 1, "is_winner": True},
        {"team_name": "D", "points": 5, "rank": 3, "is_winner": False},
    ]
    assert {k: v for k, v in after.items() if k != "entries"} == {k: v for k, v in before.items() if k != "entries"}
    # The stored snapshot is not changed in place.
    assert [e["team_name"] for e in before["entries"]] == ["A", "B", "C", "D"]


def test_without_team_leaves_nobody_a_winner_when_only_zero_points_stay():
    after = without_team(_snapshot(("A", 10), ("B", 0)), "A")
    assert after["entries"] == [{"team_name": "B", "points": 0, "rank": 1, "is_winner": False}]


def test_without_team_of_the_last_entry_leaves_no_entries():
    assert without_team(_snapshot(("A", 10)), "A")["entries"] == []


def test_without_team_returns_the_snapshot_itself_when_no_entry_carries_the_name():
    before = _snapshot(("A", 10), ("B", 5))
    assert without_team(before, "Z") is before


# ---- the dissolution changes every result link row of the run ---------------


async def _run(db: AsyncSession, org_id: UUID, name: str = "Lauf") -> Run:
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name=name,
        state=RunState.ENDED,
        ended_at=datetime.now(UTC) - timedelta(hours=1),
        join_code=uuid4().hex[:6].upper(),
        settings={"max_members_per_team": 10},
    )
    db.add(run)
    await db.flush()
    return run


async def _team(db: AsyncSession, org_id: UUID, run: Run, name: str | None) -> Team:
    team = Team(organization_id=org_id, run_id=run.id, name=name, color="#123456", share_code=uuid4().hex[:6].upper())
    db.add(team)
    await db.flush()
    db.add(
        TeamMember(
            organization_id=org_id,
            team_id=team.id,
            user_id=None,
            display_name="Gast",
            is_guest=True,
            joined_at=datetime.now(UTC),
        )
    )
    await db.flush()
    return team


async def _link(
    db: AsyncSession,
    run: Run,
    snapshot: dict[str, Any],
    *,
    revoked: bool = False,
    expired: bool = False,
) -> RunResultLink:
    now = datetime.now(UTC)
    link_id = UUID(str(uuid7()))
    link = RunResultLink(
        id=link_id,
        run_id=run.id,
        slug_hash=hash_slug(compute_slug(link_id)),
        snapshot=snapshot,
        published_by=None,
        published_at=now - timedelta(days=3),
        expires_at=now - timedelta(days=1) if expired else now + timedelta(days=30),
        revoked_at=now - timedelta(days=2) if revoked else None,
    )
    db.add(link)
    await db.flush()
    return link


async def _link_row(db: AsyncSession, link_id: UUID) -> RunResultLink:
    stmt = select(RunResultLink).where(RunResultLink.id == link_id).execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one()


def _names(link: RunResultLink) -> list[str]:
    return [entry["team_name"] for entry in link.snapshot["entries"]]


async def _three_teams(db: AsyncSession, env: JourneyEnv) -> tuple[Run, Team, Team, Team]:
    run = await _run(db, env.org.id)
    alpha = await _team(db, env.org.id, run, "Alpha")
    beta = await _team(db, env.org.id, run, "Beta")
    gamma = await _team(db, env.org.id, run, "Gamma")
    return run, alpha, beta, gamma


@pytest.mark.asyncio
async def test_removing_a_team_takes_it_out_of_the_live_snapshot(client: AsyncClient, db_session: AsyncSession):
    """Through the request of the Spielleitung, and the public page shows it at once."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-live")
    run, alpha, _beta, _gamma = await _three_teams(db_session, env)
    link = await _link(db_session, run, _snapshot(("Alpha", 30), ("Beta", 20), ("Gamma", 20)))
    await db_session.commit()
    link_id = link.id
    before = (link.slug_hash, link.published_at, link.expires_at, link.revoked_at)

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{alpha.id}",
        params={"confirm_dissolution": "true"},
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 204

    row = await _link_row(db_session, link_id)
    assert row.snapshot["entries"] == [
        {"team_name": "Beta", "points": 20, "rank": 1, "is_winner": True},
        {"team_name": "Gamma", "points": 20, "rank": 1, "is_winner": True},
    ]
    assert row.snapshot["run_name"] == "Lauf"
    assert row.snapshot["language"] == "de"
    assert (row.slug_hash, row.published_at, row.expires_at, row.revoked_at) == before

    public = await client.post("/api/public/results/lookup", json={"slug": compute_slug(link_id)})
    assert public.status_code == 200
    assert "Alpha" not in public.text
    assert [(e["team_name"], e["rank"], e["is_winner"]) for e in public.json()["entries"]] == [
        ("Beta", 1, True),
        ("Gamma", 1, True),
    ]


@pytest.mark.asyncio
async def test_the_snapshot_changes_in_the_transaction_of_the_dissolution(
    client: AsyncClient, db_session: AsyncSession
):
    """The caller owns the transaction. A rollback takes the change of the snapshot back with the team."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-tx")
    run, alpha, _beta, _gamma = await _three_teams(db_session, env)
    link = await _link(db_session, run, _snapshot(("Alpha", 30), ("Beta", 20), ("Gamma", 20)))
    await db_session.commit()
    link_id, alpha_id = link.id, alpha.id

    await dissolve_team(db_session, alpha)
    assert _names(await _link_row(db_session, link_id)) == ["Beta", "Gamma"]
    await db_session.rollback()

    assert _names(await _link_row(db_session, link_id)) == ["Alpha", "Beta", "Gamma"]
    team = (
        await db_session.execute(select(Team).where(Team.id == alpha_id).execution_options(populate_existing=True))
    ).scalar_one()
    assert team.is_deleted is False


@pytest.mark.asyncio
async def test_revoked_and_expired_links_lose_the_team_too(client: AsyncClient, db_session: AsyncSession):
    """Every row of the run, and nothing but the snapshot changes."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-dead")
    run, _alpha, beta, _gamma = await _three_teams(db_session, env)
    other_run = await _run(db_session, env.org.id, name="Anderer Lauf")
    await _team(db_session, env.org.id, other_run, "Beta")
    teams = (("Alpha", 30), ("Beta", 20), ("Gamma", 10))
    live = await _link(db_session, run, _snapshot(*teams))
    revoked = await _link(db_session, run, _snapshot(*teams), revoked=True)
    expired = await _link(db_session, run, _snapshot(*teams), revoked=True, expired=True)
    foreign = await _link(db_session, other_run, _snapshot(*teams))
    await db_session.commit()
    link_ids = [live.id, revoked.id, expired.id]
    foreign_id = foreign.id
    before = {
        link.id: (link.slug_hash, link.published_at, link.expires_at, link.revoked_at)
        for link in (live, revoked, expired)
    }

    await dissolve_team(db_session, beta)
    await db_session.commit()

    for link_id in link_ids:
        row = await _link_row(db_session, link_id)
        assert row.snapshot["entries"] == [
            {"team_name": "Alpha", "points": 30, "rank": 1, "is_winner": True},
            {"team_name": "Gamma", "points": 10, "rank": 2, "is_winner": False},
        ]
        assert (row.slug_hash, row.published_at, row.expires_at, row.revoked_at) == before[row.id]
    # The link of another run keeps its team of the same name.
    assert _names(await _link_row(db_session, foreign_id)) == ["Alpha", "Beta", "Gamma"]


@pytest.mark.asyncio
async def test_the_live_scores_are_not_read(client: AsyncClient, db_session: AsyncSession):
    """Gamma was regraded to 99 after the publish. The public page keeps the points it published."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-regrade")
    run, alpha, _beta, gamma = await _three_teams(db_session, env)
    station = RunStation(organization_id=env.org.id, run_id=run.id, sort_order=0)
    db_session.add(station)
    await db_session.flush()
    challenge = RunChallenge(
        organization_id=env.org.id,
        run_id=run.id,
        run_station_id=station.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "Frage"},
        instructions={"de": "Antworte!"},
        scoring_rules={"max_points": 100},
        sort_order=0,
    )
    db_session.add(challenge)
    await db_session.flush()
    submission = Submission(
        organization_id=env.org.id,
        run_id=run.id,
        team_id=gamma.id,
        run_challenge_id=challenge.id,
        content={"text": "Antwort"},
    )
    db_session.add(submission)
    await db_session.flush()
    db_session.add(Score(organization_id=env.org.id, submission_id=submission.id, points=99, max_points=100))
    link = await _link(db_session, run, _snapshot(("Alpha", 30), ("Beta", 20), ("Gamma", 10)))
    await db_session.commit()
    link_id = link.id

    await dissolve_team(db_session, alpha)
    await db_session.commit()

    assert (await _link_row(db_session, link_id)).snapshot["entries"] == [
        {"team_name": "Beta", "points": 20, "rank": 1, "is_winner": True},
        {"team_name": "Gamma", "points": 10, "rank": 2, "is_winner": False},
    ]


@pytest.mark.asyncio
async def test_a_team_that_was_not_in_the_snapshot_leaves_it_untouched(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="snap-absent")
    run, alpha, _beta, _gamma = await _three_teams(db_session, env)
    link = await _link(db_session, run, _snapshot(("Beta", 20), ("Gamma", 10)))
    await db_session.commit()
    link_id = link.id

    await dissolve_team(db_session, alpha)
    await db_session.commit()

    assert (await _link_row(db_session, link_id)).snapshot == _snapshot(("Beta", 20), ("Gamma", 10))


@pytest.mark.asyncio
async def test_neutral_names_still_match_after_the_anonymize_sweep(client: AsyncClient, db_session: AsyncSession):
    """The sweep gives the team row and the snapshot the same neutral name, so the match holds."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-neutral")
    run, _alpha, beta, _gamma = await _three_teams(db_session, env)
    run.ended_at = datetime.now(UTC) - timedelta(days=DeletionClass.LK_C.retention_days + 1)
    link = await _link(db_session, run, _snapshot(("Alpha", 30), ("Beta", 20), ("Gamma", 10)))
    await db_session.commit()
    link_id, beta_id = link.id, beta.id
    # The sweep loads its rows itself, as it does in its own session in production.
    db_session.expunge_all()

    await retention_tasks.sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await db_session.commit()

    swept = await _link_row(db_session, link_id)
    beta = (
        await db_session.execute(select(Team).where(Team.id == beta_id).execution_options(populate_existing=True))
    ).scalar_one()
    neutral = beta.name
    assert neutral not in ("Alpha", "Beta", "Gamma")
    assert sorted(_names(swept)) == sorted({"Team 1", "Team 2", "Team 3"})
    points_of = {entry["team_name"]: entry["points"] for entry in swept.snapshot["entries"]}
    assert points_of[neutral] == 20

    await dissolve_team(db_session, beta)
    await db_session.commit()

    after = await _link_row(db_session, link_id)
    assert neutral not in _names(after)
    assert [(e["points"], e["rank"], e["is_winner"]) for e in after.snapshot["entries"]] == [
        (30, 1, True),
        (10, 2, False),
    ]


@pytest.mark.asyncio
async def test_list_by_run_id_returns_live_revoked_and_expired_rows(client: AsyncClient, db_session: AsyncSession):
    env = await setup_journey_environment(db_session, client, org_slug="snap-repo")
    run = await _run(db_session, env.org.id)
    other_run = await _run(db_session, env.org.id, name="Anderer Lauf")
    live = await _link(db_session, run, _snapshot(("Alpha", 1)))
    revoked = await _link(db_session, run, _snapshot(("Alpha", 1)), revoked=True)
    expired = await _link(db_session, run, _snapshot(("Alpha", 1)), revoked=True, expired=True)
    await _link(db_session, other_run, _snapshot(("Alpha", 1)))
    await db_session.commit()

    rows = await RunResultLinkRepository(db_session).list_by_run_id_for_update(run.id)

    assert [row.id for row in rows] == sorted([live.id, revoked.id, expired.id])


# ---- a team whose name is still pending -------------------------------------
#
# A guest can create a team without a name, and a run can end and be published
# with it. Its entry has ``team_name`` null, so the name cannot be the key. A
# new publish stores the team id in each entry; an older snapshot has
# none.


def test_rank_entries_keeps_the_team_id_it_is_given():
    assert rank_entries([(None, 5, "id-a"), ("B", 9, "id-b"), ("C", 1)]) == [
        {"team_name": "B", "points": 9, "rank": 1, "is_winner": True, "team_id": "id-b"},
        {"team_name": None, "points": 5, "rank": 2, "is_winner": False, "team_id": "id-a"},
        {"team_name": "C", "points": 1, "rank": 3, "is_winner": False},
    ]


def _unnamed(*teams: tuple[int, str | None]) -> dict[str, Any]:
    return {
        "run_name": "Lauf",
        "ended_at": "2026-11-01T10:00:00+00:00",
        "language": "de",
        "entries": rank_entries([(None, points, team_id) for points, team_id in teams]),
    }


def test_without_team_finds_an_entry_without_a_name_by_its_team_id():
    after = without_team(_unnamed((30, "id-a"), (20, "id-b")), None, team_id="id-a")
    assert after["entries"] == [{"team_name": None, "points": 20, "rank": 1, "is_winner": True, "team_id": "id-b"}]


def test_without_team_prefers_the_team_id_over_the_name():
    """Two entries with one name cannot exist among live teams, but the id decides anyway."""
    before = {**_snapshot(), "entries": rank_entries([("A", 30, "id-1"), ("A", 20, "id-2")])}
    assert [e["team_id"] for e in without_team(before, "A", team_id="id-2")["entries"]] == ["id-1"]


def test_without_team_never_takes_an_entry_with_another_team_id_by_its_name():
    before = {**_snapshot(), "entries": rank_entries([("A", 30, "id-1")])}
    assert without_team(before, "A", team_id="id-9") is before


def test_without_team_keeps_old_entries_without_a_name_while_a_team_stays():
    """An old snapshot has no ids: nothing says which entry without a name is whose. None goes."""
    before = _unnamed((30, None), (20, None))
    assert without_team(before, None, team_id="id-a") is before
    assert without_team(before, None, team_id="id-a", teams_staying=1) is before


def test_without_team_drops_old_entries_without_a_name_when_no_team_stays():
    before = {**_snapshot(), "entries": rank_entries([(None, 30), (None, 20)])}
    assert without_team(before, None, team_id="id-a", teams_staying=0)["entries"] == []


async def _points(db: AsyncSession, org_id: UUID, run: Run, team: Team, points: int) -> None:
    station = RunStation(organization_id=org_id, run_id=run.id, sort_order=0)
    db.add(station)
    await db.flush()
    challenge = RunChallenge(
        organization_id=org_id,
        run_id=run.id,
        run_station_id=station.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "Frage"},
        instructions={"de": "Antworte!"},
        scoring_rules={"max_points": 100},
        sort_order=0,
    )
    db.add(challenge)
    await db.flush()
    submission = Submission(
        organization_id=org_id,
        run_id=run.id,
        team_id=team.id,
        run_challenge_id=challenge.id,
        content={"text": "Antwort"},
    )
    db.add(submission)
    await db.flush()
    db.add(Score(organization_id=org_id, submission_id=submission.id, points=points, max_points=100))
    await db.flush()


async def _publish(client: AsyncClient, env: JourneyEnv, run_id: UUID) -> str:
    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=env.auth_headers["coordinator"]
    )
    assert response.status_code == 200, response.text
    return str(response.json()["slug"])


async def _remove_team(client: AsyncClient, env: JourneyEnv, run_id: UUID, team_id: UUID) -> None:
    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        params={"confirm_dissolution": "true"},
        headers=env.auth_headers["coordinator"],
    )
    assert response.status_code == 204, response.text


async def _public_entries(client: AsyncClient, slug: str) -> list[dict[str, Any]]:
    response = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert response.status_code == 200, response.text
    entries: list[dict[str, Any]] = response.json()["entries"]
    return entries


@pytest.mark.asyncio
async def test_the_last_team_with_a_pending_name_leaves_an_empty_result(client: AsyncClient, db_session: AsyncSession):
    """Published with a team that has no name yet, then that team dissolves."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-pending-last")
    run = await _run(db_session, env.org.id)
    team = await _team(db_session, env.org.id, run, None)
    await _points(db_session, env.org.id, run, team, 7)
    await db_session.commit()
    run_id, team_id = run.id, team.id

    slug = await _publish(client, env, run_id)
    assert await _public_entries(client, slug) == [{"team_name": None, "points": 7, "rank": 1, "is_winner": True}]

    await _remove_team(client, env, run_id, team_id)

    assert await _public_entries(client, slug) == []


@pytest.mark.asyncio
async def test_of_two_teams_with_a_pending_name_the_one_that_stays_keeps_points_and_rank(
    client: AsyncClient, db_session: AsyncSession
):
    """The team id is the key, in the live link and in a revoked one. The public answer shows no id."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-pending-two")
    run = await _run(db_session, env.org.id)
    first = await _team(db_session, env.org.id, run, None)
    second = await _team(db_session, env.org.id, run, None)
    await _points(db_session, env.org.id, run, first, 30)
    await _points(db_session, env.org.id, run, second, 20)
    await db_session.commit()
    run_id, first_id, second_id = run.id, first.id, second.id

    revoked_slug = await _publish(client, env, run_id)
    revoked = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=env.auth_headers["coordinator"]
    )
    assert revoked.status_code == 204
    slug = await _publish(client, env, run_id)
    assert slug != revoked_slug
    assert await _public_entries(client, slug) == [
        {"team_name": None, "points": 30, "rank": 1, "is_winner": True},
        {"team_name": None, "points": 20, "rank": 2, "is_winner": False},
    ]

    await _remove_team(client, env, run_id, first_id)

    assert await _public_entries(client, slug) == [{"team_name": None, "points": 20, "rank": 1, "is_winner": True}]
    rows = (
        (
            await db_session.execute(
                select(RunResultLink).where(RunResultLink.run_id == run_id).execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    for row in rows:
        assert [(e["team_id"], e["points"]) for e in row.snapshot["entries"]] == [(str(second_id), 20)]


@pytest.mark.asyncio
async def test_an_old_snapshot_without_ids_never_loses_the_entry_of_a_team_that_stays(
    client: AsyncClient, db_session: AsyncSession
):
    """Two teams with a pending name in a snapshot from before the ids: the safe side.

    Nothing says which entry is whose, so the first dissolution removes none.
    When the last team of the run has gone, no entry can belong to a team that
    stays, and the entries without a name go.
    """
    env = await setup_journey_environment(db_session, client, org_slug="snap-pending-old")
    run = await _run(db_session, env.org.id)
    first = await _team(db_session, env.org.id, run, None)
    second = await _team(db_session, env.org.id, run, None)
    old = {**_snapshot(), "entries": rank_entries([(None, 30), (None, 20)])}
    link = await _link(db_session, run, old)
    await db_session.commit()
    link_id = link.id

    await dissolve_team(db_session, first)
    await db_session.commit()
    assert (await _link_row(db_session, link_id)).snapshot == old

    await dissolve_team(db_session, second)
    await db_session.commit()
    assert (await _link_row(db_session, link_id)).snapshot["entries"] == []


# ---- the last team of a run takes every entry without an id with it ---------
#
# An old snapshot has no ids. While another team of the run stays, an entry
# without a name is kept, because nothing says whose it is. When the last team
# dissolves, whatever its name, no entry without an id can belong to a team
# that stays, and all of them go.


def test_without_team_drops_every_entry_without_an_id_when_no_team_stays():
    """The last team has a name, and the old snapshot still holds a nameless entry of a team that went before."""
    before = {**_snapshot(), "entries": rank_entries([(None, 30), ("Beta", 20)])}
    assert without_team(before, "Beta", team_id="id-b", teams_staying=0)["entries"] == []


def test_without_team_keeps_the_other_entries_when_a_named_team_goes_and_a_team_stays():
    before = {**_snapshot(), "entries": rank_entries([(None, 30), ("Beta", 20)])}
    assert without_team(before, "Beta", team_id="id-b", teams_staying=1)["entries"] == [
        {"team_name": None, "points": 30, "rank": 1, "is_winner": True}
    ]


def _name_and_points(link: RunResultLink) -> list[tuple[str | None, int, int]]:
    return [(entry["team_name"], entry["points"], entry["rank"]) for entry in link.snapshot["entries"]]


@pytest.mark.asyncio
async def test_a_nameless_old_entry_goes_when_the_named_last_team_dissolves(
    client: AsyncClient, db_session: AsyncSession
):
    """The pending name first, then Beta. Empty at the end, in the live link and in a revoked one."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-old-mixed-a")
    run = await _run(db_session, env.org.id)
    pending = await _team(db_session, env.org.id, run, None)
    beta = await _team(db_session, env.org.id, run, "Beta")
    old = {**_snapshot(), "entries": rank_entries([(None, 30), ("Beta", 20)])}
    live = await _link(db_session, run, old)
    revoked = await _link(db_session, run, old, revoked=True)
    await db_session.commit()
    live_id, revoked_id = live.id, revoked.id
    slug = compute_slug(live_id)

    await dissolve_team(db_session, pending)
    await db_session.commit()
    for link_id in (live_id, revoked_id):
        assert (await _link_row(db_session, link_id)).snapshot == old

    await dissolve_team(db_session, beta)
    await db_session.commit()
    for link_id in (live_id, revoked_id):
        assert (await _link_row(db_session, link_id)).snapshot["entries"] == []
    assert await _public_entries(client, slug) == []


@pytest.mark.asyncio
async def test_a_nameless_old_entry_goes_when_the_pending_last_team_dissolves_after_the_named_one(
    client: AsyncClient, db_session: AsyncSession
):
    """The other order: Beta first, then the team with the pending name."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-old-mixed-b")
    run = await _run(db_session, env.org.id)
    pending = await _team(db_session, env.org.id, run, None)
    beta = await _team(db_session, env.org.id, run, "Beta")
    old = {**_snapshot(), "entries": rank_entries([(None, 30), ("Beta", 20)])}
    live = await _link(db_session, run, old)
    revoked = await _link(db_session, run, old, revoked=True)
    await db_session.commit()
    live_id, revoked_id = live.id, revoked.id
    slug = compute_slug(live_id)

    await dissolve_team(db_session, beta)
    await db_session.commit()
    for link_id in (live_id, revoked_id):
        assert _name_and_points(await _link_row(db_session, link_id)) == [(None, 30, 1)]

    await dissolve_team(db_session, pending)
    await db_session.commit()
    for link_id in (live_id, revoked_id):
        assert (await _link_row(db_session, link_id)).snapshot["entries"] == []
    assert await _public_entries(client, slug) == []


@pytest.mark.asyncio
async def test_three_teams_two_nameless_in_an_old_snapshot_step_by_step(client: AsyncClient, db_session: AsyncSession):
    """After each step the entries that stay are right, and no entry of a team that stays is lost."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-old-three")
    run = await _run(db_session, env.org.id)
    first = await _team(db_session, env.org.id, run, None)
    second = await _team(db_session, env.org.id, run, None)
    beta = await _team(db_session, env.org.id, run, "Beta")
    old = {**_snapshot(), "entries": rank_entries([(None, 30), ("Beta", 20), (None, 10)])}
    live = await _link(db_session, run, old)
    revoked = await _link(db_session, run, old, revoked=True)
    await db_session.commit()
    link_ids = (live.id, revoked.id)

    # A nameless team goes while two teams stay: nothing says which entry is its own.
    await dissolve_team(db_session, first)
    await db_session.commit()
    for link_id in link_ids:
        assert (await _link_row(db_session, link_id)).snapshot == old

    # The named team goes while a nameless one stays: only its own entry, the rest ranked again.
    await dissolve_team(db_session, beta)
    await db_session.commit()
    for link_id in link_ids:
        assert _name_and_points(await _link_row(db_session, link_id)) == [(None, 30, 1), (None, 10, 2)]

    # The last team goes: no entry can belong to a team that stays.
    await dissolve_team(db_session, second)
    await db_session.commit()
    for link_id in link_ids:
        assert (await _link_row(db_session, link_id)).snapshot["entries"] == []


# ---- a publish beside a dissolution -----------------------------------------
#
# Two real transactions, steered by events. One request is held at a named
# point; the other is started beside it and runs until it is done or waits for
# a row lock, which the database itself reports; then the first goes on. No
# sleep decides anything.
#
# The publish and every dissolving act take the row lock on the run as their
# first lock, so the one that comes second waits there until the first has
# committed.


class _Hold:
    """Holds the first call of ``module.name`` after it has returned, until ``release`` is set."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        original = getattr(module, name)
        hold = self

        async def held(*args: Any, **kwargs: Any) -> Any:
            result = await original(*args, **kwargs)
            if not hold.reached.is_set():
                hold.reached.set()
                await hold.release.wait()
            return result

        monkeypatch.setattr(module, name, held)

    async def wait_reached(self) -> None:
        await asyncio.wait_for(self.reached.wait(), timeout=20)


async def _until_done_or_waiting_for_a_lock(test_engine: Any, task: "asyncio.Task[Any]") -> None:
    """Return when ``task`` has finished or a transaction of this database waits for a lock."""

    async def watch() -> None:
        async with test_engine.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            while not task.done():
                waiting = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                        )
                    )
                ).scalar_one()
                if waiting:
                    return

    await asyncio.wait_for(watch(), timeout=20)


async def _two_teams_with_a_member(db: AsyncSession, env: JourneyEnv) -> tuple[Run, Team, Team]:
    run = await _run(db, env.org.id)
    alpha = await _team(db, env.org.id, run, "Alpha")
    beta = await _team(db, env.org.id, run, "Beta")
    return run, alpha, beta


@pytest.mark.asyncio
async def test_a_publish_that_has_read_the_scoreboard_cannot_bring_a_dissolved_team_back(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The publish built its snapshot with Alpha, Alpha dissolves beside it, the publish goes on."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-race-publish")
    run, alpha, _beta = await _two_teams_with_a_member(db_session, env)
    await db_session.commit()
    run_id, alpha_id = run.id, alpha.id

    hold = _Hold(monkeypatch, runs_router, "_build_results_snapshot")
    publish = asyncio.create_task(_publish(concurrent_client, env, run_id))
    await hold.wait_reached()
    removal = asyncio.create_task(_remove_team(concurrent_client, env, run_id, alpha_id))
    await _until_done_or_waiting_for_a_lock(test_engine, removal)
    hold.release.set()
    slug = await publish
    await removal

    assert [e["team_name"] for e in await _public_entries(concurrent_client, slug)] == ["Beta"]
    (row,) = await RunResultLinkRepository(db_session).list_by_run_id_for_update(run_id)
    # Read before the rollback, which expires the row.
    stored = _names(row)
    await db_session.rollback()
    assert stored == ["Beta"]


@pytest.mark.asyncio
async def test_a_publish_that_starts_beside_an_open_dissolution_does_not_store_the_team(
    client: AsyncClient,
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
):
    """The other order: the dissolution has changed the snapshots and is not committed, a publish starts."""
    env = await setup_journey_environment(db_session, client, org_slug="snap-race-dissolve")
    run, alpha, _beta = await _two_teams_with_a_member(db_session, env)
    await db_session.commit()
    run_id, alpha_id = run.id, alpha.id
    slug = await _publish(concurrent_client, env, run_id)
    assert [e["team_name"] for e in await _public_entries(concurrent_client, slug)] == ["Alpha", "Beta"]

    hold = _Hold(monkeypatch, team_dissolution, "_remove_from_result_snapshots")
    removal = asyncio.create_task(_remove_team(concurrent_client, env, run_id, alpha_id))
    await hold.wait_reached()
    publish = asyncio.create_task(_publish(concurrent_client, env, run_id))
    await _until_done_or_waiting_for_a_lock(test_engine, publish)
    hold.release.set()
    await removal
    assert await publish == slug

    assert [e["team_name"] for e in await _public_entries(concurrent_client, slug)] == ["Beta"]
    (row,) = await RunResultLinkRepository(db_session).list_by_run_id_for_update(run_id)
    # Read before the rollback, which expires the row.
    stored = _names(row)
    await db_session.rollback()
    assert stored == ["Beta"]
