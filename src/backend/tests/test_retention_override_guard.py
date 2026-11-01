"""An out-of-range retention override cannot stop the sweep.

The general organization settings endpoint (``PUT /api/orgs/{slug}``) validates the bounds of
any ``settings.retention`` value, as the dedicated retention endpoint does. Without that guard,
an override like ``LK_B: 1000000000`` would be stored, ``read_org_override`` would return it,
and ``ended_due_run_ids`` would raise ``OverflowError`` in ``timedelta``,
stopping the sweep for *every* organization.

The bounds live in one place (``retention_config.validate_period``) and guard
the write through the general settings, the read, and the sweep.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run, RunState
from app.services import retention, retention_tasks
from app.services.gdpr import DeletionClass
from app.services.retention_config import effective_retention_days, read_org_override
from app.services.retention_tasks import ended_due_run_ids

OUT_OF_RANGE = 1_000_000_000  # above RETENTION_MAX_DAYS (3650); overflows timedelta


def _event_name(call) -> str | None:
    """The event name of a CapturingLogger call.

    Other test files reconfigure structlog, so the event arrives as a positional
    event dict or stdlib-style, as a positional event name with kwargs.
    """
    if call.args and isinstance(call.args[0], dict):
        return call.args[0].get("event")
    if call.args and isinstance(call.args[0], str):
        return call.args[0]
    return None


def _out_of_range_warnings(capturing) -> int:
    """The out-of-range warnings a CapturingLogger saw, in either call shape."""
    return sum(1 for call in capturing.calls if _event_name(call) == "retention_override_out_of_range")


async def _ended_run(db: AsyncSession, org, *, ended_days_ago: int) -> Run:
    run = Run(
        organization_id=org.id,
        name=f"R-{uuid.uuid4().hex[:6]}",
        state=RunState.ENDED,
        join_code=uuid.uuid4().hex[:6].upper(),
        ended_at=datetime.now(UTC) - timedelta(days=ended_days_ago),
    )
    db.add(run)
    await db.flush()
    return run


# ---------------------------------------------------------------------------
# The write through the general settings endpoint is refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_general_settings_refuses_out_of_range_override(client_org_a: AsyncClient):
    """PUT /api/orgs/{slug} with settings.retention.LK_B out of range → 422."""
    resp = await client_org_a.put(
        "/api/orgs/org-a",
        json={"settings": {"retention": {"LK_B": OUT_OF_RANGE}}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_general_settings_refuses_below_floor_override(client_org_a: AsyncClient):
    """A value below the backup-rotation floor is refused like on the retention endpoint."""
    resp = await client_org_a.put(
        "/api/orgs/org-a",
        json={"settings": {"retention": {"LK_B": 7}}},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_general_settings_accepts_in_range_override(client_org_a: AsyncClient):
    """An in-range override through the general settings is still accepted and stored."""
    resp = await client_org_a.put(
        "/api/orgs/org-a",
        json={"settings": {"retention": {"LK_B": 120}}},
    )
    assert resp.status_code == 200
    assert resp.json()["settings"]["retention"]["LK_B"] == 120


# ---------------------------------------------------------------------------
# A stored out-of-range value reads as the default, logged once per run
# ---------------------------------------------------------------------------


def test_stored_out_of_range_override_reads_as_default():
    settings_json = {"retention": {"LK_B": OUT_OF_RANGE}}
    assert read_org_override(settings_json, DeletionClass.LK_B) is None
    assert effective_retention_days(DeletionClass.LK_B, settings_json) == (DeletionClass.LK_B.retention_days)


@pytest.mark.asyncio
async def test_sweep_logs_out_of_range_override_once_per_run(
    db_session: AsyncSession, org_a, monkeypatch: pytest.MonkeyPatch
):
    from structlog.testing import CapturingLogger

    org_a.settings = {"retention": {"LK_B": OUT_OF_RANGE}}
    await db_session.flush()

    # Spy on the module logger rather than structlog's global capture: other
    # test files reconfigure structlog (``configured_logging``), and whether
    # events reach ``capture_logs`` depends on that leftover configuration.
    capturing = CapturingLogger()
    monkeypatch.setattr(retention_tasks, "logger", capturing)

    ids = await ended_due_run_ids(db_session, datetime.now(UTC), DeletionClass.LK_B)

    assert ids == []  # no run is due, and nothing raised
    assert _out_of_range_warnings(capturing) == 1


# ---------------------------------------------------------------------------
# One broken org does not stop the sweep for the others
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broken_org_does_not_stop_sweep_for_others(db_session: AsyncSession, org_a, org_b):
    """Org A's broken override is skipped; org B's due run is still collected."""
    now = datetime.now(UTC)
    org_a.settings = {"retention": {"LK_B": OUT_OF_RANGE}}
    await db_session.flush()
    # Org A's run is inside the over-fetched window, so the broken override is
    # what ``ended_due_run_ids`` computes its eligibility with.
    await _ended_run(db_session, org_a, ended_days_ago=100)
    run_b = await _ended_run(db_session, org_b, ended_days_ago=100)

    ids = await ended_due_run_ids(db_session, now, DeletionClass.LK_B)

    # Compare as strings: model ids are uuid_utils.UUID, rows come back as
    # stdlib uuid.UUID, and the two do not compare equal across types.
    assert str(run_b.id) in {str(run_id) for run_id in ids}


# ---------------------------------------------------------------------------
# Zero and negative stored periods are validated and warned like the rest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, -90, OUT_OF_RANGE, 30 - 1])
def test_stored_non_positive_or_below_floor_reads_as_default(value: int):
    """Every stored integer outside the accepted range reads as the default."""
    settings_json = {"retention": {"LK_B": value}}
    assert read_org_override(settings_json, DeletionClass.LK_B) is None
    assert effective_retention_days(DeletionClass.LK_B, settings_json) == DeletionClass.LK_B.retention_days


# ---------------------------------------------------------------------------
# The out-of-range warning is logged once per sweep, not once per read
# ---------------------------------------------------------------------------


def _sweep_with_tasks(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, tasks) -> None:
    """Run ``retention_sweep_once`` over the test session with a reduced task list."""
    import contextlib

    @contextlib.asynccontextmanager
    async def _test_session_maker():
        yield db_session

    monkeypatch.setattr(retention, "async_session_maker", lambda: _test_session_maker())
    monkeypatch.setattr(retention, "_RETENTION_TASKS", tuple(tasks))


@pytest.mark.asyncio
async def test_out_of_range_warning_logged_once_per_sweep(
    db_session: AsyncSession, org_a, monkeypatch: pytest.MonkeyPatch
):
    """A sweep reads LK_B eligibility twice, for media and chat, and warns once. The next sweep warns again."""
    from structlog.testing import CapturingLogger

    org_a.settings = {"retention": {"LK_B": OUT_OF_RANGE}}
    await db_session.flush()

    capturing = CapturingLogger()
    monkeypatch.setattr(retention_tasks, "logger", capturing)
    _sweep_with_tasks(
        monkeypatch,
        db_session,
        [("lk_b_media", retention_tasks.sweep_run_media), ("lk_b_chat", retention_tasks.sweep_run_chat)],
    )

    await retention.retention_sweep_once()
    assert _out_of_range_warnings(capturing) == 1

    # No manual reset: the sweep boundary alone makes the next sweep warn again.
    await retention.retention_sweep_once()
    assert _out_of_range_warnings(capturing) == 2


@pytest.mark.asyncio
async def test_out_of_range_warning_once_when_media_step_is_slow(
    db_session: AsyncSession, org_a, monkeypatch: pytest.MonkeyPatch
):
    """The dedup does not depend on elapsed time: a media step longer than any window still warns once."""
    import time

    from structlog.testing import CapturingLogger

    org_a.settings = {"retention": {"LK_B": OUT_OF_RANGE}}
    await db_session.flush()

    capturing = CapturingLogger()
    monkeypatch.setattr(retention_tasks, "logger", capturing)

    clock = {"now": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])

    async def slow_media(session: AsyncSession) -> int:
        count = await retention_tasks.sweep_run_media(session)
        clock["now"] += 24 * 3600.0  # the media step "took" a day
        return count

    _sweep_with_tasks(
        monkeypatch, db_session, [("lk_b_media", slow_media), ("lk_b_chat", retention_tasks.sweep_run_chat)]
    )

    await retention.retention_sweep_once()
    assert _out_of_range_warnings(capturing) == 1


# ---------------------------------------------------------------------------
# Settings that are no JSON object never abort the sweep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("broken_settings", [["LK_B"], "nope", 42, {"retention": "nope"}, {"retention": [1000]}])
@pytest.mark.asyncio
async def test_malformed_settings_do_not_stop_sweep_for_others(
    db_session: AsyncSession, org_a, org_b, broken_settings, monkeypatch: pytest.MonkeyPatch
):
    """A stored non-object settings value (or retention key) is no override."""
    from structlog.testing import CapturingLogger

    now = datetime.now(UTC)
    org_a.settings = broken_settings  # type: ignore[assignment]
    await db_session.flush()
    await _ended_run(db_session, org_a, ended_days_ago=100)
    run_b = await _ended_run(db_session, org_b, ended_days_ago=100)

    capturing = CapturingLogger()
    monkeypatch.setattr(retention_tasks, "logger", capturing)
    ids = await ended_due_run_ids(db_session, now, DeletionClass.LK_B)

    # Both runs are due under the 90-day default. Broken settings resolve to
    # "no override", so org B is collected normally, and org A too.
    assert str(run_b.id) in {str(run_id) for run_id in ids}
    assert not [c for c in capturing.calls if _event_name(c) == "retention_eligibility_failed"]
