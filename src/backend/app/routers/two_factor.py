"""
Two-factor authentication router.

Provides endpoints for TOTP 2FA setup, verification, and authentication.
This implements TOTP 2FA per RFC 6238 with recovery codes.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.passwords import verify_password
from app.auth.tokens import (
    SECOND_FACTOR_PROOF_COOKIE_NAME,
    clear_second_factor_proof_cookie,
    create_access_token,
    decode_second_factor_proof,
    decode_token,
    second_factor_proof_last_login_marker,
    set_auth_cookie,
)
from app.auth.totp import (
    find_and_remove_recovery_code,
    generate_provisioning_uri,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    verify_totp_code,
)
from app.client_address import client_address
from app.config import get_settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.middleware.rate_limit import limiter
from app.models.user import User
from app.repositories.organization import OrganizationRepository
from app.repositories.user import UserRepository
from app.routers.schemas import (
    ErrorResponse,
    TotpAuthenticateRequest,
    TotpAuthenticateResponse,
    TotpDisableRequest,
    TotpDisableResponse,
    TotpSetupResponse,
    TotpVerifyRequest,
    TotpVerifyResponse,
)
from app.services.audit import (
    audit_config_changed,
    audit_user_locked,
    audit_user_login,
    audit_user_login_failed,
)

router = APIRouter()


def _password_step_proven(proof_token: str | None, user: User) -> dict[str, Any] | None:
    """
    The validated proof payload iff the proof cookie shows that
    /api/auth/login checked THIS account's password on this login, else None.

    Signature and expiry are verified by decode_second_factor_proof. The proof is
    signed with its own key and is not a session token anywhere. The ``sub`` claim
    binds the proof to the account named in the request, the ``sv`` claim dies
    with the proof when the password changes mid-flight, and the ``ll`` snapshot
    must still match users.last_login: a login the proof already completed has
    changed last_login, so a retained or replayed proof matches no account.

    Returns the payload so callers can carry the ``ll`` marker, NOT a value
    reread from the user object, into the proof-consuming UPDATE. A refresh
    between validation and consumption would adopt a concurrent winner's
    last_login and let both requests consume one proof.
    """
    if not proof_token:
        return None
    payload = decode_second_factor_proof(proof_token)
    if payload is None:
        return None
    if (
        payload.get("sub") == str(user.id)
        and payload.get("sv", 0) == user.session_version
        and payload.get("ll") == second_factor_proof_last_login_marker(user.last_login)
    ):
        return payload
    return None


def _proof_expected_last_login(payload: dict[str, Any]) -> datetime | None:
    """The users.last_login the proof was minted with, from the proof itself.

    A naive marker cannot occur, because the marker is always UTC-normalized.
    The UTC assumption keeps the comparison with the timestamptz column correct
    either way.
    """
    marker = payload.get("ll")
    if marker is None:
        return None
    parsed = datetime.fromisoformat(marker)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


async def _consume_second_factor_proof(
    db: AsyncSession,
    user: User,
    expected_last_login: datetime | None,
    *,
    verified_version: int,
    verified_totp_secret: str | None,
) -> bool:
    """
    Make the proof single-use on the server: set last_login (and reset the
    failed-attempt counter) with ONE UPDATE whose WHERE clause still names the
    last_login the proof was minted with. ``expected_last_login`` comes from the
    validated proof, never from the user object, which a repository refresh may
    have moved to a concurrent winner's value in the meantime.

    The same UPDATE names the account state this request verified: the session
    version the proof was checked against and the TOTP secret that was loaded for
    the code. A password reset, a password change
    or another revocation that committed while the sign-in ran has raised the
    version, so the UPDATE matches no row and no session is minted for
    somebody who proved the state from before.

    The request that wins the race changes last_login, so a replay — even of
    a retained cookie, even concurrently — matches no row and gets no
    session. Returns True iff this request was the winner. The loser is
    rolled back whole, so whatever else it staged in this transaction (a
    recovery-code removal) is undone with it.
    """
    stmt = (
        update(User)
        .where(User.id == user.id, User.session_version == verified_version)
        .values(last_login=datetime.now(UTC), failed_login_attempts=0)
        .execution_options(synchronize_session=False)
    )
    if verified_totp_secret is None:
        stmt = stmt.where(User.totp_secret.is_(None))
    else:
        stmt = stmt.where(User.totp_secret == verified_totp_secret)
    if expected_last_login is None:
        stmt = stmt.where(User.last_login.is_(None))
    else:
        stmt = stmt.where(User.last_login == expected_last_login)
    result = await db.execute(stmt)
    if not (isinstance(result, CursorResult) and result.rowcount == 1):
        await db.rollback()
        return False
    await db.commit()
    return True


async def _refused_completion_reason(db: AsyncSession, user_id: UUID, verified_version: int) -> str:
    """Why the completing UPDATE matched no row, for the audit of the refused sign-in.

    Read after the fact and used for the audit only: the decision was taken by
    the UPDATE. A version other than the verified one means a revocation
    committed while the sign-in ran, anything else that another request
    completed this login first.
    """
    current = (await db.execute(select(User.session_version).where(User.id == user_id))).scalar_one_or_none()
    return "2fa_proof_already_used" if current == verified_version else "2fa_account_changed_during_sign_in"


def _password_step_required_response() -> CodedHTTPException:
    """The one answer to a second factor without a valid, unused proof of the password step."""
    return CodedHTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Password verification required before the second factor",
        error_code="PASSWORD_STEP_REQUIRED",
    )


def _refused_without_proof_reason(user: User | None) -> str:
    """The audit reason of a second factor refused for want of a valid proof.

    The client gets one answer for all of these. The audit keeps the reason the
    account itself would have given.
    """
    if user is None:
        return "2fa_user_not_found"
    if not user.totp_enabled or not user.totp_secret:
        return "2fa_not_enabled"
    if not user.is_active:
        return "2fa_account_disabled"
    if user.locked_until and user.locked_until > datetime.now(UTC):
        return "2fa_account_locked"
    return "2fa_missing_password_proof"


async def _refuse_locked_account(
    db: AsyncSession,
    user_repo: UserRepository,
    user: User,
    email: str,
    client_ip: str,
    reason: str,
) -> None:
    """
    Refuse a locked account before any second factor is checked. An expired lock
    is cleared, exactly like login clears it.
    """
    if not user.locked_until:
        return
    now = datetime.now(UTC)
    if user.locked_until > now:
        audit_user_login_failed(
            email=email,
            ip=client_ip,
            reason=reason,
            organization_id=user.organization_id,
        )
        raise CodedHTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account locked until {user.locked_until.isoformat()}. Please try again later.",
            error_code="ACCOUNT_LOCKED",
            extra={"locked_until": user.locked_until.isoformat()},
        )
    await user_repo.update(user.id, locked_until=None, failed_login_attempts=0)
    await db.commit()


async def _count_failed_attempt(
    db: AsyncSession,
    user_repo: UserRepository,
    user: User,
    client_ip: str,
) -> datetime | None:
    """
    A wrong second factor counts like a wrong password: it
    increments the shared failed-attempt counter and locks the account at the
    configured threshold. Returns the lock deadline when this attempt caused
    the lock, else None.
    """
    settings = get_settings()
    await user_repo.increment_failed_login_attempts(user.id)
    await db.commit()

    refreshed = await user_repo.get_by_id(user.id)
    if refreshed is None or refreshed.failed_login_attempts < settings.auth.max_login_attempts:
        return None

    lockout_until = datetime.now(UTC) + timedelta(minutes=settings.auth.lockout_duration_minutes)
    await user_repo.update(refreshed.id, locked_until=lockout_until)
    await db.commit()
    audit_user_locked(
        user=refreshed,
        ip=client_ip,
        organization_id=refreshed.organization_id,
        reason="max_failed_attempts",
    )
    return lockout_until


def _account_locked_response(lockout_until: datetime) -> CodedHTTPException:
    return CodedHTTPException(
        status_code=status.HTTP_423_LOCKED,
        detail=f"Account locked until {lockout_until.isoformat()} due to too many failed login attempts. "
        f"Please try again later.",
        error_code="ACCOUNT_LOCKED",
        extra={"locked_until": lockout_until.isoformat()},
    )


@router.post(
    "/totp/setup",
    response_model=TotpSetupResponse,
    responses={
        200: {"description": "TOTP setup initiated successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        409: {
            "description": "2FA already enabled",
            "model": ErrorResponse,
        },
    },
)
async def totp_setup(
    _request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TotpSetupResponse:
    """
    Start the TOTP setup of the signed-in user and return the secret and the recovery codes.

    Open to every signed-in user. The route stores a new TOTP secret and eight
    hashed recovery codes. TOTP stays off until the user confirms a code with
    POST /auth/2fa/totp/verify. A second call before that replaces the secret and
    the codes.

    The response carries the secret, a provisioning URI for a QR code and the
    eight recovery codes in plain text. This is the only time the codes are
    shown, so the user must save them.

    Answers 409 when TOTP is already enabled, and 400 when the account has no
    email.
    """
    settings = get_settings()
    user_repo = UserRepository(db, current_user.organization_id)

    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="TOTP 2FA is already enabled for this account",
        )

    secret = generate_totp_secret()

    if not current_user.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required for TOTP setup")
    provisioning_uri = generate_provisioning_uri(
        secret=secret,
        email=current_user.email,
        issuer=settings.webauthn.rp_name,
    )

    recovery_codes = generate_recovery_codes(8)

    hashed_recovery_codes = [hash_recovery_code(code) for code in recovery_codes]

    # Store the secret and the hashed recovery codes. 2FA is not enabled yet.
    await user_repo.update(
        current_user.id,
        totp_secret=secret,
        totp_recovery_codes={"codes": hashed_recovery_codes},
    )

    await db.commit()

    return TotpSetupResponse(
        secret=secret,
        provisioning_uri=provisioning_uri,
        recovery_codes=recovery_codes,
        message="TOTP setup initiated. Save recovery codes securely. "
        "Scan the QR code with your authenticator app and verify to enable 2FA.",
    )


@router.post(
    "/totp/verify",
    response_model=TotpVerifyResponse,
    responses={
        200: {"description": "TOTP 2FA enabled successfully"},
        400: {
            "description": "Invalid TOTP code or setup not initiated",
            "model": ErrorResponse,
        },
        401: {"description": "Not authenticated", "model": ErrorResponse},
    },
)
async def totp_verify(
    request: Request,
    response: Response,
    body: TotpVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> TotpVerifyResponse:
    """
    Enable TOTP for the signed-in user after a correct code from the authenticator app.

    Open to every signed-in user. Call POST /auth/2fa/totp/setup first. Once the
    code is correct, later sign-ins require a second factor. Answers 400 when the
    setup was not started, when TOTP is already enabled, or when the code is wrong.

    A token with the purpose 2fa_setup is issued at the sign-in of an
    organization admin whose TOTP is not enabled yet. With such a token the route
    also completes the sign-in: it sets the normal auth cookie, updates last_login
    and returns the user. If the account changed since the token was issued, for
    example by a password reset, the route answers 401 with the error code
    SESSION_REVOKED and leaves TOTP off.

    Emits audit event: config_changed (key totp_2fa_enabled)
    Emits audit event: user_login (method totp_setup, only when it completes a sign-in)
    """
    client_ip = client_address(request)
    user_repo = UserRepository(db, current_user.organization_id)

    # The state this request verifies: the session version its token was
    # accepted for and the secret the code is checked with.
    verified_version = current_user.session_version
    verified_totp_secret = current_user.totp_secret

    if not verified_totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP setup not initiated. Call POST /auth/2fa/totp/setup first.",
        )

    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP 2FA is already enabled",
        )

    if not verify_totp_code(verified_totp_secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code. Please try again.",
        )

    # Find out whether this is a 2fa_setup token (an org_admin completing the
    # mandatory setup). The token must be the one get_current_user authenticated,
    # in the same order: the Bearer header before the cookie. With the cookie
    # first, beside a Bearer session a cookie that nobody had checked (of a
    # signed-out session, of another account) would decide whether this request
    # completes a sign-in and gets a new session cookie.
    token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1] if " " in authorization else None
    elif access_token:
        token = access_token

    is_setup_token = False
    if token:
        try:
            payload = decode_token(token)
            is_setup_token = payload.get("purpose") == "2fa_setup"
        except Exception:
            # A token that does not decode is not a setup token. This is not
            # expected, because get_current_user already validated it.
            pass

    if is_setup_token:
        # The setup token completes a sign-in, so enabling the factor and
        # recording the login are ONE UPDATE that names the verified state. A
        # password reset or another revocation that committed since the token
        # was accepted has raised the version: the UPDATE matches no row, the
        # factor stays off and no session is minted for somebody who proved
        # the state from before.
        result = await db.execute(
            update(User)
            .where(
                User.id == current_user.id,
                User.session_version == verified_version,
                User.totp_secret == verified_totp_secret,
                User.totp_enabled.is_(False),
            )
            .values(totp_enabled=True, last_login=datetime.now(UTC), failed_login_attempts=0)
            .execution_options(synchronize_session=False)
        )
        if not (isinstance(result, CursorResult) and result.rowcount == 1):
            await db.rollback()
            raise CodedHTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session revoked by a password change. Please sign in again.",
                error_code="SESSION_REVOKED",
                headers={"WWW-Authenticate": "Bearer"},
            )
        await db.commit()
        await db.refresh(current_user)
    else:
        await user_repo.update(
            current_user.id,
            totp_enabled=True,
        )

        await db.commit()

    audit_config_changed(
        actor_user=current_user,
        actor_ip=client_ip,
        config_key="totp_2fa_enabled",
        old_value="false",
        new_value="true",
        organization_id=current_user.organization_id,
    )

    if is_setup_token:
        # The normal token is for the version that was verified: a revocation that
        # commits after the UPDATE above raises the version, and this token ends
        # with it.
        normal_token = create_access_token(
            user_id=current_user.id,
            role=current_user.role.value,
            organization_id=current_user.organization_id,
            session_version=verified_version,
        )
        set_auth_cookie(response, normal_token)

        audit_user_login(
            user=current_user,
            ip=client_ip,
            organization_id=current_user.organization_id,
            method="totp_setup",
        )

        return TotpVerifyResponse(
            message="TOTP 2FA enabled successfully. Login complete.",
            status="success",
            user_id=current_user.id,
            email=current_user.email,
            display_name=current_user.display_name,
            role=current_user.role.value,
        )

    return TotpVerifyResponse(
        message="TOTP 2FA enabled successfully. You will now be required to provide a TOTP code when logging in."
    )


@router.post(
    "/totp/authenticate",
    response_model=TotpAuthenticateResponse,
    responses={
        200: {"description": "2FA authentication successful"},
        401: {
            "description": (
                "No valid proof of the password step (error code PASSWORD_STEP_REQUIRED), "
                "or, after a valid proof, an invalid code or an account that cannot sign in"
            ),
            "model": ErrorResponse,
        },
        423: {"description": "Account locked (only after a valid proof)", "model": ErrorResponse},
    },
)
@limiter.limit(lambda: get_settings().auth.login_rate_limit)
async def totp_authenticate(
    request: Request,
    body: TotpAuthenticateRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    second_factor_proof: str | None = Cookie(default=None, alias=SECOND_FACTOR_PROOF_COOKIE_NAME),
) -> TotpAuthenticateResponse:
    """
    Complete a sign-in with a TOTP code or a recovery code.

    Needs no session. Call it after POST /auth/login answered with
    status '2fa_required'. The request must carry the proof cookie that login
    set after the password step for the account it names. The route checks
    that proof first. Every request without a valid proof gets the same 401
    with the error code PASSWORD_STEP_REQUIRED, whatever the email, so the
    answer says nothing about the account. The proof works once: a replay or a
    concurrent second request gets the same 401. The route is rate limited
    like login.

    The request names the account by email. The code is either a 6-digit TOTP
    code or a recovery code of 8 characters. A recovery code works once.

    After a valid proof: an account without TOTP or a disabled account gets
    401. A locked account gets 423 with the error code ACCOUNT_LOCKED, before
    any code is checked. A wrong code gets 401, counts as a failed login
    attempt, and locks the account at the configured limit. A member of an
    archived organization gets 403 with the error code ORG_ARCHIVED and no
    session.

    On success the route sets the auth cookie, clears the proof cookie, resets the
    failed attempts and updates last_login. With a recovery code, the response
    also says how many codes are left and warns when two or fewer remain.

    Emits audit event: user_login (method totp or recovery_code)
    Emits audit event: user_login_failed (when the account or the factor is refused, not for ORG_ARCHIVED)
    Emits audit event: user_locked (when a wrong code reaches the lock limit)
    """
    client_ip = client_address(request)

    # The organization is unknown at this point, so the lookup by email is unscoped.
    user = await UserRepository.get_by_email_unscoped(db, body.email)

    # The second factor completes only a login that the password started: without
    # the proof cookie from /api/auth/login, a code alone, even a correct one,
    # completes nothing. The validated payload is kept because its ``ll`` marker,
    # not a reread of the user object, feeds the proof-consuming UPDATE below.
    #
    # The proof is checked before anything about the account, and every request
    # without a valid one gets the same answer. The audit keeps the reason of
    # each refusal: the second factor is the control that exists to be attacked,
    # and without a record brute force against it is invisible.
    proof = _password_step_proven(second_factor_proof, user) if user else None
    if user is None or proof is None:
        audit_user_login_failed(
            email=body.email,
            ip=client_ip,
            reason=_refused_without_proof_reason(user),
            organization_id=user.organization_id if user else None,
        )
        raise _password_step_required_response()

    # From here on the request proved this account's password, so the answers
    # may name the account's state.
    if not user.totp_enabled or not user.totp_secret:
        audit_user_login_failed(
            email=body.email,
            ip=client_ip,
            reason="2fa_not_enabled",
            organization_id=user.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="2FA not enabled for this account",
        )

    if not user.is_active:
        audit_user_login_failed(
            email=body.email,
            ip=client_ip,
            reason="2fa_account_disabled",
            organization_id=user.organization_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled",
        )

    user_repo = UserRepository(db, user.organization_id)

    # A locked account is refused before any code is checked. The second factor
    # must not be a way around the lock that a wrong password set.
    await _refuse_locked_account(db, user_repo, user, body.email, client_ip, reason="2fa_account_locked")

    # The state this request verifies: the session version the proof was
    # checked against and the secret the code is checked with. The session is
    # minted for this version only, and only while the row still shows both.
    verified_version = user.session_version
    verified_totp_secret = user.totp_secret

    # A recovery code has 8 characters, a TOTP code 6 digits.
    code = body.code.strip()
    is_recovery_code = len(code) == 8
    recovery_code_used = False
    remaining_recovery_codes = None

    if is_recovery_code:
        if not user.totp_recovery_codes or "codes" not in user.totp_recovery_codes:
            audit_user_login_failed(
                email=body.email,
                ip=client_ip,
                reason="2fa_no_recovery_codes",
                organization_id=user.organization_id,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid recovery code",
            )

        hashed_codes = user.totp_recovery_codes["codes"]
        valid, remaining_codes = find_and_remove_recovery_code(code.upper(), hashed_codes)

        if not valid:
            # A wrong code counts like a wrong password.
            lockout_until = await _count_failed_attempt(db, user_repo, user, client_ip)
            audit_user_login_failed(
                email=body.email,
                ip=client_ip,
                reason="2fa_invalid_recovery_code",
                organization_id=user.organization_id,
            )
            if lockout_until:
                raise _account_locked_response(lockout_until)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid recovery code",
            )

        # Remove the used code.
        await user_repo.update(
            user.id,
            totp_recovery_codes={"codes": remaining_codes},
        )

        recovery_code_used = True
        remaining_recovery_codes = len(remaining_codes)

    else:
        if not verify_totp_code(verified_totp_secret, code):
            # A wrong code counts like a wrong password.
            lockout_until = await _count_failed_attempt(db, user_repo, user, client_ip)
            audit_user_login_failed(
                email=body.email,
                ip=client_ip,
                reason="2fa_invalid_totp_code",
                organization_id=user.organization_id,
            )
            if lockout_until:
                raise _account_locked_response(lockout_until)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
            )

    # A member of an archived organization completes no login here: no session is
    # issued. This is checked after the code, so the archived state is only
    # disclosed to someone holding both factors.
    if await OrganizationRepository.is_archived(db, user.organization_id):
        raise CodedHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This organization is archived.",
            error_code="ORG_ARCHIVED",
        )

    # Complete the login. The proof is consumed server-side by the same UPDATE that
    # records the login (compare-and-swap on the last_login the proof carries): a
    # replayed proof matches no row and completes nothing. The recovery-code
    # removal staged above is part of this transaction: the loser is rolled back
    # whole, so it does not burn the recovery code either. The rollback expires the
    # ORM object, so the ids for the loser's audit are captured up front.
    #
    # The same UPDATE refuses a sign-in whose account changed since it was read: a
    # reset or another revocation that committed while the code was checked has
    # raised the session version.
    user_id = user.id
    organization_id = user.organization_id
    if not await _consume_second_factor_proof(
        db,
        user,
        _proof_expected_last_login(proof),
        verified_version=verified_version,
        verified_totp_secret=verified_totp_secret,
    ):
        audit_user_login_failed(
            email=body.email,
            ip=client_ip,
            reason=await _refused_completion_reason(db, user_id, verified_version),
            organization_id=organization_id,
        )
        raise _password_step_required_response()

    await db.refresh(user)

    # The token is for the version that was verified and not for the one the
    # refresh shows: a revocation that commits after the UPDATE above raises the
    # version, and this token ends with it.
    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        session_version=verified_version,
    )
    set_auth_cookie(response, token)

    # The proof is used up: a second login needs a new password step.
    clear_second_factor_proof_cookie(response)

    audit_user_login(
        user=user,
        ip=client_ip,
        organization_id=user.organization_id,
        method="recovery_code" if recovery_code_used else "totp",
    )

    resp = TotpAuthenticateResponse(
        status="success",
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        message="2FA authentication successful",
    )

    if recovery_code_used:
        resp.recovery_code_used = True
        resp.remaining_recovery_codes = remaining_recovery_codes
        if remaining_recovery_codes == 0:
            resp.message = "2FA authentication successful. WARNING: No recovery codes remaining!"
        elif remaining_recovery_codes is not None and remaining_recovery_codes <= 2:
            resp.message = (
                f"2FA authentication successful. WARNING: Only {remaining_recovery_codes} recovery codes remaining!"
            )

    return resp


@router.delete(
    "/totp",
    response_model=TotpDisableResponse,
    responses={
        200: {"description": "TOTP 2FA disabled successfully"},
        401: {"description": "Not authenticated", "model": ErrorResponse},
        403: {"description": "Invalid password", "model": ErrorResponse},
        422: {"description": "TOTP is not enabled", "model": ErrorResponse},
    },
)
async def totp_disable(
    request: Request,
    body: TotpDisableRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TotpDisableResponse:
    """
    Turn TOTP off for the signed-in user and delete the secret and the recovery codes.

    Open to every signed-in user. The request must carry the current password.
    Answers 422 when TOTP is not enabled and 403 when the password is wrong.
    The current session stays valid.

    Emits audit event: config_changed (key totp_2fa_enabled)
    """
    client_ip = client_address(request)
    user_repo = UserRepository(db, current_user.organization_id)

    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="TOTP 2FA is not enabled for this account",
        )

    if not current_user.password_hash or not verify_password(body.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid password",
        )

    # The person does this in their own session, with their own password. The
    # session version stays, so this session holds (pinned in
    # tests/test_second_factor_reset_ends_sessions.py). A reset by somebody else
    # raises it instead, see reset_instance_admin_totp.
    await user_repo.update(
        current_user.id,
        totp_secret=None,
        totp_recovery_codes=None,
        totp_enabled=False,
    )

    await db.commit()

    audit_config_changed(
        actor_user=current_user,
        actor_ip=client_ip,
        config_key="totp_2fa_enabled",
        old_value="true",
        new_value="false",
        organization_id=current_user.organization_id,
    )

    return TotpDisableResponse(message="TOTP has been disabled")
