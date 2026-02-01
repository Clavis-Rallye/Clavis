"""
S3-compatible object-store client wrapper (the store is Garage).

Provides file storage operations with:
- MIME type validation against allowlist
- File size limits
- Presigned URL generation
- Deletion verification
- Bucket management
"""

import mimetypes
from datetime import timedelta
from typing import Any

import structlog
from minio import Minio
from minio.error import S3Error

from app.config import settings

logger = structlog.get_logger("app")

# MIME type allowlist for submission uploads.
#
# The media families are allowed wholesale rather than by subtype. The client
# declares the type, so a list of subtypes adds no security: any file can call
# itself audio/mpeg. It would only reject real devices: iPhones record voice
# memos as m4a, film video/quicktime and take heic photos, Signal exports aac,
# older Androids produce 3gpp/amr. Media challenges would be unusable on the
# phones they exist for.
#
# What guards these uploads: the size caps below, and the per-type MIME
# cross-check in the submission endpoint (an AUDIO answer must reference an
# audio/* object, a VIDEO answer a video/* one). Station images keep their own
# stricter list in routers/stations.py.
ALLOWED_MIME_FAMILIES = ("image/", "audio/", "video/")

# Non-media types stay enumerated: no family of them is worth allowing whole.
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/zip",
}


def is_allowed_mime_type(content_type: str) -> bool:
    """True if the declared type may be uploaded as a submission file."""
    return content_type.startswith(ALLOWED_MIME_FAMILIES) or content_type in ALLOWED_MIME_TYPES


# Maximum file size in bytes. Configurable via MAX_FILE_SIZE_MB (default 10 MB)
# and MAX_VIDEO_FILE_SIZE_MB (default 100 MB, applies to video/* MIME uploads
# only, see app/config.py:UploadSettings).
MAX_FILE_SIZE = settings.uploads.max_file_size_mb * 1024 * 1024
MAX_VIDEO_FILE_SIZE = settings.uploads.max_video_file_size_mb * 1024 * 1024

# Per-part cap for the multipart parser. Starlette 1.x defaults this to 1 MB,
# which would reject our uploads during form parsing, before the endpoint's
# own size check can return a clean 413. The upload routes (app/upload_route.py)
# raise it to the larger of MAX_FILE_SIZE and MAX_VIDEO_FILE_SIZE plus 1 MB of
# header/encoding slack. A part still over this cap surfaces as a 413 through
# the global MultiPartException handler in app/main.py.
MAX_MULTIPART_PART_SIZE = max(MAX_FILE_SIZE, MAX_VIDEO_FILE_SIZE) + 1024 * 1024  # +1 MB slack

# What a multipart request may hold besides its file: boundaries, part headers,
# the other form fields. The ceiling of a request on an upload route is the
# largest file that route takes plus this allowance (app/upload_route.py).
MULTIPART_FRAME_ALLOWANCE = 1024 * 1024


def max_upload_size(content_type: str | None) -> int:
    """The size cap in bytes of a submission file, by its declared type.

    ``video/*`` gets the raised video cap, everything else the base cap.
    The type is compared as the answer route normalizes it, lower case and
    without blanks around it, so the multipart parser, which stops the
    read of a part at this cap, and the route decide alike.
    """
    declared = (content_type or "").strip().lower()
    return MAX_VIDEO_FILE_SIZE if declared.startswith("video/") else MAX_FILE_SIZE


def get_minio_client() -> Minio:
    """Build the S3 client from the storage settings."""
    return Minio(
        endpoint=settings.storage.endpoint,
        access_key=settings.storage.access_key,
        secret_key=settings.storage.secret_key,
        secure=settings.storage.secure,
        # Without a region the client asks the store for the
        # bucket region before its first signed request. Garage answers with
        # its configured region, and a mismatch fails with
        # AuthorizationHeaderMalformed. A fixed region removes that round trip
        # and that failure.
        region=settings.storage.region,
    )


def ensure_bucket() -> None:
    """Create the configured bucket if it does not exist. Called at startup.

    Raises ``S3Error`` if the bucket cannot be checked or created.
    """
    client = get_minio_client()
    bucket_name = settings.storage.bucket

    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
            logger.info(
                "minio_bucket_created",
                bucket=bucket_name,
            )
        else:
            logger.info(
                "minio_bucket_exists",
                bucket=bucket_name,
            )
    except S3Error as e:
        logger.error(
            "minio_bucket_ensure_failed",
            bucket=bucket_name,
            error=str(e),
        )
        raise


def upload_file(
    file_data: bytes,
    filename: str,
    content_type: str | None = None,
    max_size: int = MAX_FILE_SIZE,
) -> str:
    """Upload a file and return its object key, ``uploads/YYYY-MM-DD/<filename>``.

    ``content_type`` is guessed from ``filename`` if None. Raises ``ValueError`` if
    the size is over ``max_size`` or the type is not allowed, ``S3Error`` if the upload fails.
    """
    file_size = len(file_data)
    if file_size > max_size:
        raise ValueError(f"File size {file_size} bytes exceeds maximum allowed size {max_size} bytes")

    if content_type is None:
        detected_type, _ = mimetypes.guess_type(filename)
        if detected_type is None:
            raise ValueError(
                f"Could not detect MIME type for file: {filename}. Please provide content_type explicitly."
            )
        content_type = detected_type

    if not is_allowed_mime_type(content_type):
        families = ", ".join(f"{f}*" for f in ALLOWED_MIME_FAMILIES)
        raise ValueError(
            f"MIME type '{content_type}' is not allowed. Allowed: {families}, {', '.join(sorted(ALLOWED_MIME_TYPES))}"
        )

    import io
    from datetime import datetime

    today = datetime.utcnow().strftime("%Y-%m-%d")
    object_key = f"uploads/{today}/{filename}"

    client = get_minio_client()
    bucket_name = settings.storage.bucket

    try:
        client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=io.BytesIO(file_data),
            length=file_size,
            content_type=content_type,
        )

        logger.info(
            "minio_file_uploaded",
            object_key=object_key,
            size=file_size,
            content_type=content_type,
        )

        return object_key

    except S3Error as e:
        logger.error(
            "minio_upload_failed",
            object_key=object_key,
            error=str(e),
        )
        raise


# Segment of a station title image key: {org_id}/stations/{station_id}/<file>.
STATION_IMAGE_SEGMENT = "stations"


def station_image_url(object_key: str) -> str:
    """The same-origin URL of a station title image.

    A presigned URL signs against S3_ENDPOINT, an internal host the browser
    cannot reach, and the CSP allows images only from 'self'. GET
    /api/files/{key} streams the object through the API for any member of the
    station's organization, as it does for answer media.
    """
    return f"/api/files/{object_key}"


def get_presigned_url(
    object_key: str,
    expiry: timedelta = timedelta(hours=1),
) -> str:
    """Return a presigned GET URL for ``object_key``, valid for ``expiry``."""
    client = get_minio_client()
    bucket_name = settings.storage.bucket

    try:
        url = client.presigned_get_object(
            bucket_name=bucket_name,
            object_name=object_key,
            expires=expiry,
        )

        logger.info(
            "minio_presigned_url_generated",
            object_key=object_key,
            expiry_seconds=expiry.total_seconds(),
        )

        return url

    except S3Error as e:
        logger.error(
            "minio_presigned_url_failed",
            object_key=object_key,
            error=str(e),
        )
        raise


def delete_file(object_key: str) -> bool:
    """Delete a file and verify that it is gone.

    Stats the object first to learn whether it existed, deletes it, then
    stats it again. If that second stat succeeds, the deletion failed and
    ``RuntimeError`` is raised. Deleting an already-missing object is
    not an error (S3 delete is idempotent). The pre-delete stat lets a caller
    (e.g. the org-deletion sweep) count only objects that actually existed
    instead of every key it merely attempted.

    Returns True if the object existed and was removed, False if it was
    already missing. Raises ``S3Error`` if the deletion fails.
    """
    client = get_minio_client()
    bucket_name = settings.storage.bucket

    existed = True
    try:
        client.stat_object(bucket_name=bucket_name, object_name=object_key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            existed = False
        else:
            raise

    try:
        client.remove_object(
            bucket_name=bucket_name,
            object_name=object_key,
        )

        # A successful stat means the object still exists. RuntimeError, because
        # the S3Error constructor needs response parameters.
        try:
            client.stat_object(bucket_name=bucket_name, object_name=object_key)
            raise RuntimeError(
                f"Object {object_key} still exists after deletion attempt (code: DeletionVerificationFailed)"
            )
        except S3Error as e:
            # NoSuchKey means the object is gone.
            if e.code == "NoSuchKey":
                logger.info(
                    "minio_file_deleted",
                    object_key=object_key,
                    existed=existed,
                )
            else:
                raise

    except S3Error as e:
        if e.code != "NoSuchKey":
            logger.error(
                "minio_delete_failed",
                object_key=object_key,
                error=str(e),
            )
            raise

    return existed


def list_all_files(prefix: str = "") -> list[dict[str, Any]]:
    """List every object under a prefix, with no result cap, unlike list_files.

    list_files stops at ``max_results`` (default 1000) because its callers
    shape API responses; a retention sweep must see the whole bucket or it
    silently skips everything past the first page. The client library's lazy
    listing already pages through continuation tokens, so exhausting the
    iterator is all it takes. The dicts have the shape of ``list_files``.
    """
    client = get_minio_client()
    bucket_name = settings.storage.bucket

    try:
        objects = client.list_objects(
            bucket_name=bucket_name,
            prefix=prefix,
            recursive=True,
        )

        results = [
            {
                "key": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified,
                "content_type": obj.content_type,
            }
            for obj in objects
        ]

        logger.info(
            "minio_files_listed",
            prefix=prefix,
            count=len(results),
        )

        return results

    except S3Error as e:
        logger.error(
            "minio_list_failed",
            prefix=prefix,
            error=str(e),
        )
        raise


def list_files(prefix: str = "", max_results: int = 1000) -> list[dict[str, Any]]:
    """List files under ``prefix``, at most ``max_results``.

    Each dict has the keys ``key``, ``size``, ``last_modified`` and ``content_type``.
    """
    client = get_minio_client()
    bucket_name = settings.storage.bucket

    try:
        objects = client.list_objects(
            bucket_name=bucket_name,
            prefix=prefix,
            recursive=True,
        )

        results = []
        for obj in objects:
            results.append(
                {
                    "key": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified,
                    "content_type": obj.content_type,
                }
            )

            if len(results) >= max_results:
                break

        logger.info(
            "minio_files_listed",
            prefix=prefix,
            count=len(results),
        )

        return results

    except S3Error as e:
        logger.error(
            "minio_list_failed",
            prefix=prefix,
            error=str(e),
        )
        raise
