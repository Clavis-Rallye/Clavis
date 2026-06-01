"""Guest lifecycle: a guest outlives the run and is swept by the retention sweep afterwards."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_departure import RunDeparture
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.services.retention_tasks import sweep_run_guests
from tests.journey_helpers import activate_run, setup_journey_environment


async def _run_with_guest(client: AsyncClient, db_session: AsyncSession, slug_suffix: str):
    """A started run with one guest in one team. Returns the pieces a test needs."""
    env = await setup_journey_environment(db_session, client, org_slug=f"guest-lifecycle-{slug_suffix}")
    coord_headers = env.auth_headers["coordinator"]
    # Read the ORM attributes before any expire_all() below — afterwards a plain
    # attribute access would lazy-load outside the async context (MissingGreenlet).
    org_id = env.org.id
    slug = env.slug

    create = await client.post(
        f"/api/orgs/{slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Guest Lifecycle Run"},
    )
    assert create.status_code == 201
    run_id = create.json()["id"]
    join_code = create.json()["join_code"]

    await activate_run(client, env.slug, run_id, coord_headers)

    join = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Gast Team"},
    )
    assert join.status_code == 201

    db_session.expire_all()
    guest = (
        (await db_session.execute(select(User).where(User.organization_id == org_id, User.role == UserRole.GUEST)))
        .scalars()
        .one()
    )
    return slug, run_id, join.json()["team_id"], guest.id, coord_headers


@pytest.mark.asyncio
async def test_guest_survives_the_run_ending(client: AsyncClient, db_session: AsyncSession) -> None:
    """Ending a run does not delete its guests.

    Ending emits RESULTS_AVAILABLE and FEEDBACK_PROMPT. The team is invited to look at
    its result and answer the feedback. Deleting the accounts in the same breath would
    make every following request 401 and drop people on the login screen. The
    retention sweep owns this deletion and applies the configured window of the organization.
    """
    slug, run_id, team_id, guest_id, coord_headers = await _run_with_guest(client, db_session, "survives")

    end = await client.post(f"/api/orgs/{slug}/runs/{run_id}/end", headers=coord_headers)
    assert end.status_code == 200

    db_session.expire_all()
    guest = (await db_session.execute(select(User).where(User.id == guest_id))).scalar_one_or_none()
    assert guest is not None, "the guest account must outlive the run it belongs to"

    member = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team_id))).scalars().one()
    assert member.user_id == guest_id


@pytest.mark.asyncio
async def test_lk_a_sweep_deletes_the_guest_once_the_window_passes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The guest goes away on schedule, not on the coordinator's click."""
    slug, run_id, team_id, guest_id, coord_headers = await _run_with_guest(client, db_session, "sweep")

    assert (await client.post(f"/api/orgs/{slug}/runs/{run_id}/end", headers=coord_headers)).status_code == 200
    db_session.expire_all()

    # Right after the run ends the window has not passed, so nothing is swept.
    assert await sweep_run_guests(db_session, reference_date=datetime.now(UTC)) == 0
    db_session.expire_all()
    assert (await db_session.execute(select(User).where(User.id == guest_id))).scalar_one_or_none() is not None

    # Well past the window, the account is removed.
    assert await sweep_run_guests(db_session, reference_date=datetime.now(UTC) + timedelta(days=400)) == 1
    db_session.expire_all()
    assert (await db_session.execute(select(User).where(User.id == guest_id))).scalar_one_or_none() is None

    # The roster row stays, with user_id nulled: who was in which team survives
    # the account. The foreign key must not cascade (migration 025).
    member = (await db_session.execute(select(TeamMember).where(TeamMember.team_id == team_id))).scalars().one()
    assert member.user_id is None
    assert member.display_name == "Deleted member"  # the roster copy of the name goes with the account


@pytest.mark.asyncio
async def test_guest_can_still_read_the_run_after_it_ended(client: AsyncClient, db_session: AsyncSession) -> None:
    """The point of keeping the account: the result stays reachable."""
    slug, run_id, _team_id, _guest_id, coord_headers = await _run_with_guest(client, db_session, "reads")

    # The join response left the guest's session cookie on this client; the
    # coordinator's call below authenticates by header, so the cookie survives it.
    assert client.cookies.get("access_token") is not None

    assert (await client.post(f"/api/orgs/{slug}/runs/{run_id}/end", headers=coord_headers)).status_code == 200

    for path in (
        f"/api/orgs/{slug}/runs/{run_id}/stations/",
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
    ):
        resp = await client.get(path)
        assert resp.status_code == 200, f"{path} → {resp.status_code} {resp.text[:200]}"


@pytest.mark.asyncio
async def test_guest_who_left_an_open_run_stays_out_after_the_sweep_of_another_run(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The sweep of an old run does not take the account that an open run still needs.

    The guest left the open run, so no membership holds the account. What
    refuses a new join of that run from the same browser is the departure
    row, and it goes with the account. An older run of the organization that
    the guest also left is due. Its sweep leaves the account, and the open run
    still answers the browser with the refusal.
    """
    slug, run_id, team_id, guest_id, _coord_headers = await _run_with_guest(client, db_session, "left-open-run")

    leave = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/leave", json={"confirm_dissolution": True}
    )
    assert leave.status_code == 204, leave.text

    db_session.expire_all()
    open_run = (await db_session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    org_id, join_code = open_run.organization_id, open_run.join_code
    now = datetime.now(UTC)
    old_run = Run(
        organization_id=org_id,
        name="Old Run",
        state=RunState.ENDED,
        join_code="OLDRUN",
        ended_at=now - timedelta(days=400),
    )
    db_session.add(old_run)
    await db_session.flush()
    db_session.add(RunDeparture(organization_id=org_id, run_id=old_run.id, user_id=guest_id))
    await db_session.commit()

    assert await sweep_run_guests(db_session, reference_date=now) == 0
    await db_session.commit()

    db_session.expire_all()
    assert (await db_session.execute(select(User).where(User.id == guest_id))).scalar_one_or_none() is not None
    departures = (
        (await db_session.execute(select(RunDeparture.run_id).where(RunDeparture.user_id == guest_id))).scalars().all()
    )
    assert len(departures) == 2

    again = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Zweiter Versuch"},
    )
    assert again.status_code == 403, again.text
    assert again.json()["error_code"] == "RUN_DEPARTED"


# ---- The guest cleanup takes the accounts and leaves the teams in every result ----

_POINTS_BY_TEAM = {"Erste": 9, "Zweite": 6, "Dritte": 3}


async def _ended_run_with_three_guest_teams(client: AsyncClient, db_session: AsyncSession, slug_suffix: str):
    """An ended run with three teams of one guest each, graded 9, 6 and 3, and a published result.

    Returns the slug of the organization, the run id, the team ids by name, the
    coordinator's headers and the slug of the public result.
    """
    env = await setup_journey_environment(
        db_session, client, org_slug=f"guest-lifecycle-{slug_suffix}", num_stations=1, challenges_per_station=1
    )
    coord_headers = env.auth_headers["coordinator"]
    org_id = env.org.id
    slug = env.slug

    create = await client.post(
        f"/api/orgs/{slug}/runs/",
        headers=coord_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Three Guest Teams"},
    )
    assert create.status_code == 201
    run_id = create.json()["id"]
    join_code = create.json()["join_code"]
    await activate_run(client, slug, run_id, coord_headers)

    team_ids: dict[str, str] = {}
    for name in _POINTS_BY_TEAM:
        # A fresh browser per guest: with the cookie of the guest before, the
        # join would be the same person again.
        client.cookies.clear()
        join = await client.post(
            f"/api/join/{join_code}",
            json={"mode": "create", "display_name": f"Gast {name}", "team_name": name},
        )
        assert join.status_code == 201, join.text
        team_ids[name] = join.json()["team_id"]
    client.cookies.clear()

    challenge_id = (
        await db_session.execute(select(RunChallenge.id).where(RunChallenge.run_id == UUID(run_id)))
    ).scalar_one()
    for name, points in _POINTS_BY_TEAM.items():
        submission = Submission(
            organization_id=org_id,
            run_id=UUID(run_id),
            team_id=UUID(team_ids[name]),
            run_challenge_id=challenge_id,
            content={"text": f"Antwort {name}"},
        )
        db_session.add(submission)
        await db_session.flush()
        db_session.add(Score(organization_id=org_id, submission_id=submission.id, points=points, max_points=10))
        await db_session.flush()
    await db_session.commit()

    assert (await client.post(f"/api/orgs/{slug}/runs/{run_id}/end", headers=coord_headers)).status_code == 200
    publish = await client.post(f"/api/orgs/{slug}/runs/{run_id}/results/publish", headers=coord_headers)
    assert publish.status_code in (200, 201), publish.text
    return slug, run_id, team_ids, coord_headers, publish.json()["slug"]


async def _three_views(
    client: AsyncClient, slug: str, run_id: str, coord_headers: dict[str, str], result_slug: str
) -> dict[str, Any]:
    """Scoreboard, statistics of the run and the public result, as the three routes answer."""
    scoreboard = await client.get(f"/api/orgs/{slug}/runs/{run_id}/scores/scoreboard", headers=coord_headers)
    assert scoreboard.status_code == 200, scoreboard.text
    statistics = await client.get(f"/api/orgs/{slug}/runs/{run_id}/analytics", headers=coord_headers)
    assert statistics.status_code == 200, statistics.text
    public = await client.post("/api/public/results/lookup", json={"slug": result_slug})
    assert public.status_code == 200, public.text
    return {
        "scoreboard": scoreboard.json()["entries"],
        "participation": statistics.json()["participation"],
        "statistics": statistics.json()["teams"],
        "public": public.json()["entries"],
    }


def _points_and_ranks(views: dict[str, Any]) -> dict[str, Any]:
    """What must hold for a team whatever happened to its member rows: it is listed, with points and rank."""
    return {
        "scoreboard": [(e["team_name"], e["total_points"], e["rank"]) for e in views["scoreboard"]],
        "statistics": sorted((t["team_name"], t["total_points"]) for t in views["statistics"]),
        "team_count": views["participation"]["team_count"],
        "public": [(e["team_name"], e["points"], e["rank"]) for e in views["public"]],
    }


_EXPECTED_POINTS_AND_RANKS = {
    "scoreboard": [("Erste", 9, 1), ("Zweite", 6, 2), ("Dritte", 3, 3)],
    "statistics": [("Dritte", 3), ("Erste", 9), ("Zweite", 6)],
    "team_count": 3,
    "public": [("Erste", 9, 1), ("Zweite", 6, 2), ("Dritte", 3, 3)],
}


async def _guest_accounts(db_session: AsyncSession, run_id: str) -> int:
    org_id = select(Run.organization_id).where(Run.id == UUID(run_id)).scalar_subquery()
    return (
        await db_session.scalar(
            select(func.count()).select_from(User).where(User.organization_id == org_id, User.role == UserRole.GUEST)
        )
        or 0
    )


async def _assert_no_team_dissolved(db_session: AsyncSession, run_id: str) -> None:
    db_session.expire_all()
    rows = (await db_session.execute(select(Team.is_deleted, Team.deleted_at).where(Team.run_id == UUID(run_id)))).all()
    assert len(rows) == 3
    assert all(is_deleted is False and deleted_at is None for is_deleted, deleted_at in rows)


@pytest.mark.asyncio
async def test_guest_cleanup_keeps_every_team_in_scoreboard_statistics_and_result(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The cleanup deletes every guest account, and no team leaves a result or is dissolved by it."""
    slug, run_id, _team_ids, coord_headers, result_slug = await _ended_run_with_three_guest_teams(
        client, db_session, "three-teams"
    )
    before = await _three_views(client, slug, run_id, coord_headers, result_slug)
    assert _points_and_ranks(before) == _EXPECTED_POINTS_AND_RANKS
    assert await _guest_accounts(db_session, run_id) == 3

    assert await sweep_run_guests(db_session, reference_date=datetime.now(UTC) + timedelta(days=400)) == 3
    await db_session.commit()

    assert await _guest_accounts(db_session, run_id) == 0
    await _assert_no_team_dissolved(db_session, run_id)
    # Every field of the three answers, the team sizes and the guest counts included.
    assert await _three_views(client, slug, run_id, coord_headers, result_slug) == before

    # A result published after the cleanup is built from the same three teams.
    again = await client.post(f"/api/orgs/{slug}/runs/{run_id}/results/publish", headers=coord_headers)
    assert again.status_code in (200, 201), again.text
    assert await _three_views(client, slug, run_id, coord_headers, again.json()["slug"]) == before


@pytest.mark.asyncio
async def test_team_whose_member_rows_went_with_the_guests_stays_in_every_result(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A team without member rows is not a dissolved team: it keeps its place in every result.

    Data from before migration 025 can have this state, because deleting a guest
    account took its member rows along. The rows of one team are deleted by hand here.
    """
    slug, run_id, team_ids, coord_headers, result_slug = await _ended_run_with_three_guest_teams(
        client, db_session, "old-data"
    )
    before = await _three_views(client, slug, run_id, coord_headers, result_slug)
    later = datetime.now(UTC) + timedelta(days=400)

    assert await sweep_run_guests(db_session, reference_date=later) == 3
    await db_session.execute(delete(TeamMember).where(TeamMember.team_id == UUID(team_ids["Zweite"])))
    await db_session.commit()
    # A later pass finds the team without rows and leaves it alone.
    assert await sweep_run_guests(db_session, reference_date=later) == 0
    await db_session.commit()

    await _assert_no_team_dissolved(db_session, run_id)
    remaining = await db_session.scalar(
        select(func.count()).select_from(TeamMember).where(TeamMember.team_id == UUID(team_ids["Zweite"]))
    )
    assert remaining == 0

    after = await _three_views(client, slug, run_id, coord_headers, result_slug)
    assert _points_and_ranks(after) == _points_and_ranks(before) == _EXPECTED_POINTS_AND_RANKS
    assert after["scoreboard"] == before["scoreboard"]
    assert after["public"] == before["public"]

    again = await client.post(f"/api/orgs/{slug}/runs/{run_id}/results/publish", headers=coord_headers)
    assert again.status_code in (200, 201), again.text
    republished = await _three_views(client, slug, run_id, coord_headers, again.json()["slug"])
    assert _points_and_ranks(republished) == _EXPECTED_POINTS_AND_RANKS
