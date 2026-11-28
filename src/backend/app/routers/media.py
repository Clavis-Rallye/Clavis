"""
Organisation media: images that creators place inside rich text.

Two routes, both scoped to an organisation:

- ``POST /api/orgs/{slug}/media/`` uploads an image (creator and above). The object
  lands under ``{org_id}/media/{uuid}_{filename}`` and the response carries the
  application route the editor puts into the document.
- ``GET /api/orgs/{slug}/media/{key}`` streams it back to any authenticated member of
  the organisation, guests included, because participants read the texts in a run.
  An image that one of the organisation's legal texts shows (imprint, privacy,
  accessibility) streams to anyone, signed in or not, because those pages are public.

The browser never talks to the S3 store. The host is internal, which is why
submission files stream through the API as well (``files.serve_file``). The
same pattern, one prefix further: the ``media`` segment lets the retention
sweeper and the purge tell content media from participant media.
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, File, Header, HTTPException, Request, Response, UploadFile, status
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from uuid_utils import uuid7

from app.auth.dependencies import get_current_user, get_org_from_slug
from app.auth.rbac import require_role
from app.auth.tokens import INSTANCE_ADMIN_COOKIE_NAME
from app.client_address import client_address
from app.config import settings
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.repositories.organization import OrganizationRepository
from app.routers.files import release_stream
from app.services.audit import actor_from_user, record_audit_event
from app.services.file_response import file_response_headers
from app.services.image_variants import bare_content_type, clean_image_upload, image_not_decodable
from app.services.richtext import image_object_keys, media_prefix_for
from app.storage import MAX_FILE_SIZE, get_minio_client
from app.upload_route import ImageUploadRoute

# Images only: the route class holds a part and the request to the base cap and
# checks the session of an upload before its body is parsed. The GET below takes
# no form, so it keeps its own rule: a legal-text image needs no session.
router = APIRouter(route_class=ImageUploadRoute)

IMAGE_MIME_TYPES: set[str] = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MEDIA_SEGMENT = "media"

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str | None) -> str:
    base = (name or "bild").rsplit("/", 1)[-1]
    cleaned = _SAFE_FILENAME.sub("-", base).strip("-.")
    return cleaned[:80] or "bild"


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Image stored, response carries the route to reference it"},
        413: {
            "description": "Image exceeds the base upload cap, or its pixel dimensions the decode cap (IMAGE_TOO_LARGE)"
        },
        415: {
            "description": (
                "Not an accepted image type, an empty file, or bytes the image library cannot decode "
                "(error_code IMAGE_NOT_DECODABLE)"
            )
        },
    },
)
async def upload_org_media(
    request: Request,
    file: UploadFile = File(..., description="Image (png, jpeg, webp, gif) up to the base upload cap"),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> dict[str, str]:
    """Upload an image that an organisation places inside rich text.

    Open to creators and organization admins. The route accepts PNG, JPEG, WebP
    and GIF. It stores the image re-encoded and without metadata, the same cleaning
    as for the photo of a submission. The response holds the storage `key` and the
    `url` of the route that serves the image. A file of another type, an empty
    file and bytes that cannot be decoded as an image answer 415
    `IMAGE_NOT_DECODABLE`, and nothing is stored. A file above the upload limit
    answers 413. The route emits the audit event `media.uploaded`.
    """
    # A parameter on the claimed type ("image/png; charset=binary") does not
    # make it another type, as on the answer route. The list itself is
    # not widened: the object is stored under the bare type from the list.
    content_type = bare_content_type(file.content_type)
    if content_type not in IMAGE_MIME_TYPES:
        raise image_not_decodable(
            f"Image type {content_type or 'unknown'} is not accepted. Allowed: {', '.join(sorted(IMAGE_MIME_TYPES))}"
        )
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Image size {len(data)} bytes exceeds the limit of {MAX_FILE_SIZE} bytes",
        )
    # An empty file is refused by the cleaning, as on the answer route.
    data, _thumb, _display = await clean_image_upload(data)

    object_key = f"{org.id}/{MEDIA_SEGMENT}/{uuid7()}_{_safe_filename(file.filename)}"
    client = get_minio_client()
    try:
        from io import BytesIO

        await run_in_threadpool(
            client.put_object,
            bucket_name=settings.storage.bucket,
            object_name=object_key,
            data=BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
    except S3Error as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store image: {e!s}",
        ) from None

    record_audit_event(
        event="media.uploaded",
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "media", "object_key": object_key, "size": len(data), "content_type": content_type},
        organization_id=org.id,
    )
    return {"key": object_key, "url": f"{media_prefix_for(org.slug)}{object_key}"}


async def _member_or_refusal(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
    instance_token: str | None = Cookie(default=None, alias=INSTANCE_ADMIN_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> User | HTTPException:
    """The signed-in user, or the refusal ``get_current_user`` raised.

    The route decides with it: a legal-text image needs no session, every
    other image answers with exactly this refusal.
    """
    try:
        return await get_current_user(request, response, db, access_token, instance_token, authorization)
    except HTTPException as refusal:
        return refusal


def _shown_in_legal_text(org: Organization, key: str) -> bool:
    """True when the imprint, privacy or accessibility text of ``org`` shows this image.

    The decision opens an object to anyone without sign-in, so it reads the ``src`` of real ``img``
    elements only and compares object keys in one form: the key of the request, which the server has
    percent-decoded, with the percent-decoded key of each image source under the media route of this
    organisation. Text that only looks like a source, for example inside another attribute, opens nothing.
    """
    prefix = media_prefix_for(org.slug)
    return any(
        key in image_object_keys(text, prefix)
        for text in (org.legal_imprint, org.legal_privacy, org.legal_accessibility)
    )


@router.get(
    "/{key:path}",
    responses={
        200: {"description": "The image"},
        401: {"description": "Not authenticated, and the image is in no legal text"},
        403: {"description": "Image belongs to another organisation"},
        404: {"description": "Unknown key"},
    },
)
async def serve_org_media(
    key: str,
    slug: str,
    auth: User | HTTPException = Depends(_member_or_refusal),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream an image of an organisation.

    Every signed-in member of the organisation may read it, guests included. An
    image that the imprint, the privacy text or the accessibility text of the
    organisation shows is open to anyone without sign-in, because those pages are
    public. An image in a station or a rallye text needs the session of a member.
    An archived organisation shows the legal texts of the instance instead, so
    its images need a session again. An image of another organisation answers 403,
    an unknown key 404, and a missing session 401.
    """
    # The checks of require_role("guest") and get_org_from_slug, in their
    # order, for every image that no legal text shows.
    org = await OrganizationRepository(db).get_by_slug(slug)
    if org is None:
        if isinstance(auth, HTTPException):
            raise auth
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    if org.archived_at is not None or not _shown_in_legal_text(org, key):
        if isinstance(auth, HTTPException):
            raise auth
        if auth.organization_id != org.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    parts = key.split("/")
    if len(parts) != 3 or parts[1] != MEDIA_SEGMENT or ".." in key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid object key format")
    try:
        key_org_id = UUID(parts[0])
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid object key format") from None
    if key_org_id != org.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: image belongs to a different organization",
        )

    client = get_minio_client()
    bucket_name = settings.storage.bucket
    try:
        stat = await run_in_threadpool(client.stat_object, bucket_name=bucket_name, object_name=key)
        obj = await run_in_threadpool(client.get_object, bucket_name=bucket_name, object_name=key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found") from None
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve image: {e!s}",
        ) from None

    # The request session (the same one the org and role checks used) lives
    # until the last byte is sent. End its read transaction now, so the
    # stream holds no pooled connection.
    await db.commit()

    filename = key.rsplit("/", 1)[-1].split("_", 1)[-1]
    return StreamingResponse(
        obj.stream(64 * 1024),
        media_type=stat.content_type or "application/octet-stream",
        headers={
            # The protection of the file endpoint: nosniff, the no-script policy, and an
            # attachment for every stored type that is no raster image. The general
            # policy of app/main.py allows inline script, so without these headers an
            # object stored as image/svg+xml would run script on direct navigation.
            **file_response_headers(stat.content_type, filename=filename),
            "Cache-Control": "private, max-age=3600",
            "Content-Length": str(stat.size),
        },
        background=BackgroundTask(release_stream, obj),
    )
