"""Retention of the feedback module's notes.

Notes are session material: the kit exports them into its session directory,
and the App keeps them for ``QA_FEEDBACK_RETENTION_DAYS``. The sweep runs
whether the module is on or off, so a switched-off module still empties itself.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.qa_feedback import QaNote, QaState


async def sweep_qa_notes(session: AsyncSession, reference_date: datetime | None = None) -> int:
    """Delete notes older than the retention window, and clear a plan untouched for as long.

    A plan may hold the stand's logins, so it must not outlive its session.
    Does not commit. Returns the deleted notes plus the cleared plans.
    """
    now = reference_date or datetime.now(UTC)
    cutoff = now - timedelta(days=get_settings().qa_feedback.retention_days)
    notes = await session.execute(delete(QaNote).where(QaNote.created_at < cutoff))
    plans = await session.execute(
        update(QaState)
        .where(QaState.updated_at < cutoff, QaState.key_hash.is_not(None))
        .values(plan=[], step=None, key_hash=None, message=None, message_at=None)
    )
    return (notes.rowcount or 0) + (plans.rowcount or 0)  # type: ignore[attr-defined]
