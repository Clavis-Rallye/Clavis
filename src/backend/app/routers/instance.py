"""
The operator's endpoints above all organizations, guarded by ``require_instance_admin``.

- ``router``, mounted at ``/api/instance/organizations``: list, create, edit, archive and restore an
  organization, schedule its hard deletion, and invite an admin to replace a lost one.
- ``operator_router``, mounted at ``/api/instance``: the instance audit log, read from the same log as
  the organization log viewer in ``routers/admin.py``, and the instance legal texts, stored in the
  single-row ``instance_settings`` table. Their public counterpart is ``GET /api/public/legal/{kind}``
  in ``routers/public.py``.

Every audit event of this router carries no organization id, because the operator acts on the instance
level. An event on an organization names its target slug. No event holds the changed values of an
organization, the text of a legal text or an invited address. The raw token of an invitation exists only
in the mail, never in a response, log or audit record. ``instance.org_deleted`` comes from the daily
sweep (``app/services/org_deletion.py``), not from this router.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_instance_admin
from app.auth.instance import actor_from_instance_admin
from app.client_address import client_address
from app.config import TILE_URL_DEFAULT, get_settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.instance_admin import InstanceAdmin
from app.models.instance_setting import InstanceSetting
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.repositories.instance_setting import InstanceSettingRepository
from app.repositories.invitation import InvitationRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.user import UserRepository
from app.routers.admin import _parse_timestamp, _read_log_entries, _sort_key, get_log_dir
from app.routers.schemas import (
    ErrorResponse,
    InstanceAuditLogEntryResponse,
    InstanceAuditLogListResponse,
    InstanceLegalResponse,
    InstanceLegalUpdateRequest,
    InstanceOrgAdminInvitationRequest,
    InstanceOrgAdminInvitationResponse,
    InstanceOrgAdminListResponse,
    InstanceOrgAdminSummary,
    InstanceOrganizationActionResponse,
    InstanceOrganizationCreateRequest,
    InstanceOrganizationDeletionRequest,
    InstanceOrganizationDeletionResponse,
    InstanceOrganizationListItem,
    InstanceOrganizationListResponse,
    InstanceOrganizationResponse,
    InstanceOrganizationUpdateRequest,
)
from app.services import mail
from app.services.audit import record_audit_event
from app.services.geo_search import index_summary
from app.services.mail.templates import SUPPORTED_LANGUAGES, normalize_language
from app.services.org_deletion import compute_deletion_due_at
from app.services.retention_config import CONFIGURABLE_CLASSES, instance_default_days
from app.services.richtext import sanitize_html
from app.services.ws_manager import manager

router = APIRouter()

# Mounted separately in main.py at /api/instance, because `router` is mounted at
# /api/instance/organizations and cannot reach a sibling path such as /api/instance/audit.
operator_router = APIRouter()

# No upload route serves the instance texts, unlike the legal texts of an organization
# (/api/orgs/{slug}/media/). This prefix never matches a real src, so sanitize_html strips every <img>.
_LEGAL_MEDIA_PREFIX = "/api/instance/media/"

_LEGAL_FIELD_TO_KIND = {
    "legal_imprint": "imprint",
    "legal_privacy": "privacy",
    "legal_accessibility": "accessibility",
}

# The operator console shows them all in one view. A deployment has tens of organizations,
# not tens of thousands.
_LIST_LIMIT = 10_000


def _to_response(org: Organization) -> InstanceOrganizationResponse:
    return InstanceOrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        content_languages=org.languages,
        created_at=org.created_at,
        is_archived=org.archived_at is not None,
    )


@router.get(
    "",
    response_model=InstanceOrganizationListResponse,
    responses={
        200: {"description": "Every organization with counts"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
    },
)
async def list_organizations(
    _request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceOrganizationListResponse:
    """List every organization with its counts and its last activity.

    Open to the operator (instance admin) only.

    Each item has the name, slug, content languages, archive state, the counts
    of users, organization admins, pending admin invitations, rallyes, runs and
    active runs, the time of the last activity and, for an organization with a
    scheduled deletion, the time it is due. The items carry no user, team or
    submission data, only counts and timestamps.
    """
    repo = OrganizationRepository(db)
    orgs = await repo.list(limit=_LIST_LIMIT)

    # One grouped count query per kind over all organizations.
    user_counts = await repo.count_users_by_organization()
    rallye_counts = await repo.count_rallyes_by_organization()
    run_counts = await repo.count_runs_by_organization()
    org_admin_counts = await repo.count_org_admins_by_organization()
    pending_admin_invitation_counts = await InvitationRepository(
        db
    ).count_pending_org_admin_invitations_by_organization()
    active_run_counts = await repo.count_active_runs_by_organization()
    last_activity_by_org = await repo.last_activity_by_organization()

    items = [
        InstanceOrganizationListItem(
            name=org.name,
            slug=org.slug,
            content_languages=org.languages,
            created_at=org.created_at,
            is_archived=org.archived_at is not None,
            user_count=user_counts.get(org.id, 0),
            rallye_count=rallye_counts.get(org.id, 0),
            run_count=run_counts.get(org.id, 0),
            org_admin_count=org_admin_counts.get(org.id, 0),
            pending_admin_invitation_count=pending_admin_invitation_counts.get(org.id, 0),
            active_run_count=active_run_counts.get(org.id, 0),
            last_activity_at=last_activity_by_org.get(org.id),
            deletion_due_at=compute_deletion_due_at(org.deletion_requested_at),
        )
        for org in orgs
    ]
    return InstanceOrganizationListResponse(items=items, total=len(items))


async def _org_admins(db: AsyncSession, organization_id: UUID) -> list[User]:
    """The organization's admins, deleted accounts excluded."""
    result = await db.execute(
        select(User)
        .where(
            User.organization_id == organization_id,
            User.role == UserRole.ORG_ADMIN,
            User.is_deleted.is_(False),
        )
        .order_by(User.display_name, User.email)
    )
    return list(result.scalars().all())


async def _validated_replacements(db: AsyncSession, organization_id: UUID, ids: list[UUID]) -> list[str] | None:
    """The admin ids an invitation replaces, each checked to be an admin of this org."""
    if not ids:
        return None
    admins = {a.id for a in await _org_admins(db, organization_id)}
    unknown = [str(i) for i in ids if i not in admins]
    if unknown:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only current admins of this organization can be replaced",
            error_code="REPLACE_NOT_ORG_ADMIN",
        )
    return sorted({str(i) for i in ids})


async def _refuse_address_with_account(db: AsyncSession, email: str) -> None:
    """Refuse an invitation to an address that already belongs to an account.

    Accepting it can never work: the accept step answers 409, because an
    address is unique across every organization, soft-deleted accounts
    included. Refused here, the operator learns it at once instead of the
    invited person meeting a dead end. The answer names no organization and no
    person.
    """
    if await UserRepository.get_by_email_including_deleted(db, email):
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{email}' already belongs to an account",
            error_code="EMAIL_HAS_ACCOUNT",
        )


@router.post(
    "",
    response_model=InstanceOrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Organization created, first-admin invitation mail queued"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
        409: {
            "description": "Slug taken, the email already has a pending invitation, or it belongs to an account "
            "(error_code ORG_SLUG_TAKEN / EMAIL_HAS_PENDING_INVITATION / EMAIL_HAS_ACCOUNT)",
            "model": ErrorResponse,
        },
    },
)
async def create_organization(
    request: Request,
    body: InstanceOrganizationCreateRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceOrganizationResponse:
    """Create an organization and invite its first organization admin.

    Open to the operator (instance admin) only.

    The invitation mail goes out in the background, in the first content
    language of the organization. The operator is not a member of the
    organization, so the invitation names the instance brand as the inviter.
    Organization and invitation are saved together. A refused invitation leaves
    no organization behind.

    Refuses with 409 `ORG_SLUG_TAKEN` for a slug that exists, deleted
    organizations included, 409 `EMAIL_HAS_ACCOUNT` for an address that belongs
    to an account, and 409 `EMAIL_HAS_PENDING_INVITATION` for an address with
    an open invitation.

    Emits the audit event `instance.org_created` with the target slug and no
    organization id.
    """
    settings = get_settings()
    repo = OrganizationRepository(db)

    await _refuse_address_with_account(db, body.first_admin_email)

    if await repo.slug_taken(body.slug):
        # Soft-deleted organizations count: the unique constraint on the slug spans deleted rows.
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization slug '{body.slug}' already exists",
            error_code="ORG_SLUG_TAKEN",
        )

    try:
        org = await repo.create(
            name=body.name,
            slug=body.slug,
            languages=body.content_languages,
        )
    except IntegrityError as exc:
        # A concurrent create took the slug after the check. The unique constraint decides, and
        # nothing is committed yet, so no organization and no invitation are left behind.
        await db.rollback()
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization slug '{body.slug}' already exists",
            error_code="ORG_SLUG_TAKEN",
        ) from exc

    try:
        invitation, raw_token = await InvitationRepository(db).create(
            organization_id=org.id,
            email=body.first_admin_email,
            role=UserRole.ORG_ADMIN,
            invited_by_user_id=None,
            invited_by_name=settings.brand_name,
            expiry_days=settings.mail.invitation_expiry_days,
            language=normalize_language(body.content_languages[0]),
        )
    except IntegrityError as exc:
        # The partial unique index on open invitation emails caught a taken address. The rollback
        # removes the organization too, so no organization is left without its first admin invitation.
        await db.rollback()
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{body.first_admin_email}' already has a pending invitation",
            error_code="EMAIL_HAS_PENDING_INVITATION",
        ) from exc

    await db.commit()
    await db.refresh(org)

    record_audit_event(
        event="instance.org_created",
        actor=actor_from_instance_admin(admin, client_address(request)),
        organization_id=None,
        target_slug=org.slug,
    )

    # Composed and sent as in routers/invitations.py. The raw token goes into the mail only.
    message = mail.compose_invitation(
        to=invitation.email,
        language=invitation.language,
        organization_name=org.name,
        role=invitation.role.value,
        inviter_name=invitation.invited_by_name,
        token=raw_token,
    )
    mail.send_in_background(background, message)

    return _to_response(org)


async def _get_org_by_slug(repo: OrganizationRepository, slug: str) -> Organization:
    org = await repo.get_by_slug(slug)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    return org


@router.patch(
    "/{slug}",
    response_model=InstanceOrganizationResponse,
    responses={
        200: {"description": "Organization updated"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
        422: {
            "description": "Blank name, empty language list or an unsupported language code "
            "(error_code ORG_NAME_BLANK / ORG_LANGUAGES_EMPTY / ORG_LANGUAGE_UNSUPPORTED)",
            "model": ErrorResponse,
        },
    },
)
async def update_organization(
    request: Request,
    slug: str,
    body: InstanceOrganizationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceOrganizationResponse:
    """Change the name and the content languages of an organization.

    Open to the operator (instance admin) only.

    Both fields are optional. The slug never changes: a slug in the body is
    ignored. Works for archived organizations too.

    Refuses with 422 and changes nothing for a blank name (`ORG_NAME_BLANK`),
    an empty language list (`ORG_LANGUAGES_EMPTY`) or a language the app does
    not support (`ORG_LANGUAGE_UNSUPPORTED`). A language the organization
    already carries stays valid. A request that changes no value answers 200
    and emits no event.

    Emits the audit event `instance.org_updated` with the target slug and the
    names of the changed fields, never their values.
    """
    repo = OrganizationRepository(db)
    org = await _get_org_by_slug(repo, slug)

    if body.name is not None and not body.name.strip():
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Organization name must not be blank",
            error_code="ORG_NAME_BLANK",
        )

    if body.content_languages is not None:
        if not body.content_languages:
            raise CodedHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="content_languages must not be empty",
                error_code="ORG_LANGUAGES_EMPTY",
            )
        # Only a language the operator adds must be one the app supports. The organization's admin
        # may enable any code, and the edit dialog sends the whole list back, so a stricter check
        # would block every rename of an organization with such a code.
        unsupported = [
            lang
            for lang in body.content_languages
            if lang not in SUPPORTED_LANGUAGES and lang not in (org.languages or [])
        ]
        if unsupported:
            raise CodedHTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unsupported language code(s): {', '.join(unsupported)}",
                error_code="ORG_LANGUAGE_UNSUPPORTED",
            )

    changes: dict[str, Any] = {}
    if body.name is not None and body.name != org.name:
        changes["name"] = body.name
    if body.content_languages is not None and body.content_languages != org.languages:
        changes["languages"] = body.content_languages

    if not changes:
        return _to_response(org)

    updated_org = await repo.update(org.id, **changes)
    assert updated_org is not None
    await db.commit()
    await db.refresh(updated_org)

    record_audit_event(
        event="instance.org_updated",
        actor=actor_from_instance_admin(admin, client_address(request)),
        organization_id=None,
        target_slug=org.slug,
        changed_fields=sorted(changes.keys()),
    )

    return _to_response(updated_org)


@router.post(
    "/{slug}/archive",
    response_model=InstanceOrganizationActionResponse,
    responses={
        200: {"description": "Organization archived (idempotent)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def archive_organization(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceOrganizationActionResponse:
    """Archive an organization and shut its members out.

    Open to the operator (instance admin) only.

    Its members can no longer sign in (403 `ORG_ARCHIVED` on every login path
    and on open sessions), their open WebSockets close, and its runs leave the
    public endpoints. Its data stays. Archiving an archived organization changes
    nothing and emits no event.

    Also clears a scheduled deletion, which only an archived organization can
    carry.

    Emits the audit event `instance.org_archived` with the target slug and no
    organization id.
    """
    repo = OrganizationRepository(db)
    org = await _get_org_by_slug(repo, slug)

    if org.archived_at is None:
        await repo.update(org.id, archived_at=datetime.now(UTC), deletion_requested_at=None)
        await db.commit()
        # Its members keep no open socket.
        member_ids = (await db.execute(select(User.id).where(User.organization_id == org.id))).scalars().all()
        for member_id in member_ids:
            await manager.close_channel(f"user:{member_id}", code=4000, reason="session revoked")
        record_audit_event(
            event="instance.org_archived",
            actor=actor_from_instance_admin(admin, client_address(request)),
            organization_id=None,
            target_slug=org.slug,
        )

    return InstanceOrganizationActionResponse(
        slug=org.slug,
        is_archived=True,
        message="Organization archived",
    )


@router.post(
    "/{slug}/restore",
    response_model=InstanceOrganizationActionResponse,
    responses={
        200: {"description": "Organization restored (idempotent)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def restore_organization(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceOrganizationActionResponse:
    """Restore an archived organization: sign-in, sessions and public visibility return.

    Open to the operator (instance admin) only.

    Also cancels a scheduled deletion. If the daily deletion sweep has already
    deleted the organization, the answer is 404. Restoring an active
    organization changes nothing and emits no event.

    Emits the audit event `instance.org_restored`, and
    `instance.org_deletion_cancelled` when a scheduled deletion was cleared.
    Both carry the target slug and no organization id.
    """
    repo = OrganizationRepository(db)
    org = await _get_org_by_slug(repo, slug)
    org_id = org.id
    org_slug = org.slug  # captured now: a rollback below expires `org`'s attributes

    locked_row = (
        await db.execute(
            select(Organization.archived_at, Organization.deletion_requested_at)
            .where(Organization.id == org_id)
            .with_for_update()
        )
    ).one_or_none()

    if locked_row is None:
        # Gone since the lookup above, for example because the deletion sweep just committed.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    archived_at, deletion_requested_at = locked_row

    if archived_at is not None:
        had_scheduled_deletion = deletion_requested_at is not None
        # A restore never leaves a scheduled deletion pending.
        await repo.update(org_id, archived_at=None, deletion_requested_at=None)
        await db.commit()

        actor = actor_from_instance_admin(admin, client_address(request))
        record_audit_event(
            event="instance.org_restored",
            actor=actor,
            organization_id=None,
            target_slug=org_slug,
        )
        if had_scheduled_deletion:
            record_audit_event(
                event="instance.org_deletion_cancelled",
                actor=actor,
                organization_id=None,
                target_slug=org_slug,
            )
    else:
        await db.rollback()  # idempotent no-op: release the lock cleanly

    return InstanceOrganizationActionResponse(
        slug=org_slug,
        is_archived=False,
        message="Organization restored",
    )


@router.post(
    "/{slug}/deletion",
    response_model=InstanceOrganizationDeletionResponse,
    responses={
        200: {"description": "Deletion scheduled 30 days ahead"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
        409: {"description": "Organization is not archived (error_code ORG_NOT_ARCHIVED)", "model": ErrorResponse},
        422: {
            "description": "confirm_slug does not match the organization's slug (error_code CONFIRMATION_MISMATCH)",
            "model": ErrorResponse,
        },
    },
)
async def request_organization_deletion(
    request: Request,
    slug: str,
    body: InstanceOrganizationDeletionRequest,
    db: AsyncSession = Depends(get_db),
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceOrganizationDeletionResponse:
    """Schedule the hard deletion of an archived organization.

    Open to the operator (instance admin) only.

    The daily deletion sweep deletes the organization 30 days after this
    request, and the response names that time. A restore before then cancels
    the deletion.

    Refuses and changes nothing with 409 `ORG_NOT_ARCHIVED` for an
    organization that is not archived, also when a restore came in between, and
    with 422 `CONFIRMATION_MISMATCH` when `confirm_slug` is not the slug of the
    organization.

    Emits the audit event `instance.org_deletion_requested` with the target
    slug and no organization id.
    """
    repo = OrganizationRepository(db)
    org = await _get_org_by_slug(repo, slug)

    locked_archived_at = (
        await db.execute(select(Organization.archived_at).where(Organization.id == org.id).with_for_update())
    ).one_or_none()

    if locked_archived_at is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    if locked_archived_at[0] is None:
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization '{slug}' is not archived",
            error_code="ORG_NOT_ARCHIVED",
        )

    if body.confirm_slug != org.slug:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Confirmation slug does not match the organization's slug",
            error_code="CONFIRMATION_MISMATCH",
        )

    now = datetime.now(UTC)
    updated_org = await repo.update(org.id, deletion_requested_at=now)
    assert updated_org is not None
    await db.commit()
    await db.refresh(updated_org)

    record_audit_event(
        event="instance.org_deletion_requested",
        actor=actor_from_instance_admin(admin, client_address(request)),
        organization_id=None,
        target_slug=org.slug,
    )

    deletion_due_at = compute_deletion_due_at(updated_org.deletion_requested_at)
    assert deletion_due_at is not None
    return InstanceOrganizationDeletionResponse(
        slug=org.slug,
        deletion_due_at=deletion_due_at,
        message="Organization deletion scheduled",
    )


@router.get(
    "/{slug}/admins",
    response_model=InstanceOrgAdminListResponse,
    responses={
        200: {"description": "The organization's admins, to pick whom an invitation replaces"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def list_org_admins(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceOrgAdminListResponse:
    """List the admins of an organization, to pick whom an invitation replaces.

    Open to the operator (instance admin) only.

    This is the one place where the operator sees people. The operator needs
    it when no admin of the organization can act. Each item has only the email
    address, the name, the active state and the last sign-in. Deleted accounts
    are left out.

    Emits the audit event `instance.org_admins_viewed` with the target slug,
    the number of admins and no organization id.
    """
    org = await _get_org_by_slug(OrganizationRepository(db), slug)
    admins = await _org_admins(db, org.id)
    record_audit_event(
        event="instance.org_admins_viewed",
        actor=actor_from_instance_admin(admin, client_address(request)),
        organization_id=None,
        target_slug=org.slug,
        count=len(admins),
    )
    return InstanceOrgAdminListResponse(
        items=[
            InstanceOrgAdminSummary(
                id=a.id, email=a.email, display_name=a.display_name, is_active=a.is_active, last_login=a.last_login
            )
            for a in admins
        ]
    )


@router.post(
    "/{slug}/admin-invitations",
    response_model=InstanceOrgAdminInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Invitation created (or renewed) and mail queued"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
        409: {
            "description": "Organization archived, the email already has a pending invitation elsewhere, or it "
            "belongs to an account (error_code ORG_ARCHIVED / EMAIL_HAS_PENDING_INVITATION / EMAIL_HAS_ACCOUNT)",
            "model": ErrorResponse,
        },
    },
)
async def invite_org_admin(
    request: Request,
    slug: str,
    body: InstanceOrgAdminInvitationRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceOrgAdminInvitationResponse:
    """Invite an organization admin, to replace admins who cannot act.

    Open to the operator (instance admin) only.

    The invitation mail goes out in the background and names the instance brand
    as the inviter. `replace_admin_ids` lists the admins the invitation
    replaces. An open invitation for the same address in this organization is
    renewed with a fresh token instead of duplicated.

    Refuses with 409 `ORG_ARCHIVED` for an archived organization,
    409 `EMAIL_HAS_ACCOUNT` for an address that belongs to an account,
    409 `EMAIL_HAS_PENDING_INVITATION` for an address with an open invitation
    in another organization, and 422 `REPLACE_NOT_ORG_ADMIN` for an id that is
    not an admin of this organization.

    Emits the audit event `instance.org_admin_invited` with the target slug,
    the number of replaced admins and no organization id. The event never
    holds the email address.
    """
    settings = get_settings()
    repo = OrganizationRepository(db)
    org = await _get_org_by_slug(repo, slug)

    if org.archived_at is not None:
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization '{slug}' is archived",
            error_code="ORG_ARCHIVED",
        )

    await _refuse_address_with_account(db, body.email)
    replaces = await _validated_replacements(db, org.id, body.replace_admin_ids)

    invitation_repo = InvitationRepository(db)
    language = normalize_language(body.language) if body.language else normalize_language(org.languages[0])
    existing = await invitation_repo.get_open_for_email(body.email)

    if existing is not None and existing.organization_id != org.id:
        # The address is already spoken for in another organization.
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{body.email}' already has a pending invitation",
            error_code="EMAIL_HAS_PENDING_INVITATION",
        )

    if existing is not None:
        existing.role = UserRole.ORG_ADMIN
        existing.language = language
        existing.invited_by_user_id = None
        existing.invited_by_name = settings.brand_name
        raw_token = await invitation_repo.rotate_token(existing, settings.mail.invitation_expiry_days)
        existing.replaces_user_ids = replaces
        invitation = existing
    else:
        try:
            invitation, raw_token = await invitation_repo.create(
                organization_id=org.id,
                email=body.email,
                role=UserRole.ORG_ADMIN,
                invited_by_user_id=None,
                invited_by_name=settings.brand_name,
                expiry_days=settings.mail.invitation_expiry_days,
                language=language,
            )
            invitation.replaces_user_ids = replaces
        except IntegrityError as exc:
            # Two operator invites for the same address at once. The partial unique index caught the loser.
            await db.rollback()
            raise CodedHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' already has a pending invitation",
                error_code="EMAIL_HAS_PENDING_INVITATION",
            ) from exc

    await db.commit()
    await db.refresh(invitation)

    record_audit_event(
        event="instance.org_admin_invited",
        actor=actor_from_instance_admin(admin, client_address(request)),
        organization_id=None,
        target_slug=org.slug,
        # How many admins the invited person will replace, never who.
        replaces_count=len(replaces or []),
    )

    # Composed and sent as in create_organization. The raw token goes into the mail only.
    message = mail.compose_invitation(
        to=invitation.email,
        language=invitation.language,
        organization_name=org.name,
        role=invitation.role.value,
        inviter_name=invitation.invited_by_name,
        token=raw_token,
    )
    mail.send_in_background(background, message)

    return InstanceOrgAdminInvitationResponse(
        id=invitation.id,
        email=invitation.email,
        language=invitation.language,
        invited_by_name=invitation.invited_by_name,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        mail_delivered=mail.is_enabled(),
    )


def _to_audit_log_response(entry: dict[str, Any]) -> InstanceAuditLogEntryResponse:
    actor = entry.get("actor")
    actor = actor if isinstance(actor, dict) else {}
    target = entry.get("target")
    target = target if isinstance(target, dict) else {}
    # An operator account as the target: the changes of the server command
    # and a failed sign-in name the account they concern.
    target_email = target.get("email") if target.get("entity") == "instance_admin" else None
    return InstanceAuditLogEntryResponse(
        timestamp=_parse_timestamp(entry.get("timestamp")),
        event=entry.get("event"),
        operator_id=actor.get("instance_admin_id"),
        operator_email=actor.get("email"),
        target_slug=entry.get("target_slug"),
        target_email=target_email,
        actor_type=actor.get("type"),
    )


@operator_router.get(
    "/audit",
    response_model=InstanceAuditLogListResponse,
    responses={
        200: {"description": "Paginated instance.* audit events, newest first"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
    },
)
async def list_instance_audit_log(
    event: str | None = Query(None, description="Filter by exact event type, e.g. instance.org_created"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=200, description="Maximum entries to return"),
    _admin: InstanceAdmin = Depends(require_instance_admin),
    log_dir: str = Depends(get_log_dir),
) -> InstanceAuditLogListResponse:
    """List the instance audit events, newest first.

    Open to the operator (instance admin) only.

    Shows only events whose name starts with `instance.`, optionally one exact
    event. Events of an organization never appear here, and instance events
    never appear in the log of an organization.
    """
    raw_entries = _read_log_entries(log_dir, ["audit"])

    filtered = [
        entry
        for entry in raw_entries
        if isinstance(entry.get("event"), str)
        and entry["event"].startswith("instance.")
        and (event is None or entry["event"] == event)
    ]
    filtered.sort(key=_sort_key, reverse=True)

    total = len(filtered)
    page = filtered[offset : offset + limit]

    return InstanceAuditLogListResponse(
        items=[_to_audit_log_response(e) for e in page],
        total=total,
        offset=offset,
        limit=limit,
    )


async def _address_search_sources(db: AsyncSession) -> list[Literal["local", "nominatim", "photon"]]:
    """Where the address search takes its hits from: the same settings and the
    same index query /api/geocode/config answers from, so the
    operator's privacy template states what the installation does. The config
    endpoint itself rejects an instance-only operator session, so the legal
    answer carries the result instead."""
    settings = get_settings().geocoder
    size, _middle = await index_summary(db)
    sources: list[Literal["local", "nominatim", "photon"]] = ["local"] if size > 0 else []
    if settings.upstream != "none":
        sources.append(settings.upstream)
    return sources


async def _legal_response(db: AsyncSession, setting: InstanceSetting | None) -> InstanceLegalResponse:
    log_settings = get_settings().logging
    saved_backup_days = setting.backup_retention_days if setting is not None else None
    sources = await _address_search_sources(db)
    return InstanceLegalResponse(
        legal_imprint=setting.legal_imprint if setting is not None else None,
        legal_privacy=setting.legal_privacy if setting is not None else None,
        legal_accessibility=setting.legal_accessibility if setting is not None else None,
        updated_at=setting.updated_at if setting is not None else None,
        backup_retention_days=(
            saved_backup_days if saved_backup_days is not None else get_settings().retention.backup_rotation_days
        ),
        backup_retention_source="instance" if saved_backup_days is not None else "default",
        retention_days={dc.value: instance_default_days(dc) for dc in CONFIGURABLE_CLASSES},
        technical_log_days=max(
            log_settings.app_log_retention_days,
            log_settings.error_log_retention_days,
            log_settings.access_log_retention_days,
        ),
        audit_log_days=log_settings.audit_log_retention_days,
        abandoned_run_days=get_settings().retention.abandoned_run_days,
        result_link_days=get_settings().retention.result_link_days,
        map_tile_url=get_settings().geocoder.map_tile_url or TILE_URL_DEFAULT,
        address_search_sources=sources,
    )


def _sanitize_legal_text(value: str | None) -> str | None:
    if value is None:
        return None
    return sanitize_html(value, _LEGAL_MEDIA_PREFIX)


@operator_router.get(
    "/legal",
    response_model=InstanceLegalResponse,
    responses={
        200: {"description": "The instance's own legal texts (null fields before the first save)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
    },
)
async def get_instance_legal(
    db: AsyncSession = Depends(get_db),
    _admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceLegalResponse:
    """Read the imprint, privacy and accessibility texts of the instance.

    Open to the operator (instance admin) only.

    Before the first save, the three texts and `updated_at` are null. The
    response also carries the retention periods and the address search sources
    that the privacy text states. They are read-only here, except
    `backup_retention_days`.
    """
    setting = await InstanceSettingRepository(db).get()
    return await _legal_response(db, setting)


@operator_router.put(
    "/legal",
    response_model=InstanceLegalResponse,
    responses={
        200: {"description": "Legal texts saved (or unchanged, if nothing differed)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
    },
)
async def update_instance_legal(
    request: Request,
    body: InstanceLegalUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceLegalResponse:
    """Save the imprint, privacy and accessibility texts of the instance.

    Open to the operator (instance admin) only.

    Only the fields in the body change. A field set to `null` is cleared, and
    an omitted field stays as it was. Each text is sanitized with the same
    allowlist as the legal texts of an organization, and images are removed.
    `backup_retention_days` is saved the same way, and `null` returns it to the
    default of the instance.

    Emits the audit event `instance.legal_updated` when something changed. It
    has no organization id, the changed kinds such as `["imprint"]` and never
    their text. A change of the backup period adds its old and new value.
    """
    repo = InstanceSettingRepository(db)
    setting = await repo.get()

    fields_in_payload = body.model_fields_set & set(_LEGAL_FIELD_TO_KIND)
    new_values: dict[str, Any] = {field: _sanitize_legal_text(getattr(body, field)) for field in fields_in_payload}

    changed_fields = [
        field
        for field in fields_in_payload
        if new_values[field] != (getattr(setting, field) if setting is not None else None)
    ]

    # The backup period is saved with the legal texts, because the privacy text states it. An omitted
    # field stays as it was, and an explicit null reverts to BACKUP_ROTATION_DAYS.
    backup_before = setting.backup_retention_days if setting is not None else None
    backup_changed = "backup_retention_days" in body.model_fields_set and body.backup_retention_days != backup_before
    if backup_changed:
        new_values["backup_retention_days"] = body.backup_retention_days

    if not changed_fields and not backup_changed:
        return await _legal_response(db, setting)

    setting = await repo.upsert(**new_values, updated_at=datetime.now(UTC))
    await db.commit()
    await db.refresh(setting)

    record_audit_event(
        event="instance.legal_updated",
        actor=actor_from_instance_admin(admin, client_address(request)),
        organization_id=None,
        changed_kinds=sorted(_LEGAL_FIELD_TO_KIND[field] for field in changed_fields),
        **(
            {"backup_retention_days": {"from": backup_before, "to": body.backup_retention_days}}
            if backup_changed
            else {}
        ),
    )

    return await _legal_response(db, setting)
