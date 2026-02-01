"""JWT creation, validation and cookie handling for authentication (HS256).

It covers the user session token, the instance operator token, the second-factor
proof, and the record of signed-out sessions.
"""

import hashlib
import hmac
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import jwt
from fastapi import Response
from jwt import PyJWTError

from app.config import get_settings

if TYPE_CHECKING:
    from app.models.user import User


def is_account_locked(user: "User", now: datetime | None = None) -> bool:
    """
    Say whether ``user`` is locked at ``now`` (default: this moment).

    The lock rule: a lock holds while ``locked_until`` lies in the future. The
    HTTP dependency, ``ws_authenticate`` and ``is_full_current_session`` ask it.
    ``ws_refusal`` applies the same rule to the row it reads. Each transport
    keeps its own answer to a lock (423, WebSocket close 1008), and the HTTP
    dependency also clears an expired lock.
    """
    if user.locked_until is None:
        return False
    return user.locked_until > (now or datetime.now(UTC))


def is_full_current_session(
    payload: dict[str, Any],
    user: "User",
    *,
    allow_purposes: Collection[str] = (),
) -> bool:
    """
    Say whether a decoded token is a full, current session of ``user``.

    The one rule behind the HTTP dependency, the join flow, the WebSocket and
    the public membership reads:
    - The token is not an instance token. ``scope: "instance"`` is the operator's.
    - It carries no ``purpose``. A token with one (``2fa_setup``) is restricted,
      never a full session. A caller that serves it on a few paths of its own
      names the purpose in ``allow_purposes``.
    - Its ``sv`` claim equals the account's ``session_version``. A password
      change, for example, ends older sessions. A token without the claim reads as 0.
    - The account is not locked now (``is_account_locked``).

    The caller answers the account's ``is_active`` flag and its organization
    with its own error.
    """
    if payload.get("scope") == "instance":
        return False
    purpose = payload.get("purpose")
    if purpose is not None and purpose not in allow_purposes:
        return False
    if payload.get("sv", 0) != user.session_version:
        return False
    return not is_account_locked(user)


# How far past its ``exp`` a token is still accepted: not at all. One value for
# ``decode_token`` and for ``is_token_expired``.
TOKEN_EXPIRY_LEEWAY = timedelta(0)

# How long a WebSocket token lives (GET /api/auth/ws-token).
WS_TOKEN_TTL = timedelta(minutes=5)

# The signed-out sessions: session id -> the moment the record may be forgotten.
#
# A session is a signed token, so a sign-out changes nothing in the database.
# The checks ask this record instead:
# - ``ws_refusal``, before a socket is accepted, after its subscriptions and at
#   each keepalive. A socket not yet subscribed when the sign-out closed the
#   account's sockets would otherwise live on.
# - ``dependencies.get_current_user``, so a copy of the token kept elsewhere is refused.
#
# The record lives in this process only, like the open sockets of
# ``ws_manager.manager``, so the explicit closes rely on the API running as one
# process. A second process, and this one after a restart, does not know the
# sign-out. It keeps a socket of the session open until its token expires
# (``WS_TOKEN_TTL`` at most), admits a new socket with a token minted before the
# sign-out for the same time, and accepts an ordinary request with a token of
# the session until that token expires. ``pg_notify`` reaches the other
# processes for broadcasts, but not a process that starts after the sign-out.
# Only a table would hold across processes and restarts.
_signed_out_sessions: dict[str, datetime] = {}


def token_session_id(payload: dict[str, Any], token: str) -> str:
    """
    Return the id of the session of a decoded token, its ``sid`` claim.

    A token minted before the claim existed is its own session, named by a hash
    of itself, and the tokens minted from it carry that hash as their ``sid``.
    """
    sid = payload.get("sid")
    if isinstance(sid, str) and sid:
        return sid
    return hashlib.sha256(token.encode()).hexdigest()


def sign_out_session(session_id: str, until: datetime) -> None:
    """
    Record a session as signed out until ``until``, when no token of it can be valid any more.

    Only this session ends. The account's sessions on other devices have other
    ids, and the account's session version is not touched.
    """
    now = datetime.now(UTC)
    for known, expires in list(_signed_out_sessions.items()):
        if expires <= now:
            del _signed_out_sessions[known]
    previous = _signed_out_sessions.get(session_id)
    _signed_out_sessions[session_id] = max(until, previous) if previous else until


def is_session_signed_out(session_id: str | None, now: datetime | None = None) -> bool:
    """Say whether the session was signed out and its tokens may still be valid."""
    if session_id is None:
        return False
    until = _signed_out_sessions.get(session_id)
    return until is not None and until > (now or datetime.now(UTC))


def is_token_expired(payload: dict[str, Any], now: datetime | None = None) -> bool:
    """
    Say whether the ``exp`` claim of a decoded token has passed at ``now`` (default: this moment).

    The same rule as ``decode_token``, for claims decoded earlier and asked
    again (``dependencies.authenticate_again``): a token is expired from the
    second of its ``exp`` on, with ``TOKEN_EXPIRY_LEEWAY``, and a token
    without ``exp`` does not expire.
    """
    exp = payload.get("exp")
    if isinstance(exp, datetime):
        exp = exp.timestamp()
    if not isinstance(exp, int | float):
        return False
    return exp <= ((now or datetime.now(UTC)) - TOKEN_EXPIRY_LEEWAY).timestamp()


def is_session_that_may_act(
    payload: dict[str, Any],
    token: str,
    user: "User",
    *,
    allow_purposes: Collection[str] = (),
) -> bool:
    """
    Say whether a decoded token is a session of ``user`` that may act.

    The one question for a reader of the session cookie that decodes it itself:
    the token is a full, current session (``is_full_current_session``) and its
    session is not signed out (``is_session_signed_out``). ``token`` is the raw
    token of ``payload``, because a token without ``sid`` is named by a hash of
    itself.

    The guest entry and the two public routes that answer a team member ask
    this. The HTTP dependency and the WebSocket admission ask the two rules one
    after the other, each with a refusal of its own. A reader that asks only the
    first rule takes the kept cookie of a signed-out session for an identity.

    The caller answers ``is_active`` and the organization, as for
    ``is_full_current_session``.
    """
    if not is_full_current_session(payload, user, allow_purposes=allow_purposes):
        return False
    return not is_session_signed_out(token_session_id(payload, token))


def create_access_token(
    user_id: UUID,
    role: str,
    organization_id: UUID,
    expires_delta: timedelta | None = None,
    purpose: str | None = None,
    session_version: int = 0,
    session_id: str | None = None,
) -> str:
    """
    Create a session token with the claims ``sub``, ``role``, ``org_id``, ``exp``, ``iat``, ``sv`` and ``sid``.

    Args:
        expires_delta: None uses SESSION_TIMEOUT_MINUTES from config.
        purpose: Purpose claim of a restricted token, for example "2fa_setup".
        session_version: Every mint site must pass the account's current
            value. A password change increments it, and get_current_user
            refuses a token whose sv claim differs.
        session_id: The session this token continues: ``token_session_id`` of
            the token it is minted from (the sliding cookie, the WebSocket
            token). None starts a new session, as a sign-in does. A sign-out
            records the id, and a WebSocket of that session ends.
    """
    settings = get_settings()

    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.auth.session_timeout_minutes)

    now = datetime.now(UTC)
    expire = now + expires_delta

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "org_id": str(organization_id),
        "exp": expire,
        "iat": now,
        "sv": session_version,
        "sid": session_id or uuid4().hex,
    }

    if purpose:
        payload["purpose"] = purpose

    encoded_jwt = jwt.encode(
        payload,
        settings.auth.jwt_secret_key,
        algorithm="HS256",
    )

    return encoded_jwt


def decode_token(token: str) -> dict[str, Any]:
    """Return the claims of a session token. Raises PyJWTError on an invalid, tampered or expired token."""
    settings = get_settings()

    payload = jwt.decode(
        token,
        settings.auth.jwt_secret_key,
        algorithms=["HS256"],
        leeway=TOKEN_EXPIRY_LEEWAY,
    )

    return payload


def set_auth_cookie(response: Response, token: str) -> None:
    """
    Set the session token as the ``access_token`` cookie.

    - HttpOnly: JavaScript cannot read it (XSS protection).
    - Secure: only in production, so that development works over plain HTTP.
    - SameSite lax: CSRF protection that still allows navigation.
    - Path "/" and Max-Age of SESSION_TIMEOUT_MINUTES.
    """
    settings = get_settings()

    is_production = settings.environment.lower() == "production"

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
        max_age=settings.auth.session_timeout_minutes * 60,
    )


def clear_auth_cookie(response: Response) -> None:
    """Clear the ``access_token`` cookie (logout)."""
    response.delete_cookie(
        key="access_token",
        path="/",  # Must match the path used in set_auth_cookie
    )


# Cookie that carries the proof that the password step of a login succeeded.
# Not a session: it is signed with a key derived for this purpose, so
# decode_token and every session consumer behind it reject it outright.
SECOND_FACTOR_PROOF_COOKIE_NAME = "second_factor_proof"
SECOND_FACTOR_PROOF_TTL_MINUTES = 10


def _second_factor_proof_signing_key() -> str:
    """
    The signing key of the proof token, derived from the JWT secret.

    A proof must never decode as a session token. ``decode_token`` verifies with
    jwt_secret_key, so it fails the signature of a proof, and no second secret
    needs to be configured or rotated.
    """
    settings = get_settings()
    return hmac.new(
        settings.auth.jwt_secret_key.encode(),
        b"clavis-second-factor-proof",
        hashlib.sha256,
    ).hexdigest()


def second_factor_proof_last_login_marker(last_login: datetime | None) -> str | None:
    """
    Return users.last_login in the form the proof carries. A naive datetime reads as UTC.

    The proof carries last_login of the moment the password was checked. The
    successful second-factor login changes last_login with a compare-and-swap,
    which makes the proof single-use on the server.
    """
    if last_login is None:
        return None
    if last_login.tzinfo is None:
        last_login = last_login.replace(tzinfo=UTC)
    return last_login.astimezone(UTC).isoformat()


def create_second_factor_proof(
    user_id: UUID,
    session_version: int = 0,
    last_login: datetime | None = None,
) -> str:
    """
    Create the proof that /api/auth/login checked the password of an account that needs a second factor.

    It carries the user id (``sub``), ``purpose: "2fa_proof"``, the session
    version and ``ll``, the ``last_login`` that the login consumes atomically.
    It lives at most ten minutes. The second-factor endpoints demand it for the
    account named in the request. It is not a session token and decodes only
    through :func:`decode_second_factor_proof`.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "purpose": "2fa_proof",
        "exp": now + timedelta(minutes=SECOND_FACTOR_PROOF_TTL_MINUTES),
        "iat": now,
        "sv": session_version,
        "ll": second_factor_proof_last_login_marker(last_login),
    }

    return jwt.encode(payload, _second_factor_proof_signing_key(), algorithm="HS256")


def decode_second_factor_proof(token: str) -> dict[str, Any] | None:
    """
    Return the claims of a valid second-factor proof, or None.

    The one place a proof is accepted: it checks the signature with the proof
    key, the expiry and the ``2fa_proof`` purpose. A proof must never decode
    through :func:`decode_token`.
    """
    try:
        payload = jwt.decode(
            token,
            _second_factor_proof_signing_key(),
            algorithms=["HS256"],
        )
    except PyJWTError:
        return None
    if payload.get("purpose") != "2fa_proof":
        return None
    return payload


def set_second_factor_proof_cookie(response: Response, token: str) -> None:
    """Set the proof cookie with the same flags as the session cookie, but
    its own short max-age."""
    settings = get_settings()
    is_production = settings.environment.lower() == "production"

    response.set_cookie(
        key=SECOND_FACTOR_PROOF_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
        max_age=SECOND_FACTOR_PROOF_TTL_MINUTES * 60,
    )


def clear_second_factor_proof_cookie(response: Response) -> None:
    """Consume the proof: a successful second factor must not enable a
    second login without a new password step."""
    response.delete_cookie(key=SECOND_FACTOR_PROOF_COOKIE_NAME, path="/")


# The operator session cookie. Its own name lets an operator who is also an
# organization member keep both sessions, so neither login clobbers the other.
INSTANCE_ADMIN_COOKIE_NAME = "instance_token"


def create_instance_admin_token(
    admin_id: UUID,
    expires_delta: timedelta | None = None,
    session_version: int = 0,
    session_id: str | None = None,
) -> str:
    """
    Create the session token of an instance operator.

    The token carries the operator id as ``sub`` and ``scope: "instance"``, and
    no ``org_id`` and no ``role``, because an operator is a member of no
    organization. The organization dependencies reject it with 403, and
    ``require_instance_admin`` rejects an organization token with 403.

    Args:
        expires_delta: None uses SESSION_TIMEOUT_MINUTES from config.
        session_version: The operator's current session version, the same
            mechanism as the ``sv`` claim of the user token.
        session_id: The session this token continues. Pass ``token_session_id``
            of the token it is minted from (the sliding cookie, the cookie a
            password change mints). None starts a new session, as a sign-in
            does. The operator's sign-out records the id, as the sign-out of an
            organization account does.
    """
    settings = get_settings()

    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.auth.session_timeout_minutes)

    now = datetime.now(UTC)
    expire = now + expires_delta

    payload: dict[str, Any] = {
        "sub": str(admin_id),
        "scope": "instance",
        "exp": expire,
        "iat": now,
        "sv": session_version,
        "sid": session_id or uuid4().hex,
    }

    return jwt.encode(
        payload,
        settings.auth.jwt_secret_key,
        algorithm="HS256",
    )


def set_instance_admin_cookie(response: Response, token: str) -> None:
    """
    Set the operator session cookie with the flags of :func:`set_auth_cookie`.

    Its own name lets an operator who is also an organization member keep both sessions.
    """
    settings = get_settings()

    is_production = settings.environment.lower() == "production"

    response.set_cookie(
        key=INSTANCE_ADMIN_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
        max_age=settings.auth.session_timeout_minutes * 60,
    )


def clear_instance_admin_cookie(response: Response) -> None:
    """Clear the instance-operator session cookie (operator logout)."""
    response.delete_cookie(
        key=INSTANCE_ADMIN_COOKIE_NAME,
        path="/",  # Must match the path used in set_instance_admin_cookie
    )
