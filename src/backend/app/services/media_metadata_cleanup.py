"""Removal of the metadata of images stored before uploads were cleaned.

Every image upload is stored re-encoded without metadata
(``image_variants.clean_image_upload``). An image stored by an earlier release
keeps its EXIF block, with the GPS position and the device. The serve routes
stream it as it is, and a duplicated station copies it as it is.

``clean_stored_image_metadata`` sends every stored image through the cleaning
of a new upload:

- photo answers, ``{org_id}/{run_id}/{team_id}/…`` (``routers/files.py``),
  and the WebP variants beside them, each an object of its own,
- rich-text images, ``{org_id}/media/…`` (``routers/media.py``),
- station title images, ``{org_id}/stations/{station_id}/…``
  (``routers/stations.py``).

An object whose cleaned form carries less metadata is written back under the
same key with the same content type, so every reference stays valid. No
object is created or deleted.

Only the operator command ``python -m app.cli image-metadata clean`` may call
it: not the server start, not a migration, not a sweep. The stored content
survives an update untouched, and the operator decides about this step.
Without ``apply`` it is a dry run that writes nothing.

Metadata is read from the object, never inferred from its age. It is what the
shared cleaning removes: the marks of the stored bytes against the marks of
their cleaned form. An object that loses nothing is not rewritten, so a second
pass changes nothing. An object the cleaning cannot decode is never touched,
and is named and counted apart. HEIC photos are among these, because the image
library cannot read them here. Every image is decoded, also in a dry run,
because only the decode shows whether the cleaning can handle it.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field

from minio.error import S3Error
from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.services.content_media_retention import ORG_MEDIA_SEGMENT
from app.services.image_variants import (
    DECODE_FAILURES,
    ImageNotDecodableError,
    ImageTooLargeError,
    clean_and_variant,
)
from app.services.retention_tasks import run_id_from_object_key, variant_base_key
from app.storage import STATION_IMAGE_SEGMENT, get_minio_client, list_all_files

# Blocks beside the pixels that the image library reports under a fixed name.
_INFO_MARKS = ("exif", "icc_profile", "xmp", "comment", "photoshop")
_GPS_TAG = 0x8825

# What tells that the shared cleaning, or the reading of the marks, cannot handle an object.
_CANNOT_DECODE: tuple[type[Exception], ...] = (
    ImageNotDecodableError,
    ImageTooLargeError,
    UnidentifiedImageError,
    *DECODE_FAILURES,
)


@dataclass
class MetadataCleanupResult:
    """What one pass found. ``changed`` holds the keys a dry run would change."""

    applied: bool
    changed: list[str] = field(default_factory=list)
    undecodable: list[str] = field(default_factory=list)
    unchanged: int = 0


def _kind_of(key: str) -> str | None:
    """Which of the three kinds of stored image a key belongs to, by its shape. None for any other key."""
    segments = key.split("/")
    if len(segments) == 3 and segments[1] == ORG_MEDIA_SEGMENT:
        return "rich-text image"
    if len(segments) == 4 and segments[1] == STATION_IMAGE_SEGMENT:
        return "station title image"
    if run_id_from_object_key(key) is not None:
        return "photo answer variant" if variant_base_key(key) is not None else "photo answer"
    return None


def _marks(data: bytes) -> set[str]:
    """What an image carries beside its pixels.

    EXIF tags are listed one by one, because a TIFF keeps its structure in the
    same tag directory: only the comparison with the cleaned form tells a
    device name from the image width.
    """
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "JPEG":
            # A PNG may carry its text and EXIF chunks behind the pixels.
            image.load()
        marks = {name for name in _INFO_MARKS if image.info.get(name)}
        exif = image.getexif()
        marks.update(f"exif:{tag}" for tag in exif)
        if _GPS_TAG in exif:
            marks.add("gps")
        marks.update(f"text:{name}" for name in getattr(image, "text", None) or {})
    return marks


def _read(key: str) -> tuple[bytes, str] | None:
    """The bytes and the stored type of an image object. None for any other type or a vanished object."""
    client = get_minio_client()
    bucket = settings.storage.bucket
    try:
        content_type = client.stat_object(bucket, key).content_type or ""
        if not content_type.startswith("image/"):
            return None
        response = client.get_object(bucket, key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            # The server runs beside the command and may delete an object after the listing.
            return None
        raise
    try:
        return response.read(), content_type
    finally:
        response.close()
        response.release_conn()


def clean_stored_image_metadata(*, apply: bool = False, emit: Callable[[str], None] = print) -> MetadataCleanupResult:
    """Remove the metadata of the stored images, or without ``apply`` list what would change.

    ``emit`` gets one line per object that is changed, would be changed, or
    cannot be decoded. Raises ``S3Error`` when the store fails: the objects
    written before stay cleaned, and the pass can be repeated.
    """
    result = MetadataCleanupResult(applied=apply)
    for key in sorted(obj["key"] for obj in list_all_files()):
        kind = _kind_of(key)
        if kind is None:
            continue
        found = _read(key)
        if found is None:
            continue
        data, content_type = found
        try:
            cleaned, thumb, _display = clean_and_variant(data)
            if thumb is None:
                raise ImageNotDecodableError("the image library does not know this format")
            removed = _marks(data) - _marks(cleaned)
        except _CANNOT_DECODE as failure:
            result.undecodable.append(key)
            emit(f"not decodable, left as it is: {kind} {key} ({failure})")
            continue
        if not removed:
            result.unchanged += 1
            continue
        if apply:
            get_minio_client().put_object(
                bucket_name=settings.storage.bucket,
                object_name=key,
                data=io.BytesIO(cleaned),
                length=len(cleaned),
                content_type=content_type,
            )
        result.changed.append(key)
        names = ", ".join(sorted({mark.split(":", 1)[0] for mark in removed}))
        emit(f"{'cleaned' if apply else 'would clean'}: {kind} {key} ({names})")
    return result
