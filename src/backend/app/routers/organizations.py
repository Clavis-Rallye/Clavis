"""
Organization CRUD endpoints.

Provides tenant-scoped organization operations with RBAC enforcement,
organization scoping, audit logging, and pagination.

URL patterns:
- POST /api/organizations: Refuses creation for every organization token (operator-only, coded 403 ORG_CREATE_INSTANCE_ONLY)
- GET/PUT/DELETE /api/orgs/{slug}: Tenant-scoped operations using slug

All endpoints require authentication and enforce role-based access control:
- List/Get: Any authenticated user can view their own organization
- Create: Operator-only; organization tokens receive 403
- Update: Only org_admin can update their organization
- Delete: Only org_admin can delete their organization

All modification operations emit audit events for security tracking.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.client_address import client_address
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.organization import Organization
from app.models.user import User
from app.repositories.organization import OrganizationRepository
from app.routers.schemas import (
    ErrorResponse,
    OrganizationDeleteResponse,
    OrganizationResponse,
    OrganizationUpdateRequest,
)
from app.services.audit import (
    audit_organization_deleted,
    audit_organization_updated,
)
from app.services.retention_config import (
    RetentionValidationError,
    SettingsNotStorableError,
    SettingsUnreadableError,
    validate_settings_overrides,
)
from app.services.richtext import media_prefix_for, sanitize_html
from app.services.ws_manager import manager

router = APIRouter()
slug_router = APIRouter()


def _sanitize_legal(text: str | None, org_slug: str, field: str) -> str | None:
    """One legal text through the rich-text allowlist. ``None`` stays ``None``. ``field`` names it in an error."""
    return None if text is None else sanitize_html(text, media_prefix_for(org_slug), loc=(field,))


def _to_response(org: Organization) -> OrganizationResponse:
    """The organization as the API shows it.

    The settings go out through the model's accessors: stored settings that
    cannot be read are an empty object and ``settings_unreadable``, never the
    marker of the decoder. A client would send the marker back with its next
    save.
    """
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        languages=org.languages,
        settings=org.readable_settings,
        settings_unreadable=org.settings_unreadable,
        legal_imprint=org.legal_imprint,
        legal_privacy=org.legal_privacy,
        legal_accessibility=org.legal_accessibility,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@slug_router.get(
    "",
    response_model=OrganizationResponse,
    responses={
        200: {"description": "Organization details"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Access denied (wrong organization)", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def get_organization(
    org: Organization = Depends(get_org_from_slug),
) -> OrganizationResponse:
    """Return the organization with the given slug.

    Open to every signed-in role, guests included, but only for the caller's own
    organization. The slug of another organization answers 403.
    """
    return _to_response(org)


@router.post(
    "",
    responses={
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {
            "description": "Organization creation is operator-only (error_code ORG_CREATE_INSTANCE_ONLY)",
            "model": ErrorResponse,
        },
    },
)
async def create_organization(
    current_user: User = Depends(require_role("guest")),  # noqa: ARG001 — auth gate only
) -> None:
    """Refuse to create an organization. Only the instance operator creates one.

    Every signed-in caller, an organization admin included, gets 403 with the
    error code `ORG_CREATE_INSTANCE_ONLY`. The operator creates organizations with
    `POST /api/instance/organizations`. This route exists so that a client gets
    this coded refusal and not a 404.
    """
    raise CodedHTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Organization creation is reserved for the instance operator; "
        "organization tokens (org_admin included) cannot create organizations.",
        error_code="ORG_CREATE_INSTANCE_ONLY",
    )


@slug_router.put(
    "",
    response_model=OrganizationResponse,
    responses={
        200: {"description": "Organization updated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
        409: {"description": "Conflict (slug already exists)", "model": ErrorResponse},
    },
)
async def update_organization(
    request: Request,
    body: OrganizationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> OrganizationResponse:
    """Change the name, slug, languages, settings or legal texts of the organization.

    Open to organization admins, for their own organization. Fields that the
    request leaves out stay as they are. An explicit null in a legal text clears
    it, and the frontend falls back to its bundled text. A new slug that another
    organization uses answers 409. The settings are replaced as a whole. Invalid
    settings, such as a retention period outside its bounds, answer 422. Settings
    that are stored but unreadable can be repaired only by sending whole new
    settings, otherwise the route answers 409 `ORG_SETTINGS_UNREADABLE`. It emits
    the audit event `organization_updated` with the changes.
    """
    client_ip = client_address(request)
    org_repo = OrganizationRepository(db)

    # A retention override smuggled in through the general settings dict obeys
    # the same bounds (not shorter than the backup rotation window) as the
    # dedicated retention endpoint. An out-of-range period must never reach
    # ``settings``, where the sweep would later trip over it (OverflowError in
    # timedelta) for every organization.
    if body.settings is not None:
        try:
            validate_settings_overrides(body.settings)
        except SettingsUnreadableError as exc:
            # The marker of the decoder for stored settings nobody could read.
            # No response shows it, and nobody may store it as the settings.
            # Whole new settings without it are the repair.
            raise CodedHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
                error_code="ORG_SETTINGS_UNREADABLE",
            ) from exc
        except SettingsNotStorableError as exc:
            # A value the row could not be read back from, or that the
            # database refuses: nested too deep, no JSON number, no jsonb
            # string. Refused here, before the write.
            raise CodedHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
                error_code="ORG_SETTINGS_NOT_STORABLE",
            ) from exc
        except RetentionValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

    # Stored settings that cannot be read are repaired by whole new settings
    # only. A save that carries none would leave the row as it is and answer as
    # if all were well, so it is refused.
    # An object, also the empty one, is a whole replacement.
    if org.settings_unreadable and body.settings is None:
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The stored settings of this organization cannot be read and must be replaced whole.",
            error_code="ORG_SETTINGS_UNREADABLE",
        )

    changes: dict[str, dict[str, Any]] = {}
    if body.name is not None and body.name != org.name:
        changes["name"] = {"from": org.name, "to": body.name}
    if body.slug is not None and body.slug != org.slug:
        changes["slug"] = {"from": org.slug, "to": body.slug}
    if body.languages is not None and body.languages != org.languages:
        changes["languages"] = {"from": org.languages, "to": body.languages}
    # Whole settings replace stored ones that cannot be read, also when they
    # equal the empty object such settings read as: that write is the repair.
    if body.settings is not None and (org.settings_unreadable or body.settings != org.readable_settings):
        changes["settings"] = {"from": org.readable_settings, "to": body.settings}

    # Legal-text fields (nullable): detect changes here too, before the
    # "nothing changed" early return below. The legal tab sends only these
    # fields, so without this a legal-only edit would be silently dropped
    # (200 OK, no DB write).
    legal_fields_in_payload = body.model_fields_set & {
        "legal_imprint",
        "legal_privacy",
        "legal_accessibility",
    }
    # A legal text is one HTML string, not a text per language. The
    # multilingual sanitiser would call .items() on it and end in a 500.
    legal_values = {field: _sanitize_legal(getattr(body, field), org.slug, field) for field in legal_fields_in_payload}
    for field in legal_fields_in_payload:
        if getattr(org, field) != legal_values[field]:
            changes[field] = {"from": getattr(org, field), "to": legal_values[field]}

    if not changes:
        return _to_response(org)

    if body.slug is not None and body.slug != org.slug:
        existing_org = await org_repo.get_by_slug(body.slug)
        if existing_org:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Organization slug '{body.slug}' already exists",
            )

    # Update organization. Pass only the fields the client actually sent —
    # OrganizationUpdateRequest makes every field Optional, and the repo
    # writes every kwarg it receives, so unfiltered None values would null
    # the unedited columns (e.g. slug → NotNullViolationError).
    update_kwargs: dict[str, Any] = {
        k: v
        for k, v in {
            "name": body.name,
            "slug": body.slug,
            "languages": body.languages,
            "settings": body.settings,
        }.items()
        if v is not None
    }
    # Legal-text fields are nullable. Honour any key present in the payload
    # (Pydantic v2 `model_fields_set`) even when the value is None: that
    # clears the override and lets the frontend fall back to its bundled
    # copy. Change detection happened above, before the early return. Here
    # we only stage the writes.
    for field in legal_fields_in_payload:
        update_kwargs[field] = legal_values[field]
    try:
        updated_org = await org_repo.update(entity_id=org.id, **update_kwargs)
        if updated_org is None:
            # Deleted between the dependency's read and this write.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        await db.commit()
        await db.refresh(updated_org)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization update failed: {str(e)}",
        ) from e

    audit_organization_updated(
        actor_user=current_user,
        actor_ip=client_ip,
        organization_id=org.id,
        changes=changes,
    )

    return _to_response(updated_org)


@slug_router.delete(
    "",
    response_model=OrganizationDeleteResponse,
    responses={
        200: {"description": "Organization deleted successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions or wrong organization", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def delete_organization(
    request: Request,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> OrganizationDeleteResponse:
    """Delete the organization. Its record stays in the database, marked as deleted.

    Open to organization admins, for their own organization. The accounts of the
    organization lose their open WebSocket connections at once. It emits the
    audit event `organization_deleted`.
    """
    client_ip = client_address(request)
    org_repo = OrganizationRepository(db)

    deleted = await org_repo.soft_delete(org.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found or already deleted",
        )

    await db.commit()

    # No account of a deleted organization keeps an open socket. The WebSocket
    # refuses them from now on.
    member_ids = (await db.execute(select(User.id).where(User.organization_id == org.id))).scalars().all()
    await manager.close_accounts(member_ids)

    audit_organization_deleted(
        actor_user=current_user,
        actor_ip=client_ip,
        organization_id=org.id,
        organization_name=org.name,
    )

    return OrganizationDeleteResponse(message="Organization deleted successfully")
