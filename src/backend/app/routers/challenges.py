"""
Library Challenge endpoints.

Challenges live in the organization-wide library (not under a single station).
This router serves the library CRUD at /api/orgs/{slug}/challenges:

- GET    /                list library challenges (paginated, each with usage)
- POST   /                create a standalone library challenge (creator+)
- GET    /{challenge_id}  challenge detail incl. usage
- PUT    /{challenge_id}  update — propagates to every station/rallye linking it (creator+)
- DELETE /{challenge_id}  soft-delete + cascade-unlink all links (creator+)

Composition (linking challenges into stations) lives on the stations router.
All endpoints are organization-scoped and enforce RBAC; modifications emit audit events.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.client_address import client_address
from app.database import get_db
from app.models.challenge import ChallengeType
from app.models.organization import Organization
from app.models.user import User
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye_station_challenge import RallyeStationChallengeRepository
from app.routers.schemas import (
    ChallengeCreateRequest,
    ChallengeListResponse,
    ChallengeResponse,
    ChallengeSortKey,
    ChallengeUpdateRequest,
    ChallengeUsage,
    ErrorResponse,
    SortOrder,
)
from app.services.audit import actor_from_user, record_audit_event
from app.services.content_copy import copy_challenge
from app.services.richtext import media_prefix_for, sanitize_multilingual, sanitize_optional_html
from app.services.scoring_rules import (
    prune_scoring_rules,
    prune_scoring_rules_for_type_change,
    validate_scoring_rules,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _challenge_response(
    challenge_repo: ChallengeRepository,
    challenge: Any,
    *,
    with_usage: bool = True,
) -> ChallengeResponse:
    """Serialize a Challenge, optionally populating its usage payload."""
    resp = ChallengeResponse.model_validate(challenge)
    if with_usage:
        usage = await challenge_repo.get_usage(challenge.id)
        resp.usage = ChallengeUsage(**usage)
        resp.usage_count = len(usage["rallye_ids"])
    return resp


@router.get(
    "/",
    response_model=ChallengeListResponse,
    responses={
        200: {"description": "List of library challenges"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def list_challenges(
    _request: Request,
    skip: int = Query(0, ge=0, description="Number of items to skip (offset)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum items to return"),
    include_deleted: bool = Query(False, description="Include archived (soft-deleted) challenges — org_admin only"),
    q: str | None = Query(None, description="Free-text search over title (every language)"),
    challenge_type: ChallengeType | None = Query(None, alias="type", description="Filter by challenge type"),
    sort: ChallengeSortKey = Query("created_at", description="Sort key"),
    order: SortOrder = Query("desc", description="Sort direction"),
    lang: str = Query("de", min_length=2, max_length=16, description="Interface language for the title sort"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("creator")),
) -> ChallengeListResponse:
    """Search, filter, sort and page the library challenges, each with its usage.

    Open to creators and organization admins. Only an organization admin may set
    `include_deleted=true` to list archived challenges, and anyone else gets 403.
    An unknown `type` or `sort` value answers 422. A search text `q` longer than
    200 characters is cut and not refused.
    """
    if include_deleted and _current_user.role.value != "org_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only org admins may view archived challenges",
        )

    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    challenges, total = await challenge_repo.search_library(
        q=q,
        challenge_type=challenge_type,
        sort=sort,
        order=order,
        lang=lang,
        skip=skip,
        limit=limit,
        include_deleted=include_deleted,
    )

    items = [await _challenge_response(challenge_repo, c) for c in challenges]
    return ChallengeListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get(
    "/{challenge_id}",
    response_model=ChallengeResponse,
    responses={
        200: {"description": "Challenge details"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied", "model": ErrorResponse},
        404: {"description": "Challenge not found", "model": ErrorResponse},
    },
)
async def get_challenge(
    challenge_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("creator")),
) -> ChallengeResponse:
    """Return one library challenge with its usage.

    Open to creators and organization admins. An unknown challenge answers 404.
    """
    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    challenge = await challenge_repo.get_by_id(challenge_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")
    return await _challenge_response(challenge_repo, challenge)


@router.post(
    "/",
    response_model=ChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Challenge created successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def create_challenge(
    request: Request,
    body: ChallengeCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> ChallengeResponse:
    """Create a library challenge that belongs to no station yet.

    Open to creators and organization admins. The station composition routes link
    the challenge to a station. The scoring rules must fit the challenge type,
    otherwise the route answers 422. It emits the audit event `challenge_created`.
    """
    client_ip = client_address(request)
    scoring_rules = prune_scoring_rules(body.challenge_type, body.scoring_rules)
    validate_scoring_rules(body.challenge_type, scoring_rules)

    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    try:
        challenge = await challenge_repo.create(
            challenge_type=body.challenge_type,
            title=body.title,
            instructions=sanitize_multilingual(body.instructions, media_prefix_for(org.slug)),
            scoring_rules=scoring_rules,
            time_estimate_minutes=body.time_estimate_minutes,
            suggested_solution=sanitize_optional_html(body.suggested_solution, media_prefix_for(org.slug)),
            created_by_user_id=current_user.id,
        )
        await db.commit()
        await db.refresh(challenge)
    except IntegrityError as e:
        await db.rollback()
        logger.exception("challenge_create_integrity_error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Challenge creation failed due to a data integrity error.",
        ) from e

    record_audit_event(
        event="challenge_created",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "challenge", "id": str(challenge.id)},
        organization_id=org.id,
    )

    return await _challenge_response(challenge_repo, challenge)


@router.put(
    "/{challenge_id}",
    response_model=ChallengeResponse,
    responses={
        200: {"description": "Challenge updated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Challenge not found", "model": ErrorResponse},
    },
)
async def update_challenge(
    challenge_id: UUID,
    request: Request,
    body: ChallengeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> ChallengeResponse:
    """Change the fields of a library challenge that the request sends.

    Open to creators and organization admins. The change shows in every station
    and rallye that links the challenge. Fields that the request leaves out stay
    as they are. An explicit null in `suggested_solution` clears the reference
    answer. A new challenge type discards the scoring rules of the old type. The
    scoring rules must fit the type, otherwise the route answers 422. It emits
    the audit event `challenge_updated` when a field changes.
    """
    client_ip = client_address(request)
    challenge_repo = ChallengeRepository(db, organization_id=org.id)

    challenge = await challenge_repo.get_by_id(challenge_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    effective_type = body.challenge_type if body.challenge_type is not None else challenge.challenge_type
    if body.scoring_rules is not None:
        # The creator sent rules for the new type. Prune them against the effective
        # type, so that rules meant for the old type cannot ride along.
        effective_scoring_rules = prune_scoring_rules(effective_type, body.scoring_rules)
    else:
        # Nothing but the stored rules to go on. A real type change discards the
        # old type's configuration, `auto_approve` included — see
        # prune_scoring_rules_for_type_change for why the shared flag goes too.
        effective_scoring_rules = prune_scoring_rules_for_type_change(
            challenge.challenge_type, effective_type, challenge.scoring_rules
        )
    validate_scoring_rules(effective_type, effective_scoring_rules)

    changes: dict[str, dict[str, Any]] = {}
    if body.challenge_type is not None and body.challenge_type != challenge.challenge_type:
        changes["challenge_type"] = {"from": challenge.challenge_type.value, "to": body.challenge_type.value}
    if body.title is not None and body.title != challenge.title:
        changes["title"] = {"from": challenge.title, "to": body.title}
    if body.instructions is not None and body.instructions != challenge.instructions:
        changes["instructions"] = {"from": challenge.instructions, "to": body.instructions}
    if effective_scoring_rules != challenge.scoring_rules:
        changes["scoring_rules"] = {"from": challenge.scoring_rules, "to": effective_scoring_rules}
    if body.time_estimate_minutes is not None and body.time_estimate_minutes != challenge.time_estimate_minutes:
        changes["time_estimate_minutes"] = {
            "from": challenge.time_estimate_minutes,
            "to": body.time_estimate_minutes,
        }
    # The reference answer is rich text (the editor works in all editorial fields). An explicit null clears it:
    # the editor sends null for an emptied field, and "not sent" alone keeps it.
    solution_sent = "suggested_solution" in body.model_fields_set
    new_solution = sanitize_optional_html(body.suggested_solution, media_prefix_for(org.slug))
    if solution_sent and new_solution != challenge.suggested_solution:
        changes["suggested_solution"] = {"from": challenge.suggested_solution, "to": new_solution}

    if not changes:
        return await _challenge_response(challenge_repo, challenge)

    update_kwargs: dict[str, Any] = {}
    if body.challenge_type is not None:
        update_kwargs["challenge_type"] = body.challenge_type
    if body.title is not None:
        update_kwargs["title"] = body.title
    if body.instructions is not None:
        update_kwargs["instructions"] = sanitize_multilingual(body.instructions, media_prefix_for(org.slug))
    if effective_scoring_rules != challenge.scoring_rules:
        update_kwargs["scoring_rules"] = effective_scoring_rules
    if body.time_estimate_minutes is not None:
        update_kwargs["time_estimate_minutes"] = body.time_estimate_minutes
    if solution_sent:
        update_kwargs["suggested_solution"] = new_solution

    try:
        updated = await challenge_repo.update(entity_id=challenge_id, **update_kwargs)
        await db.commit()
        await db.refresh(updated)
    except IntegrityError as e:
        await db.rollback()
        logger.exception("challenge_update_integrity_error", extra={"challenge_id": str(challenge_id)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Challenge update failed due to a data integrity error.",
        ) from e

    record_audit_event(
        event="challenge_updated",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "challenge", "id": str(challenge_id)},
        changes=changes,
        organization_id=org.id,
    )

    return await _challenge_response(challenge_repo, updated)


@router.delete(
    "/{challenge_id}",
    response_model=ChallengeUsage,
    responses={
        200: {"description": "Challenge soft-deleted; usage prior to deletion returned"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
        404: {"description": "Challenge not found", "model": ErrorResponse},
    },
)
async def delete_challenge(
    challenge_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> ChallengeUsage:
    """Archive a library challenge and unlink it from every station.

    Open to creators and organization admins. The challenge stays in the
    library as an archived item that an organization admin can restore. The
    response is the usage of the challenge as it was just before the archive,
    so that a client can show what the archive affected. It emits the audit event
    `challenge_deleted`.
    """
    client_ip = client_address(request)
    challenge_repo = ChallengeRepository(db, organization_id=org.id)

    challenge = await challenge_repo.get_by_id(challenge_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    usage = await challenge_repo.get_usage(challenge_id)

    rsc_repo = RallyeStationChallengeRepository(db, organization_id=org.id)
    await rsc_repo.delete_all_for_challenge(challenge_id)

    deleted = await challenge_repo.soft_delete(challenge_id)
    if not deleted:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Challenge not found or already deleted",
        )

    await db.commit()

    record_audit_event(
        event="challenge_deleted",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "challenge", "id": str(challenge_id), "title": challenge.title},
        organization_id=org.id,
    )

    return ChallengeUsage(**usage)


@router.post(
    "/{challenge_id}/duplicate",
    response_model=ChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "A complete copy of the challenge"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Requires creator role", "model": ErrorResponse},
        404: {"description": "Challenge not found", "model": ErrorResponse},
    },
)
async def duplicate_challenge(
    challenge_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("creator")),
) -> ChallengeResponse:
    """Copy a library challenge with every language, its scoring rules and its reference answer.

    Open to creators and organization admins. The copy is a new library item on no
    station. Each language of its title gets the suffix " (Kopie)" in German and
    " (Copy)" in every other language. It emits the audit event
    `challenge_duplicated`.
    """
    challenge_repo = ChallengeRepository(db, organization_id=org.id)
    source = await challenge_repo.get_by_id(challenge_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")
    challenge = await copy_challenge(db, org.id, source, current_user.id)
    await db.commit()
    await db.refresh(challenge)
    record_audit_event(
        event="challenge_duplicated",
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "challenge", "id": str(challenge.id), "source_id": str(challenge_id)},
        organization_id=org.id,
    )
    return await _challenge_response(challenge_repo, challenge)


@router.post(
    "/{challenge_id}/restore",
    response_model=ChallengeResponse,
    responses={
        200: {"description": "Challenge restored successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "Challenge not found", "model": ErrorResponse},
        409: {"description": "Challenge is not archived", "model": ErrorResponse},
    },
)
async def restore_challenge(
    challenge_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> ChallengeResponse:
    """Restore an archived library challenge into the library.

    Open to organization admins only. A challenge that is not archived answers 409.
    The restore does not recreate the station links that the archive removed. It
    emits the audit event `challenge_restored`.
    """
    client_ip = client_address(request)
    challenge_repo = ChallengeRepository(db, organization_id=org.id)

    challenge = await challenge_repo.get_by_id(challenge_id, include_deleted=True)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")
    if not challenge.is_deleted:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Challenge is not archived")

    await challenge_repo.restore(challenge_id)
    await db.commit()
    await db.refresh(challenge)

    record_audit_event(
        event="challenge_restored",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "challenge", "id": str(challenge_id), "title": challenge.title},
        organization_id=org.id,
    )

    return await _challenge_response(challenge_repo, challenge)


@router.delete(
    "/{challenge_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Challenge permanently deleted"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "Challenge not found", "model": ErrorResponse},
        409: {"description": "Challenge is not archived — archive it first", "model": ErrorResponse},
    },
)
async def permanently_delete_challenge(
    challenge_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> None:
    """Delete an archived library challenge for good.

    Open to organization admins only. A challenge must be archived first, otherwise
    the route answers 409. The challenge's runs keep their copy of it, which then
    refers to no library challenge. It emits the audit event
    `challenge_permanently_deleted`.
    """
    client_ip = client_address(request)
    challenge_repo = ChallengeRepository(db, organization_id=org.id)

    challenge = await challenge_repo.get_by_id(challenge_id, include_deleted=True)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")
    if not challenge.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Challenge is not archived — archive it first",
        )

    # Audit before the row is gone (title still readable)
    record_audit_event(
        event="challenge_permanently_deleted",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "challenge", "id": str(challenge_id), "title": challenge.title},
        organization_id=org.id,
    )

    await db.delete(challenge)
    await db.commit()
