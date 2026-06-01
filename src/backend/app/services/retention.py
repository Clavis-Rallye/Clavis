"""The daily data retention sweep.

It runs inside the API process, started from the FastAPI lifespan like
PgNotifyListener. The delivery is a single self-contained stack, so an
in-process loop is preferred over host cron.

Each pass runs these tasks, each in its own transaction, so that one failing
task never blocks the others. The periods are the defaults. An organization
or the installation can change those of LK_A, LK_B, LK_C and LK_H.

- LK_G: admin-deleted users past the 30-day grace window → anonymized
- LK_B: submission raw media and chat messages of runs ended ≥ 90 days ago
  → deleted
- LK_A: guest accounts of ended or abandoned runs → deleted, their names in
  the team rosters replaced
- LK_C: team names and the remaining roster names of runs ended ≥ 1 year ago
  → anonymized
- LK_H: answer text, quiz choices, score comments and feedback comments of
  runs ended ≥ 1 year ago → removed, the rows stay
- roster names of accounts that are already anonymized → replaced
- station media orphans: title images of soft-deleted stations that no live
  station and no run station references → deleted
- orphaned participant media: run-scoped uploads no submission references,
  older than 7 days → deleted
- orphaned rich-text images: organization media no text, copy or run shows,
  older than 7 days → deleted
- dead invitations: accepted, revoked or expired more than 30 days ago
  → deleted
- expired result links: published run result links past their expiry
  → deleted
- org_deletion: archived organizations whose scheduled deletion is due (30
  days after the request) → every row and every referenced object
  hard-deleted, then the organization row
- qa_notes: notes of the feedback module older than QA_FEEDBACK_RETENTION_DAYS
  → deleted, with the module on or off
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.services.content_media_retention import sweep_orphaned_content_media
from app.services.gdpr import sweep_expired_grace_deletions
from app.services.org_deletion import sweep_due_organizations
from app.services.qa_feedback_retention import sweep_qa_notes
from app.services.retention_tasks import (
    override_warning_scope,
    sweep_anonymized_member_names,
    sweep_dead_invitations,
    sweep_expired_result_links,
    sweep_orphaned_station_media,
    sweep_orphaned_submission_media,
    sweep_run_anonymize,
    sweep_run_answer_text,
    sweep_run_chat,
    sweep_run_guests,
    sweep_run_media,
)

logger = structlog.get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 24 * 60 * 60

# Each task takes a session and returns a count. Each runs in its own
# transaction, so a failure is isolated and retried at the next pass. The
# tasks are independent, so the order does not matter.
#
# org_deletion commits or rolls back once per organization itself, so an
# audited deletion is never undone by a later failure of another
# organization. The commit of ``_run_task`` then finds nothing pending.
_RETENTION_TASKS: tuple[tuple[str, Callable[[AsyncSession], Awaitable[int]]], ...] = (
    ("lk_g_users", sweep_expired_grace_deletions),
    ("lk_b_media", sweep_run_media),
    ("lk_b_chat", sweep_run_chat),
    ("lk_a_guests", sweep_run_guests),
    ("lk_c_anonymized", sweep_run_anonymize),
    ("lk_h_answer_text", sweep_run_answer_text),
    ("anonymized_member_names", sweep_anonymized_member_names),
    ("station_media_orphans", sweep_orphaned_station_media),
    ("orphaned_submission_media", sweep_orphaned_submission_media),
    ("orphaned_content_media", sweep_orphaned_content_media),
    ("dead_invitations", sweep_dead_invitations),
    ("expired_result_links", sweep_expired_result_links),
    ("org_deletion", sweep_due_organizations),
    ("qa_notes", sweep_qa_notes),
)


async def _run_task(name: str, task: Callable[[AsyncSession], Awaitable[int]]) -> int:
    """Run one task in its own session and commit on success.

    A failure is logged and returns 0: the loop must survive any failing
    task, and the session rolls the partial transaction back.
    """
    try:
        async with async_session_maker() as session:
            count = await task(session)
            await session.commit()
        return count
    except Exception:
        logger.exception("retention_task_failed", task=name)
        return 0


async def retention_sweep_once() -> dict[str, int]:
    """Run every task once, each isolated, and return the count of each."""
    summary: dict[str, int] = {}
    # One scope per sweep call: a broken override is warned once per sweep.
    with override_warning_scope():
        for name, task in _RETENTION_TASKS:
            summary[name] = await _run_task(name, task)

    if any(summary.values()):
        logger.info("retention_sweep_completed", **summary)
    return summary


class RetentionSweeper:
    """The daily retention sweep as a supervised asyncio task.

    It runs once at startup to catch up after downtime, then every 24 hours.
    A failing sweep is logged and retried at the next interval, and the loop
    never dies on an exception.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="retention-sweeper")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await retention_sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("retention_sweep_failed")
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
