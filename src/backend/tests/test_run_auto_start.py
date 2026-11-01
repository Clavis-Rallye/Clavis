"""
Automatic start of planned runs, and the run edit before the start.

A run with a start time starts on its own at that time, through the same snapshot, transition and
notification as the "Starten" button, unless its organization switched this off. The run page edits
name, team limit and start time before the start. The tests move the clock instead of waiting:
``start_due_runs_once(now=...)``.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.organization import Organization
from app.models.run import Run, RunState
from app.models.run_station import RunStation
from app.models.user import UserRole
from app.services import run_start
from tests.factories import (
    create_test_rallye,
    create_test_station,
    create_test_user_with_org,
    get_auth_headers,
    link_station_to_rallye,
)


class _AuditRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **kwargs: object) -> None:
        self.events.append({"event": event, **kwargs})

    def named(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> _AuditRecorder:
    recorder = _AuditRecorder()
    monkeypatch.setattr(run_start, "record_audit_event", recorder)
    return recorder


@pytest.fixture
def service_sessions(test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    """The service opens its own sessions; point them at the test database."""
    monkeypatch.setattr(
        run_start,
        "async_session_maker",
        async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False),
    )


async def _coordinator_with_rallye(db_session: AsyncSession, *, slug: str, stations: int = 1):
    user, org = await create_test_user_with_org(db_session, role=UserRole.COORDINATOR, org_slug=slug)
    rallye = await create_test_rallye(db_session, org, title={"de": "Plan-Rallye"})
    for index in range(stations):
        station = await create_test_station(db_session, org, title={"de": f"Station {index + 1}"})
        await link_station_to_rallye(db_session, org, rallye, station)
    await db_session.commit()
    return user, org, rallye


async def _create_run(client: AsyncClient, org: Organization, headers: dict, rallye, scheduled_for: datetime | None):
    body: dict = {"rallye_id": str(rallye.id), "name": "Geplanter Lauf"}
    if scheduled_for is not None:
        body["scheduled_for"] = scheduled_for.isoformat()
    response = await client.post(f"/api/orgs/{org.slug}/runs/", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _run(db_session: AsyncSession, run_id: str) -> Run:
    db_session.expire_all()
    return (await db_session.execute(select(Run).where(Run.id == run_id))).scalar_one()


@pytest.mark.asyncio
async def test_a_start_time_from_the_wizard_makes_the_run_scheduled(client: AsyncClient, db_session: AsyncSession):
    """A run created with a start time is "Geplant", and one created without is "Konfiguriert"."""
    user, org, rallye = await _coordinator_with_rallye(db_session, slug="auto-wizard")
    headers = get_auth_headers(user)

    planned = await _create_run(client, org, headers, rallye, datetime.now(UTC) + timedelta(hours=2))
    assert planned["state"] == RunState.SCHEDULED.value

    unplanned = await _create_run(client, org, headers, rallye, None)
    assert unplanned["state"] == RunState.CONFIGURED.value


@pytest.mark.asyncio
async def test_a_due_run_starts_on_its_own_like_the_button(
    client: AsyncClient, db_session: AsyncSession, service_sessions: None, audit: _AuditRecorder
):
    """At its time the run is ACTIVE with its snapshot, and the start is audited as automatic."""
    user, org, rallye = await _coordinator_with_rallye(db_session, slug="auto-due", stations=2)
    start_at = datetime.now(UTC) + timedelta(minutes=30)
    created = await _create_run(client, org, get_auth_headers(user), rallye, start_at)

    early = await run_start.start_due_runs_once(now=start_at - timedelta(minutes=1))
    assert early == {}
    assert (await _run(db_session, created["id"])).state == RunState.SCHEDULED

    summary = await run_start.start_due_runs_once(now=start_at + timedelta(seconds=10))
    assert summary == {"started": 1}
    run = await _run(db_session, created["id"])
    assert run.state == RunState.ACTIVE
    assert run.started_at is not None
    stations = (await db_session.execute(select(RunStation).where(RunStation.run_id == run.id))).scalars().all()
    assert len(stations) == 2

    started = audit.named("run.started")
    assert len(started) == 1
    assert started[0]["actor"] == run_start.SYSTEM_AUTO_START_ACTOR
    assert started[0]["target"]["auto_start"] is True
    assert started[0]["target"]["missed"] is False

    assert await run_start.start_due_runs_once(now=start_at + timedelta(minutes=5)) == {}


@pytest.mark.asyncio
async def test_a_start_missed_while_the_api_was_down_happens_at_the_next_check(
    client: AsyncClient, db_session: AsyncSession, service_sessions: None, audit: _AuditRecorder
):
    """The first check after downtime starts the run and marks the start as missed."""
    user, org, rallye = await _coordinator_with_rallye(db_session, slug="auto-missed")
    start_at = datetime.now(UTC) + timedelta(minutes=10)
    created = await _create_run(client, org, get_auth_headers(user), rallye, start_at)

    summary = await run_start.start_due_runs_once(now=start_at + timedelta(hours=1))
    assert summary == {"started": 1}
    assert (await _run(db_session, created["id"])).state == RunState.ACTIVE
    target = audit.named("run.started")[0]["target"]
    assert target["missed"] is True
    assert target["late_seconds"] >= 3600


@pytest.mark.asyncio
async def test_with_the_setting_off_the_time_is_only_a_plan(
    client: AsyncClient, db_session: AsyncSession, service_sessions: None, audit: _AuditRecorder
):
    """An organization that switched automatic start off keeps the start manual."""
    user, org, rallye = await _coordinator_with_rallye(db_session, slug="auto-off")
    await db_session.execute(
        update(Organization).where(Organization.id == org.id).values(settings={run_start.AUTO_START_SETTING: False})
    )
    await db_session.commit()
    start_at = datetime.now(UTC) + timedelta(minutes=5)
    created = await _create_run(client, org, get_auth_headers(user), rallye, start_at)

    summary = await run_start.start_due_runs_once(now=start_at + timedelta(minutes=1))
    assert summary == {"off": 1}
    assert (await _run(db_session, created["id"])).state == RunState.SCHEDULED
    assert audit.named("run.started") == []


@pytest.mark.asyncio
async def test_a_rallye_without_stations_keeps_the_run_scheduled_and_says_why(
    client: AsyncClient, db_session: AsyncSession, service_sessions: None, audit: _AuditRecorder
):
    """
    The run stays "Geplant", the reason is on the run, and the audit event comes once.

    A station added later lets the next check start the run and drop the reason.
    """
    user, org, rallye = await _coordinator_with_rallye(db_session, slug="auto-empty", stations=0)
    headers = get_auth_headers(user)
    start_at = datetime.now(UTC) + timedelta(minutes=5)
    created = await _create_run(client, org, headers, rallye, start_at)

    for minutes in (1, 2):
        summary = await run_start.start_due_runs_once(now=start_at + timedelta(minutes=minutes))
        assert summary == {"blocked": 1}
    run = await _run(db_session, created["id"])
    assert run.state == RunState.SCHEDULED
    assert run.settings[run_start.AUTO_START_BLOCKED]["code"] == "RALLYE_HAS_NO_STATIONS"
    assert len(audit.named("run.auto_start_blocked")) == 1

    await db_session.refresh(org)
    await db_session.refresh(rallye)
    page = await client.get(f"/api/orgs/{org.slug}/runs/{created['id']}", headers=headers)
    assert page.json()["settings"]["auto_start_blocked"]["code"] == "RALLYE_HAS_NO_STATIONS"

    station = await create_test_station(db_session, org, title={"de": "Nachgereicht"})
    await link_station_to_rallye(db_session, org, rallye, station)
    await db_session.commit()
    summary = await run_start.start_due_runs_once(now=start_at + timedelta(minutes=3))
    assert summary == {"started": 1}
    run = await _run(db_session, created["id"])
    assert run.state == RunState.ACTIVE
    assert run_start.AUTO_START_BLOCKED not in (run.settings or {})


@pytest.mark.asyncio
async def test_run_edit_before_the_start(client: AsyncClient, db_session: AsyncSession):
    """
    Name, team limit and start time change before the start, and only the fields sent.

    A start time plans the run and clearing it un-plans it. An empty name and any edit after the start are refused.
    """
    user, org, rallye = await _coordinator_with_rallye(db_session, slug="run-edit")
    headers = get_auth_headers(user)
    created = await _create_run(client, org, headers, rallye, None)
    url = f"/api/orgs/{org.slug}/runs/{created['id']}"

    response = await client.put(url, headers=headers, json={"max_teams": 4})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Geplanter Lauf"
    assert response.json()["max_teams"] == 4

    planned_for = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    response = await client.put(url, headers=headers, json={"scheduled_for": planned_for.isoformat()})
    assert response.json()["state"] == RunState.SCHEDULED.value
    response = await client.put(url, headers=headers, json={"scheduled_for": None, "max_teams": None})
    data = response.json()
    assert data["state"] == RunState.CONFIGURED.value
    assert data["scheduled_for"] is None
    assert data["max_teams"] is None

    assert (await client.put(url, headers=headers, json={"name": None})).status_code == 422
    assert (await client.post(f"{url}/start", headers=headers)).status_code == 200
    assert (await client.put(url, headers=headers, json={"name": "Zu spät"})).status_code == 422
