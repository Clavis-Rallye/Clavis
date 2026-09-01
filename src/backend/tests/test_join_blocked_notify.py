"""Integration tests for the live `join_blocked` event.

Where a join or a team creation is refused with RUN_NOT_STARTED or RUN_PAUSED,
the refusal broadcasts a `join_blocked` event on
the run's coordination channel (`run_state:{run_id}`) before it answers — no
personal data, nothing stored, the refusal itself unchanged. Every other
refusal stays silent.
"""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feed_event import FeedEvent
from app.models.run import Run, RunState
from app.models.team import Team
from tests.journey_helpers import setup_journey_environment


async def _make_run(db_session: AsyncSession, org_id: UUID, state: RunState) -> Run:
    """Insert a Run directly (no API round-trips needed for setup)."""
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name=f"Test Run {uuid4().hex[:6]}",
        state=state,
        join_code=uuid4().hex[:6].upper(),
        settings={"max_members_per_team": 10},
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _make_team(db_session: AsyncSession, org_id: UUID, run: Run, name: str) -> Team:
    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name=name,
        color="#123456",
        share_code=uuid4().hex[:6].upper(),
    )
    db_session.add(team)
    await db_session.flush()
    return team


class BroadcastSpy:
    """Records ws_manager broadcasts instead of sending them."""

    def __init__(self) -> None:
        self.broadcasts: list[tuple[str, dict]] = []

    async def broadcast(self, channel: str, message: dict) -> None:
        self.broadcasts.append((channel, message))


@pytest.fixture
def broadcast_spy(monkeypatch: pytest.MonkeyPatch) -> BroadcastSpy:
    """Replace the ws_manager the notify helpers broadcast through."""
    import app.services.notify as notify_module

    spy = BroadcastSpy()
    monkeypatch.setattr(notify_module, "ws_manager", spy)
    return spy


def _join_blocked_events(spy: BroadcastSpy, run: Run) -> list[tuple[str, dict]]:
    return [(channel, message) for channel, message in spy.broadcasts if message.get("type") == "join_blocked"]


@pytest.mark.asyncio
async def test_guest_join_not_started_broadcasts_join_blocked(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """A guest join on a not-started run refuses with RUN_NOT_STARTED and
    sends one `join_blocked` event with that reason to the run channel."""
    env = await setup_journey_environment(db_session, client, org_slug="jb-guest-notstarted")
    run = await _make_run(db_session, env.org.id, state=RunState.CONFIGURED)
    await db_session.commit()

    response = await client.post(
        f"/api/join/{run.join_code}",
        json={"mode": "create", "display_name": "Early Guest", "team_name": "Early Team"},
    )

    # The refusal itself is unchanged.
    assert response.status_code == 422
    assert response.json()["error_code"] == "RUN_NOT_STARTED"

    events = _join_blocked_events(broadcast_spy, run)
    assert len(events) == 1
    channel, message = events[0]
    assert channel == f"run_state:{run.id}"
    assert isinstance(message["timestamp"], str)
    # Exact equality: the payload carries the run and the reason — and nothing
    # else, in particular no personal data.
    assert message["data"] == {"run_id": str(run.id), "reason": "RUN_NOT_STARTED"}

    # Nothing stored: no feed event records the refused attempt.
    feed_events = await db_session.execute(select(FeedEvent).where(FeedEvent.run_id == run.id))
    assert feed_events.scalars().first() is None


@pytest.mark.asyncio
async def test_guest_join_paused_broadcasts_join_blocked(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """A guest join on a paused run refuses with RUN_PAUSED and sends the
    event with that reason (join-by-code mode)."""
    env = await setup_journey_environment(db_session, client, org_slug="jb-guest-paused")
    run = await _make_run(db_session, env.org.id, state=RunState.PAUSED)
    team = await _make_team(db_session, env.org.id, run, "Paused Team")
    await db_session.commit()

    response = await client.post(
        f"/api/join/{run.join_code}",
        json={"mode": "join", "team_code": team.share_code},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "RUN_PAUSED"

    events = _join_blocked_events(broadcast_spy, run)
    assert len(events) == 1
    channel, message = events[0]
    assert channel == f"run_state:{run.id}"
    assert message["data"] == {"run_id": str(run.id), "reason": "RUN_PAUSED"}


@pytest.mark.asyncio
async def test_create_team_not_started_broadcasts_join_blocked(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """A team creation on a not-started run refuses with RUN_NOT_STARTED
    and sends the event."""
    env = await setup_journey_environment(db_session, client, org_slug="jb-create-notstarted")
    run = await _make_run(db_session, env.org.id, state=RunState.CONFIGURED)
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "Too Early"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "RUN_NOT_STARTED"

    events = _join_blocked_events(broadcast_spy, run)
    assert len(events) == 1
    _, message = events[0]
    assert message["data"] == {"run_id": str(run.id), "reason": "RUN_NOT_STARTED"}


@pytest.mark.asyncio
async def test_join_team_by_code_paused_broadcasts_join_blocked(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """A registered participant's join-by-code on a paused run refuses
    with RUN_PAUSED and sends the event."""
    env = await setup_journey_environment(db_session, client, org_slug="jb-bycode-paused")
    run = await _make_run(db_session, env.org.id, state=RunState.PAUSED)
    team = await _make_team(db_session, env.org.id, run, "Paused Team")
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/by-code",
        headers=env.auth_headers["participant1"],
        json={"team_code": team.share_code},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "RUN_PAUSED"

    events = _join_blocked_events(broadcast_spy, run)
    assert len(events) == 1
    _, message = events[0]
    assert message["data"] == {"run_id": str(run.id), "reason": "RUN_PAUSED"}


@pytest.mark.asyncio
async def test_join_team_by_id_not_started_broadcasts_join_blocked(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """Joining a team by id on a not-started run refuses with
    RUN_NOT_STARTED and sends the event."""
    env = await setup_journey_environment(db_session, client, org_slug="jb-byid-notstarted")
    run = await _make_run(db_session, env.org.id, state=RunState.CONFIGURED)
    team = await _make_team(db_session, env.org.id, run, "Waiting Team")
    await db_session.commit()

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}/join",
        headers=env.auth_headers["participant1"],
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "RUN_NOT_STARTED"

    events = _join_blocked_events(broadcast_spy, run)
    assert len(events) == 1
    _, message = events[0]
    assert message["data"] == {"run_id": str(run.id), "reason": "RUN_NOT_STARTED"}


@pytest.mark.asyncio
async def test_guest_join_ended_run_stays_silent(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """An ended run refuses with RUN_ENDED — a reason nobody waits for, so no
    event is sent."""
    env = await setup_journey_environment(db_session, client, org_slug="jb-guest-ended")
    run = await _make_run(db_session, env.org.id, state=RunState.ENDED)
    await db_session.commit()

    response = await client.post(
        f"/api/join/{run.join_code}",
        json={"mode": "create", "display_name": "Late Guest"},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "RUN_ENDED"
    assert broadcast_spy.broadcasts == []


@pytest.mark.asyncio
async def test_active_run_refusals_stay_silent(
    client: AsyncClient, db_session: AsyncSession, broadcast_spy: BroadcastSpy
):
    """Refusals that are not about the run state — an unknown team code (404)
    and a taken team name (409 TEAM_NAME_TAKEN) — send no event."""
    env = await setup_journey_environment(db_session, client, org_slug="jb-active-silent")
    run = await _make_run(db_session, env.org.id, state=RunState.ACTIVE)
    await _make_team(db_session, env.org.id, run, "Taken Name")
    await db_session.commit()

    unknown_code = await client.post(
        f"/api/join/{run.join_code}",
        json={"mode": "join", "team_code": "ZZZZZZ"},
    )
    assert unknown_code.status_code == 404

    name_taken = await client.post(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/",
        headers=env.auth_headers["participant1"],
        json={"name": "taken name"},
    )
    assert name_taken.status_code == 409
    assert name_taken.json()["error_code"] == "TEAM_NAME_TAKEN"

    assert broadcast_spy.broadcasts == []
