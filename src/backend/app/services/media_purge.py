"""Purge of an organization's participant media by an org admin.

An org admin may count, and after typing the organization's slug delete,
every participant medium of one type before the LK_B period ends. The purge
serves the organization's duty to erase data. It deletes the media objects
and keeps every submission row and its score, so statistics and leaderboards
stay consistent. A purged submission loses the media reference and gets the
``media_removed`` marker, which the views show as "Medium entfernt".

The type of the object decides, not its field: a photo purge removes every
photo object, in ``photo_url`` or in a ``media_urls`` entry. The first of
these sources that knows the type decides. A later source decides only when
the earlier ones give no type at all:

1. the content type stored with the object. A PNG uploaded as ``photo.txt``
   is a photo.
2. the file name extension (``mimetypes.guess_type``),
3. the type of a scalar field (``photo_url``, ``video_url``, ``audio_url``)
   that references the key.

A ``media_urls`` entry without a stored type and without a known extension
has no type. It is left alone and logged. A failed metadata lookup is no
missing stored type: that key is skipped, reported as not classified, logged,
and never classified by its file name.

A WebP variant that a submission references directly is its own key. It is
purged and counted through that reference, not as a side effect of its
original going, which would make the count depend on the collection order.

The media fields are the constants of ``retention_tasks.py``, so a renamed or
added media field is picked up here too.

The purge writes in the caller's transaction and does not commit. It is
idempotent, tolerates an object that is already missing, and never clears a
reference whose object is still live: a failed delete is logged, and every
reference to that key stays for a later retry.
"""

import dataclasses
import mimetypes
from collections import defaultdict
from typing import Any, Literal
from uuid import UUID

import structlog
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from urllib3.exceptions import HTTPError

from app.config import settings
from app.models.submission import Submission
from app.services.retention_tasks import (
    SUBMISSION_MEDIA_LIST_FIELDS,
    SUBMISSION_MEDIA_SCALAR_FIELDS,
    unreferenced_variant_keys,
)
from app.storage import delete_file, get_minio_client

logger = structlog.get_logger(__name__)

MediaPurgeType = Literal["photo", "video", "audio"]

# The scalar field of each purge type, the last source of a key's type. The
# assert ties it to the fields of retention_tasks, so a renamed media field
# fails at import instead of silently missing purges.
MEDIA_TYPE_FIELDS: dict[MediaPurgeType, str] = {
    "photo": "photo_url",
    "video": "video_url",
    "audio": "audio_url",
}
assert set(MEDIA_TYPE_FIELDS.values()) <= set(SUBMISSION_MEDIA_SCALAR_FIELDS)
FIELD_TYPES: dict[str, MediaPurgeType] = {field: media_type for media_type, field in MEDIA_TYPE_FIELDS.items()}

# MIME type prefix to purge type, for the stored type and for the extension.
_MIME_PREFIX_TYPES: tuple[tuple[str, MediaPurgeType], ...] = (
    ("image/", "photo"),
    ("video/", "video"),
    ("audio/", "audio"),
)

# Content marker set on every submission touched by a purge. The purged
# reference is gone, and the views show "Medium entfernt" for it.
MEDIA_REMOVED_FLAG = "media_removed"

# One reference to a key: the submission and the field, enough to clear it.
_MediaRef = tuple[Submission, str]


async def _collect_org_media_refs(session: AsyncSession, org_id: UUID) -> dict[str, list[_MediaRef]]:
    """Every object key the org's submissions reference, with its references.

    Reads the scalar media fields and every ``media_urls`` entry, so a key in
    several fields or submissions is one entry.
    """
    refs_by_key: dict[str, list[_MediaRef]] = defaultdict(list)
    submissions = (
        (await session.execute(select(Submission).where(Submission.organization_id == org_id))).scalars().all()
    )
    for submission in submissions:
        content = submission.content or {}
        seen: set[tuple[str, str]] = set()
        for field in SUBMISSION_MEDIA_SCALAR_FIELDS:
            value = content.get(field)
            if isinstance(value, str) and value.strip() and (value, field) not in seen:
                refs_by_key[value].append((submission, field))
                seen.add((value, field))
        for field in SUBMISSION_MEDIA_LIST_FIELDS:
            values = content.get(field)
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and value.strip() and (value, field) not in seen:
                        refs_by_key[value].append((submission, field))
                        seen.add((value, field))
    return refs_by_key


@dataclasses.dataclass
class MediaPurgeResult:
    """What one purge did.

    ``purged`` counts the distinct media objects that existed and were
    removed. ``unclassified`` lists the keys whose metadata lookup failed:
    neither deleted nor cleared, so a later purge can retry them.
    """

    purged: int = 0
    unclassified: list[str] = dataclasses.field(default_factory=list)


async def _stored_content_type(key: str) -> str | None:
    """The content type stored with the object, or None.

    None means the lookup worked and found no type: the object is missing
    (``NoSuchKey``) or has no stored type, and the file name decides. Any
    other failure (a store error, no connection) is no missing type. It
    raises ``S3Error``, ``RuntimeError``, urllib3 ``HTTPError`` or
    ``OSError``, and the caller treats the key as not classified.
    """
    try:
        stat = await run_in_threadpool(
            get_minio_client().stat_object,
            settings.storage.bucket,
            key,
        )
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            return None
        raise
    return stat.content_type


def _classify(key: str, refs: list[_MediaRef], stored_type: str | None = None) -> MediaPurgeType | None:
    """The purge type of ``key``, or None when nothing tells it.

    The stored type decides first, then the file name extension, then a
    scalar field among ``refs``. A later source decides only when the earlier
    ones give no type at all, and an empty stored type is no type. A type that
    matches no purge type returns None, and no later source is asked. A key referenced only from ``media_urls``
    has no field to fall back on.
    """
    # The store returns the type as it was sent: in any case, padded, or
    # empty. An empty type falls back like a missing one.
    stored = stored_type.strip().lower() if stored_type is not None else ""
    for candidate in (stored, mimetypes.guess_type(key)[0]):
        if candidate:
            for prefix, media_type in _MIME_PREFIX_TYPES:
                if candidate.startswith(prefix):
                    return media_type
            return None
    for _submission, field in refs:
        field_type = FIELD_TYPES.get(field)
        if field_type is not None:
            return field_type
    return None


async def _classify_all(
    refs_by_key: dict[str, list[_MediaRef]], org_id: UUID
) -> tuple[dict[str, MediaPurgeType | None], list[str]]:
    """Classify every key, and list apart the keys whose metadata lookup failed.

    A failed key is logged and never classified by its file name.
    """
    classifications: dict[str, MediaPurgeType | None] = {}
    unclassified: list[str] = []
    for key, refs in refs_by_key.items():
        try:
            stored_type = await _stored_content_type(key)
        except (S3Error, RuntimeError, HTTPError, OSError) as exc:
            logger.warning(
                "media_purge_key_lookup_failed",
                organization_id=str(org_id),
                object_key=key,
                error=str(exc),
            )
            unclassified.append(key)
            continue
        classifications[key] = _classify(key, refs, stored_type)
    return classifications, unclassified


async def count_org_media(session: AsyncSession, org_id: UUID) -> dict[MediaPurgeType, int]:
    """Count the org's participant media per type, the keys a purge of that type would delete.

    An unreferenced photo variant goes uncounted with its original, as in the
    sweep and the purge. A key without a type, or whose lookup failed, counts
    toward no type.
    """
    refs_by_key = await _collect_org_media_refs(session, org_id)
    counts: dict[MediaPurgeType, int] = {"photo": 0, "video": 0, "audio": 0}
    classifications, _unclassified = await _classify_all(refs_by_key, org_id)
    for media_type in classifications.values():
        if media_type is not None:
            counts[media_type] += 1
    return counts


def _cleared_content(content: dict[str, Any], field: str, key: str) -> dict[str, Any]:
    """A copy of ``content`` without ``key`` in ``field``, with the marker set.

    A scalar field is nulled. A list field loses every entry equal to ``key``,
    matched by value and not by a remembered index, which the clear of
    another key in the same list would shift.
    """
    cleared = dict(content)
    if field in SUBMISSION_MEDIA_LIST_FIELDS:
        values = cleared.get(field)
        if isinstance(values, list):
            cleared[field] = [v for v in values if v != key]
    else:
        cleared[field] = None
    cleared[MEDIA_REMOVED_FLAG] = True
    return cleared


async def purge_org_media(session: AsyncSession, org_id: UUID, media_type: MediaPurgeType) -> int:
    """``purge_org_media_report`` for callers that need only the count."""
    return (await purge_org_media_report(session, org_id, media_type)).purged


async def purge_org_media_report(session: AsyncSession, org_id: UUID, media_type: MediaPurgeType) -> MediaPurgeResult:
    """Delete every participant medium of ``media_type`` of the org.

    For each distinct key of the type, the object goes through ``delete_file``
    (verified, tolerant of a missing object), with the unreferenced WebP
    variants beside a photo. Then every reference to the key, in any field of
    any submission, is cleared and ``media_removed`` set, but only when the
    object delete did not raise. Rows and scores are never touched.

    ``purged`` counts the objects that existed: a key whose object was already
    gone clears its references but is not counted. A key whose lookup failed
    is skipped, listed in ``unclassified`` and logged.

    A variant that a submission references directly is a target key of its
    own and is purged and counted only through it. Only an unreferenced
    variant goes with its original, uncounted.
    """
    refs_by_key = await _collect_org_media_refs(session, org_id)
    referenced_keys = set(refs_by_key)
    classifications, unclassified = await _classify_all(refs_by_key, org_id)
    for key, classified in classifications.items():
        if classified is None:
            logger.warning("media_purge_key_type_unknown", organization_id=str(org_id), object_key=key)
    if unclassified:
        logger.warning(
            "media_purge_keys_unclassified",
            organization_id=str(org_id),
            media_type=media_type,
            count=len(unclassified),
        )
    target_keys = [key for key, classified in classifications.items() if classified == media_type]

    purged = 0
    for key in target_keys:
        try:
            existed = await run_in_threadpool(delete_file, key)
        except (S3Error, RuntimeError) as exc:
            logger.warning(
                "media_purge_delete_failed",
                object_key=key,
                media_type=media_type,
                error=str(exc),
            )
            continue  # every reference to this key stays for a later retry

        # A failed variant delete only orphans a run-scoped object, which the
        # orphan sweep collects. Unlike a live referenced object, it does not
        # block the clear below.
        for variant_key in unreferenced_variant_keys(key, referenced_keys):
            try:
                await run_in_threadpool(delete_file, variant_key)
            except (S3Error, RuntimeError) as exc:
                logger.warning(
                    "media_purge_delete_failed",
                    object_key=variant_key,
                    media_type=media_type,
                    error=str(exc),
                )

        if existed:
            purged += 1

        for submission, field in refs_by_key[key]:
            # A new dict, so SQLAlchemy marks the JSONB column dirty. The
            # content is read fresh, so a submission with several purged keys
            # keeps every clear.
            submission.content = _cleared_content(submission.content or {}, field, key)

    # Autoflush is off in tests, and the caller commits once at the end.
    await session.flush()

    return MediaPurgeResult(purged=purged, unclassified=unclassified)
