"""
Copies of library stations and challenges.

The library stays shared: a rallye links library items, and an edit changes
them in every rallye that uses them. To build a rallye from adapted items, a
creator copies them. A copy is complete: every language of every text, the
location, the tips, the scoring rules and the reference answer. Its title gets
" (Kopie)" or " (Copy)" per language, like a duplicated rallye.

A station's title image gets its own object in the store. The original's key
must not be shared, because replacing or removing an image, and the permanent
delete, remove the object under that key.
"""

from __future__ import annotations

import copy
from uuid import UUID, uuid4

import structlog
from minio.commonconfig import CopySource
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.challenge import Challenge
from app.models.station import Station
from app.repositories.challenge import ChallengeRepository
from app.repositories.station import StationRepository
from app.storage import get_minio_client

logger = structlog.get_logger("app")

_SUFFIXES = {"de": " (Kopie)", "en": " (Copy)"}


def copy_title(title: dict[str, str]) -> dict[str, str]:
    """Each language's title with its copy suffix (English for unknown languages)."""
    return {locale: (value or "") + _SUFFIXES.get(locale, " (Copy)") for locale, value in (title or {}).items()}


def _copy_image(org_id: UUID, station_id: UUID, source_key: str) -> str | None:
    """Copy a title image to the new station's own key. None when the store refuses."""
    name = source_key.rsplit("/", 1)[-1].split("_", 1)[-1] or "image"
    target_key = f"{org_id}/stations/{station_id}/{str(uuid4())[:8]}_{name}"
    bucket = settings.storage.bucket
    try:
        get_minio_client().copy_object(bucket, target_key, CopySource(bucket, source_key))
    except Exception:
        # A copy without its image beats no copy. The creator sees the gap.
        logger.warning("station copy: title image copy failed", source_key=source_key, exc_info=True)
        return None
    return target_key


async def copy_station(db: AsyncSession, org_id: UUID, source: Station, user_id: UUID | None) -> Station:
    """A complete copy of a library station, flushed but not committed."""
    repo = StationRepository(db, organization_id=org_id)
    station = await repo.create(
        title=copy_title(source.title),
        description=copy.deepcopy(source.description or {}),
        address=source.address,
        location_lat=source.location_lat,
        location_lng=source.location_lng,
        tips=copy.deepcopy(source.tips) if source.tips is not None else None,
        created_by_user_id=user_id,
    )
    if source.title_image_key:
        key = _copy_image(org_id, station.id, source.title_image_key)
        if key:
            station.title_image_key = key
            await db.flush()
    return station


async def copy_challenge(db: AsyncSession, org_id: UUID, source: Challenge, user_id: UUID | None) -> Challenge:
    """A complete copy of a library challenge, flushed but not committed."""
    repo = ChallengeRepository(db, organization_id=org_id)
    return await repo.create(
        challenge_type=source.challenge_type,
        title=copy_title(source.title),
        instructions=copy.deepcopy(source.instructions or {}),
        scoring_rules=copy.deepcopy(source.scoring_rules or {}),
        time_estimate_minutes=source.time_estimate_minutes,
        suggested_solution=source.suggested_solution,
        created_by_user_id=user_id,
    )
