"""FastAPI dependencies that authenticate a request from its JWT.

The token comes from the session cookie or the Authorization header. A user
session and an instance operator session are separate realms.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Path, Request, Response, status
from jwt import PyJWTError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import (
    INSTANCE_ADMIN_COOKIE_NAME,
    create_access_token,
    create_instance_admin_token,
    decode_token,
    is_account_locked,
    is_full_current_session,
    is_session_signed_out,
    is_token_expired,
    set_auth_cookie,
    set_instance_admin_cookie,
    token_session_id,
)
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.instance_admin import InstanceAdmin
from app.models.organization import Organization
from app.models.user import User
from app.repositories.instance_admin import InstanceAdminRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.user import UserRepository


async def _clear_expired_lockout(db: AsyncSession, user_id: UUID) -> User | None:
    """Clear a lockout that has run out, at once and in a transaction of its own.

    Lock order: this is the first row lock of an authenticated request and the
    only one the authentication takes, the account row for one statement. The
    commit releases it before the request takes its own locks. A write left
    pending on the account object would flush after the team and member locks
    of a dissolution, and close a cycle with the erasure of the account, which
    locks the account first and its teams second (lock inventory in
    ``services/team_dissolution``).

    The statement waits while another transaction holds the account, for
    example an erasure or a deactivation, so the account is read again and
    returned as it is now. None means the row is gone.
    """
    await db.execute(
        update(User)
        .where(User.id == user_id, User.locked_until.is_not(None), User.locked_until <= datetime.now(UTC))
        .values(locked_until=None, failed_login_attempts=0)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return await db.get(User, user_id, populate_existing=True)


async def _authorize_session(db: AsyncSession, payload: dict[str, Any], user_id: UUID, session_id: str) -> User:
    """Load the account of a decoded token and refuse it unless the token is a session of it right now.

    These are the checks whose answer can change while a request runs, in one
    place so that ``authenticate_again`` gets the same answers: the token has
    not expired, its session is not signed out, the account exists, is active
    and is not locked, the token carries the account's session version, and
    the organization is not archived. ``session_id`` is ``token_session_id`` of
    the token of ``payload``.
    """
    # For the first authentication, the decode checked the expiry a moment ago.
    # For ``authenticate_again`` this is the only check of the clock against the
    # token, with the decode's answer.
    if is_token_expired(payload):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token: Signature has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A sign-out clears the cookie, but its token keeps a valid signature until it
    # expires, so a copy kept elsewhere would pass. The sign-out records the
    # session (tokens.sign_out_session) and every token names its session, so the
    # record is asked here, before the database is read, with the answer a
    # revocation gives. The record lives in this process only: a second process,
    # and this one after a restart, passes such a token until it expires
    # (SESSION_TIMEOUT_MINUTES at most). See tokens._signed_out_sessions.
    if is_session_signed_out(session_id):
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session signed out. Please sign in again.",
            error_code="SESSION_REVOKED",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Unscoped: the organization is not known yet.
    user = await UserRepository.get_by_id_unscoped(db, user_id)

    # A lockout that has run out is cleared before anything else is decided,
    # and the account is read again after that write.
    if user is not None and user.locked_until is not None and not is_account_locked(user):
        user = await _clear_expired_lockout(db, user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        # The code lets the app tell a deactivated person why the session ended,
        # instead of showing a bare sign-in page.
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
            error_code="ACCOUNT_DISABLED",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.locked_until is not None and is_account_locked(user):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account locked until {user.locked_until.isoformat()}",
        )

    # Inactivity ends a session through the token's own expiry: a token lives
    # SESSION_TIMEOUT_MINUTES and decode_token() refuses it after that with 401.
    # get_current_user mints the cookie of a cookie session again on each
    # request, so only inactivity past the timeout expires it.

    # A token carries the account's session_version of its mint time (claim sv),
    # and every password change increments it. A mismatch means that change ended
    # the session, which must sign in again. A token without sv reads as 0 and
    # stays valid until the account's first password change. The same rule
    # refuses a restricted purpose. 2fa_setup is the one served here, on its two
    # paths in get_current_user.
    if not is_full_current_session(payload, user, allow_purposes=("2fa_setup",)):
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked by a password change. Please sign in again.",
            error_code="SESSION_REVOKED",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Backstop for sessions opened before the archive: a token minted while the
    # organization was active stops authorizing the moment the operator archives it.
    if await OrganizationRepository.is_archived(db, user.organization_id):
        raise CodedHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This organization is archived.",
            error_code="ORG_ARCHIVED",
        )

    return user


async def authenticate_again(request: Request, db: AsyncSession, user_id: UUID) -> User:
    """Ask the authentication again for a request that was authenticated before, and return the account as it is now.

    For a request that starts its work over in a fresh transaction, such as
    the retry of ``services/team_dissolution``. A change since the first
    authentication gets its usual answer: 401 for an expired token, 401
    ``SESSION_REVOKED`` for a sign-out or a password change, 423 for a
    lockout, 401 for a deactivated or missing account, 403 for an archived
    organization. Both functions ask every question whose answer can change
    in ``_authorize_session``. The rest of ``get_current_user`` reads only the
    token and the request path, which do not change.

    The token is the one ``get_current_user`` accepted for this request. A
    request without it, or with it for another account, gets 401.
    """
    payload: dict[str, Any] | None = getattr(request.state, "session_claims", None)
    session_id: str | None = getattr(request.state, "session_id", None)
    if payload is None or session_id is None or payload.get("sub") != str(user_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # The account is read again from the database, not from the session's
    # copy of the first authentication.
    await db.get(User, user_id, populate_existing=True)
    return await _authorize_session(db, payload, user_id, session_id)


async def get_current_user(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Cookie(default=None),
    instance_token: str | None = Cookie(default=None, alias=INSTANCE_ADMIN_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> User:
    """Return the authenticated user of the request, from the JWT in the header or the session cookie.

    An ``Authorization: Bearer <token>`` header wins over the ``access_token``
    cookie. The session checks are in ``_authorize_session``. For a cookie
    session this dependency mints the cookie again, which slides the inactivity
    timeout.

    Raises 401 for a missing or invalid token, an unknown or deactivated
    account or a session that ended, 423 for a locked account, and 403 for an
    archived organization or an instance-scoped token. A restricted
    ``2fa_setup`` token is served on the two TOTP setup paths only.

    ``instance_token`` is the operator cookie. It is never a user credential,
    but a valid instance-scoped one turns the anonymous 401 into a realm 403.
    """
    # The SPA never sets Authorization, so browsers use the cookie only. An API
    # client gets the identity it asked for even with a stale cookie on the
    # connection, and a guest cookie set by /api/join/{code} cannot shadow a
    # coordinator's Bearer header on the next call from the same client.
    token: str | None = None
    token_from_cookie = False

    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1] if " " in authorization else None
    elif access_token:
        token = access_token
        token_from_cookie = True

    # No user token, but the operator cookie: a signed-in operator in the wrong
    # realm gets 403, as an instance Bearer token does, not the anonymous 401. An
    # operator cookie without instance scope, an invalid or expired one, and the
    # kept cookie of a signed-out operator session are no session, so they name
    # no realm.
    if not token and instance_token:
        try:
            instance_payload = decode_token(instance_token)
        except PyJWTError:
            instance_payload = None
        if instance_payload is not None and is_session_signed_out(token_session_id(instance_payload, instance_token)):
            instance_payload = None
        if instance_payload is not None and instance_payload.get("scope") == "instance":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Instance operator token not valid for organization endpoints",
            )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(token)
    except PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # An operator token belongs to no organization and no role, so it must never
    # authorize an organization endpoint, as a Bearer header or as a cookie.
    # get_org_from_slug inherits this check through this dependency.
    if payload.get("scope") == "instance":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Instance operator token not valid for organization endpoints",
        )

    user_id_str: str | None = payload.get("sub")

    # The second-factor proof is never a session. decode_token above already
    # rejects it, because it is signed with the proof's own key. This check is a backstop against a future token type
    # that shares the session key. 2fa_setup is restricted further down.
    if user_id_str is not None and payload.get("purpose") == "2fa_proof":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not a session token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: missing user ID",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = UUID(user_id_str)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: malformed user ID",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # ``_authorize_session`` asks whether this session was signed out.
    session_id = token_session_id(payload, token)

    user = await _authorize_session(db, payload, user_id, session_id)

    # The claims, for an act of this request that loses its transaction and
    # runs again (``authenticate_again``).
    request.state.session_claims = payload

    # The session of this request, for the handlers that mint a token from it
    # (/ws-token) or end it (/logout).
    request.state.session_id = session_id
    request.state.session_expires_at = payload.get("exp")

    # A restricted 2fa_setup token reaches only the two TOTP setup paths.
    token_purpose = payload.get("purpose")
    if token_purpose == "2fa_setup":
        allowed_paths = {
            "/api/auth/2fa/totp/setup",
            "/api/auth/2fa/totp/verify",
        }
        request_path = request.url.path
        if request_path not in allowed_paths:
            # Machine-readable code so the frontend can route the user back to
            # /2fa-setup with an explanation instead of showing a bare 403.
            raise CodedHTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Complete 2FA setup first",
                error_code="2FA_SETUP_REQUIRED",
            )

    # Slide the session window: a normal session cookie is minted again, so the
    # inactivity timeout restarts. Bearer clients manage their own tokens. The
    # restricted 2fa_setup token is never extended. Logout must clear the
    # cookie, so it is skipped.
    if token_from_cookie and token_purpose is None and request.url.path != "/api/auth/logout":
        set_auth_cookie(
            response,
            create_access_token(
                user_id=user.id,
                role=user.role.value,
                organization_id=user.organization_id,
                session_version=user.session_version,
                # The same session goes on: a sign-out ends this cookie's
                # sockets, whichever copy of the cookie minted their tokens
                session_id=session_id,
            ),
        )

    return user


async def get_org_from_slug(
    slug: str = Path(..., description="Organization slug from URL path"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Organization:
    """Resolve the organization of the URL slug and require that the user belongs to it.

    This is the primary tenant isolation check of the multi-tenant endpoints
    (for example ``/api/orgs/{slug}/rallyes``). Raises 404 for an unknown slug
    and 403 when the user belongs to another organization.
    """
    org_repo = OrganizationRepository(db)
    org = await org_repo.get_by_slug(slug)

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    if current_user.organization_id != org.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return org


async def require_instance_admin(
    db: AsyncSession = Depends(get_db),
    instance_token: str | None = Cookie(default=None, alias=INSTANCE_ADMIN_COOKIE_NAME),
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
    request: Request = None,  # type: ignore[assignment]
    response: Response = None,  # type: ignore[assignment]
) -> InstanceAdmin:
    """Return the instance operator of the request, from an instance-scoped JWT.

    A Bearer header wins over the operator cookie, as in ``get_current_user``.
    The token must have ``scope: "instance"``, the operator session that
    ``POST /api/instance/auth/login`` issues.

    Raises 401 for a missing, invalid or expired token, a session that ended
    or an operator that no longer exists, and 403 for a token without instance
    scope, for example an organization user token, and for a disabled operator.

    ``access_token`` is the user cookie. It is never an operator credential,
    but a valid organization token turns the anonymous 401 into a realm 403.
    """
    token: str | None = None

    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1] if " " in authorization else None
    elif instance_token:
        token = instance_token

    # No instance token, but the user cookie: the mirror of the realm check in
    # get_current_user, 403 and not the anonymous 401. An invalid or expired
    # cookie and the kept cookie of a signed-out session are no session, so they
    # name no realm.
    if not token and access_token:
        try:
            user_payload = decode_token(access_token)
        except PyJWTError:
            user_payload = None
        if user_payload is not None and is_session_signed_out(token_session_id(user_payload, access_token)):
            user_payload = None
        if user_payload is not None and user_payload.get("scope") != "instance":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Organization token not valid for instance endpoints",
            )

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(token)
    except PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # A signed-out session authorizes nothing more, with the answer of
    # get_current_user. The operator's sign-out records the session
    # (tokens.sign_out_session) and every operator token names it, the sliding
    # cookie included, so a copy kept elsewhere is refused here, before the
    # database is read. This comes before the realm check, because a signed-out
    # token of either realm is no session. The record lives in this process
    # only. See tokens._signed_out_sessions for the bounds.
    session_id = token_session_id(payload, token)
    if is_session_signed_out(session_id):
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session signed out. Please sign in again.",
            error_code="SESSION_REVOKED",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Only instance-scoped tokens may open an instance endpoint. An organization
    # user token (no scope claim) is a valid token for the wrong realm. That is a
    # permission problem (403), not an authentication problem (401).
    if payload.get("scope") != "instance":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization token not valid for instance endpoints",
        )

    admin_id_str: str | None = payload.get("sub")
    if not admin_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: missing operator ID",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        admin_id = UUID(admin_id_str)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: malformed operator ID",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    admin = await InstanceAdminRepository(db).get_by_id(admin_id)

    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Operator not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Backstop for sessions opened before the operator was disabled: a token
    # minted while the account was active stops authorizing the moment it is
    # disabled.
    if admin.disabled_at is not None:
        raise CodedHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operator account is disabled.",
            error_code="INSTANCE_ADMIN_DISABLED",
        )

    # Mirrors get_current_user: a token whose sv claim predates the account's
    # last password change no longer authorizes. Tokens minted before the claim
    # existed read as 0 and stay valid until the first change.
    if payload.get("sv", 0) != admin.session_version:
        raise CodedHTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked by a password change. Please sign in again.",
            error_code="SESSION_REVOKED",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # The session of this request, for the handlers that end it (logout) or
    # mint a cookie from it (password change). A direct caller passes no
    # request and needs neither.
    if request is not None:
        request.state.session_id = session_id
        request.state.session_expires_at = payload.get("exp")

    # Slide the operator session like the organization one, so the timeout
    # counts from the last activity. The new cookie continues the session of
    # the old one, so one sign-out ends them all. Bearer clients keep their own
    # token. Logout clears the cookie, so it is not minted again there.
    from_cookie = token == instance_token and not (authorization and authorization.startswith("Bearer "))
    if from_cookie and response is not None and request is not None and request.url.path != "/api/instance/auth/logout":
        set_instance_admin_cookie(
            response,
            create_instance_admin_token(admin.id, session_version=admin.session_version, session_id=session_id),
        )

    return admin
