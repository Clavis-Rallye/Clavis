"""Deletion of rich-text images that no text shows any more.

The editor uploads an image the moment the creator inserts it
(``POST /api/orgs/{slug}/media/``, ``routers/media.py``). The object lies under
``{org_id}/media/{uuid}_{name}``, and the text carries its route in an
``<img src>``. There is no DELETE route on purpose: one object can stand in
several places at once, and a delete when the image leaves one text would
break the others.

- the languages of one text share the image,
- a copied station or challenge carries the same ``src`` (``content_copy.py``),
- a run shows the frozen copy of its texts (``snapshot.py``) until the run is
  deleted,
- a soft-deleted rallye, station or challenge can be restored.

So the daily sweep counts references. An object goes only when both hold:

- it is older than ``CONTENT_MEDIA_ORPHAN_MIN_AGE_DAYS``, so an image the
  creator inserted but did not save yet survives the edit,
- no row of a reference table names its key. The check reads every text and
  JSON column of these tables, soft-deleted rows and run copies included, not
  a list of known fields. A new rich-text field is covered without a change
  here, and a test makes every table with such a column choose a side
  (``REFERENCE_TABLES`` or ``NOT_CONTENT_TABLES``).

The grace counts from the upload, not from the removal: an image that leaves
its last text goes at the first daily sweep after its grace. A creator who
removes an image, saves, and restores it by undo after a sweep gets a broken
image.

The whole prefix of a deleted organization goes in ``org_deletion.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from minio.error import S3Error
from sqlalchemy import JSON, String, Table, Text, cast, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

import app.models  # noqa: F401 — registers every table on Base.metadata
from app.database import Base
from app.models.organization import Organization
from app.services.audit import audit_retention_swept
from app.services.gdpr import SYSTEM_RETENTION_ACTOR, DeletionClass
from app.services.retention_tasks import is_old_enough, variant_base_key
from app.storage import delete_file, list_all_files

logger = structlog.get_logger(__name__)

# The path segment of organization media, as ``routers.media.MEDIA_SEGMENT``
# writes it. A test holds the two together.
ORG_MEDIA_SEGMENT = "media"

# An image the creator inserted is uploaded at once and saved with the text
# later. Seven days cover an editing session that spans a weekend, the same
# grace as the participant uploads (``ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS``).
CONTENT_MEDIA_ORPHAN_MIN_AGE_DAYS = 7

# Tables whose text and JSON columns can name an image of an organization. Every
# such column is read, soft-deleted rows and run copies included.
REFERENCE_TABLES: tuple[str, ...] = (
    "rallyes",  # description
    "stations",  # description, tips header
    "challenges",  # instructions
    "organizations",  # the three legal texts, settings
    "instance_settings",  # the installation's legal texts
    "runs",  # settings of a run
    "run_stations",  # the frozen copy of a station: description, tips
    "run_challenges",  # the frozen copy of a challenge: instructions
    "run_result_links",  # the frozen result of a published run
    "feed_events",  # announcements of a run
)

# Tables with text or JSON columns that never carry an organization's image.
# Reading them would cost time and prove nothing.
NOT_CONTENT_TABLES: dict[str, str] = {
    "chat_messages": "plain text of participants, never rendered as rich text",
    "feedback": "rating comments of participants, plain text",
    "geo_places": "the regional address index",
    "instance_admins": "operator accounts",
    "password_reset_tokens": "token hashes",
    "qa_notes": "notes of the feedback module",
    "qa_state": "state of the feedback module",
    "scores": "coordinator comments, plain text",
    "submissions": "participant answers, their files lie under the run prefix",
    "team_members": "display names",
    "teams": "team names and colors",
    "user_invitations": "invitation addresses",
    "users": "accounts",
}


def text_columns(table: Table) -> list[Any]:
    """Every column of ``table`` that can hold text: strings, texts, enums and JSON."""
    return [column for column in table.columns if isinstance(column.type, (String, JSON))]


async def referencing_texts(session: AsyncSession) -> str:
    """Every stored value of the reference tables that mentions organization media, joined.

    Only values that contain ``/media/`` leave the database. A key counts as
    referenced when it appears anywhere in the result, as a substring. That is
    on the safe side: a key in an unexpected spot keeps its object.
    """
    marker = f"%/{ORG_MEDIA_SEGMENT}/%"
    chunks: list[str] = []
    for table_name in REFERENCE_TABLES:
        table = Base.metadata.tables[table_name]
        for column in text_columns(table):
            as_text = cast(column, Text)
            values = (await session.execute(select(as_text).where(as_text.like(marker)))).scalars()
            chunks.extend(value for value in values if value)
    return "\n".join(chunks)


async def sweep_orphaned_content_media(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """Delete the organization images that no text, copy or run shows any more, and return how many.

    Lists ``{org_id}/media/`` of every organization row, archived and
    soft-deleted ones included, before it reads the references. That keeps the
    window small in which a text saved during the sweep names an object
    already judged. A failure on one key is logged and skipped. A second pass
    finds nothing.
    """
    now = reference_date or datetime.now(UTC)
    cutoff = now - timedelta(days=CONTENT_MEDIA_ORPHAN_MIN_AGE_DAYS)

    organization_ids = (await session.execute(select(Organization.id))).scalars().all()
    candidates: list[dict[str, Any]] = []
    for organization_id in organization_ids:
        prefix = f"{organization_id}/{ORG_MEDIA_SEGMENT}/"
        candidates.extend(await run_in_threadpool(list_all_files, prefix=prefix))
    if not candidates:
        return 0

    texts = await referencing_texts(session)

    def _referenced(key: str) -> bool:
        # A derived variant (``<key>.thumb.webp``) lives as long as its original.
        base = variant_base_key(key) or key
        return key in texts or base in texts

    deleted = 0
    for obj in candidates:
        key = obj["key"]
        if _referenced(key) or not is_old_enough(obj, cutoff):
            continue
        try:
            if await run_in_threadpool(delete_file, key):
                deleted += 1
        except (S3Error, RuntimeError) as exc:
            logger.warning("retention_content_media_delete_failed", object_key=key, error=str(exc))

    if deleted:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_B.value,
            swept=deleted,
            kind="rich_text_image",
        )
        logger.info("retention_swept_content_media", objects_deleted=deleted)
    return deleted
