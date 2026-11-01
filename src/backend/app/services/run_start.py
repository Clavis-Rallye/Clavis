"""
Starting a run, by hand and at its planned time.

A run starts in one way, whoever starts it: the frozen snapshot of the rallye,
the transition to ACTIVE with its timestamps, and the state notification that
tells every open page. The "Starten" button (``routers/runs.py``) and the
automatic start below share :func:`begin_run`, so an automatic start is
indistinguishable from a manual one for teams and coordination.

Automatic start: a run in state SCHEDULED
starts on its own once its ``scheduled_for`` has passed, unless its
organization switched this off (``organizations.settings["auto_start_runs"]``,
default on). With the switch off, the start time is only a plan and the start
stays manual. :class:`AutoStarter` checks every 30 seconds and once at API
start, so a start missed while the API was down happens at the next check and
is audited as late.

A run that cannot start (its rallye has no station, or is gone) stays
SCHEDULED and is tried again at every check, so adding a station is enough.
The reason sits in ``runs.settings["auto_start_blocked"]`` for the run page,
and one audit event records it when it first appears.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.models.organization import Organization
from app.models.run import Run, RunState
from app.services.audit import record_audit_event
from app.services.notify import notify_run_state_change
from app.services.run_state import RunStateService
from app.services.snapshot import create_run_snapshot

logger = structlog.get_logger("app")

# organizations.settings key. Missing means on: automatic starts are the default.
AUTO_START_SETTING = "auto_start_runs"
# runs.settings key holding why the last automatic start failed.
AUTO_START_BLOCKED = "auto_start_blocked"

CHECK_INTERVAL_SECONDS = 30
# A start this much after its time was missed (API down), not just the next tick.
LATE_AFTER_SECONDS = 2 * CHECK_INTERVAL_SECONDS

SYSTEM_AUTO_START_ACTOR: dict[str, Any] = {"system": "auto_start", "ip": "internal"}


def auto_start_enabled(org: Organization) -> bool:
    """Whether this organization starts planned runs on its own (default on)."""
    return org.readable_settings.get(AUTO_START_SETTING, True) is not False


def blocked_reason(error: ValueError) -> str:
    """A stable code for a snapshot refusal, for the run page to word."""
    text = str(error)
    if "has no stations" in text:
        return "RALLYE_HAS_NO_STATIONS"
    if "not found" in text:
        return "RALLYE_NOT_FOUND"
    return "SNAPSHOT_FAILED"


def _without_blocked(settings: dict[str, Any] | None) -> dict[str, Any]:
    clean = dict(settings or {})
    clean.pop(AUTO_START_BLOCKED, None)
    return clean


async def begin_run(db: AsyncSession, run: Run, organization_id: UUID) -> str:
    """Snapshot the rallye and move the run to ACTIVE by start, without committing.

    The caller holds the run's row lock and has read ``run`` under it. Returns
    the previous state for the notification. Raises ``ValueError`` when the
    start, the snapshot or the transition is refused; the caller rolls back.
    The start is checked first, so a refused state never builds a snapshot.
    """
    previous_state = run.state.value
    RunStateService.check_start(run.state)
    await create_run_snapshot(db, run, organization_id)
    RunStateService.start(run)
    if run.settings and AUTO_START_BLOCKED in run.settings:
        run.settings = _without_blocked(run.settings)
    return previous_state


async def _start_one(run_id: UUID, now: datetime) -> str:
    """Start one due run in its own transaction. Returns what happened."""
    async with async_session_maker() as db:
        # Row lock, skipped when another transaction holds it: one start only. A
        # manual start holds the run's row lock (RunRepository.lock_row) from before
        # its read to its commit, so this skips it, and the next check finds the run
        # active. The state is read by this locking statement, so it is the state
        # under the lock, whatever the list of due runs said before.
        row = (
            await db.execute(
                select(Run, Organization)
                .join(Organization, Organization.id == Run.organization_id)
                .where(Run.id == run_id)
                .with_for_update(of=Run, skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None:
            return "locked"
        run, org = row
        if run.state != RunState.SCHEDULED or run.scheduled_for is None or run.scheduled_for > now:
            return "not_due"
        if org.archived_at is not None or org.is_deleted or not auto_start_enabled(org):
            return "off"

        scheduled_for = run.scheduled_for
        try:
            previous_state = await begin_run(db, run, org.id)
        except ValueError as error:
            await db.rollback()
            reason = blocked_reason(error)
            return await _mark_blocked(db, run_id, reason, now)

        await db.commit()
        await db.refresh(run)
        await notify_run_state_change(db, run, previous_state)
        await db.commit()

        late_seconds = int((now - scheduled_for).total_seconds())
        record_audit_event(
            event="run.started",
            actor=dict(SYSTEM_AUTO_START_ACTOR),
            target={
                "entity": "run",
                "id": str(run.id),
                "name": run.name,
                "new_state": RunState.ACTIVE.value,
                "auto_start": True,
                "scheduled_for": scheduled_for.isoformat(),
                "late_seconds": late_seconds,
                "missed": late_seconds > LATE_AFTER_SECONDS,
            },
            organization_id=org.id,
        )
        logger.info("run_auto_started", run_id=str(run.id), late_seconds=late_seconds)
        return "started"


async def _mark_blocked(db: AsyncSession, run_id: UUID, reason: str, now: datetime) -> str:
    """Keep the run SCHEDULED and note why, auditing a new reason once."""
    run = (await db.execute(select(Run).where(Run.id == run_id).with_for_update())).scalar_one_or_none()
    if run is None:
        return "gone"
    previous = (run.settings or {}).get(AUTO_START_BLOCKED) or {}
    if previous.get("code") == reason:
        await db.rollback()
        return "blocked"
    run.settings = {**(run.settings or {}), AUTO_START_BLOCKED: {"code": reason, "at": now.isoformat()}}
    await db.commit()
    record_audit_event(
        event="run.auto_start_blocked",
        actor=dict(SYSTEM_AUTO_START_ACTOR),
        target={"entity": "run", "id": str(run.id), "name": run.name, "reason": reason},
        organization_id=run.organization_id,
    )
    logger.warning("run_auto_start_blocked", run_id=str(run.id), reason=reason)
    return "blocked"


async def start_due_runs_once(now: datetime | None = None) -> dict[str, int]:
    """Start every scheduled run whose time has come. Returns counts per outcome.

    ``now`` is injectable so a test can move time instead of waiting.
    """
    moment = now or datetime.now(UTC)
    async with async_session_maker() as db:
        due = (
            (
                await db.execute(
                    select(Run.id).where(
                        Run.state == RunState.SCHEDULED,
                        Run.scheduled_for.is_not(None),
                        Run.scheduled_for <= moment,
                    )
                )
            )
            .scalars()
            .all()
        )
    summary: dict[str, int] = {}
    for run_id in due:
        try:
            outcome = await _start_one(run_id, moment)
        except Exception:
            logger.exception("run_auto_start_failed", run_id=str(run_id))
            outcome = "error"
        summary[outcome] = summary.get(outcome, 0) + 1
    return summary


class AutoStarter:
    """The automatic start as a supervised asyncio task.

    Checks once at start-up (catches starts missed while the API was down),
    then every :data:`CHECK_INTERVAL_SECONDS`. A failing check is logged and
    the loop goes on.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="run-auto-starter")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await start_due_runs_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("run_auto_start_check_failed")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
