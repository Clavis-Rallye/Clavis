"""
File upload and serving endpoints.

Provides file storage operations with:
- Upload endpoint for submission media (photos, audio, video, documents)
- Serve endpoint that streams file content through the API
- MIME type and size validation via storage module
- Organization-scoped object keys for multi-tenant isolation
- Presigned URL generation for secure, time-limited access

All endpoints require authentication and enforce role-based access control:
- Upload: participant+ role, must be a member of a team in the run
- Serve: guest+ role, must be a member of that team in that run (coordinator+ bypasses team check)
"""

import io
import uuid
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from minio.datatypes import Object
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from urllib3.response import BaseHTTPResponse

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import RoleLevel, require_role
from app.client_address import client_address
from app.config import settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.organization import Organization
from app.models.run import RunState
from app.models.user import User
from app.repositories.run import RunRepository
from app.repositories.team_member import TeamMemberRepository
from app.routers.schemas import ErrorResponse, FileUploadResponse
from app.services.audit import actor_from_user, record_audit_event
from app.services.file_response import file_response_headers
from app.services.image_variants import bare_content_type, clean_image_upload
from app.services.media_metadata import UnsupportedMediaError, strip_media_metadata
from app.services.retention_tasks import PHOTO_VARIANT_SUFFIXES
from app.storage import (
    ALLOWED_MIME_FAMILIES,
    ALLOWED_MIME_TYPES,
    STATION_IMAGE_SEGMENT,
    get_minio_client,
    get_presigned_url,
    is_allowed_mime_type,
    max_upload_size,
)
from app.upload_route import LargeUploadRoute

# The route class checks the session before the body is parsed and stops the
# read of a part at the size cap of its declared type: the video cap for
# video/*, the base cap for everything else.
router = APIRouter(route_class=LargeUploadRoute)

# Photo containers Pillow cannot decode in this deployment (no HEIC plugin is
# installed). Phones hand these over as ordinary inert photos, so they pass
# through: stored as uploaded, without variants. Every other image/*
# claim that does not decode (an SVG with an onload handler above all) is
# refused, because it would later be served under its claimed image type.
PASS_THROUGH_IMAGE_TYPES = frozenset({"image/heic", "image/heif"})


@router.post(
    "/",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "File uploaded successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Run not found or user not in a team", "model": ErrorResponse},
        413: {
            "description": (
                "File size exceeds limit (base cap, or the raised video/* cap), or an "
                "image's pixel dimensions exceed the decode cap (error_code IMAGE_TOO_LARGE)"
            ),
            "model": ErrorResponse,
        },
        415: {
            "description": ("An image/* upload the image library cannot decode (error_code IMAGE_NOT_DECODABLE)"),
            "model": ErrorResponse,
        },
        422: {
            "description": (
                "Run not active, invalid file type, or a video or audio file whose container "
                "cannot be read and cleaned (error_code MEDIA_NOT_READABLE)"
            ),
            "model": ErrorResponse,
        },
    },
)
async def upload_submission_file(
    run_id: UUID,
    request: Request,
    file: UploadFile = File(..., description="File to upload (video/* uploads get a raised size cap)"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("guest")),
) -> FileUploadResponse:
    """Upload a file for a submission and return a presigned URL for it.

    Open to every signed-in role, guests included, but only to a member of a team
    in the run. A caller without a team in the run gets 404 with the error code
    `NOT_TEAM_MEMBER`. The run must be active. In any other state the route answers
    422.

    The route accepts images, audio, video and documents. Any other type gets 422.
    A file above the size limit of its type gets 413. Video files have a higher
    limit than the other types.

    The file is stored under the object key
    `{org_id}/{run_id}/{team_id}/{unique_id}_{filename}`. The `filename` keeps only
    letters, digits, `.`, `_` and `-` of the uploaded name. A name that ends in
    `.thumb.webp` or `.display.webp` gets a dash instead of the dot, so that an
    upload is never mistaken for a generated variant.

    Images are stored re-encoded and without metadata. The EXIF orientation is
    applied and the GPS position is dropped. The route also stores a `thumb` variant
    (400 px) and a `display` variant (1600 px) as WebP under
    `<object_key>.thumb.webp` and `<object_key>.display.webp`.

    - An image that the image library cannot decode gets 415 with
      `IMAGE_NOT_DECODABLE`, and nothing is stored. An empty image upload gets the
      same answer.
    - HEIC and HEIF photos are the exception. The library cannot decode them, so
      the route stores them as uploaded, without variants. An empty one is still
      refused.
    - An image whose pixel dimensions exceed the decode limit gets 413 with
      `IMAGE_TOO_LARGE`, and nothing is stored.

    Video and audio files are stored without container metadata. The place and time
    of the recording, the device, the software and every track that is neither
    picture nor sound are removed. Picture and sound are not re-encoded. A file whose
    container cannot be read gets 422 with `MEDIA_NOT_READABLE`, and nothing is
    stored.

    The URL in the response is valid for one hour. A failure of the object storage
    gives 500.

    Emits the audit event `file.uploaded`.
    """
    client_ip = client_address(request)
    run_repo = RunRepository(db, organization_id=org.id)

    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    if run.state != RunState.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot upload files when run is in {run.state} state. Uploads are only allowed in active runs.",
        )

    team_member_repo = TeamMemberRepository(db, organization_id=org.id)
    team_membership = await team_member_repo.get_by_user_and_run(
        user_id=current_user.id,
        run_id=run_id,
    )

    if team_membership is None:
        raise CodedHTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of any team in this run",
            # The app sends a removed member out of the run with a notice.
            error_code="NOT_TEAM_MEMBER",
        )

    team_id = team_membership.team_id

    # The bytes as read, or the working copy of the video and audio cleaner.
    file_data: bytes | bytearray = await file.read()
    file_size = len(file_data)

    original_filename = file.filename or "unnamed"
    # Keep only safe characters.
    safe_filename = "".join(c for c in original_filename if c.isalnum() or c in "._-")
    if not safe_filename:
        safe_filename = "file"

    # An uploaded original must never end in a generated-variant suffix
    # (`.thumb.webp` / `.display.webp`). The retention sweep
    # (sweep_orphaned_submission_media, app/services/retention_tasks.py) and
    # the submission-validation guard (verify_media_key_not_variant,
    # app/routers/submissions.py) both key off that suffix to decide whether
    # a stored object is a photo's own upload or a variant derived from it,
    # and an original that carries the suffix breaks that assumption. The dash
    # swap removes the ambiguity at the source: every key that still ends in
    # the suffix is either a generated variant or a legacy object from before
    # the swap.
    for suffix in PHOTO_VARIANT_SUFFIXES:
        if safe_filename.endswith(suffix):
            safe_filename = safe_filename[: -len(suffix)] + "-" + suffix[1:]
            break

    unique_id = str(uuid.uuid4())[:8]
    object_key = f"{org.id}/{run_id}/{team_id}/{unique_id}_{safe_filename}"

    # The declared type, lower case and without blanks around it, is taken once
    # here, before the size cap by kind, the allowlist, the choice of the
    # handling and the storage. A type is the same type in any case, and every
    # check below compares lower case, so "IMAGE/PNG" must not be refused by the
    # allowlist while the two content routes clean it. The stored type and the
    # type in the answer are this normalized one. A parameter
    # ("image/png; charset=binary") stays, lower case too. Where it must not
    # count, bare_content_type drops it.
    #
    # An untyped upload fails the allowlist below.
    content_type = (file.content_type or "").strip().lower() or "application/octet-stream"

    # video/* uploads get the raised video cap, and everything else is held to
    # the base cap. The route class enforces the same cap while the part is
    # read, so a request that came through it is never above the cap here. The
    # check stays as the second line, on the bytes the route holds.
    applicable_max = max_upload_size(content_type)
    if file_size > applicable_max:
        applicable_max_mb = applicable_max // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"File size {file_size} bytes exceeds maximum allowed size "
                f"{applicable_max} bytes ({applicable_max_mb}MB)"
            ),
        )

    if not is_allowed_mime_type(content_type):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"MIME type '{content_type}' is not allowed. Allowed: "
                f"{', '.join(f'{f}*' for f in ALLOWED_MIME_FAMILIES)}, "
                f"{', '.join(sorted(ALLOWED_MIME_TYPES))}"
            ),
        )

    # Each refusal of this route is raised where its check stands: the two
    # above, and below the classes the two cleaners name a bad upload with.
    # No catch stands around the cleaning. A catch of every ValueError would
    # answer a mistake of the server's own code as if the upload were at
    # fault. Such a mistake passes through and shows as a server error, as on
    # the two content routes.

    # An image/* upload is stored re-encoded, with EXIF orientation applied and
    # every metadata block (GPS above all) dropped, and with a 400 px and a
    # 1600 px WebP variant next to it. Bytes that do not decode stay byte for
    # byte and get no variants. The re-encode is CPU-bound, so it runs off the
    # event loop.
    thumb_data: bytes | None = None
    display_data: bytes | None = None
    if content_type.startswith("image/"):
        # The shared cleaning of the three image upload routes. It
        # refuses what the library cannot decode with 415 IMAGE_NOT_DECODABLE
        # and an image above the decode cap with 413 IMAGE_TOO_LARGE, each by
        # its own exception class, and nothing is stored. Only the HEIC and
        # HEIF containers, which the library cannot identify at all, are kept
        # as uploaded. A parameter on the claimed type
        # ("image/heic; charset=binary") does not make it another type.
        # An empty body is refused under these two claims
        # as well, inside the shared cleaning. What is kept is therefore
        # never empty, and it is stored under a HEIC or HEIF type, which
        # file_response_headers serves as an attachment.
        file_data, thumb_data, display_data = await clean_image_upload(
            bytes(file_data),
            keep_unidentified=bare_content_type(content_type) in PASS_THROUGH_IMAGE_TYPES,
        )
        file_size = len(file_data)
    elif content_type.startswith(("video/", "audio/")):
        # Video and audio hygiene: the container metadata goes (place of
        # recording, device, software, time), the picture and the sound
        # stay byte for byte. A container the cleaner cannot read is
        # refused, so no recording is stored unchecked.
        # The name is bound to the cleaned copy, so the upload itself is freed here.
        try:
            file_data = await run_in_threadpool(strip_media_metadata, bytes(file_data))
        except UnsupportedMediaError as e:
            # Nothing is stored. The app tells the team to record again.
            raise CodedHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"The video or audio file cannot be read: {e}",
                error_code="MEDIA_NOT_READABLE",
            ) from None
        file_size = len(file_data)

    try:
        client = get_minio_client()
        bucket_name = settings.storage.bucket

        client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=io.BytesIO(file_data),
            length=file_size,
            content_type=content_type,
        )
        if thumb_data is not None and display_data is not None:
            for suffix, variant in ((".thumb.webp", thumb_data), (".display.webp", display_data)):
                client.put_object(
                    bucket_name=bucket_name,
                    object_name=f"{object_key}{suffix}",
                    data=io.BytesIO(variant),
                    length=len(variant),
                    content_type="image/webp",
                )

        url = get_presigned_url(object_key, expiry=timedelta(hours=1))
    except Exception as e:
        # Storage errors (S3Error, network issues and so on). A ValueError of
        # the storage client is one of them: it names an argument this route
        # built, not the upload.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {e!s}",
        ) from None

    record_audit_event(
        event="file.uploaded",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "file",
            "object_key": object_key,
            "size": file_size,
            "content_type": content_type,
            "run_id": str(run_id),
            "team_id": str(team_id),
        },
        organization_id=org.id,
    )

    return FileUploadResponse(
        object_key=object_key,
        url=url,
        content_type=content_type,
        size=file_size,
    )


# Separate router for file serving (registered at /api/files)
serve_router = APIRouter()


def release_stream(obj: BaseHTTPResponse) -> None:
    """Close a storage object and hand its connection back, after the last byte is sent."""
    obj.close()
    obj.release_conn()


async def _stat_and_get(object_name: str) -> tuple[Object, BaseHTTPResponse] | None:
    """Stat and fetch an object. Return ``None`` when it does not exist (NoSuchKey)."""
    client = get_minio_client()
    try:
        stat = await run_in_threadpool(client.stat_object, bucket_name=settings.storage.bucket, object_name=object_name)
        obj = await run_in_threadpool(client.get_object, bucket_name=settings.storage.bucket, object_name=object_name)
    except S3Error as e:
        if e.code == "NoSuchKey":
            return None
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve file: {e!s}",
        ) from None
    return stat, obj


@serve_router.get(
    "/{key:path}",
    responses={
        200: {"description": "File content, streamed from object storage"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Caller is not entitled to view this file", "model": ErrorResponse},
        404: {"description": "File not found", "model": ErrorResponse},
    },
)
async def serve_file(
    key: str,
    request: Request,
    variant: Literal["thumb", "display"] | None = Query(
        None,
        description=(
            "Serve the photo's WebP variant instead of the original "
            "(400 px thumb / 1600 px display); falls back to the original "
            "when the variant object does not exist"
        ),
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("guest")),
) -> StreamingResponse:
    """Stream a stored file through the API.

    Open to every signed-in role, guests included, within these limits:

    - The first segment of the object key must be the caller's organization. Another
      organization gives 403.
    - A key of the form `{org_id}/{run_id}/{team_id}/...` is the media of a team.
      Coordinators and above can read every such file of their organization. Guests
      and participants can read only the files of their own team in that run, because
      submission media often shows identifiable people or voices. Another team gives
      403.
    - A key of the form `{org_id}/stations/{station_id}/...` is the title image of a
      station. Every signed-in member of the organization can read it, because a
      station's cover is content and not a participant's media.
    - A key of any other shape gives 404, and so does a file that does not exist.

    Pass `variant` to get the WebP variant stored beside a photo, `<key>.thumb.webp`
    or `<key>.display.webp`, instead of the original. When that variant does not
    exist, for example because the upload could not be decoded, the route serves
    the original. The checks are the same, because a variant shares the key of its
    photo.

    The route streams the file through the API, because the object storage cannot be
    reached from the browser. Every answer carries `X-Content-Type-Options: nosniff`
    and a content security policy that allows no script. A stored type other than a
    raster image, audio, video or PDF is served as an attachment, so no stored file
    runs script in the origin of the API. The browser may keep a station title image
    for one day, because every upload gets a new key. It may keep other files for five
    minutes.

    Emits the audit event `file.served` for every file except a station title image.
    """
    client_ip = client_address(request)

    # Object key shape: {org_id}/{run_id}/{team_id}/{uuid}_{filename}, or for a
    # station title image {org_id}/stations/{station_id}/{uuid}_{filename}.
    key_parts = key.split("/")
    if len(key_parts) < 4:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid object key format",
        )

    is_station_image = len(key_parts) == 4 and key_parts[1] == STATION_IMAGE_SEGMENT
    key_run_id: UUID | None = None
    key_team_id: UUID | None = None
    try:
        key_org_id = UUID(key_parts[0])
        if not is_station_image:
            key_run_id = UUID(key_parts[1])
            key_team_id = UUID(key_parts[2])
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid object key format",
        ) from None

    # Tenant boundary: the key must belong to the organization of the caller.
    if key_org_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: file belongs to a different organization",
        )

    # Coordinator+ has run-wide visibility. Everyone below must be a member of
    # the target team in this run. A station title image needs only the
    # organization check above.
    user_level = RoleLevel[current_user.role.value.upper()]
    if not is_station_image and user_level < RoleLevel.COORDINATOR:
        # Set above for every key that is not a station image.
        assert key_run_id is not None and key_team_id is not None
        team_member_repo = TeamMemberRepository(db, organization_id=key_org_id)
        membership = await team_member_repo.get_by_user_and_run(
            user_id=current_user.id,
            run_id=key_run_id,
        )
        if membership is None or membership.team_id != key_team_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: you can only view files from your own team",
            )

    # The S3 store is reachable only on the internal network. A presigned-URL
    # redirect hands the browser a host it cannot resolve, so the API proxies
    # the bytes itself. With ?variant=, the WebP object beside the photo is
    # tried first and a missing variant falls back to the original.
    candidate_keys = [f"{key}.{variant}.webp", key] if variant is not None else [key]
    fetched: tuple[Object, BaseHTTPResponse] | None = None
    served_key = key
    for candidate_key in candidate_keys:
        fetched = await _stat_and_get(candidate_key)
        if fetched is not None:
            served_key = candidate_key
            break
    if fetched is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    stat, obj = fetched

    # DSGVO Art. 32: log access to sensitive participant media. A station
    # title image is content and carries no personal data.
    if not is_station_image:
        record_audit_event(
            event="file.served",
            actor=actor_from_user(current_user, client_ip),
            target={
                "entity": "file",
                "object_key": served_key,
                "run_id": str(key_run_id),
                "team_id": str(key_team_id),
            },
            organization_id=key_org_id,
        )

    # The request session lives until the last byte is sent, and a phone can
    # take seconds for a photo or video. End its read transaction now, so the
    # stream holds no pooled connection.
    await db.commit()

    filename = served_key.rsplit("/", 1)[-1].split("_", 1)[-1]
    return StreamingResponse(
        obj.stream(64 * 1024),
        media_type=stat.content_type or "application/octet-stream",
        headers={
            # Serving hardening: nosniff, the no-script policy, and inline or
            # attachment by the stored type, not by the key's filename. SVG
            # objects stored before the upload refused them are caught here too.
            **file_response_headers(stat.content_type, filename=filename),
            "Content-Length": str(stat.size),
            # Authorized media, private caching only. A station image key is
            # unique per upload (stations.py, set_station_title_image), so it
            # may stay a day. Answer media keep five minutes.
            "Cache-Control": "private, max-age=86400" if is_station_image else "private, max-age=300",
        },
        background=BackgroundTask(release_stream, obj),
    )
