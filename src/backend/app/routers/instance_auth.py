"""
Instance-operator auth router: sign-in of ``instance_admins`` with a password
and a mandatory TOTP, sign-out, and the password change.

No route here creates an operator. Only ``python -m app.cli instance-admin
create`` does. ``last_login_at`` marks the one handout of the provisioning URI
before the TOTP is confirmed. The session is an instance-scoped JWT
(``scope: "instance"``, no organization), and the audit events carry
``organization_id=None``. Never log a password, a TOTP code or the TOTP secret.

A disabled operator (``instance_admins.disabled_at``, set by ``instance-admin
disable``) is refused after the password check, so only someone who proved the
credentials learns the state. ``require_instance_admin`` refuses an open
session of it at the next request.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_instance_admin
from app.auth.instance import actor_from_instance_admin
from app.auth.passwords import check_password_reuse, hash_password, validate_password_policy, verify_password
from app.auth.tokens import (
    INSTANCE_ADMIN_COOKIE_NAME,
    clear_instance_admin_cookie,
    create_instance_admin_token,
    set_instance_admin_cookie,
    sign_out_session,
)
from app.auth.totp import generate_provisioning_uri, verify_totp_code
from app.client_address import client_address
from app.config import get_settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.middleware.rate_limit import limiter
from app.models.instance_admin import InstanceAdmin
from app.repositories.instance_admin import InstanceAdminRepository
from app.routers.schemas import (
    ErrorResponse,
    InstanceLoginRequest,
    InstanceLoginResponse,
    InstanceLogoutResponse,
    InstanceMeResponse,
    InstancePasswordChangeRequest,
    InstancePasswordChangeResponse,
    InstanceTotpConfirmRequest,
    InstanceTotpConfirmResponse,
    ValidationErrorResponse,
)
from app.services.audit import record_audit_event

router = APIRouter()


def _audit_login_failed(email: str, ip: str, reason: str) -> None:
    record_audit_event(
        event="instance.login_failed",
        actor={"ip": ip},
        target={"entity": "instance_admin", "email": email},
        organization_id=None,
        reason=reason,
    )


def _audit_login(admin: InstanceAdmin, ip: str, auth_method: str) -> None:
    record_audit_event(
        event="instance.login",
        actor=actor_from_instance_admin(admin, ip),
        target={"entity": "instance_admin", "id": str(admin.id), "email": admin.email},
        organization_id=None,
        auth_method=auth_method,
    )


def _issue_session(response: Response, admin: InstanceAdmin, session_version: int) -> None:
    """Set the operator cookie for the session version the sign-in VERIFIED.

    Never the version of a row read after the proof: a reset that committed
    while the sign-in ran raised it, and a cookie minted from the new version
    would be a valid session for somebody who proved the old factor.
    """
    set_instance_admin_cookie(response, create_instance_admin_token(admin.id, session_version=session_version))


def _refuse_changed_account(email: str, ip: str) -> HTTPException:
    """Refuse a sign-in whose account changed between its read and its write.

    A reset or a password change committed meanwhile, so what the request
    proved is no longer the account's state. The answer is the generic 401 of
    every refused sign-in. The audit reason tells the cases apart.
    """
    _audit_login_failed(email, ip, "account_changed_during_sign_in")
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
    )


@router.post(
    "/login",
    response_model=InstanceLoginResponse,
    responses={
        200: {"description": "Login successful (or TOTP setup required)"},
        401: {"description": "Invalid credentials", "model": ErrorResponse},
    },
)
# Five requests from one address per minute, the sixth is refused. The limiter
# is the one of the user login.
@limiter.limit("5/minute")
async def instance_login(
    request: Request,
    body: InstanceLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> InstanceLoginResponse:
    """Sign in an instance operator with email, password and a TOTP code.

    On success the route sets the operator cookie. An unknown email, a wrong
    password and a wrong or missing code all answer the same 401, so the answer
    never shows which factor failed. A disabled operator account gets 403
    `INSTANCE_ADMIN_DISABLED`, but only after the password was right. While
    the TOTP setup is not confirmed, a correct password returns
    `totp_setup_required` and no cookie. The first such answer carries the
    provisioning URI, and later ones do not. The route allows 5 requests per
    minute. It emits the audit event `instance.login` on success and
    `instance.login_failed` on failure.
    """
    settings = get_settings()
    client_ip = client_address(request)

    admin = await InstanceAdminRepository(db).get_by_email(body.email)

    if admin is None:
        _audit_login_failed(body.email, client_ip, "user_not_found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # The state this request verifies. The write at the end names it, so a
    # reset that commits while the request runs refuses the sign-in instead of
    # handing its new session version to this request.
    verified_version = admin.session_version
    verified_secret = admin.totp_secret
    verified_confirmed_at = admin.totp_confirmed_at

    if not verify_password(body.password, admin.password_hash):
        _audit_login_failed(body.email, client_ip, "invalid_password")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # After the password, so only someone who proved the credentials learns that
    # the account is disabled. The user login does the same for ORG_ARCHIVED.
    if admin.disabled_at is not None:
        _audit_login_failed(body.email, client_ip, "account_disabled")
        raise CodedHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operator account is disabled.",
            error_code="INSTANCE_ADMIN_DISABLED",
        )

    # Enrollment pending: the provisioning URI goes out once, at the first login
    # (last_login_at is null), then only the setup-required answer until
    # totp/confirm completes the enrollment.
    if admin.totp_confirmed_at is None:
        provisioning_uri: str | None = None
        if admin.last_login_at is None and admin.totp_secret:
            provisioning_uri = generate_provisioning_uri(
                secret=admin.totp_secret,
                email=admin.email,
                issuer=settings.webauthn.rp_name,
            )

        # The handout is recorded on the state it was built from: after a
        # reset in between, the new secret's handout is still owed.
        handed_out = await InstanceAdminRepository(db).complete_sign_in(
            admin.id,
            verified_session_version=verified_version,
            verified_totp_secret=verified_secret,
            verified_totp_confirmed_at=verified_confirmed_at,
            last_login_at=datetime.now(UTC),
        )
        if handed_out is None:
            raise _refuse_changed_account(body.email, client_ip)
        await db.commit()

        return InstanceLoginResponse(
            status="totp_setup_required",
            totp_setup_required=True,
            provisioning_uri=provisioning_uri,
        )

    # TOTP confirmed: the code is part of the login from here on.
    if not admin.totp_secret or not body.totp_code or not verify_totp_code(admin.totp_secret, body.totp_code):
        _audit_login_failed(body.email, client_ip, "invalid_totp")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    signed_in = await InstanceAdminRepository(db).complete_sign_in(
        admin.id,
        verified_session_version=verified_version,
        verified_totp_secret=verified_secret,
        verified_totp_confirmed_at=verified_confirmed_at,
        last_login_at=datetime.now(UTC),
    )
    if signed_in is None:
        raise _refuse_changed_account(body.email, client_ip)
    await db.commit()

    _issue_session(response, admin, verified_version)
    _audit_login(admin, client_ip, auth_method="password+totp")

    return InstanceLoginResponse(
        status="success",
        email=admin.email,
    )


@router.post(
    "/totp/confirm",
    response_model=InstanceTotpConfirmResponse,
    responses={
        200: {"description": "TOTP confirmed, operator logged in"},
        400: {"description": "TOTP setup not initiated", "model": ErrorResponse},
        401: {"description": "Invalid credentials", "model": ErrorResponse},
        409: {"description": "TOTP already confirmed", "model": ErrorResponse},
    },
)
@limiter.limit("5/minute")
async def instance_totp_confirm(
    request: Request,
    body: InstanceTotpConfirmRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> InstanceTotpConfirmResponse:
    """Confirm the TOTP setup of an instance operator and sign the operator in.

    No session exists before the setup is complete, so the request carries the
    email and the password again, with the code from the provisioning URI. On
    success the route sets the operator cookie. A wrong email, password or code
    answers 401. A disabled account answers 403 `INSTANCE_ADMIN_DISABLED` after
    the password was right. An account that already confirmed its TOTP answers
    409, and an account without a TOTP secret answers 400. The route allows 5
    requests per minute. It emits the audit event `instance.login` on success
    and `instance.login_failed` on failure.
    """
    client_ip = client_address(request)

    admin = await InstanceAdminRepository(db).get_by_email(body.email)

    if admin is None:
        _audit_login_failed(body.email, client_ip, "user_not_found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # The state this request verifies, as in instance_login.
    verified_version = admin.session_version
    verified_secret = admin.totp_secret
    verified_confirmed_at = admin.totp_confirmed_at

    if not verify_password(body.password, admin.password_hash):
        _audit_login_failed(body.email, client_ip, "invalid_password")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # The check of instance_login, at the same place. Without it, a disabled
    # operator with a pending enrollment, for example after instance-admin
    # reset-totp, could sign in here.
    if admin.disabled_at is not None:
        _audit_login_failed(body.email, client_ip, "account_disabled")
        raise CodedHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operator account is disabled.",
            error_code="INSTANCE_ADMIN_DISABLED",
        )

    if admin.totp_confirmed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="TOTP is already confirmed for this account",
        )

    if not admin.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP setup not initiated. The account has no TOTP secret.",
        )

    if not verify_totp_code(admin.totp_secret, body.totp_code):
        _audit_login_failed(body.email, client_ip, "invalid_totp")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    # Confirm the secret the code was checked against, and no other: after a
    # reset in between the row holds a new secret nobody has proven.
    now = datetime.now(UTC)
    signed_in = await InstanceAdminRepository(db).complete_sign_in(
        admin.id,
        verified_session_version=verified_version,
        verified_totp_secret=verified_secret,
        verified_totp_confirmed_at=verified_confirmed_at,
        totp_confirmed_at=now,
        last_login_at=now,
    )
    if signed_in is None:
        raise _refuse_changed_account(body.email, client_ip)
    await db.commit()

    _issue_session(response, admin, verified_version)
    _audit_login(admin, client_ip, auth_method="totp_confirm")

    return InstanceTotpConfirmResponse(
        status="success",
        email=admin.email,
    )


@router.get(
    "/me",
    response_model=InstanceMeResponse,
    responses={
        200: {"description": "Current operator info"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Token is not instance-scoped", "model": ErrorResponse},
    },
)
async def instance_me(
    admin: InstanceAdmin = Depends(require_instance_admin),
) -> InstanceMeResponse:
    """Return the id, the email and the TOTP state of the signed-in instance operator.

    Open to an instance operator session only. The operator page calls it to check
    the session when it loads.
    """
    return InstanceMeResponse(
        id=admin.id,
        email=admin.email,
        totp_confirmed=admin.totp_confirmed_at is not None,
    )


@router.post(
    "/logout",
    response_model=InstanceLogoutResponse,
    responses={
        200: {"description": "Logout successful"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
    },
)
async def instance_logout(
    request: Request,
    response: Response,
    _admin: InstanceAdmin = Depends(require_instance_admin),
    _instance_token: str | None = Cookie(default=None, alias=INSTANCE_ADMIN_COOKIE_NAME),
) -> InstanceLogoutResponse:
    """Sign out the instance operator on this device.

    The route clears the operator cookie and records the session as signed out.
    A copy of the session token kept elsewhere is refused from then on, although
    its signature stays valid until it expires. Only this session ends. The
    operator's sessions on other devices and an organization session in the same
    browser stay open.
    """
    # Kept until no token of the session can be valid: the token of this
    # request, or a copy the sliding cookie minted a moment ago. The record
    # lives in this process (see tokens._signed_out_sessions).
    until = datetime.now(UTC) + timedelta(minutes=get_settings().auth.session_timeout_minutes)
    expires_at = request.state.session_expires_at
    if isinstance(expires_at, int | float):
        until = max(until, datetime.fromtimestamp(expires_at, tz=UTC))
    sign_out_session(request.state.session_id, until)

    clear_instance_admin_cookie(response)

    return InstanceLogoutResponse(message="Logout successful")


@router.post(
    "/password",
    response_model=InstancePasswordChangeResponse,
    responses={
        200: {"description": "Password changed successfully"},
        400: {
            "description": "New password violates the password policy, or reuses the current/last password",
            "model": ValidationErrorResponse,
        },
        401: {
            "description": "Not authenticated, current password incorrect, or the session was revoked meanwhile",
            "model": ErrorResponse,
        },
        403: {"description": "Token is not instance-scoped, or the account is disabled", "model": ErrorResponse},
    },
)
# The rate limit of the sign-in, before the current password is verified and hashed. The
# decorator runs after the session check, which costs no hash.
@limiter.limit("5/minute")
async def instance_password_change(
    request: Request,
    body: InstancePasswordChangeRequest,
    response: Response,
    admin: InstanceAdmin = Depends(require_instance_admin),
    db: AsyncSession = Depends(get_db),
) -> InstancePasswordChangeResponse:
    """Change the password of the signed-in instance operator.

    The request needs the current password, and a wrong one answers 401. The new
    password must meet the password policy and must not equal the current or the
    last password, otherwise the route answers 400. The change ends every other
    session of the account. This session stays open with a fresh operator cookie.

    When a reset ended the session while the request ran, the route answers 401
    `SESSION_REVOKED` and sets no cookie. The route allows 5 requests per minute.
    It emits the audit event `instance.password_changed`.
    """
    # The version require_instance_admin accepted the token for.
    verified_version = admin.session_version

    if not verify_password(body.old_password, admin.password_hash):
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

    if check_password_reuse(body.new_password, admin.password_hash) or check_password_reuse(
        body.new_password, admin.last_password_hash
    ):
        raise CodedHTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must not match the current or the last password.",
            error_code="INSTANCE_PASSWORD_REUSE_NOT_ALLOWED",
        )

    # The database raises the version in the UPDATE that stores the new hash.
    # This request learns the new value from the row. It raises it only from the
    # version this session was accepted for: a reset that committed since has
    # ended the session, and a cookie minted here would bring it back.
    previous_hash = admin.password_hash
    updated = await InstanceAdminRepository(db).raise_session_version_from(
        admin.id,
        verified_version,
        password_hash=hash_password(body.new_password),
        last_password_hash=previous_hash,
    )
    if updated is None:
        await db.rollback()
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked by a password change. Please sign in again.",
            error_code="SESSION_REVOKED",
            headers={"WWW-Authenticate": "Bearer"},
        )
    new_session_version = updated.session_version
    await db.commit()

    # Re-mint this session's cookie for the new version. It keeps the session
    # id, so its sign-out ends this cookie too.
    set_instance_admin_cookie(
        response,
        create_instance_admin_token(admin.id, session_version=new_session_version, session_id=request.state.session_id),
    )

    record_audit_event(
        event="instance.password_changed",
        actor=actor_from_instance_admin(admin, client_address(request)),
        target={"entity": "instance_admin", "id": str(admin.id), "email": admin.email},
        organization_id=None,
    )

    return InstancePasswordChangeResponse()
