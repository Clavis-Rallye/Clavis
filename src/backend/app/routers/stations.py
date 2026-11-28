"""
Library Station endpoints (location-only).

Stations live in the organization-wide library (not under a single rallye). This
router serves the library CRUD at /api/orgs/{slug}/stations:

- GET    /                list library stations (paginated, each with usage)
- POST   /                create a standalone library station (creator+)
- GET    /{station_id}    station detail incl. usage
- PUT    /{station_id}    update — propagates to every rallye linking it (creator+)
- DELETE /{station_id}    soft-delete + cascade-unlink all links (creator+)

Challenges are matched to a station per rallye now; that composition lives on the
rallyes router. All endpoints are organization-scoped and enforce RBAC; modifications
emit audit events.
"""

import io
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.client_address import client_address
from app.config import settings
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.repositories.rallye_station import RallyeStationRepository
from app.repositories.station import StationRepository
from app.routers.schemas import (
    ErrorResponse,
    SortOrder,
    StationCreateRequest,
    StationListResponse,
    StationResponse,
    StationSortKey,
    StationUpdateRequest,
    StationUsage,
)
from app.services.audit import actor_from_user, record_audit_event
from app.services.content_copy import copy_station
from app.services.image_variants import bare_content_type, clean_image_upload, image_not_decodable
from app.services.richtext import media_prefix_for, sanitize_multilingual, sanitize_station_tips
from app.storage import MAX_FILE_SIZE, delete_file, get_minio_client, station_image_url
from app.upload_route import ImageUploadRoute

logger = logging.getLogger(__name__)

# The title-image route takes images only: the route class holds a part and the
# request to the base cap and checks the session before the body is parsed.
# The JSON routes of this router get the same ceiling of the request.
router = APIRouter(route_class=ImageUploadRoute)


def _clean_tips(tips: dict[str, Any] | None, org_slug: str) -> dict[str, Any] | None:
    """Sanitise the rich-text header of a tips block, leave the rest untouched."""
    return sanitize_station_tips(tips, org_slug)


# Station title images are images only (a subset of the broader upload
# allowlist) — no audio/video/documents.
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}


def is_creator(user: User) -> bool:
    """Whether the user authors library content, so also sees what is unpublished."""
    return user.role.value in ("creator", "org_admin")


async def _station_response(
    station_repo: StationRepository,
    station: Any,
    *,
    with_usage: bool = True,
    published_only: bool = False,
) -> StationResponse:
    """Serialize a Station, optionally populating its usage payload.

    ``published_only`` names in the usage only the published, non-archived rallyes.
    """
    resp = StationResponse.model_validate(station)
    # The API streams the image same-origin (storage.station_image_url).
    if station.title_image_key:
        resp.title_image_url = station_image_url(station.title_image_key)
    if with_usage:
        usage = await station_repo.get_usage(station.id, published_only=published_only)
        resp.usage = StationUsage(**usage)
        resp.usage_count = len(usage["rallye_ids"])
    return resp


# ============================================================================
# Library: stations
# ============================================================================


@router.get(
    "/",
    response_model=StationListResponse,
    responses={
        200: {"description": "List of library stations"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def list_stations(
    _request: Request,
    skip: int = Query(0, ge=0, description="Number of items to skip (offset)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum items to return"),
    include_deleted: bool = Query(False, description="Include archived (soft-deleted) stations — org_admin only"),
    q: str | None = Query(None, description="Free-text search over title (every language) and address"),
    sort: StationSortKey = Query("created_at", description="Sort key"),
    order: SortOrder = Query("desc", description="Sort direction"),
    lang: str = Query("de", min_length=2, max_length=16, description="Interface language for the title sort"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("participant")),
) -> StationListResponse:
    """Search, sort and page the library stations, each with its usage.

    Open to participants and above. Creators and organization admins see every
    station of the library, and the usage of each names every rallye that links
    it and is not archived. A participant or a coordinator sees only the stations
    that a published, non-archived rallye links, with the same search and sort,
    and the usage of each names only such rallyes; `usage_count` counts only
    those, and `sort=usage` orders by that same count. Only an organization admin may set `include_deleted=true` to list
    archived stations, and anyone else gets 403. The search text `q` matches the
    title in every language and the address.
    """
    if include_deleted and current_user.role.value != "org_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only org admins may view archived stations",
        )

    station_repo = StationRepository(db, organization_id=org.id)
    published_only = not is_creator(current_user)

    stations, total = await station_repo.search_library(
        q=q,
        sort=sort,
        order=order,
        lang=lang,
        skip=skip,
        limit=limit,
        include_deleted=include_deleted,
        published_only=published_only,
    )

    items = [await _station_response(station_repo, s, published_only=published_only) for s in stations]
    return StationListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get(
    "/{station_id}",
    response_model=StationResponse,
    responses={
        200: {"description": "Station details"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        404: {"description": "Station not found", "model": ErrorResponse},
    },
)
async def get_station(
    station_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("participant")),
) -> StationResponse:
    """Return one library station with its usage.

    Open to participants and above. Creators and organization admins read every
    station, and the usage names every rallye that links it and is not archived.
    A participant or a coordinator reads a station only when a published,
    non-archived rallye links it, otherwise the route answers 404, the same rule
    as the list. Their usage names only such rallyes, and `usage_count` counts
    only those.
    """
    station_repo = StationRepository(db, organization_id=org.id)
    station = await station_repo.get_by_id(station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")

    if not is_creator(current_user):
        usage = await station_repo.get_usage(station_id, published_only=True)
        if not usage["rallye_ids"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")
        resp = StationResponse.model_validate(station)
        resp.usage = StationUsage(**usage)
        resp.usage_count = len(usage["rallye_ids"])
        return resp

    return await _station_response(station_repo, station)


@router.post(
    "/",
    response_model=StationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Station created successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def create_station(
    request: Request,
    body: StationCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> StationResponse:
    """Create a library station that no rallye links yet.

    Open to creators and organization admins. The rallye routes link the station to
    a rallye. It emits the audit event `station_created`.
    """
    client_ip = client_address(request)
    station_repo = StationRepository(db, organization_id=org.id)

    try:
        station = await station_repo.create(
            title=body.title,
            description=sanitize_multilingual(body.description, media_prefix_for(org.slug), loc=("description",)),
            address=body.address,
            location_lat=body.location_lat,
            location_lng=body.location_lng,
            tips=_clean_tips(body.tips.model_dump() if body.tips else None, org.slug),
            created_by_user_id=current_user.id,
        )
        await db.commit()
        await db.refresh(station)
    except IntegrityError as e:
        await db.rollback()
        logger.exception("station_create_integrity_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Station creation failed due to a data integrity error.",
        ) from e

    record_audit_event(
        event="station_created",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "station", "id": str(station.id)},
        organization_id=org.id,
    )

    return await _station_response(station_repo, station)


@router.put(
    "/{station_id}",
    response_model=StationResponse,
    responses={
        200: {"description": "Station updated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Station not found", "model": ErrorResponse},
    },
)
async def update_station(
    station_id: UUID,
    request: Request,
    body: StationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> StationResponse:
    """Change the fields of a library station that the request sends.

    Open to creators and organization admins. The change shows in every rallye
    that links the station. Fields that the request leaves out stay as they are,
    and an explicit null in `tips` clears the tips. A station with an address
    needs `location_lat` and `location_lng`, otherwise the route answers 422. It
    emits the audit event `station_updated` when a field changes.
    """
    client_ip = client_address(request)
    station_repo = StationRepository(db, organization_id=org.id)

    station = await station_repo.get_by_id(station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")

    changes: dict[str, dict[str, Any]] = {}
    if body.title is not None and body.title != station.title:
        changes["title"] = {"from": station.title, "to": body.title}
    if body.description is not None and body.description != station.description:
        changes["description"] = {"from": station.description, "to": body.description}
    if body.address is not None and body.address != station.address:
        changes["address"] = {"from": station.address, "to": body.address}
    if body.location_lat is not None and body.location_lat != station.location_lat:
        changes["location_lat"] = {"from": station.location_lat, "to": body.location_lat}
    if body.location_lng is not None and body.location_lng != station.location_lng:
        changes["location_lng"] = {"from": station.location_lng, "to": body.location_lng}
    # tips is clearable: explicit null clears, omission leaves unchanged,
    # distinguished via model_fields_set.
    new_tips = _clean_tips(body.tips.model_dump() if body.tips else None, org.slug)
    if "tips" in body.model_fields_set and new_tips != station.tips:
        changes["tips"] = {"from": station.tips, "to": new_tips}

    if not changes:
        return await _station_response(station_repo, station)

    # An address without a map position would put a station on the wrong house.
    effective_address = body.address if body.address is not None else station.address
    effective_lat = body.location_lat if body.location_lat is not None else station.location_lat
    effective_lng = body.location_lng if body.location_lng is not None else station.location_lng
    if (effective_address or "").strip() and (effective_lat is None or effective_lng is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A station with an address needs location_lat and location_lng",
        )

    update_kwargs: dict[str, Any] = {}
    if body.title is not None:
        update_kwargs["title"] = body.title
    if body.description is not None:
        update_kwargs["description"] = sanitize_multilingual(
            body.description, media_prefix_for(org.slug), loc=("description",)
        )
    if body.address is not None:
        update_kwargs["address"] = body.address
    if body.location_lat is not None:
        update_kwargs["location_lat"] = body.location_lat
    if body.location_lng is not None:
        update_kwargs["location_lng"] = body.location_lng
    if "tips" in body.model_fields_set:
        update_kwargs["tips"] = new_tips

    try:
        updated = await station_repo.update(entity_id=station_id, **update_kwargs)
        await db.commit()
        await db.refresh(updated)
    except IntegrityError as e:
        await db.rollback()
        logger.exception("station_update_integrity_error", extra={"station_id": str(station_id)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Station update failed due to a data integrity error.",
        ) from e

    record_audit_event(
        event="station_updated",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "station", "id": str(station_id)},
        changes=changes,
        organization_id=org.id,
    )

    return await _station_response(station_repo, updated)


@router.delete(
    "/{station_id}",
    response_model=StationUsage,
    responses={
        200: {"description": "Station soft-deleted; usage prior to deletion returned"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Station not found", "model": ErrorResponse},
    },
)
async def delete_station(
    station_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> StationUsage:
    """Archive a library station and remove it from every rallye.

    Open to creators and organization admins. The rallyes lose the station and the
    challenge links of that station. The station stays in the library as an
    archived item that an organization admin can restore. The response is the
    usage of the station as it was just before the archive. It emits the audit
    event `station_deleted`.
    """
    client_ip = client_address(request)
    station_repo = StationRepository(db, organization_id=org.id)
    rs_repo = RallyeStationRepository(db, organization_id=org.id)

    station = await station_repo.get_by_id(station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")

    # Capture usage before we tear down the links.
    usage = await station_repo.get_usage(station_id)

    await rs_repo.delete_all_for_station(station_id)

    deleted = await station_repo.soft_delete(station_id)
    if not deleted:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Station not found or already deleted",
        )

    await db.commit()

    record_audit_event(
        event="station_deleted",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "station", "id": str(station_id), "title": station.title},
        organization_id=org.id,
    )

    return StationUsage(**usage)


@router.post(
    "/{station_id}/duplicate",
    response_model=StationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "A complete copy of the station"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Requires creator role", "model": ErrorResponse},
        404: {"description": "Station not found", "model": ErrorResponse},
    },
)
async def duplicate_station(
    station_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> StationResponse:
    """Copy a library station with every language, its location, its tips and its title image.

    Open to creators and organization admins. The copy is a new library item that
    no rallye links. Each language of its title gets the suffix " (Kopie)" in
    German and " (Copy)" in every other language. The copy gets its own copy of
    the image file. It emits the audit event `station_duplicated`.
    """
    station_repo = StationRepository(db, organization_id=org.id)
    source = await station_repo.get_by_id(station_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")
    station = await copy_station(db, org.id, org.slug, source, current_user.id)
    await db.commit()
    await db.refresh(station)
    record_audit_event(
        event="station_duplicated",
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "station", "id": str(station.id), "source_id": str(station_id)},
        organization_id=org.id,
    )
    return await _station_response(station_repo, station)


@router.post(
    "/{station_id}/restore",
    response_model=StationResponse,
    responses={
        200: {"description": "Station restored successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "Station not found", "model": ErrorResponse},
        409: {"description": "Station is not archived", "model": ErrorResponse},
    },
)
async def restore_station(
    station_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> StationResponse:
    """Restore an archived library station into the library.

    Open to organization admins only. A station that is not archived answers 409.
    The restore does not recreate the rallye links that the archive removed. It
    emits the audit event `station_restored`.
    """
    client_ip = client_address(request)
    station_repo = StationRepository(db, organization_id=org.id)

    station = await station_repo.get_by_id(station_id, include_deleted=True)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")
    if not station.is_deleted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Station is not archived")

    await station_repo.restore(station_id)
    await db.commit()
    await db.refresh(station)

    record_audit_event(
        event="station_restored",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "station", "id": str(station_id), "title": station.title},
        organization_id=org.id,
    )

    return await _station_response(station_repo, station)


@router.delete(
    "/{station_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Station permanently deleted"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "Station not found", "model": ErrorResponse},
        409: {"description": "Station is not archived — archive it first", "model": ErrorResponse},
    },
)
async def permanently_delete_station(
    station_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> None:
    """Delete an archived library station for good.

    Open to organization admins only. A station must be archived first, otherwise
    the route answers 409. The runs keep their frozen copy of the station and
    then refer to no library station. The title image file is deleted after the
    commit, and a failure to delete it does not fail the request. It emits the
    audit event `station_permanently_deleted`.
    """
    client_ip = client_address(request)
    station_repo = StationRepository(db, organization_id=org.id)

    station = await station_repo.get_by_id(station_id, include_deleted=True)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")
    if not station.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Station is not archived — archive it first",
        )

    # Audit before the row is gone (title still readable)
    record_audit_event(
        event="station_permanently_deleted",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "station", "id": str(station_id), "title": station.title},
        organization_id=org.id,
    )

    title_image_key = station.title_image_key
    await db.delete(station)
    await db.commit()

    # Best-effort storage cleanup once the DB delete is committed.
    if title_image_key:
        try:
            delete_file(title_image_key)
        except Exception:
            logger.warning("station permanent-delete: title image cleanup failed", exc_info=True)


@router.put(
    "/{station_id}/title-image",
    response_model=StationResponse,
    responses={
        200: {"description": "Title image set"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Station not found", "model": ErrorResponse},
        413: {
            "description": "Image too large, in bytes or in pixel dimensions (error_code IMAGE_TOO_LARGE)",
            "model": ErrorResponse,
        },
        415: {
            "description": (
                "Not an allowed image type, an empty file, or bytes the image library cannot decode "
                "(error_code IMAGE_NOT_DECODABLE)"
            ),
            "model": ErrorResponse,
        },
    },
)
async def set_station_title_image(
    station_id: UUID,
    request: Request,
    file: UploadFile = File(..., description="Title image (png/jpeg/webp, max 10MB)"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> StationResponse:
    """Set the title image of a station, or replace it.

    Open to creators and organization admins. The route accepts PNG, JPEG and
    WebP. It stores the image re-encoded and without metadata. A replaced image
    file is deleted. A file of another type, an empty file and bytes that cannot
    be decoded as an image answer 415 `IMAGE_NOT_DECODABLE`, and nothing is
    stored. A file above the upload limit answers 413. It emits the audit event
    `station_title_image_set`.
    """
    client_ip = client_address(request)
    station_repo = StationRepository(db, organization_id=org.id)

    station = await station_repo.get_by_id(station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Image size {len(data)} bytes exceeds the {MAX_FILE_SIZE}-byte limit.",
        )
    # A parameter on the claimed type ("image/png; charset=binary") does not
    # make it another type, as on the answer route. The list itself is
    # not widened: the object is stored under the bare type from the list.
    content_type = bare_content_type(file.content_type)
    if content_type not in IMAGE_MIME_TYPES:
        raise image_not_decodable(
            f"Image type '{content_type or 'unknown'}' is not allowed. Allowed: {', '.join(sorted(IMAGE_MIME_TYPES))}."
        )
    data, _thumb, _display = await clean_image_upload(data)

    safe_name = "".join(c for c in (file.filename or "image") if c.isalnum() or c in "._-") or "image"
    object_key = f"{org.id}/stations/{station_id}/{str(uuid4())[:8]}_{safe_name}"

    get_minio_client().put_object(
        bucket_name=settings.storage.bucket,
        object_name=object_key,
        data=io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )

    old_key = station.title_image_key
    updated = await station_repo.update(entity_id=station_id, title_image_key=object_key)
    await db.commit()
    await db.refresh(updated)

    # Delete the previous object only after the new key is persisted, so a
    # delete failure never loses the current image. An orphan here is left to
    # the storage sweep. Log it, don't fail the request.
    if old_key and old_key != object_key:
        try:
            delete_file(old_key)
        except Exception:
            logger.warning(
                "station_title_image_orphan",
                extra={"station_id": str(station_id), "orphan_key": old_key},
            )

    record_audit_event(
        event="station_title_image_set",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "station", "id": str(station_id)},
        changes={"title_image_key": {"from": old_key, "to": object_key}},
        organization_id=org.id,
    )

    return await _station_response(station_repo, updated)


@router.delete(
    "/{station_id}/title-image",
    response_model=StationResponse,
    responses={
        200: {"description": "Title image removed (or already absent)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Station not found", "model": ErrorResponse},
    },
)
async def remove_station_title_image(
    station_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> StationResponse:
    """Remove the title image of a station.

    Open to creators and organization admins. The route deletes the image file. A
    station without an image answers 200 and changes nothing. It emits the audit
    event `station_title_image_removed` when it removes an image.
    """
    client_ip = client_address(request)
    station_repo = StationRepository(db, organization_id=org.id)

    station = await station_repo.get_by_id(station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")

    old_key = station.title_image_key
    if old_key is None:
        return await _station_response(station_repo, station)

    # The base `update()` skips None values, so clear the column directly.
    station.title_image_key = None
    await db.commit()
    await db.refresh(station)

    try:
        delete_file(old_key)
    except Exception:
        logger.warning(
            "station_title_image_orphan",
            extra={"station_id": str(station_id), "orphan_key": old_key},
        )

    record_audit_event(
        event="station_title_image_removed",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "station", "id": str(station_id)},
        changes={"title_image_key": {"from": old_key, "to": None}},
        organization_id=org.id,
    )

    return await _station_response(station_repo, station)
