"""
User invitation endpoints.

Two routers with deliberately different trust levels:

- :data:`admin_router` — mounted under ``/api/orgs/{slug}/invitations``, org_admin
  only. Create, list, resend, revoke.
- :data:`public_router` — mounted under ``/api/invitations``, unauthenticated.
  The recipient of an invitation has no account yet, so redeeming one cannot
  require a session. The token is the credential; it is rate-limited, and the
  preview reveals nothing the mail did not already say.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.passwords import hash_password, validate_password_policy
from app.auth.rbac import require_role
from app.client_address import client_address
from app.config import get_settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.middleware.rate_limit import limiter
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.user_invitation import UserInvitation
from app.repositories.invitation import InvitationRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.user import UserRepository
from app.routers.schemas import (
    ErrorResponse,
    InstanceOrgAdminSummary,
    InvitationAcceptRequest,
    InvitationAcceptResponse,
    InvitationCreateRequest,
    InvitationListResponse,
    InvitationPreviewResponse,
    InvitationResponse,
    TakeoverNoticeResponse,
    ValidationErrorResponse,
)
from app.services import mail
from app.services.audit import actor_from_user, record_audit_event
from app.services.ws_manager import manager

admin_router = APIRouter()
public_router = APIRouter()


def _to_response(invitation: UserInvitation, *, mail_delivered: bool = True) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role.value,
        display_name=invitation.display_name_suggestion,
        language=invitation.language,
        invited_by_name=invitation.invited_by_name,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        mail_delivered=mail_delivered,
    )


def _send_invitation_mail(
    background: BackgroundTasks,
    *,
    invitation: UserInvitation,
    organization: Organization,
    raw_token: str,
) -> bool:
    """Queue the invitation mail; report whether this deployment can deliver it.

    The message is always composed and handed to the transport — deciding
    whether that transport actually reaches a mail server is the transport's
    job, and the logging one records the message either way. The return value
    only drives what the admin is told, so an unconfigured deployment says
    "not delivered" instead of implying the invitee has mail waiting.
    """
    message = mail.compose_invitation(
        to=invitation.email,
        language=invitation.language,
        organization_name=organization.name,
        role=invitation.role.value,
        inviter_name=invitation.invited_by_name,
        token=raw_token,
    )
    mail.send_in_background(background, message)
    return mail.is_enabled()


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@admin_router.post(
    "/",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Invitation created and mail queued"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        409: {"description": "Email already belongs to a user", "model": ErrorResponse},
        422: {"description": "Invalid role", "model": ErrorResponse},
    },
)
async def create_invitation(
    request: Request,
    body: InvitationCreateRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> InvitationResponse:
    """Invite a person by email to join the organization with a role.

    Open to organization admins. The invitee sets the password through the link in
    the mail, so the admin never sees it. The guest role cannot be invited, since
    guests join a run with its code, and any unknown role answers 422. An address
    that already belongs to a user answers 409 with an error code that names the
    case. An address with an open invitation of this organization gets a new
    token, and the previous link stops working. An open invitation of another
    organization, or an operator invitation that replaces admins, answers 409.
    The response says whether this installation can send mail. It emits the
    audit event `user_invited`.
    """
    settings = get_settings()
    role_value = body.role.lower()

    if role_value == "guest":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cannot invite guests. Guests join a run via its code.",
        )
    try:
        role = UserRole(role_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid role: {body.role}",
        ) from exc

    existing_user = await UserRepository.get_by_email_including_deleted(db, body.email)
    if existing_user is not None:
        # A soft-deleted account still owns its address: the unique index covers
        # deleted rows, so inviting it again would fail on INSERT with nothing
        # visible in the user list to explain why. Name the real situation and
        # the real remedy instead.
        if existing_user.is_deleted:
            raise CodedHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Email '{body.email}' belongs to a deleted account. Restore that account "
                    f"instead — enable 'show deleted' in user management."
                ),
                error_code="EMAIL_BELONGS_TO_DELETED_USER",
            )
        # A deactivated account in the invited organisation gets its own code, so
        # the dialog can still offer a remedy for it later. It is distinct from the
        # neutral "someone else already owns this address" case below, where naming
        # the other organisation would leak its membership.
        if not existing_user.is_active and existing_user.organization_id == org.id:
            raise CodedHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' belongs to a deactivated account in this organization",
                error_code="EMAIL_BELONGS_TO_INACTIVE_USER",
            )
        # Active and in this organisation: carry the user id so the dialog can
        # offer "send a password reset link" instead of a dead end.
        # Every other case (a different organisation, active or not) answers
        # the same code without an id: a neutral text, no remedy.
        same_org = existing_user.organization_id == org.id  # active is already established above
        detail = str(existing_user.id) if same_org else f"Email '{body.email}' already belongs to a user"
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            error_code="EMAIL_BELONGS_TO_USER",
        )

    repo = InvitationRepository(db)
    existing = await repo.get_open_for_email(body.email)

    if existing is not None:
        if existing.organization_id != org.id:
            # The address is spoken for in another organization. Saying which
            # one would leak that organization's membership.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' already has a pending invitation",
            )
        # A takeover invitation is the operator's, not the organization's.
        # Renewing it would hand the admin its role, its token and, by way of
        # the acceptance, its replacement list. In the hostile case that list is
        # the admin being replaced.
        if existing.replaces_user_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' already has a pending invitation",
            )
        # The read above took no lock, so an operator can have turned this row
        # into a takeover since. The UPDATE carries the condition and the
        # database decides on the row as it is now.
        renewed_token = await repo.renew_unless_takeover(
            existing,
            expiry_days=settings.mail.invitation_expiry_days,
            role=role,
            language=body.language,
            display_name_suggestion=body.display_name,
            invited_by_user_id=current_user.id,
            invited_by_name=current_user.display_name,
        )
        if renewed_token is None:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' already has a pending invitation",
            )
        raw_token = renewed_token
        invitation = existing
    else:
        try:
            invitation, raw_token = await repo.create(
                organization_id=org.id,
                email=body.email,
                role=role,
                invited_by_user_id=current_user.id,
                invited_by_name=current_user.display_name,
                expiry_days=settings.mail.invitation_expiry_days,
                language=body.language,
                display_name_suggestion=body.display_name,
            )
        except IntegrityError as exc:
            # Two admins invited the same address in the same instant; the
            # partial unique index caught the loser.
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' already has a pending invitation",
            ) from exc

    await db.commit()
    await db.refresh(invitation)

    record_audit_event(
        event="user_invited",
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "invitation", "id": str(invitation.id), "email": invitation.email},
        changes={"role": {"from": None, "to": invitation.role.value}},
        organization_id=org.id,
    )

    delivered = _send_invitation_mail(background, invitation=invitation, organization=org, raw_token=raw_token)
    return _to_response(invitation, mail_delivered=delivered)


@admin_router.get(
    "/",
    response_model=InvitationListResponse,
    responses={
        200: {"description": "Pending invitations"},
        403: {"description": "Insufficient permissions", "model": ErrorResponse},
    },
)
async def list_invitations(
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _: User = Depends(require_role("org_admin")),
) -> InvitationListResponse:
    """List the invitations of the organization that are still open.

    Open to organization admins. An invitation that was accepted, revoked or has
    expired is not listed. An invitation from the instance operator that replaces
    admins is not listed either, because the admins it replaces must not be able
    to withdraw or resend it. The operator console manages it.
    """
    invitations = [i for i in await InvitationRepository(db).list_pending(org.id) if not i.replaces_user_ids]
    # Per-invitation delivery is not persisted, but "this deployment cannot send
    # mail at all" is knowable and decisive: claiming every listed invitation was
    # delivered on a mail-less deployment is simply false, and it is exactly the
    # case where the admin most needs to be told.
    delivered = mail.is_enabled()
    return InvitationListResponse(
        items=[_to_response(invitation, mail_delivered=delivered) for invitation in invitations],
        total=len(invitations),
    )


@admin_router.get(
    "/takeover",
    response_model=TakeoverNoticeResponse,
    responses={200: {"description": "The accounts the caller took over through an operator invitation, if any"}},
)
async def get_takeover_notice(
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> TakeoverNoticeResponse:
    """Return the admin accounts that the caller replaced by accepting an operator invitation.

    Open to organization admins. User management shows the result as a notice to
    the new admin for 30 days after the acceptance. For every other caller, and
    after the 30 days, the response is empty.
    """
    result = await db.execute(
        select(UserInvitation)
        .where(
            UserInvitation.organization_id == org.id,
            UserInvitation.accepted_user_id == current_user.id,
            UserInvitation.replaces_user_ids.is_not(None),
        )
        .order_by(UserInvitation.accepted_at.desc())
    )
    invitation = result.scalars().first()
    if (
        invitation is None
        or invitation.accepted_at is None
        or invitation.accepted_at < datetime.now(UTC) - timedelta(days=30)
    ):
        return TakeoverNoticeResponse()
    ids = [UUID(i) for i in invitation.replaces_user_ids or []]
    users = (
        (await db.execute(select(User).where(User.id.in_(ids), User.organization_id == org.id))).scalars().all()
        if ids
        else []
    )
    return TakeoverNoticeResponse(
        accepted_at=invitation.accepted_at,
        replaced=[
            InstanceOrgAdminSummary(
                id=u.id, email=u.email, display_name=u.display_name, is_active=u.is_active, last_login=u.last_login
            )
            for u in users
        ],
    )


@admin_router.post(
    "/{invitation_id}/resend",
    response_model=InvitationResponse,
    responses={
        200: {"description": "A fresh invitation mail was queued"},
        404: {"description": "Invitation not found", "model": ErrorResponse},
    },
)
async def resend_invitation(
    request: Request,
    invitation_id: UUID,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> InvitationResponse:
    """Send an open invitation again with a new token.

    Open to organization admins. The previous link stops working at once, so a
    leaked old link does not stay valid. An unknown invitation, one that is not
    open, and an operator invitation that replaces admins answer 404. It emits the
    audit event `user_invitation_resent`.
    """
    settings = get_settings()
    repo = InvitationRepository(db)
    invitation = await repo.get_by_id_in_org(invitation_id, org.id)

    # A takeover invitation is the operator's, not the organization's.
    if invitation is None or not invitation.is_pending or invitation.replaces_user_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")

    # The condition of the read again, in the UPDATE: an operator can have
    # turned the row into a takeover since.
    raw_token = await repo.renew_unless_takeover(
        invitation,
        expiry_days=settings.mail.invitation_expiry_days,
        invited_by_user_id=current_user.id,
        invited_by_name=current_user.display_name,
    )
    if raw_token is None:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    await db.commit()
    await db.refresh(invitation)

    record_audit_event(
        event="user_invitation_resent",
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "invitation", "id": str(invitation.id), "email": invitation.email},
        organization_id=org.id,
    )

    delivered = _send_invitation_mail(background, invitation=invitation, organization=org, raw_token=raw_token)
    return _to_response(invitation, mail_delivered=delivered)


@admin_router.delete(
    "/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Invitation revoked"},
        404: {"description": "Invitation not found", "model": ErrorResponse},
    },
)
async def revoke_invitation(
    request: Request,
    invitation_id: UUID,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> None:
    """Withdraw an open invitation. Its link stops working at once.

    Open to organization admins. An unknown invitation, one that is not open, and
    an operator invitation that replaces admins answer 404. It emits the audit
    event `user_invitation_revoked`.
    """
    repo = InvitationRepository(db)
    invitation = await repo.get_by_id_in_org(invitation_id, org.id)

    # A takeover invitation is the operator's, not the organization's.
    if invitation is None or not invitation.is_pending or invitation.replaces_user_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")

    # The condition of the read again, in the UPDATE: an operator can have
    # turned the row into a takeover since.
    if not await repo.revoke_unless_takeover(invitation):
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    await db.commit()

    record_audit_event(
        event="user_invitation_revoked",
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "invitation", "id": str(invitation.id), "email": invitation.email},
        organization_id=org.id,
    )


# ---------------------------------------------------------------------------
# Public endpoints — the invitee has no account yet
# ---------------------------------------------------------------------------


async def _take_over(db: AsyncSession, invitation: UserInvitation, new_user_id: UUID) -> list[User]:
    """Deactivate the admins an operator invitation replaces, and end their sessions.

    Only accounts that are still admins of the invitation's organization and
    not deleted are touched; anything else changed since the invitation and is
    left alone. Deactivated, not deleted: the successor can still look them up.

    The list applies only to an invitation for the admin role: a lower role
    must never deactivate admins, or its acceptance could leave the
    organization without an active one.
    """
    if not invitation.replaces_user_ids or invitation.role != UserRole.ORG_ADMIN:
        return []
    ids = {UUID(i) for i in invitation.replaces_user_ids}
    ids.discard(new_user_id)
    result = await db.execute(
        select(User).where(
            User.id.in_(ids),
            User.organization_id == invitation.organization_id,
            User.role == UserRole.ORG_ADMIN,
            User.is_deleted.is_(False),
        )
    )
    replaced = list(result.scalars().all())
    for user in replaced:
        user.is_active = False
        # A new session version ends every open session at its next request.
        # The database raises it. The row read above may be older than another
        # revocation of the same account.
        await UserRepository.raise_session_version(db, user)
    return replaced


def _reject_unusable(invitation: UserInvitation | None) -> UserInvitation:
    """Turn an unusable invitation into a 404/410 the UI can explain.

    The three end states are distinguished on purpose. "This link no longer
    works" without a reason is exactly the dead end that generates a phone call
    to the administrator.
    """
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invitation_not_found")
    if invitation.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invitation_already_accepted")
    if invitation.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invitation_revoked")
    if invitation.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="invitation_expired")
    return invitation


@public_router.get(
    "/{token}",
    response_model=InvitationPreviewResponse,
    responses={
        200: {"description": "Invitation details"},
        404: {"description": "Unknown token", "model": ErrorResponse},
        410: {"description": "Expired, revoked or already accepted", "model": ErrorResponse},
    },
)
@limiter.limit("30/minute")
async def preview_invitation(
    request: Request,  # noqa: ARG001 — slowapi resolves the client IP from this parameter by name
    token: str,
    db: AsyncSession = Depends(get_db),
) -> InvitationPreviewResponse:
    """Describe an invitation so that the accept page can show it.

    Open without sign-in. The token is the credential, and the route allows 30
    requests per minute. The response holds only what the mail already told the
    recipient: the organization, the address, the role and the expiry of the link.
    An unknown token answers 404. An invitation that was accepted, revoked or has
    expired answers 410, and the detail says which of the three it is.
    """
    invitation = _reject_unusable(await InvitationRepository(db).get_by_token(token))

    organization = await OrganizationRepository(db).get_by_id(invitation.organization_id)
    org_name = organization.name if organization else ""

    return InvitationPreviewResponse(
        email=invitation.email,
        role=invitation.role.value,
        organization_name=org_name,
        display_name_suggestion=invitation.display_name_suggestion,
        language=invitation.language,
        expires_at=invitation.expires_at,
    )


@public_router.post(
    "/{token}/accept",
    response_model=InvitationAcceptResponse,
    responses={
        200: {"description": "Account created"},
        400: {"description": "Password policy violation", "model": ValidationErrorResponse},
        404: {"description": "Unknown token", "model": ErrorResponse},
        409: {"description": "Email was claimed in the meantime", "model": ErrorResponse},
        410: {"description": "Expired, revoked or already accepted", "model": ErrorResponse},
    },
)
@limiter.limit("10/minute")
async def accept_invitation(
    request: Request,
    token: str,
    body: InvitationAcceptRequest,
    db: AsyncSession = Depends(get_db),
) -> InvitationAcceptResponse:
    """Accept an invitation and create the account with the invited role.

    Open without sign-in. The token is the credential, and the route allows 10
    requests per minute. The password must meet the password policy, otherwise the
    route answers 400. The account and the use of the invitation are saved
    together, so a failure leaves no account without a used invitation and no used
    invitation without an account. An address that has an account in the
    meantime answers 409. An unknown token answers 404, and an invitation that was
    accepted, revoked or has expired answers 410.

    An operator invitation for the admin role also deactivates the admins that it
    replaces and ends their sessions. The route emits the audit event
    `user_invitation_accepted`. A takeover also emits `user_deactivated` for each
    replaced admin and `instance.org_admin_takeover`.
    """
    repo = InvitationRepository(db)
    invitation = _reject_unusable(await repo.get_by_token(token))

    policy_errors = validate_password_policy(body.password)
    if policy_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"detail": "Password does not meet policy requirements", "errors": policy_errors},
        )

    # Someone may have registered this address between invitation and click.
    if await UserRepository.get_by_email_including_deleted(db, invitation.email):
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists",
            error_code="EMAIL_HAS_ACCOUNT",
        )

    user_repo = UserRepository(db, organization_id=invitation.organization_id)
    try:
        new_user = await user_repo.create(
            email=invitation.email,
            display_name=body.display_name,
            password_hash=hash_password(body.password),
            role=invitation.role,
            is_active=True,
        )
        await repo.mark_accepted(invitation, new_user.id)
        replaced = await _take_over(db, invitation, new_user.id)
        await db.commit()
    except IntegrityError as exc:
        # The check above is not a lock: two clicks on the same link, or a
        # registration landing in between, both reach here and the partial
        # unique index on users.email settles it. Answer the loser honestly
        # instead of letting the constraint surface as a 500.
        await db.rollback()
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists",
            error_code="EMAIL_HAS_ACCOUNT",
        ) from exc
    await db.refresh(new_user)

    # The replaced admins keep no open socket.
    for old_admin in replaced:
        await manager.close_channel(f"user:{old_admin.id}", code=4000, reason="session revoked")

    record_audit_event(
        event="user_invitation_accepted",
        actor={
            "user_id": str(new_user.id),
            "display_name": new_user.display_name,
            "role": new_user.role.value,
            "email": new_user.email,
            "ip": client_address(request),
        },
        target={"entity": "invitation", "id": str(invitation.id), "email": invitation.email},
        organization_id=invitation.organization_id,
    )

    if replaced:
        organization = await OrganizationRepository(db).get_by_id(invitation.organization_id)
        # Both logs record the takeover: the organization's own log names
        # the accounts, the operator's log only how many.
        for old_admin in replaced:
            record_audit_event(
                event="user_deactivated",
                actor={
                    "user_id": str(new_user.id),
                    "display_name": new_user.display_name,
                    "ip": client_address(request),
                },
                target={"entity": "user", "id": str(old_admin.id), "email": old_admin.email},
                organization_id=invitation.organization_id,
                reason="operator_takeover",
            )
        record_audit_event(
            event="instance.org_admin_takeover",
            actor={"system": "invitation_accept", "ip": client_address(request)},
            organization_id=None,
            target_slug=organization.slug if organization else None,
            replaced_count=len(replaced),
        )

    return InvitationAcceptResponse(
        message="Account created successfully",
        email=new_user.email or invitation.email,
        role=new_user.role.value,
    )
