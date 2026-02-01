"""
User management endpoints.

Provides org_admin-only endpoints for managing users within an organization:
- List users with pagination and filters
- Create users with roles
- Get user details
- Update user roles, status, and display name
- Delete or anonymize users

All endpoints enforce org_admin role and organization scoping.
"""

import contextlib
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_org_from_slug
from app.auth.passwords import hash_password, validate_password_policy
from app.auth.rbac import require_role
from app.client_address import client_address
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.user import UserRepository
from app.routers.schemas import (
    ErrorResponse,
    UserCreateRequest,
    UserFullProfile,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
    ValidationErrorResponse,
)
from app.services import mail
from app.services.audit import (
    actor_from_user,
    audit_user_created,
    audit_user_deleted,
    audit_user_restored,
    audit_user_role_changed,
    record_audit_event,
)
from app.services.gdpr import anonymize_user, restore_user
from app.services.mail.templates import normalize_language
from app.services.ws_manager import manager

router = APIRouter()


def _org_language(org: Organization) -> str:
    """The organisation's default mail language.

    Admin-triggered mails have no requesting recipient to ask, so the
    organisation's own default is the best available signal.
    """
    default = org.readable_settings.get("default_language")
    return normalize_language(default if isinstance(default, str) else None)


@router.get(
    "/",
    response_model=UserListResponse,
    responses={
        200: {"description": "List of users in the organization"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
    },
)
async def list_users(
    skip: int = Query(0, ge=0, description="Number of items to skip (offset)"),
    limit: int = Query(50, ge=1, le=200, description="Maximum items to return"),
    role: str | None = Query(None, description="Filter by role (guest, participant, coordinator, creator, org_admin)"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    include_deleted: bool = Query(False, description="Include soft-deleted users (grace-window + anonymized)"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("org_admin")),
) -> UserListResponse:
    """List the users of the organization, one page at a time.

    Open to organization admins. The list can be filtered by role and by active
    state. A `role` value that names no role is ignored. Deleted users are listed
    only with `include_deleted=true`.
    """
    user_repo = UserRepository(db, organization_id=org.id)

    role_filter: UserRole | None = None
    if role is not None:
        with contextlib.suppress(ValueError):
            role_filter = UserRole(role.lower())

    users = await user_repo.list_by_org(
        skip=skip,
        limit=limit,
        role_filter=role_filter,
        active_filter=is_active,
        include_deleted=include_deleted,
    )
    total = await user_repo.count_by_org(
        role_filter=role_filter,
        active_filter=is_active,
        include_deleted=include_deleted,
    )

    return UserListResponse(
        items=[
            UserResponse(
                id=u.id,
                email=u.email,
                display_name=u.display_name,
                role=u.role.value,
                is_active=u.is_active,
                totp_enabled=u.totp_enabled,
                created_at=u.created_at,
                last_login=u.last_login,
                is_deleted=u.is_deleted,
                deleted_at=u.deleted_at,
            )
            for u in users
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "User created successfully"},
        400: {"description": "Password policy violation", "model": ValidationErrorResponse},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "Organization not found", "model": ErrorResponse},
        409: {"description": "Email already exists", "model": ErrorResponse},
        422: {"description": "Invalid role (cannot create guest)", "model": ErrorResponse},
    },
)
async def create_user(
    request: Request,
    body: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> UserResponse:
    """Create an active user in the organization with a password that the admin sets.

    Open to organization admins. The role can be participant, coordinator, creator
    or org_admin. Guests join with a run code, so the guest role answers 422. A
    password that breaks the password policy answers 400. An email address that
    belongs to another account answers 409 with an error code that says whether
    that account is deleted. It emits the audit event `user_created`.
    """
    client_ip = client_address(request)

    # Validate role — guests are run-bound (created via the join flow), so they
    # cannot be created here. Every other role, including org_admin, may be
    # created by an org_admin.
    role_lower = body.role.lower()
    if role_lower == "guest":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cannot create users with guest role. Guests join via run code.",
        )

    try:
        user_role = UserRole(role_lower)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid role: {body.role}. Must be participant, coordinator, creator, or org_admin.",
        ) from e

    policy_errors = validate_password_policy(body.password)
    if policy_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": "Password does not meet policy requirements",
                "errors": policy_errors,
            },
        )

    # Check email uniqueness (unscoped, across all orgs). Deleted rows included:
    # the unique index covers them too, so a soft-deleted address would
    # otherwise fail the INSERT with a 500 instead of a readable 409.
    existing_user = await UserRepository.get_by_email_including_deleted(db, body.email)
    if existing_user:
        if existing_user.is_deleted:
            raise CodedHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Email '{body.email}' belongs to a deleted account. Restore that account "
                    f"instead — enable 'show deleted' in user management."
                ),
                error_code="EMAIL_BELONGS_TO_DELETED_USER",
            )
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{body.email}' already registered",
            error_code="EMAIL_BELONGS_TO_USER",
        )

    password_hash = hash_password(body.password)

    user_repo = UserRepository(db, organization_id=org.id)
    new_user = await user_repo.create(
        email=body.email,
        display_name=body.display_name,
        password_hash=password_hash,
        role=user_role,
        is_active=True,
    )

    await db.commit()
    await db.refresh(new_user)

    if not new_user.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required for user creation")
    audit_user_created(
        actor_user=current_user,
        actor_ip=client_ip,
        new_user=new_user,
        organization_id=org.id,
    )

    return UserResponse(
        id=new_user.id,
        email=new_user.email,
        display_name=new_user.display_name,
        role=new_user.role.value,
        is_active=new_user.is_active,
        totp_enabled=new_user.totp_enabled,
        created_at=new_user.created_at,
        last_login=new_user.last_login,
    )


@router.get(
    "/{user_id}",
    response_model=UserFullProfile,
    responses={
        200: {"description": "User details"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "User not found or belongs to different organization", "model": ErrorResponse},
    },
)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("org_admin")),
) -> UserFullProfile:
    """Return the full profile of one user of the organization.

    Open to organization admins. The profile includes the second factors, the
    failed sign-ins and the lock time. A user that does not exist, or belongs to
    another organization, answers 404.
    """
    try:
        from uuid import UUID as UUIDType

        parsed_user_id = UUIDType(user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from e

    user_repo = UserRepository(db, organization_id=org.id)
    user = await user_repo.get_by_id_in_org(parsed_user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserFullProfile(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        organization_id=user.organization_id,
        is_active=user.is_active,
        is_guest=user.role == UserRole.GUEST,
        totp_enabled=user.totp_enabled,
        webauthn_enabled=bool(user.webauthn_credentials),
        failed_login_attempts=user.failed_login_attempts,
        locked_until=user.locked_until,
        last_login=user.last_login,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.put(
    "/{user_id}",
    response_model=UserResponse,
    responses={
        200: {"description": "User updated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "User not found or belongs to different organization", "model": ErrorResponse},
        422: {"description": "Validation error (cannot change own role, invalid role)", "model": ErrorResponse},
    },
)
async def update_user(
    request: Request,
    user_id: str,
    body: UserUpdateRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> UserResponse:
    """Change the role, the active state, the display name or the email of a user.

    Open to organization admins. Fields that the request leaves out stay as they
    are. The route answers 422 when an admin changes their own role, sets the
    guest role, or changes the role or the email of a guest. A new email that
    another account uses answers 409. A deactivated user loses open WebSocket
    connections. A changed role ends the connections that the new role does not
    allow, and the user gets a mail about the change. The route emits the audit
    event `user_role_changed` when the role changes and `user_email_changed` when
    the email changes.
    """
    client_ip = client_address(request)

    try:
        from uuid import UUID as UUIDType

        parsed_user_id = UUIDType(user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from e

    user_repo = UserRepository(db, organization_id=org.id)
    user = await user_repo.get_by_id_in_org(parsed_user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    old_role = user.role.value
    old_email = user.email

    # Guests hold no persistent account and upgrade themselves: an admin may
    # not change a guest's role or email. This is defence in depth for the
    # fields the UI disables.
    if user.role == UserRole.GUEST and (body.role is not None or body.email is not None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cannot change a guest's role or email; guests upgrade themselves.",
        )

    if body.role is not None:
        if parsed_user_id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Cannot change your own role",
            )

        # Validate role. Guests are run-bound, so no one can be moved into that
        # role here. org_admin is assignable, matching create and invite: an
        # org_admin may appoint peers on every path.
        role_lower = body.role.lower()
        if role_lower == "guest":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Cannot set role to guest",
            )

        try:
            new_role = UserRole(role_lower)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid role: {body.role}. Must be participant, coordinator, creator, or org_admin.",
            ) from e

        user.role = new_role

    if body.is_active is not None:
        user.is_active = body.is_active

    if body.display_name is not None:
        user.display_name = body.display_name

    # Email change: org-global uniqueness (login is by email), normalized to
    # lowercase by the schema (NormalizedEmail).
    if body.email is not None and body.email != user.email:
        existing = await UserRepository.get_by_email_including_deleted(db, body.email)
        if existing is not None and existing.id != user.id:
            if existing.is_deleted:
                raise CodedHTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Email '{body.email}' belongs to a deleted account. Restore that account "
                        f"instead — enable 'show deleted' in user management."
                    ),
                    error_code="EMAIL_BELONGS_TO_DELETED_USER",
                )
            raise CodedHTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email address is already in use.",
                error_code="EMAIL_BELONGS_TO_USER",
            )
        user.email = body.email

    await db.commit()
    await db.refresh(user)

    # A deactivated account keeps no open socket: the WebSocket checks the
    # token only when it connects.
    if not user.is_active:
        await manager.close_channel(f"user:{user.id}", code=4000, reason="session revoked")
    elif body.role is not None and old_role != user.role.value:
        # A changed role ends the sockets the new role does not allow: a staff feed,
        # an assisted chat of a non-member. Each socket of the account asks its
        # admission rule again. The rest stay.
        await manager.recheck_channel(f"user:{user.id}")

    if body.role is not None and old_role != user.role.value:
        audit_user_role_changed(
            actor_user=current_user,
            actor_ip=client_ip,
            target_user=user,
            old_role=old_role,
            new_role=user.role.value,
            organization_id=org.id,
        )
        # Someone's permissions changed without them doing anything. Telling
        # them is both a courtesy and the only way they notice an unintended
        # change.
        if user.email:
            mail.send_in_background(
                background,
                mail.compose_role_changed(
                    to=user.email,
                    language=_org_language(org),
                    organization_name=org.name,
                    display_name=user.display_name,
                    new_role=user.role.value,
                ),
            )

    # Emit audit event if email changed (old address kept only in the audit log)
    if body.email is not None and old_email != user.email:
        record_audit_event(
            "user_email_changed",
            actor=actor_from_user(current_user, client_ip),
            target={"user_id": str(user.id)},
            changes={"email": {"from": old_email, "to": user.email}},
            organization_id=org.id,
        )

    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        is_active=user.is_active,
        totp_enabled=user.totp_enabled,
        created_at=user.created_at,
        last_login=user.last_login,
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "User deleted (grace: restorable; immediate: anonymized)"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "User not found or belongs to different organization", "model": ErrorResponse},
        422: {"description": "Cannot delete yourself", "model": ErrorResponse},
    },
)
async def delete_user(
    request: Request,
    user_id: str,
    mode: Literal["grace", "immediate"] = Query(
        "grace",
        description=(
            "grace: soft-delete. The user can be restored for 30 days, then the retention "
            "sweep anonymizes the account. immediate: erasure under GDPR Article 17. "
            "All personal data is anonymized right away and this cannot be undone."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> None:
    """Delete a user of the organization, with a grace period or at once.

    Open to organization admins. With `mode=grace`, the default, the account is
    marked as deleted and deactivated but keeps its data. The restore route can
    bring it back within 30 days, and the daily retention sweep anonymizes it
    afterwards. With `mode=immediate` the route anonymizes the email, the display
    name, the password and the second factors right away, which cannot be undone.
    An admin cannot delete their own account and gets 422. The user loses open
    WebSocket connections. The route emits the audit event `user_deleted` with
    the deletion mode.
    """
    client_ip = client_address(request)

    try:
        from uuid import UUID as UUIDType

        parsed_user_id = UUIDType(user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from e

    if parsed_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cannot delete yourself",
        )

    user_repo = UserRepository(db, organization_id=org.id)
    user = await user_repo.get_by_id_in_org(parsed_user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Emit audit event before any mutation (PII still available on the row)
    audit_user_deleted(
        actor_user=current_user,
        actor_ip=client_ip,
        target_user=user,
        organization_id=org.id,
        deletion_mode=mode,
    )

    if mode == "immediate":
        # GDPR Art. 17 path — anonymize right away.
        # Note: anonymize_user also emits audit_data_deletion_requested internally
        await anonymize_user(
            session=db,
            user_id=parsed_user_id,
            actor=actor_from_user(current_user, client_ip),
        )
        # anonymize_user also raises the account's session version: it clears
        # the second factor, and somebody else than its owner does it. The account
        # is inactive as well, which is the second lock.
    else:
        # Grace path — soft-delete + deactivate, PII stays for restorability.
        user.is_deleted = True
        user.deleted_at = datetime.now(UTC)
        user.is_active = False

    await db.commit()

    # The removed account keeps no open socket.
    await manager.close_channel(f"user:{parsed_user_id}", code=4000, reason="session revoked")


@router.post(
    "/{user_id}/restore",
    response_model=UserResponse,
    responses={
        200: {"description": "User restored successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "User not found or belongs to different organization", "model": ErrorResponse},
        409: {"description": "User is not deleted", "model": ErrorResponse},
        410: {"description": "Grace window passed — user already anonymized", "model": ErrorResponse},
    },
)
async def restore_deleted_user(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> UserResponse:
    """Restore a user that was deleted with the grace period.

    Open to organization admins. The restore works until the retention sweep
    anonymizes the account, 30 days after the deletion. A user that is not
    deleted answers 409, and an account that is anonymized already answers 410. It
    emits the audit event `user_restored`.
    """
    client_ip = client_address(request)

    try:
        from uuid import UUID as UUIDType

        parsed_user_id = UUIDType(user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        ) from e

    # Org-scoped lookup including soft-deleted rows
    user_repo = UserRepository(db, organization_id=org.id)
    user = await user_repo.get_by_id_in_org(parsed_user_id, include_deleted=True)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    outcome = await restore_user(session=db, user_id=parsed_user_id)

    if outcome == "already_anonymized":
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Grace window has passed — user is anonymized and cannot be restored",
        )
    if outcome == "not_deleted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is not deleted",
        )
    if outcome != "restored":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    audit_user_restored(
        actor_user=current_user,
        actor_ip=client_ip,
        target_user=user,
        organization_id=org.id,
    )

    await db.commit()
    await db.refresh(user)

    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        is_active=user.is_active,
        totp_enabled=user.totp_enabled,
        created_at=user.created_at,
        last_login=user.last_login,
    )


@router.post(
    "/{user_id}/send-password-reset",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Reset mail queued"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Insufficient permissions (requires org_admin)", "model": ErrorResponse},
        404: {"description": "User not found", "model": ErrorResponse},
        422: {"description": "User has no email address", "model": ErrorResponse},
    },
)
async def send_password_reset(
    request: Request,
    user_id: str,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> dict[str, str]:
    """Mail a user a password reset link, the same link that the user could request.

    Open to organization admins. The admin never sees the link and never chooses
    the password. An earlier link of the user stops working. A user without an
    email address and a deactivated user answer 422. It emits the audit event
    `user_password_reset_sent`.
    """
    client_ip = client_address(request)

    try:
        from uuid import UUID as UUIDType

        parsed_user_id = UUIDType(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from e

    user_repo = UserRepository(db, organization_id=org.id)
    user = await user_repo.get_by_id_in_org(parsed_user_id)

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not user.email:
        # Guests are run-bound and have no address; there is nowhere to send.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="This user has no email address",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cannot send a reset link to a deactivated account",
        )

    reset_repo = PasswordResetRepository(db)
    await reset_repo.invalidate_for_user(user.id)
    raw_token = await reset_repo.create_token(user.id)
    await db.commit()

    record_audit_event(
        event="user_password_reset_sent",
        actor=actor_from_user(current_user, client_ip),
        target={"entity": "user", "id": str(user.id), "email": user.email},
        organization_id=org.id,
    )

    mail.send_in_background(
        background,
        mail.compose_password_reset(
            to=user.email,
            language=_org_language(org),
            organization_name=org.name,
            display_name=user.display_name,
            token=raw_token,
            expires_in_minutes=PasswordResetRepository.DEFAULT_EXPIRY_HOURS * 60,
        ),
    )

    return {"message": f"A password reset link was sent to {user.email}."}
