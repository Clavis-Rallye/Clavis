"""
Authentication router.

Provides endpoints for user registration, login, logout, and password management.
All endpoints emit appropriate audit log events for security tracking.
"""

from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user
from app.auth.passwords import (
    check_password_reuse,
    hash_password,
    validate_password_policy,
    verify_password,
)
from app.auth.tokens import (
    WS_TOKEN_TTL,
    clear_auth_cookie,
    create_access_token,
    create_second_factor_proof,
    set_auth_cookie,
    set_second_factor_proof_cookie,
    sign_out_session,
)
from app.client_address import client_address
from app.config import get_settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.middleware.rate_limit import limiter
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.repositories.organization import OrganizationRepository
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.user import UserRepository
from app.routers.schemas import (
    CurrentUserResponse,
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
    PasswordForgotRequest,
    PasswordForgotResponse,
    PasswordResetRequest,
    PasswordResetResponse,
    ProfileUpdateRequest,
    ProfileUpdateResponse,
    RegisterRequest,
    RegisterResponse,
    ValidationErrorResponse,
)
from app.services import mail
from app.services.audit import (
    audit_config_changed,
    audit_user_created,
    audit_user_locked,
    audit_user_login,
    audit_user_login_failed,
    audit_user_logout,
    audit_user_updated,
    record_audit_event,
)
from app.services.mail.templates import normalize_language
from app.services.ws_manager import manager

logger = structlog.get_logger("app")

router = APIRouter()


def _request_language(request: Request, organization: Organization | None = None) -> str:
    """Pick the language for a mail triggered by this request.

    Order: the client's explicit choice (``X-Clavis-Language``, sent by the
    frontend from its i18n state) → the browser's ``Accept-Language`` → the
    organisation's default → German. The user's own language preference is not
    stored on the account, so the request is the only signal available at the
    moment a reset is asked for.
    """
    explicit = request.headers.get("X-Clavis-Language")
    if explicit:
        return normalize_language(explicit)

    accept = request.headers.get("Accept-Language")
    if accept:
        # "de-DE,de;q=0.9,en;q=0.8" → "de-DE". Full q-value negotiation would be
        # overkill for a two-language choice.
        first = accept.split(",")[0].strip()
        if first and not first.startswith("*"):
            return normalize_language(first)

    if organization is not None:
        default = organization.readable_settings.get("default_language")
        if isinstance(default, str):
            return normalize_language(default)

    return normalize_language(None)


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Registration successful"},
        400: {
            "description": "Validation error (password policy violation)",
            "model": ValidationErrorResponse,
        },
        409: {
            "description": "Conflict (email or slug already exists)",
            "model": ErrorResponse,
        },
        403: {
            "description": "Self-signup into an archived organization (error_code ORG_ARCHIVED)",
            "model": ErrorResponse,
        },
        429: {"description": "Too many registrations from this address", "model": ErrorResponse},
    },
)
@limiter.limit(lambda: get_settings().auth.register_rate_limit)
async def register(
    request: Request,
    body: RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    """Register a user, either with a new organization or into an existing one.

    Open to unauthenticated callers. The mode follows the request body:

    - **Bootstrap** (the body has `organization_name`): creates the organization
      and an `org_admin` user. Once any organization exists, this mode is
      refused with 403 `ORG_BOOTSTRAP_CLOSED`, unless the instance allows
      unlimited organization registrations. A taken `organization_slug` gives 409.
    - **Self-signup** (the body omits `organization_name`): creates a
      `participant` user in the organization named by `organization_slug`. An
      unknown slug gives 404. An archived organization gives 403 `ORG_ARCHIVED`.

    In both modes the password must meet the password policy (400 otherwise),
    and the email must not belong to any account, deleted ones included (409).
    The response sets the session cookie, so the user is signed in at once.

    A new `org_admin` on an instance that enforces 2FA for organization admins
    gets no session cookie. The response has `requires_2fa` set and a
    `setup_token` that is valid for 30 minutes and works only on the TOTP setup
    routes.

    Emits the audit event `user_created`. A bootstrap registration also emits
    `instance.org_created` with the new slug and no organization id, as the
    operator's creation of an organization does. The actor of that event holds
    only the type `registration`, the id of the new account and the client
    address. It holds no email and no display name, because the operator's log
    shows the event and must not show the data of a member.
    """
    settings = get_settings()
    client_ip = client_address(request)

    policy_errors = validate_password_policy(body.password)
    if policy_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": "Password does not meet policy requirements",
                "errors": policy_errors,
            },
        )

    org_repo = OrganizationRepository(db)
    bootstrap_mode = body.organization_name is not None

    if bootstrap_mode:
        # Bootstrap mode creates a new org and an admin user. This path is
        # UNAUTHENTICATED, so without a gate anyone could mint themselves an
        # org_admin account on a live instance. On a normal deployment bootstrap is
        # a one-time act: once any organisation exists, refuse unless the operator
        # has explicitly opted into open organisation signup.
        #
        # The server enforces this. Hiding the route in the frontend is a
        # convenience, not the control.
        if not settings.auth.allow_unlimited_org_registrations and await org_repo.count() > 0:
            raise CodedHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organisation bootstrap is closed on this instance.",
                error_code="ORG_BOOTSTRAP_CLOSED",
            )

        existing_org = await org_repo.get_by_slug(body.organization_slug)
        if existing_org:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Organization slug '{body.organization_slug}' already exists",
            )

        # The email must be unique across all orgs. Deleted rows are included, so a
        # soft-deleted address fails here with a 409 rather than the unique index
        # answering with a 500 on INSERT. Registration is unauthenticated, so the
        # answer stays generic either way. It must not confirm that a deleted
        # account exists.
        existing_user = await UserRepository.get_by_email_including_deleted(db, body.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' already registered",
            )

        password_hash = await run_in_threadpool(hash_password, body.password)
        organization = await org_repo.create(
            name=body.organization_name,
            slug=body.organization_slug,
        )
        user_repo = UserRepository(db, organization.id)
        user = await user_repo.create(
            email=body.email,
            display_name=body.display_name,
            password_hash=password_hash,
            role=UserRole.ORG_ADMIN,
            is_active=True,
        )
    else:
        # Self-signup mode must reference an existing organisation.
        found_org = await org_repo.get_by_slug(body.organization_slug)
        if found_org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization '{body.organization_slug}' not found",
            )
        # An archived organization admits no new members. Refuse before a user is
        # created or a cookie is set.
        if found_org.archived_at is not None:
            raise CodedHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This organization is archived.",
                error_code="ORG_ARCHIVED",
            )
        organization = found_org

        # Deleted rows are included, with the same generic answer as in bootstrap
        # mode and for the same reason.
        existing_user = await UserRepository.get_by_email_including_deleted(db, body.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{body.email}' already registered",
            )

        password_hash = await run_in_threadpool(hash_password, body.password)
        user_repo = UserRepository(db, organization.id)
        user = await user_repo.create(
            email=body.email,
            display_name=body.display_name,
            password_hash=password_hash,
            role=UserRole.PARTICIPANT,
            is_active=True,
        )

    await user_repo.update(user.id, last_login=datetime.now(UTC))

    await db.commit()
    await db.refresh(user)

    # Surface the per-user 2FA-required flag. For brand-new users TOTP is never
    # enabled yet. The only situation where they must set it up before continuing
    # is when the org-admin enforcement toggle is on and they were just created as
    # an admin.
    requires_2fa = settings.auth.enforce_org_admin_totp and user.role == UserRole.ORG_ADMIN and not user.totp_enabled

    setup_token: str | None = None
    if requires_2fa:
        # A session cookie before the second factor would defeat the
        # enforcement, so bootstrap hands out the same restricted setup token
        # login issues for this case. The guard in get_current_user admits it
        # on the two TOTP routes only, and totp_verify completes the flow by
        # setting the normal session cookie. The token lasts 30 minutes, because
        # first-time enrollment can include installing an authenticator app.
        setup_token = create_access_token(
            user_id=user.id,
            role=user.role.value,
            organization_id=user.organization_id,
            expires_delta=timedelta(minutes=30),
            purpose="2fa_setup",
            session_version=user.session_version,
        )
    else:
        token = create_access_token(
            user_id=user.id,
            role=user.role.value,
            organization_id=user.organization_id,
        )
        set_auth_cookie(response, token)

    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required for registration",
        )
    audit_user_created(
        actor_user=user,
        actor_ip=client_ip,
        new_user=user,
        organization_id=user.organization_id,
    )
    if bootstrap_mode:
        record_audit_event(
            event="instance.org_created",
            actor={"type": "registration", "user_id": str(user.id), "ip": client_ip},
            organization_id=None,
            target_slug=organization.slug,
        )

    return RegisterResponse(
        user_id=user.id,
        organization_id=organization.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        requires_2fa=requires_2fa,
        setup_token=setup_token,
        message="Registration successful",
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        200: {
            "description": "Login successful (or 2FA required)",
        },
        401: {
            "description": "Invalid credentials",
            "model": ErrorResponse,
        },
        423: {
            "description": "Account locked",
            "model": ErrorResponse,
        },
        403: {
            "description": "The user's organization is archived (error_code ORG_ARCHIVED)",
            "model": ErrorResponse,
        },
    },
)
@limiter.limit(lambda: get_settings().auth.login_rate_limit)
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Sign in with email and password and set the session cookie.

    The `status` of the response says what happens next:

    - `success`: the session cookie is set.
    - `2fa_required`: the account has TOTP enabled. No session cookie is set,
      only a short-lived proof of the password step. The client must send the
      second factor to the TOTP authenticate route.
    - `2fa_setup_required`: an `org_admin` without TOTP on an instance that
      enforces it. No session cookie is set. The response carries a `setup_token`
      that is valid for 30 minutes and works only on the TOTP setup routes.

    An unknown email, a wrong password and a password that changed during the
    sign-in all give the same 401 `INVALID_CREDENTIALS`, whether the account is
    active or disabled. An account that is locked gives 423 `ACCOUNT_LOCKED`
    with `locked_until`, before the password is checked. Each failed password
    counts, on a disabled account as on an active one. When the count reaches
    the maximum, the account is locked for the configured time. A lock that
    has run out is cleared at the next sign-in. Only after a correct password
    does a disabled account give 401 `ACCOUNT_DISABLED`, and a member of an
    archived organization 403 `ORG_ARCHIVED`.

    A `success` sign-in resets the failed-attempt count and records the time of
    the last login.

    Emits the audit events `user_login`, `user_login_failed` and `user_locked`.
    """
    settings = get_settings()
    client_ip = client_address(request)

    # The organization is unknown at login, so the lookup by email is unscoped.
    user = await UserRepository.get_by_email_unscoped(db, body.email)

    # An unknown email gets the generic error, so it does not reveal that the email does not exist.
    if not user:
        audit_user_login_failed(
            email=body.email,
            ip=client_ip,
            reason="user_not_found",
            organization_id=None,
        )
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            error_code="INVALID_CREDENTIALS",
        )

    user_repo = UserRepository(db, user.organization_id)

    # The lock answers before the password is checked: checking the password
    # during a lock would turn the lock into a password oracle.
    if user.locked_until:
        now = datetime.now(UTC)
        if user.locked_until > now:
            audit_user_login_failed(
                email=body.email,
                ip=client_ip,
                reason="account_locked",
                organization_id=user.organization_id,
            )
            raise CodedHTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account locked until {user.locked_until.isoformat()}. Please try again later.",
                error_code="ACCOUNT_LOCKED",
                extra={"locked_until": user.locked_until.isoformat()},
            )
        # The lock has expired. Clear it.
        await user_repo.update(
            user.id,
            locked_until=None,
            failed_login_attempts=0,
        )
        await db.commit()

    # The state this request verifies. A session is minted for this session
    # version only, and only while the password hash is still the one the
    # password was checked against: a reset or a change that commits while the
    # sign-in runs must not hand its new version to somebody who proved the
    # old password.
    verified_version = user.session_version
    verified_password_hash = user.password_hash

    if not await run_in_threadpool(verify_password, body.password, verified_password_hash):
        await user_repo.increment_failed_login_attempts(user.id)
        await db.commit()

        # Read the user again for the updated failed_login_attempts count.
        user = await user_repo.get_by_id(user.id)
        if user is None:
            # Not expected, since the row was just updated.
            raise CodedHTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                error_code="INVALID_CREDENTIALS",
            )

        if user.failed_login_attempts >= settings.auth.max_login_attempts:
            lockout_until = datetime.now(UTC) + timedelta(minutes=settings.auth.lockout_duration_minutes)
            await user_repo.update(user.id, locked_until=lockout_until)
            await db.commit()

            audit_user_locked(
                user=user,
                ip=client_ip,
                organization_id=user.organization_id,
                reason="max_failed_attempts",
            )

            audit_user_login_failed(
                email=body.email,
                ip=client_ip,
                reason="invalid_password",
                organization_id=user.organization_id,
            )

            raise CodedHTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account locked until {lockout_until.isoformat()} due to too many failed login attempts. "
                f"Please try again later.",
                error_code="ACCOUNT_LOCKED",
                extra={"locked_until": lockout_until.isoformat()},
            )

        audit_user_login_failed(
            email=body.email,
            ip=client_ip,
            reason="invalid_password",
            organization_id=user.organization_id,
        )

        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            error_code="INVALID_CREDENTIALS",
        )

    # After the password, so only someone who proved the credentials learns that
    # the account is disabled. A wrong password on a disabled account took the
    # path above, like any wrong password. The operator sign-in does the same.
    if not user.is_active:
        audit_user_login_failed(
            email=body.email,
            ip=client_ip,
            reason="account_disabled",
            organization_id=user.organization_id,
        )
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled",
            error_code="ACCOUNT_DISABLED",
        )

    # A member of an archived organization signs in no further: no 2FA challenge,
    # no session, and the coded error explains why. This is checked after the
    # password, so the archived state is only disclosed to someone who proved
    # knowledge of the credentials.
    if await OrganizationRepository.is_archived(db, user.organization_id):
        raise CodedHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This organization is archived.",
            error_code="ORG_ARCHIVED",
        )

    if user.totp_enabled:
        # No session cookie yet. The client must call /auth/2fa/totp/authenticate
        # with the TOTP code. failed_login_attempts is not reset here, only after
        # the full 2FA flow.

        # Proof for the second-factor endpoints that the password step of THIS
        # login succeeded: short-lived, signed, bound to the account. The second
        # factor alone completes no login without it.
        set_second_factor_proof_cookie(
            response,
            create_second_factor_proof(
                user.id,
                session_version=verified_version,
                last_login=user.last_login,
            ),
        )

        await db.commit()

        return LoginResponse(
            status="2fa_required",
            requires_2fa=True,
            message="2FA required. Please provide TOTP code.",
        )

    # An org_admin without TOTP must set it up first, when the instance enforces it.
    if settings.auth.enforce_org_admin_totp and user.role == UserRole.ORG_ADMIN and not user.totp_enabled:
        # A short-lived restricted JWT, for the 2FA setup only. It lasts 30 minutes,
        # because first-time enrollment can include installing an authenticator
        # app on a phone, and 10 minutes expired mid-flow.
        setup_token = create_access_token(
            user_id=user.id,
            role=user.role.value,
            organization_id=user.organization_id,
            expires_delta=timedelta(minutes=30),
            purpose="2fa_setup",
            session_version=verified_version,
        )

        await db.commit()

        return LoginResponse(
            status="2fa_setup_required",
            setup_token=setup_token,
            setup_url="/api/auth/2fa/totp",
            message="Organization administrators must enable 2FA before proceeding.",
        )

    # No 2FA: complete the login.
    await user_repo.reset_failed_login_attempts(user.id)

    await user_repo.update(user.id, last_login=datetime.now(UTC))

    await db.commit()
    await db.refresh(user)

    # The refresh shows the row as it is now. When a reset or a password
    # change committed since the read above, the password this request proved
    # is no longer the account's: no session. One that commits after this
    # refresh raises the version above verified_version, and the cookie below
    # ends with it.
    if user.session_version != verified_version or user.password_hash != verified_password_hash:
        audit_user_login_failed(
            email=body.email,
            ip=client_ip,
            reason="account_changed_during_sign_in",
            organization_id=user.organization_id,
        )
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            error_code="INVALID_CREDENTIALS",
        )

    # The cookie is for the version that was verified.
    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        session_version=verified_version,
    )
    set_auth_cookie(response, token)

    audit_user_login(
        user=user,
        ip=client_ip,
        organization_id=user.organization_id,
        method="password",
    )

    return LoginResponse(
        status="success",
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        organization_id=user.organization_id,
        message="Login successful",
    )


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={
        200: {"description": "Current user info"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
    },
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> CurrentUserResponse:
    """Return the signed-in user's identity, role and organization.

    The frontend calls this on page load to find out whether the session is
    valid. The session comes from the cookie or from a Bearer token.
    """
    return CurrentUserResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        role=current_user.role.value,
        organization_id=current_user.organization_id,
        organization_slug=current_user.organization.slug,
        is_active=current_user.is_active,
        totp_enabled=current_user.totp_enabled,
        last_login=current_user.last_login,
        created_at=current_user.created_at,
    )


@router.get(
    "/ws-token",
    responses={
        200: {"description": "Short-lived token for WebSocket authentication"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
    },
)
async def get_ws_token(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Issue a token of 5 minutes that opens a WebSocket connection.

    The client passes the token as the `token` query parameter. The session
    cookie cannot do this, because JavaScript cannot read it and the browser
    does not send it on a cross-origin WebSocket upgrade, as in local
    development with the frontend and the API on different ports.

    The token carries the claims of the session token and the id of the
    session that asked for it. A sign-out of that session ends the sockets
    opened with this token. The WebSocket handler emits the audit events
    when it authenticates the connection, not this route.
    """
    ws_token = create_access_token(
        user_id=current_user.id,
        role=current_user.role.value,
        organization_id=current_user.organization_id,
        expires_delta=WS_TOKEN_TTL,
        session_version=current_user.session_version,
        session_id=request.state.session_id,
    )
    return {"token": ws_token}


@router.post(
    "/logout",
    response_model=LogoutResponse,
    responses={
        200: {"description": "Logout successful"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
    },
)
async def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> LogoutResponse:
    """Sign out the current session.

    Clears the session cookie and ends this session. The WebSocket connections
    of the account are closed with code 4000, and the sockets of this session
    cannot reconnect. The user's other devices stay signed in and reconnect
    with their own sessions.

    Emits the audit event `user_logout`.
    """
    client_ip = client_address(request)

    clear_auth_cookie(response)

    # The session is over, and so are its open sockets.
    #
    # First the record: a socket of this session that is accepted and not yet
    # subscribed is not on the account's channel, so the close below misses
    # it. Its token names this session (/ws-token), and its check after the
    # subscriptions and at every keepalive asks the record (ws_refusal). The
    # record is kept until no token of the session can be valid: the token of
    # this request or a copy the sliding cookie minted a moment ago, and a
    # socket token minted from either. Only this session is recorded; the
    # session version stays, so the person's other devices stay signed in.
    # The record lives in this process (see tokens._signed_out_sessions).
    now = datetime.now(UTC)
    until = now + timedelta(minutes=get_settings().auth.session_timeout_minutes)
    expires_at = request.state.session_expires_at
    if isinstance(expires_at, int | float):
        until = max(until, datetime.fromtimestamp(expires_at, tz=UTC))
    sign_out_session(request.state.session_id, until + WS_TOKEN_TTL)

    # Then the sockets that are open now. The manager does not keep a
    # socket's session, so the account's sockets close. 4000 is recoverable:
    # another device of the account reconnects with its own session, this
    # browser gets no new token.
    await manager.close_channel(f"user:{current_user.id}", code=4000, reason="session revoked")

    audit_user_logout(
        user=current_user,
        ip=client_ip,
        organization_id=current_user.organization_id,
        logout_type="manual",
    )

    return LogoutResponse(message="Logout successful")


@router.post(
    "/password-change",
    response_model=PasswordChangeResponse,
    responses={
        200: {"description": "Password changed successfully"},
        400: {
            "description": "Validation error (policy violation or password reuse)",
            "model": ValidationErrorResponse,
        },
        401: {
            "description": "Not authenticated or invalid old password",
            "model": ErrorResponse,
        },
    },
)
# The limiter of the sign-in, before the password is verified and hashed. The
# decorator runs after the session check, which costs no hash.
@limiter.limit(lambda: get_settings().auth.login_rate_limit)
async def password_change(
    request: Request,
    body: PasswordChangeRequest,
    background: BackgroundTasks,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PasswordChangeResponse:
    """Change the password of the signed-in user.

    The old password must be correct (401 otherwise). The new password must
    meet the password policy and must differ from the current and from the
    previous password (400 otherwise).

    The change ends every other session of the account and closes its open
    WebSocket connections with code 4000. This session gets a new cookie and
    stays signed in. When another password change or a reset has already ended
    this session, the route answers 401 `SESSION_REVOKED` and changes nothing. A notice of the
    change goes to the account's email.

    Rate limited like the sign-in.

    Emits the audit event `config_changed` with the key `user_password`. The
    event holds no password value.
    """
    client_ip = client_address(request)
    user_repo = UserRepository(db, current_user.organization_id)
    # The version get_current_user accepted the token for.
    verified_version = current_user.session_version

    if not verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    policy_errors = validate_password_policy(body.new_password)
    if policy_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": "New password does not meet policy requirements",
                "errors": policy_errors,
            },
        )

    # Reuse is checked against both the current and the last password.
    if check_password_reuse(body.new_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": "Password reuse not allowed",
                "errors": ["Cannot reuse your last password"],
            },
        )

    if current_user.last_password_hash and check_password_reuse(body.new_password, current_user.last_password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": "Password reuse not allowed",
                "errors": ["Cannot reuse your last password"],
            },
        )

    new_password_hash = hash_password(body.new_password)
    previous_password_hash = current_user.password_hash

    # Update the password (the old hash is kept for the reuse check) and end every
    # other session of the account: the version rises in the same transaction as
    # the new hash. The database computes it, not this request from the row it
    # read at its start.
    #
    # The version rises first, and only from the version this session was
    # accepted for. A reset that committed since has ended the session: the
    # UPDATE matches no row, nothing is written and no cookie is minted, or
    # this change would give a revoked session a new one. A match holds the row
    # lock until the commit, so the hashes below are written to the row the
    # version was raised on.
    new_session_version = await UserRepository.raise_session_version_from(db, current_user, verified_version)
    if new_session_version is None:
        await db.rollback()
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked by a password change. Please sign in again.",
            error_code="SESSION_REVOKED",
            headers={"WWW-Authenticate": "Bearer"},
        )
    await user_repo.update(
        current_user.id,
        password_hash=new_password_hash,
        last_password_hash=previous_password_hash,
    )

    await db.commit()

    # Open sockets of the account do not outlive the change. Now that the new
    # version is committed, close them all, this session's own too. The client
    # reconnects with a fresh WebSocket token.
    await manager.close_channel(f"user:{current_user.id}", code=4000, reason="session revoked")

    # Keep this session working: re-mint its cookie for the new version. This
    # must be the last Set-Cookie for access_token in the response so it lands
    # after (and replaces) the sliding-window cookie get_current_user set
    # earlier in this request.
    set_auth_cookie(
        response,
        create_access_token(
            user_id=current_user.id,
            role=current_user.role.value,
            organization_id=current_user.organization_id,
            session_version=new_session_version,
            # Still this session: a later sign-out ends its sockets.
            session_id=request.state.session_id,
        ),
    )

    audit_config_changed(
        actor_user=current_user,
        actor_ip=client_ip,
        config_key="user_password",
        old_value="[REDACTED]",
        new_value="[REDACTED]",
        organization_id=current_user.organization_id,
    )

    if current_user.email:
        organization = await OrganizationRepository(db).get_by_id(current_user.organization_id)
        mail.send_in_background(
            background,
            mail.compose_password_changed(
                to=current_user.email,
                language=_request_language(request, organization),
                organization_name=organization.name if organization else "Clavis",
                display_name=current_user.display_name,
                changed_at=datetime.now(UTC),
            ),
        )

    return PasswordChangeResponse(message="Password changed successfully")


@router.post(
    "/password-forgot",
    response_model=PasswordForgotResponse,
    responses={
        200: {"description": "Password reset request processed"},
        429: {"description": "Too many reset requests from this address"},
    },
)
# Anyone can name any address here, so an unlimited endpoint is a way to bury a
# third party's inbox in mail that legitimately comes from us.
@limiter.limit("5/minute")
async def password_forgot(
    request: Request,
    body: PasswordForgotRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> PasswordForgotResponse:
    """Email a password reset link to the account with this address.

    Open to unauthenticated callers, limited to 5 requests per minute and client address.

    The response is the same whether the email belongs to an account or not,
    so the route does not reveal which addresses are registered. For an
    active account the route invalidates its earlier reset tokens and emails a
    new link. The token is valid for 1 hour. The link is completed with
    `POST /auth/password-reset`. When the instance has no mail configured, the
    mail is not sent and the response stays the same.

    Emits no audit event, so that the audit log does not confirm an address.
    """
    # The organization is unknown here, so the lookup by email is unscoped.
    user = await UserRepository.get_by_email_unscoped(db, body.email)

    # The response is the same whether the email exists or not. A token is only
    # generated when the user exists.
    if user and user.is_active and user.email:
        # The repository stores only the SHA-256 hash of the token.
        reset_repo = PasswordResetRepository(db)

        await reset_repo.invalidate_for_user(user.id)

        raw_token = await reset_repo.create_token(user.id)

        await db.commit()

        # The token itself is never logged — a reset link in the application log
        # is a standing account-takeover primitive for anyone who can read logs.
        logger.info(
            "password_reset_token_generated",
            user_id=str(user.id),
            email=user.email,
        )

        organization = await OrganizationRepository(db).get_by_id(user.organization_id)
        mail.send_in_background(
            background,
            mail.compose_password_reset(
                to=user.email,
                language=_request_language(request, organization),
                organization_name=organization.name if organization else "Clavis",
                display_name=user.display_name,
                token=raw_token,
                expires_in_minutes=PasswordResetRepository.DEFAULT_EXPIRY_HOURS * 60,
            ),
        )

    # The work above must not change the response, neither its content nor,
    # ideally, its timing, or the endpoint becomes an account oracle.
    return PasswordForgotResponse(message="If the email exists, a password reset link has been sent")


@router.post(
    "/password-reset",
    response_model=PasswordResetResponse,
    responses={
        200: {"description": "Password reset successful"},
        400: {
            "description": "Validation error (invalid token or password policy violation)",
            "model": ValidationErrorResponse,
        },
    },
)
async def password_reset(
    request: Request,
    body: PasswordResetRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> PasswordResetResponse:
    """Set a new password with the token from the reset mail.

    The token must exist, be unexpired and unused, and belong to an active
    account. The new password must meet the password policy. Otherwise the
    route answers 400 and the token stays usable.

    The token works once. The new password ends every session of the account,
    closes its open WebSocket connections and clears the failed-attempt count
    and any lock. The user is not signed in and must sign in with the new
    password. A notice of the change goes to the account's email.

    Emits the audit event `config_changed` with the key `user_password_reset`.
    The event holds no password value.
    """
    client_ip = client_address(request)

    # The lookup is by hash and checks the expiry and used_at.
    reset_repo = PasswordResetRepository(db)
    token_record = await reset_repo.get_by_hash(body.token)

    if token_record is None:
        logger.warning(
            "password_reset_token_invalid",
            reason="token_not_found_or_expired",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": "Invalid or expired reset token",
                "errors": ["The password reset token is invalid or has expired"],
            },
        )

    # The token names only the user id, so the lookup is unscoped.
    user = await UserRepository.get_by_id_unscoped(db, token_record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": "Invalid reset token",
                "errors": ["The password reset token is invalid"],
            },
        )

    policy_errors = validate_password_policy(body.new_password)
    if policy_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": "New password does not meet policy requirements",
                "errors": policy_errors,
            },
        )

    new_password_hash = hash_password(body.new_password)

    user_repo = UserRepository(db, user.organization_id)

    # The old hash is kept for the reuse check. The version increment below ends
    # every session issued before the reset. No fresh cookie is set, because the
    # reset does not log anyone in.
    await user_repo.update(
        user.id,
        password_hash=new_password_hash,
        last_password_hash=user.password_hash,
        # The reset also clears the failed attempts and any lockout.
        failed_login_attempts=0,
        locked_until=None,
    )
    # The database raises the version in this transaction. It is not computed from
    # the row read above.
    await UserRepository.raise_session_version(db, user)

    await reset_repo.mark_used(token_record.id)

    # Expired tokens are cleaned up on each reset request.
    await reset_repo.cleanup_expired()

    await db.commit()

    # End the account's open sockets too: every session issued before the reset is
    # already dead by version, and live connections follow with the same 4000.
    await manager.close_channel(f"user:{user.id}", code=4000, reason="session revoked")

    logger.info(
        "password_reset_token_consumed",
        user_id=str(user.id),
        email=user.email,
    )

    # The actor is the account itself, because the requester is not authenticated.
    audit_config_changed(
        actor_user=user,
        actor_ip=client_ip,
        config_key="user_password_reset",
        old_value="[REDACTED]",
        new_value="[REDACTED]",
        organization_id=user.organization_id,
    )

    # Tell the account owner. If the reset was not theirs, this notice is the
    # only signal they get that someone used a link from their inbox.
    if user.email:
        organization = await OrganizationRepository(db).get_by_id(user.organization_id)
        mail.send_in_background(
            background,
            mail.compose_password_changed(
                to=user.email,
                language=_request_language(request, organization),
                organization_name=organization.name if organization else "Clavis",
                display_name=user.display_name,
                changed_at=datetime.now(UTC),
            ),
        )

    return PasswordResetResponse(message="Password has been reset successfully")


@router.put(
    "/profile",
    response_model=ProfileUpdateResponse,
    responses={
        200: {"description": "Profile updated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        422: {"description": "Validation error", "model": ValidationErrorResponse},
    },
)
async def update_profile(
    request: Request,
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileUpdateResponse:
    """Change the display name of the signed-in user.

    Open to every signed-in user. The route changes the display name only. It
    changes neither the email nor the role. A request that leaves the display
    name as it is changes nothing, answers with the message "No changes made"
    and emits no audit event.

    Emits the audit event `user_updated` when the name changed.
    """
    client_ip = client_address(request)

    changes: dict[str, dict[str, str | None]] = {}

    if body.display_name is not None and body.display_name != current_user.display_name:
        changes["display_name"] = {
            "from": current_user.display_name,
            "to": body.display_name,
        }

    if not changes:
        return ProfileUpdateResponse(
            id=current_user.id,
            email=current_user.email,
            display_name=current_user.display_name,
            role=current_user.role.value,
            organization_id=current_user.organization_id,
            message="No changes made",
        )

    user_repo = UserRepository(db, current_user.organization_id)

    update_kwargs = {}
    if body.display_name is not None:
        update_kwargs["display_name"] = body.display_name

    await user_repo.update(current_user.id, **update_kwargs)
    await db.commit()
    await db.refresh(current_user)

    audit_user_updated(
        actor_user=current_user,
        actor_ip=client_ip,
        target_user=current_user,
        changes=changes,
        organization_id=current_user.organization_id,
    )

    return ProfileUpdateResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        role=current_user.role.value,
        organization_id=current_user.organization_id,
        message="Profile updated successfully",
    )
