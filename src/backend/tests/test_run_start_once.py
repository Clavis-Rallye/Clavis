"""
A run is started once, and only a paused run is resumed.

Start builds the frozen snapshot of the rallye (run stations and run challenges) and moves the run to
`active`. Two starts of one run, a manual start against the automatic start, or a start against a
pause must leave the run with the run stations of one snapshot. Resume moves only a paused run back
to `active`: a run that never started has no snapshot and is started, not resumed.

The interleavings use two database connections (``concurrent_client`` gives every request its own
session, the automatic start opens its own). They are ordered by an event, never by a sleep: the
other side runs inside the first read of the run by the first start, right after that start has
read the state. The other side runs to its end there, unless it asks for the row lock of the run
first (``RunRepository.lock_row``). Then the first start goes on, and the other side waits at the
lock for the first start's commit.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.organization import Organization
from app.models.run import Run, RunState
from app.models.run_station import RunStation
from app.models.user import UserRole
from app.repositories.run import RunRepository
from app.services import run_start
from app.services.snapshot import create_run_snapshot
from tests.factories import (
    create_test_rallye,
    create_test_station,
    create_test_user_with_org,
    get_auth_headers,
    link_station_to_rallye,
)

# The rallye of every test has this many stations, so one snapshot is this many run stations.
STATIONS = 2


class _Env:
    def __init__(self, org: Organization, headers: dict[str, str], rallye_id: UUID) -> None:
        self.org = org
        self.headers = headers
        self.rallye_id = rallye_id

    def url(self, run_id: str, action: str) -> str:
        return f"/api/orgs/{self.org.slug}/runs/{run_id}/{action}"


async def _env(db_session: AsyncSession, slug: str) -> _Env:
    """A coordinator and a rallye with ``STATIONS`` stations, committed for the other connections."""
    user, org = await create_test_user_with_org(db_session, role=UserRole.COORDINATOR, org_slug=slug)
    rallye = await create_test_rallye(db_session, org, title={"de": "Einmal-Rallye"})
    for index in range(STATIONS):
        station = await create_test_station(db_session, org, title={"de": f"Station {index + 1}"})
        await link_station_to_rallye(db_session, org, rallye, station)
    await db_session.commit()
    return _Env(org, get_auth_headers(user), rallye.id)


async def _create_run(client: AsyncClient, env: _Env, scheduled_for: datetime | None = None) -> str:
    body: dict[str, Any] = {"rallye_id": str(env.rallye_id), "name": "Einmal-Lauf"}
    if scheduled_for is not None:
        body["scheduled_for"] = scheduled_for.isoformat()
    response = await client.post(f"/api/orgs/{env.org.slug}/runs/", headers=env.headers, json=body)
    assert response.status_code == 201, response.text
    run_id: str = response.json()["id"]
    return run_id


async def _run_stations(db_session: AsyncSession, run_id: str) -> int:
    """The number of run stations of the run, as committed."""
    count = await db_session.scalar(select(func.count()).select_from(RunStation).where(RunStation.run_id == run_id))
    return int(count or 0)


async def _run(db_session: AsyncSession, run_id: str) -> Run:
    """The run as committed."""
    return (
        await db_session.execute(select(Run).where(Run.id == run_id).execution_options(populate_existing=True))
    ).scalar_one()


def _other_side_after_the_first_read(
    monkeypatch: pytest.MonkeyPatch, other: Callable[[], Awaitable[Any]]
) -> list["asyncio.Task[Any]"]:
    """Run ``other`` once, right after the first read of a run (``RunRepository.get_by_id``) returned.

    The reader waits until ``other`` has ended, or until ``other`` asks for the row lock of the run
    (``RunRepository.lock_row``) after that first read. Returns the list that receives the task of
    ``other``, for the test to await after the first side.
    """
    original_read = RunRepository.get_by_id
    original_lock = RunRepository.lock_row
    fired = False
    asks_for_the_lock = asyncio.Event()
    tasks: list[asyncio.Task[Any]] = []

    async def lock_row(self: RunRepository, run_id: UUID) -> None:
        if fired:
            asks_for_the_lock.set()
        await original_lock(self, run_id)

    async def read(self: RunRepository, *args: Any, **kwargs: Any) -> Run | None:
        nonlocal fired
        result = await original_read(self, *args, **kwargs)
        if not fired:
            fired = True
            task = asyncio.create_task(other())
            tasks.append(task)
            waiter = asyncio.create_task(asks_for_the_lock.wait())
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
        return result

    monkeypatch.setattr(RunRepository, "get_by_id", read)
    monkeypatch.setattr(RunRepository, "lock_row", lock_row)
    return tasks


@pytest.fixture
def service_sessions(test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """The automatic start opens its own sessions; point them at the test database."""
    monkeypatch.setattr(
        run_start,
        "async_session_maker",
        async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False),
    )


# ============================================================================
# Resume accepts only a paused run
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", [False, True], ids=["configured", "scheduled"])
async def test_resume_refuses_a_run_that_never_started(
    concurrent_client: AsyncClient, db_session: AsyncSession, scheduled: bool
):
    """Resume on a configured or a scheduled run answers 422, and the run keeps its state and has no snapshot."""
    env = await _env(db_session, f"resume-never-{'scheduled' if scheduled else 'configured'}")
    scheduled_for = datetime.now(UTC) + timedelta(hours=2) if scheduled else None
    run_id = await _create_run(concurrent_client, env, scheduled_for)
    expected = RunState.SCHEDULED if scheduled else RunState.CONFIGURED

    response = await concurrent_client.post(env.url(run_id, "resume"), headers=env.headers)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "Invalid state transition" in detail
    assert expected.value in detail
    run = await _run(db_session, run_id)
    assert run.state == expected
    assert run.started_at is None
    assert await _run_stations(db_session, run_id) == 0


# ============================================================================
# A run is started once
# ============================================================================


@pytest.mark.asyncio
async def test_two_starts_at_once_start_the_run_once(
    concurrent_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Two starts of one run at once: exactly one succeeds, and the run has the run stations of one snapshot."""
    env = await _env(db_session, "start-twice")
    run_id = await _create_run(concurrent_client, env)

    async def second_start() -> Response:
        return await concurrent_client.post(env.url(run_id, "start"), headers=env.headers)

    others = _other_side_after_the_first_read(monkeypatch, second_start)
    first = await concurrent_client.post(env.url(run_id, "start"), headers=env.headers)
    (second,) = await asyncio.gather(*others)

    assert sorted([first.status_code, second.status_code]) == [200, 422], (first.text, second.text)
    run = await _run(db_session, run_id)
    assert run.state == RunState.ACTIVE
    assert await _run_stations(db_session, run_id) == STATIONS


@pytest.mark.asyncio
async def test_a_manual_start_against_the_automatic_start_starts_the_run_once(
    concurrent_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    service_sessions: None,
):
    """A manual start against the automatic start of the same due, scheduled run: one start, one snapshot."""
    env = await _env(db_session, "start-manual-auto")
    scheduled_for = datetime.now(UTC) + timedelta(minutes=30)
    run_id = await _create_run(concurrent_client, env, scheduled_for)
    due = scheduled_for + timedelta(seconds=10)

    async def automatic_start() -> dict[str, int]:
        return await run_start.start_due_runs_once(now=due)

    others = _other_side_after_the_first_read(monkeypatch, automatic_start)
    manual = await concurrent_client.post(env.url(run_id, "start"), headers=env.headers)
    (summary,) = await asyncio.gather(*others)

    started = (1 if manual.status_code == 200 else 0) + summary.get("started", 0)
    assert started == 1, (manual.status_code, manual.text, summary)
    run = await _run(db_session, run_id)
    assert run.state == RunState.ACTIVE
    assert await _run_stations(db_session, run_id) == STATIONS


@pytest.mark.asyncio
async def test_a_start_against_a_pause_leaves_a_consistent_run_with_one_snapshot(
    concurrent_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """A start against a pause of the same run: the result is a consistent state, and one snapshot.

    After the first start has read the state, another start and a pause of the run come in.
    """
    env = await _env(db_session, "start-pause")
    run_id = await _create_run(concurrent_client, env)

    async def start_then_pause() -> tuple[Response, Response]:
        start = await concurrent_client.post(env.url(run_id, "start"), headers=env.headers)
        pause = await concurrent_client.post(env.url(run_id, "pause"), headers=env.headers)
        return start, pause

    others = _other_side_after_the_first_read(monkeypatch, start_then_pause)
    first = await concurrent_client.post(env.url(run_id, "start"), headers=env.headers)
    ((second, pause),) = await asyncio.gather(*others)

    assert sorted([first.status_code, second.status_code]) == [200, 422], (first.text, second.text)
    run = await _run(db_session, run_id)
    assert run.started_at is not None
    if pause.status_code == 200:
        assert run.state == RunState.PAUSED
        assert run.paused_at is not None
    else:
        assert run.state == RunState.ACTIVE
        assert run.paused_at is None
    assert await _run_stations(db_session, run_id) == STATIONS


# ============================================================================
# The snapshot refuses a second build
# ============================================================================


@pytest.mark.asyncio
async def test_the_snapshot_refuses_a_second_build(concurrent_client: AsyncClient, db_session: AsyncSession):
    """``create_run_snapshot`` for a run that has run stations raises and adds nothing."""
    env = await _env(db_session, "snapshot-twice")
    run_id = await _create_run(concurrent_client, env)
    started = await concurrent_client.post(env.url(run_id, "start"), headers=env.headers)
    assert started.status_code == 200, started.text
    assert await _run_stations(db_session, run_id) == STATIONS
    run = await _run(db_session, run_id)

    with pytest.raises(ValueError):
        await create_run_snapshot(db_session, run, env.org.id)

    await db_session.flush()
    assert await _run_stations(db_session, run_id) == STATIONS
    await db_session.rollback()
    assert await _run_stations(db_session, run_id) == STATIONS
