"""
Rallye CRUD endpoints.

Provides full CRUD operations for rallyes with RBAC enforcement,
organization scoping via URL slug, audit logging, and pagination.

All endpoints require authentication and enforce role-based access control:
- List/Get: participant+ can view published rallyes, creator+ can view all
- Create: creator+ can create rallyes
- Update: creator+ can update rallyes
- Delete: creator+ can soft-delete rallyes

All modification operations emit audit events for security tracking.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.client_address import client_address
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.rallye_station import RallyeStationRepository
from app.repositories.rallye_station_challenge import RallyeStationChallengeRepository
from app.repositories.station import StationRepository
from app.routers.schemas import (
    ChallengeCreateRequest,
    ChallengeResponse,
    ChallengeUsage,
    ErrorResponse,
    ImportChallengeRequest,
    ImportStationRequest,
    RallyeCreateRequest,
    RallyeListResponse,
    RallyeResponse,
    RallyeStationChallengeListResponse,
    RallyeStationChallengeResponse,
    RallyeStationListResponse,
    RallyeStationResponse,
    RallyeUpdateRequest,
    ReorderRequest,
    StationCreateRequest,
    StationResponse,
    StationUsage,
)
from app.routers.stations import is_creator
from app.services.audit import actor_from_user, record_audit_event
from app.services.content_copy import copy_challenge, copy_station
from app.services.richtext import (
    REFERENCE_ANSWER_MAX_LENGTH,
    media_prefix_for,
    sanitize_multilingual,
    sanitize_optional_html,
    sanitize_station_tips,
)
from app.services.scoring_rules import prune_scoring_rules, validate_scoring_rules

router = APIRouter()


@router.get(
    "/",
    response_model=RallyeListResponse,
    responses={
        200: {"description": "List of rallyes"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def list_rallyes(
    _request: Request,
    skip: int = Query(0, ge=0, description="Number of items to skip (offset)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum items to return"),
    include_deleted: bool = Query(False, description="Include archived (soft-deleted) rallyes — org_admin only"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RallyeListResponse:
    """List the rallyes of the organization, one page at a time.

    Open to coordinators and above. A coordinator picks a rallye for a run and
    sees the published rallyes only. Creators and organization admins see the
    published and the unpublished ones. A participant or a guest gets 403.
    Only an organization admin may set `include_deleted=true` to list archived
    rallyes, and anyone else gets 403.
    """
    if include_deleted and current_user.role.value != "org_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only org admins may view archived rallyes",
        )

    rallye_repo = RallyeRepository(db, organization_id=org.id)

    # Creator+ can see all rallyes, coordinator sees only published
    if current_user.role.value in ["creator", "org_admin"]:
        rallyes = await rallye_repo.list(skip=skip, limit=limit, include_deleted=include_deleted)
        total = await rallye_repo.count(include_deleted=include_deleted)
    else:
        rallyes = await rallye_repo.list_published(skip=skip, limit=limit)
        total = await rallye_repo.count_published()

    return RallyeListResponse(
        items=[RallyeResponse.model_validate(r) for r in rallyes],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{rallye_id}",
    response_model=RallyeResponse,
    responses={
        200: {"description": "Rallye details"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization or role)", "model": ErrorResponse},
        404: {"description": "Rallye not found, or unpublished for a coordinator", "model": ErrorResponse},
    },
)
async def get_rallye(
    rallye_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("coordinator")),
) -> RallyeResponse:
    """Return one rallye.

    Open to coordinators and above. A coordinator reads a published rallye only.
    Creators and organization admins also read an unpublished one. A rallye that
    is unknown, or unpublished for a coordinator, answers 404. A participant or a
    guest gets 403.
    """
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)

    if rallye is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rallye not found",
        )

    if not rallye.is_published and current_user.role.value not in ["creator", "org_admin"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rallye not found",
        )

    return RallyeResponse.model_validate(rallye)


@router.post(
    "/",
    response_model=RallyeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Rallye created successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def create_rallye(
    request: Request,
    body: RallyeCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeResponse:
    """Create a rallye without stations.

    Open to creators and organization admins. The new rallye is unpublished. The
    update route publishes it. It emits the audit event `rallye_created`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)

    try:
        rallye = await rallye_repo.create(
            organization_id=org.id,
            title=body.title,
            description=sanitize_multilingual(body.description, media_prefix_for(org.slug), loc=("description",)),
            is_published=False,
            created_by_user_id=current_user.id,
        )
        await db.commit()
        await db.refresh(rallye)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rallye creation failed: {str(e)}",
        ) from e

    record_audit_event(
        event="rallye_created",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye", "id": str(rallye.id)},
        organization_id=org.id,
    )

    return RallyeResponse.model_validate(rallye)


@router.put(
    "/{rallye_id}",
    response_model=RallyeResponse,
    responses={
        200: {"description": "Rallye updated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Rallye not found", "model": ErrorResponse},
    },
)
async def update_rallye(
    rallye_id: UUID,
    request: Request,
    body: RallyeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeResponse:
    """Change the title, the description or the publication state of a rallye.

    Open to creators and organization admins. Fields that the request leaves out
    stay as they are. It emits the audit event `rallye_updated` with the changes
    when a field changes.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)

    # Current rallye state, to track the changes.
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rallye not found",
        )

    changes: dict[str, dict[str, Any]] = {}
    if body.title is not None and body.title != rallye.title:
        changes["title"] = {"from": rallye.title, "to": body.title}
    if body.description is not None and body.description != rallye.description:
        changes["description"] = {"from": rallye.description, "to": body.description}
    if body.is_published is not None and body.is_published != rallye.is_published:
        changes["is_published"] = {"from": rallye.is_published, "to": body.is_published}

    if not changes:
        return RallyeResponse.model_validate(rallye)

    # Build update payload — only fields the client explicitly provided.
    update_kwargs: dict[str, Any] = {}
    if body.title is not None:
        update_kwargs["title"] = body.title
    if body.description is not None:
        update_kwargs["description"] = sanitize_multilingual(
            body.description, media_prefix_for(org.slug), loc=("description",)
        )
    if body.is_published is not None:
        update_kwargs["is_published"] = body.is_published

    try:
        updated_rallye = await rallye_repo.update(
            entity_id=rallye_id,
            **update_kwargs,
        )
        await db.commit()
        await db.refresh(updated_rallye)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rallye update failed: {str(e)}",
        ) from e

    record_audit_event(
        event="rallye_updated",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye", "id": str(rallye_id)},
        changes=changes,
        organization_id=org.id,
    )

    return RallyeResponse.model_validate(updated_rallye)


@router.delete(
    "/{rallye_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Rallye deleted successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Rallye not found", "model": ErrorResponse},
    },
)
async def delete_rallye(
    rallye_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> None:
    """Archive a rallye. The record stays in the database, marked as deleted.

    Open to creators and organization admins. An organization admin can restore
    the archived rallye or delete it for good. It emits the audit event
    `rallye_deleted`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)

    # The title is read before the deletion, for the audit log.
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rallye not found",
        )

    deleted = await rallye_repo.soft_delete(rallye_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rallye not found or already deleted",
        )

    await db.commit()

    record_audit_event(
        event="rallye_deleted",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye", "id": str(rallye_id), "title": rallye.title},
        organization_id=org.id,
    )


@router.post(
    "/{rallye_id}/restore",
    response_model=RallyeResponse,
    responses={
        200: {"description": "Rallye restored successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "Rallye not found", "model": ErrorResponse},
        409: {"description": "Rallye is not archived", "model": ErrorResponse},
    },
)
async def restore_rallye(
    rallye_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> RallyeResponse:
    """Restore an archived rallye.

    Open to organization admins only. A rallye that is not archived answers 409. It
    emits the audit event `rallye_restored`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)

    rallye = await rallye_repo.get_by_id(rallye_id, include_deleted=True)
    if rallye is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rallye not found",
        )
    if not rallye.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Rallye is not archived",
        )

    await rallye_repo.restore(rallye_id)
    await db.commit()
    await db.refresh(rallye)

    record_audit_event(
        event="rallye_restored",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye", "id": str(rallye_id), "title": rallye.title},
        organization_id=org.id,
    )

    return RallyeResponse.model_validate(rallye)


@router.delete(
    "/{rallye_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Rallye permanently deleted"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "Rallye not found", "model": ErrorResponse},
        409: {"description": "Rallye is not archived — archive it first", "model": ErrorResponse},
    },
)
async def permanently_delete_rallye(
    rallye_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> None:
    """Delete an archived rallye for good.

    Open to organization admins only. A rallye must be archived first, otherwise
    the route answers 409. The runs of the rallye keep their frozen copy of it and
    then refer to no rallye. The links to its stations are deleted with it. It
    emits the audit event `rallye_permanently_deleted`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)

    rallye = await rallye_repo.get_by_id(rallye_id, include_deleted=True)
    if rallye is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rallye not found",
        )
    if not rallye.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Rallye is not archived — archive it first",
        )

    # Audit before the row is gone (title still readable)
    record_audit_event(
        event="rallye_permanently_deleted",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye", "id": str(rallye_id), "title": rallye.title},
        organization_id=org.id,
    )

    await db.delete(rallye)
    await db.commit()


def _suffix_title(title: dict[str, str]) -> dict[str, str]:
    """
    Append a language-appropriate copy suffix to each locale in a multilingual title.

    Falls back gracefully for unknown locales using the English suffix.
    """
    suffixes = {"de": " (Kopie)", "en": " (Copy)"}
    return {locale: (value or "") + suffixes.get(locale, " (Copy)") for locale, value in title.items()}


@router.post(
    "/{rallye_id}/duplicate",
    response_model=RallyeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Rallye duplicated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Rallye not found", "model": ErrorResponse},
    },
)
async def duplicate_rallye(
    rallye_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeResponse:
    """Duplicate a rallye with its station links and challenge links.

    Open to creators and organization admins. The copy uses the same library
    stations and challenges in the same order, so an edit of one of them shows in
    both rallyes. The copy is unpublished. Its title gets the suffix " (Kopie)" in
    German and " (Copy)" in every other language. It emits the audit event
    `rallye_duplicated`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)

    source = await rallye_repo.get_by_id(rallye_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rallye not found",
        )

    # A copy is a new write: its description passes the write filter before anything is stored.
    description = sanitize_multilingual(
        dict(source.description or {}), media_prefix_for(org.slug), loc=("description",)
    )
    try:
        new_rallye = await rallye_repo.create(
            title=_suffix_title(source.title),
            description=description,
            is_published=False,
            created_by_user_id=current_user.id,
        )

        # Copy the links: same library stations, new rallye_stations rows. The
        # per-rallye-station challenge matches are copied too (same library
        # challenges, fresh rallye_station_challenges rows preserving order).
        source_links = await rs_repo.list_by_rallye(rallye_id)
        for index, link in enumerate(source_links):
            new_link = await rs_repo.create(
                rallye_id=new_rallye.id,
                station_id=link.station_id,
                sort_order=index,
            )
            challenge_links = await rsc_repo.list_by_rallye_station(link.id)
            for c_index, c_link in enumerate(challenge_links):
                await rsc_repo.create(
                    rallye_station_id=new_link.id,
                    challenge_id=c_link.challenge_id,
                    sort_order=c_index,
                )

        await db.commit()
        await db.refresh(new_rallye)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rallye duplication failed: {str(e)}",
        ) from e

    record_audit_event(
        event="rallye_duplicated",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye", "id": str(new_rallye.id), "source_id": str(rallye_id)},
        organization_id=org.id,
    )

    return RallyeResponse.model_validate(new_rallye)


# ============================================================================
# Composition: stations within a rallye
# ============================================================================


async def _station_response(station_repo: StationRepository, station: Any) -> StationResponse:
    """Serialize a Station with its usage payload populated."""
    resp = StationResponse.model_validate(station)
    usage = await station_repo.get_usage(station.id)
    resp.usage = StationUsage(**usage)
    # The count the rallye editor's sharing chip shows.
    resp.usage_count = len(set(usage["rallye_ids"]))
    return resp


async def _challenge_response(challenge_repo: ChallengeRepository, challenge: Any) -> ChallengeResponse:
    """Serialize a Challenge with its usage payload populated."""
    resp = ChallengeResponse.model_validate(challenge)
    usage = await challenge_repo.get_usage(challenge.id)
    resp.usage = ChallengeUsage(**usage)
    resp.usage_count = len(set(usage["rallye_ids"]))
    return resp


async def _rallye_station_response(
    station_repo: StationRepository,
    rsc_repo: RallyeStationChallengeRepository,
    link: Any,
) -> RallyeStationResponse:
    """Serialize a RallyeStation link with its station + per-rallye challenge aggregates."""
    challenge_links = await rsc_repo.list_by_rallye_station(link.id)
    total_points = 0
    total_duration = 0
    for rsc in challenge_links:
        rules = rsc.challenge.scoring_rules or {}
        max_points = rules.get("max_points", 0)
        if isinstance(max_points, (int, float)):
            total_points += int(max_points)
        total_duration += rsc.challenge.time_estimate_minutes or 0
    return RallyeStationResponse(
        link_id=link.id,
        rallye_id=link.rallye_id,
        sort_order=link.sort_order,
        station=await _station_response(station_repo, link.station),
        challenge_count=len(challenge_links),
        total_points=total_points,
        total_duration_minutes=total_duration,
    )


async def _require_rallye_station(
    rs_repo: RallyeStationRepository,
    rallye_id: UUID,
    rallye_station_id: UUID,
) -> Any:
    """Fetch a rallye_station link and verify it belongs to the rallye + org."""
    link = await rs_repo.get_by_id(rallye_station_id)
    if link is None or link.rallye_id != rallye_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye station not found")
    return link


async def _require_rallye_for_compose(
    rallye_repo: RallyeRepository,
    rallye_id: UUID,
    current_user: User,
) -> Any:
    """Fetch a rallye for composition, applying participant visibility rules."""
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")
    if not rallye.is_published and not is_creator(current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")
    return rallye


@router.get(
    "/{rallye_id}/stations",
    response_model=RallyeStationListResponse,
    responses={
        200: {"description": "Ordered stations composed into the rallye"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        404: {"description": "Rallye not found", "model": ErrorResponse},
    },
)
async def list_rallye_stations(
    rallye_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationListResponse:
    """List the stations of a rallye in their order.

    Open to creators and organization admins, because only the rallye editor reads
    it. A lower role could otherwise read the route of every published rallye
    before a run starts.
    """
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    await _require_rallye_for_compose(rallye_repo, rallye_id, current_user)

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    station_repo = StationRepository(db, organization_id=org.id)
    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    links = await rs_repo.list_by_rallye(rallye_id)

    items = [await _rallye_station_response(station_repo, rsc_repo, link) for link in links]
    return RallyeStationListResponse(items=items, total=len(items))


@router.post(
    "/{rallye_id}/stations",
    response_model=RallyeStationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Station created and linked"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye not found", "model": ErrorResponse},
    },
)
async def create_and_link_station(
    rallye_id: UUID,
    request: Request,
    body: StationCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationResponse:
    """Create a library station and link it to the end of the rallye.

    Open to creators and organization admins. It emits the audit event
    `station_created`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")

    station_repo = StationRepository(db, organization_id=org.id)
    rs_repo = RallyeStationRepository(db, organization_id=org.id)

    try:
        station = await station_repo.create(
            title=body.title,
            description=sanitize_multilingual(body.description, media_prefix_for(org.slug), loc=("description",)),
            address=body.address,
            location_lat=body.location_lat,
            location_lng=body.location_lng,
            # The tips are passed on, or a station created inside a rallye would lose its
            # "Tipps & Links".
            tips=sanitize_station_tips(body.tips.model_dump() if body.tips else None, org.slug),
            created_by_user_id=current_user.id,
        )
        sort_order = await rs_repo.next_sort_order(rallye_id)
        link = await rs_repo.create(
            rallye_id=rallye_id,
            station_id=station.id,
            sort_order=sort_order,
        )
        await db.commit()
        await db.refresh(link)
        await db.refresh(station)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Station creation failed due to a data integrity error.",
        ) from e

    record_audit_event(
        event="station_created",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "station", "id": str(station.id), "rallye_id": str(rallye_id)},
        organization_id=org.id,
    )

    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    refreshed = await rs_repo.get_by_id(link.id)
    assert refreshed is not None
    return await _rallye_station_response(station_repo, rsc_repo, refreshed)


@router.post(
    "/{rallye_id}/stations/import",
    response_model=RallyeStationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Existing station linked"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye or station not found", "model": ErrorResponse},
        409: {"description": "Station already linked to this rallye", "model": ErrorResponse},
    },
)
async def import_station(
    rallye_id: UUID,
    request: Request,
    body: ImportStationRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationResponse:
    """Link a library station of the organization to the rallye.

    Open to creators and organization admins. The station goes to the end unless
    the request gives a `sort_order`. A station that the rallye links already
    answers 409. An unknown station answers 404. It emits the audit event
    `rallye_station_linked`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")

    station_repo = StationRepository(db, organization_id=org.id)
    station = await station_repo.get_by_id(body.station_id)
    if station is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    existing = await rs_repo.get_link(rallye_id, body.station_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Station is already linked to this rallye",
        )

    sort_order = body.sort_order if body.sort_order is not None else await rs_repo.next_sort_order(rallye_id)
    try:
        link = await rs_repo.create(
            rallye_id=rallye_id,
            station_id=body.station_id,
            sort_order=sort_order,
        )
        await db.commit()
        await db.refresh(link)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Station is already linked to this rallye",
        ) from e

    record_audit_event(
        event="rallye_station_linked",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye_station", "rallye_id": str(rallye_id), "station_id": str(body.station_id)},
        organization_id=org.id,
    )

    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    refreshed = await rs_repo.get_by_id(link.id)
    assert refreshed is not None
    return await _rallye_station_response(station_repo, rsc_repo, refreshed)


@router.delete(
    "/{rallye_id}/stations/{station_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Station unlinked (station survives)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye, station, or link not found", "model": ErrorResponse},
    },
)
async def unlink_station(
    rallye_id: UUID,
    station_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> None:
    """Remove a station from a rallye. The station stays in the library.

    Open to creators and organization admins. It emits the audit event
    `rallye_station_unlinked`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    removed = await rs_repo.delete_link(rallye_id, station_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")

    await db.commit()

    record_audit_event(
        event="rallye_station_unlinked",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye_station", "rallye_id": str(rallye_id), "station_id": str(station_id)},
        organization_id=org.id,
    )


@router.put(
    "/{rallye_id}/stations/order",
    response_model=RallyeStationListResponse,
    responses={
        200: {"description": "Stations reordered"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye not found", "model": ErrorResponse},
        422: {"description": "ids do not match the rallye's linked stations", "model": ErrorResponse},
    },
)
async def reorder_rallye_stations(
    rallye_id: UUID,
    request: Request,
    body: ReorderRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationListResponse:
    """Set the order of the stations of a rallye.

    Open to creators and organization admins. `ids` must list every station that the
    rallye links, once each, otherwise the route answers 422. The position in
    `ids` becomes the order. It emits the audit event `rallye_stations_reordered`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    station_repo = StationRepository(db, organization_id=org.id)
    links = await rs_repo.list_by_rallye(rallye_id)

    existing_ids = {link.station_id for link in links}
    if set(body.ids) != existing_ids or len(body.ids) != len(links):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provided ids must match exactly the stations linked to this rallye",
        )

    by_station = {link.station_id: link for link in links}
    for index, station_id in enumerate(body.ids):
        by_station[station_id].sort_order = index

    await db.commit()

    record_audit_event(
        event="rallye_stations_reordered",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye", "id": str(rallye_id)},
        organization_id=org.id,
    )

    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    refreshed = await rs_repo.list_by_rallye(rallye_id)
    items = [await _rallye_station_response(station_repo, rsc_repo, link) for link in refreshed]
    return RallyeStationListResponse(items=items, total=len(items))


# ============================================================================
# Composition: challenges matched to a rallye's station
# ============================================================================


@router.get(
    "/{rallye_id}/stations/{rallye_station_id}/challenges",
    response_model=RallyeStationChallengeListResponse,
    responses={
        200: {"description": "Ordered challenges matched to the rallye-station"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        404: {"description": "Rallye or rallye-station not found", "model": ErrorResponse},
    },
)
async def list_rallye_station_challenges(
    rallye_id: UUID,
    rallye_station_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationChallengeListResponse:
    """List the challenges of a station of a rallye in their order.

    Open to creators and organization admins. Each item is the library challenge
    with its full scoring rules and its reference answer. A lower role could
    otherwise read the right answers of every published rallye. A team reads its
    tasks through the run.
    """
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    await _require_rallye_for_compose(rallye_repo, rallye_id, current_user)

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    await _require_rallye_station(rs_repo, rallye_id, rallye_station_id)

    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    links = await rsc_repo.list_by_rallye_station(rallye_station_id)

    items = [
        RallyeStationChallengeResponse(
            link_id=link.id,
            rallye_station_id=link.rallye_station_id,
            sort_order=link.sort_order,
            challenge=await _challenge_response(challenge_repo, link.challenge),
        )
        for link in links
    ]
    return RallyeStationChallengeListResponse(items=items, total=len(items))


@router.post(
    "/{rallye_id}/stations/{rallye_station_id}/challenges",
    response_model=RallyeStationChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Challenge created and linked"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye or rallye-station not found", "model": ErrorResponse},
    },
)
async def create_and_link_challenge(
    rallye_id: UUID,
    rallye_station_id: UUID,
    request: Request,
    body: ChallengeCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationChallengeResponse:
    """Create a library challenge and link it to the end of a station of the rallye.

    Open to creators and organization admins. The scoring rules must fit the
    challenge type, otherwise the route answers 422. It emits the audit event
    `challenge_created`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    await _require_rallye_station(rs_repo, rallye_id, rallye_station_id)

    scoring_rules = prune_scoring_rules(body.challenge_type, body.scoring_rules)
    validate_scoring_rules(body.challenge_type, scoring_rules)

    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)

    try:
        challenge = await challenge_repo.create(
            challenge_type=body.challenge_type,
            title=body.title,
            instructions=sanitize_multilingual(body.instructions, media_prefix_for(org.slug), loc=("instructions",)),
            scoring_rules=scoring_rules,
            time_estimate_minutes=body.time_estimate_minutes,
            suggested_solution=sanitize_optional_html(
                body.suggested_solution,
                media_prefix_for(org.slug),
                max_length=REFERENCE_ANSWER_MAX_LENGTH,
                loc=("suggested_solution",),
            ),
            created_by_user_id=current_user.id,
        )
        sort_order = await rsc_repo.next_sort_order(rallye_station_id)
        link = await rsc_repo.create(
            rallye_station_id=rallye_station_id,
            challenge_id=challenge.id,
            sort_order=sort_order,
        )
        await db.commit()
        await db.refresh(link)
        await db.refresh(challenge)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Challenge creation failed due to a data integrity error.",
        ) from e

    record_audit_event(
        event="challenge_created",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "challenge",
            "id": str(challenge.id),
            "rallye_station_id": str(rallye_station_id),
        },
        organization_id=org.id,
    )

    return RallyeStationChallengeResponse(
        link_id=link.id,
        rallye_station_id=link.rallye_station_id,
        sort_order=link.sort_order,
        challenge=await _challenge_response(challenge_repo, challenge),
    )


@router.post(
    "/{rallye_id}/stations/{rallye_station_id}/challenges/import",
    response_model=RallyeStationChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Existing challenge linked"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye, rallye-station, or challenge not found", "model": ErrorResponse},
        409: {"description": "Challenge already linked to this rallye-station", "model": ErrorResponse},
    },
)
async def import_challenge(
    rallye_id: UUID,
    rallye_station_id: UUID,
    request: Request,
    body: ImportChallengeRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationChallengeResponse:
    """Link a library challenge of the organization to a station of the rallye.

    Open to creators and organization admins. The challenge goes to the end unless
    the request gives a `sort_order`. A challenge that the station links already
    answers 409. An unknown challenge answers 404. It emits the audit event
    `rallye_station_challenge_linked`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    await _require_rallye_station(rs_repo, rallye_id, rallye_station_id)

    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    challenge = await challenge_repo.get_by_id(body.challenge_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    existing = await rsc_repo.get_link(rallye_station_id, body.challenge_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Challenge is already linked to this rallye-station",
        )

    sort_order = body.sort_order if body.sort_order is not None else await rsc_repo.next_sort_order(rallye_station_id)
    try:
        link = await rsc_repo.create(
            rallye_station_id=rallye_station_id,
            challenge_id=body.challenge_id,
            sort_order=sort_order,
        )
        await db.commit()
        await db.refresh(link)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Challenge is already linked to this rallye-station",
        ) from e

    record_audit_event(
        event="rallye_station_challenge_linked",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "rallye_station_challenge",
            "rallye_station_id": str(rallye_station_id),
            "challenge_id": str(body.challenge_id),
        },
        organization_id=org.id,
    )

    return RallyeStationChallengeResponse(
        link_id=link.id,
        rallye_station_id=link.rallye_station_id,
        sort_order=link.sort_order,
        challenge=await _challenge_response(challenge_repo, challenge),
    )


@router.put(
    "/{rallye_id}/stations/{rallye_station_id}/challenges/reorder",
    response_model=RallyeStationChallengeListResponse,
    responses={
        200: {"description": "Challenges reordered"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye or rallye-station not found", "model": ErrorResponse},
        422: {"description": "ids do not match the rallye-station's linked challenges", "model": ErrorResponse},
    },
)
async def reorder_rallye_station_challenges(
    rallye_id: UUID,
    rallye_station_id: UUID,
    request: Request,
    body: ReorderRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationChallengeListResponse:
    """Set the order of the challenges of a station of a rallye.

    Open to creators and organization admins. `ids` must list every challenge that
    the station links, once each, otherwise the route answers 422. The position in
    `ids` becomes the order. It emits the audit event
    `rallye_station_challenges_reordered`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    await _require_rallye_station(rs_repo, rallye_id, rallye_station_id)

    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    links = await rsc_repo.list_by_rallye_station(rallye_station_id)

    existing_ids = {link.challenge_id for link in links}
    if set(body.ids) != existing_ids or len(body.ids) != len(links):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Provided ids must match exactly the challenges linked to this rallye-station",
        )

    by_challenge = {link.challenge_id: link for link in links}
    for index, challenge_id in enumerate(body.ids):
        by_challenge[challenge_id].sort_order = index

    await db.commit()

    record_audit_event(
        event="rallye_station_challenges_reordered",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "rallye_station", "id": str(rallye_station_id)},
        organization_id=org.id,
    )

    refreshed = await rsc_repo.list_by_rallye_station(rallye_station_id)
    items = [
        RallyeStationChallengeResponse(
            link_id=link.id,
            rallye_station_id=link.rallye_station_id,
            sort_order=link.sort_order,
            challenge=await _challenge_response(challenge_repo, link.challenge),
        )
        for link in refreshed
    ]
    return RallyeStationChallengeListResponse(items=items, total=len(items))


@router.post(
    "/{rallye_id}/stations/{rallye_station_id}/copy",
    response_model=RallyeStationResponse,
    responses={
        200: {"description": "The rallye now uses a fresh copy of the station"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye or rallye-station not found", "model": ErrorResponse},
    },
)
async def copy_rallye_station(
    rallye_id: UUID,
    rallye_station_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationResponse:
    """Give the rallye its own copy of a station that other rallyes share.

    Open to creators and organization admins. The link in the rallye keeps its
    place and its challenges, and points to a complete copy of the station. The
    original stays unchanged in every other rallye. It emits the audit event
    `rallye_station_copied`.
    """
    if await RallyeRepository(db, organization_id=org.id).get_by_id(rallye_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")
    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    link = await _require_rallye_station(rs_repo, rallye_id, rallye_station_id)
    station_repo = StationRepository(db, organization_id=org.id)
    source = await station_repo.get_by_id(link.station_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Station not found")

    station = await copy_station(db, org.id, org.slug, source, current_user.id)
    link.station_id = station.id
    await db.commit()
    await db.refresh(link, attribute_names=["station"])

    record_audit_event(
        event="rallye_station_copied",
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "rallye", "id": str(rallye_id), "station_id": str(station.id), "source_id": str(source.id)},
        organization_id=org.id,
    )
    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    return await _rallye_station_response(station_repo, rsc_repo, link)


@router.post(
    "/{rallye_id}/stations/{rallye_station_id}/challenges/{challenge_id}/copy",
    response_model=RallyeStationChallengeResponse,
    responses={
        200: {"description": "The rallye station now uses a fresh copy of the challenge"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye, rallye-station, or link not found", "model": ErrorResponse},
    },
)
async def copy_rallye_station_challenge(
    rallye_id: UUID,
    rallye_station_id: UUID,
    challenge_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> RallyeStationChallengeResponse:
    """Give a station of the rallye its own copy of a challenge that other rallyes share.

    Open to creators and organization admins. The link keeps its place and points
    to a complete copy of the challenge. The original stays unchanged everywhere
    else. It emits the audit event `rallye_challenge_copied`.
    """
    if await RallyeRepository(db, organization_id=org.id).get_by_id(rallye_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")
    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    await _require_rallye_station(rs_repo, rallye_id, rallye_station_id)
    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    link = next(
        (lk for lk in await rsc_repo.list_by_rallye_station(rallye_station_id) if lk.challenge_id == challenge_id), None
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")

    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    challenge = await copy_challenge(db, org.id, org.slug, link.challenge, current_user.id)
    link.challenge_id = challenge.id
    await db.commit()
    await db.refresh(link, attribute_names=["challenge"])

    record_audit_event(
        event="rallye_challenge_copied",
        actor=actor_from_user(current_user, client_address(request)),
        target={
            "entity": "rallye",
            "id": str(rallye_id),
            "challenge_id": str(challenge.id),
            "source_id": str(challenge_id),
        },
        organization_id=org.id,
    )
    return RallyeStationChallengeResponse(
        link_id=link.id,
        rallye_station_id=link.rallye_station_id,
        sort_order=link.sort_order,
        challenge=await _challenge_response(challenge_repo, link.challenge),
    )


@router.delete(
    "/{rallye_id}/stations/{rallye_station_id}/challenges/{challenge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Challenge unlinked (challenge survives)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Rallye, rallye-station, or link not found", "model": ErrorResponse},
    },
)
async def unlink_challenge(
    rallye_id: UUID,
    rallye_station_id: UUID,
    challenge_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> None:
    """Remove a challenge from a station of a rallye. The challenge stays in the library.

    Open to creators and organization admins. It emits the audit event
    `rallye_station_challenge_unlinked`.
    """
    client_ip = client_address(request)
    rallye_repo = RallyeRepository(db, organization_id=org.id)
    rallye = await rallye_repo.get_by_id(rallye_id)
    if rallye is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rallye not found")

    rs_repo = RallyeStationRepository(db, organization_id=org.id)
    await _require_rallye_station(rs_repo, rallye_id, rallye_station_id)

    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    removed = await rsc_repo.delete_link(rallye_station_id, challenge_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")

    await db.commit()

    record_audit_event(
        event="rallye_station_challenge_unlinked",
        actor=actor_from_user(current_user, client_ip),
        target={
            "entity": "rallye_station_challenge",
            "rallye_station_id": str(rallye_station_id),
            "challenge_id": str(challenge_id),
        },
        organization_id=org.id,
    )
