"""
WebSocket authentication.

A WebSocket client cannot set headers, so the JWT travels as ``?token=<jwt>``.
``ws_authenticate`` reads it and closes the socket with 1008 (policy violation)
on every failure. ``ws_refusal`` is the one rule that says whether a socket may
be open. The handlers in ``routers/ws.py`` ask it with the same ``WsGrant``
before the socket is accepted, right after its subscriptions (a revocation that
commits between the two would otherwise find no socket to close), and at every
keepalive.

Close codes of an open socket, the same ones the explicit closes use:
- 4000: the account or its session ended, or its role no longer allows the
  channels it subscribed with. The client may reconnect and is admitted again
  only with a current session.
- 4002: the token expired. The client reconnects with a new token.
- 4003: the right to this channel ended (left or moved out of the team, the
  assist window closed, a guest without a team in the run). Not recoverable.
- 1008: the run or the team is gone.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import WebSocket, status
from jwt import PyJWTError
from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import RoleLevel
from app.auth.tokens import (
    decode_token,
    is_account_locked,
    is_full_current_session,
    is_session_signed_out,
    token_session_id,
)
from app.models.organization import Organization
from app.models.run import Run
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.repositories.organization import OrganizationRepository
from app.repositories.user import UserRepository


async def ws_authenticate(
    websocket: WebSocket,
    slug: str,
    db: AsyncSession,
) -> tuple[User, Organization]:
    """
    Authenticate a WebSocket, not yet accepted, by the JWT of its ``token`` query parameter.

    Returns the user and the organization of ``slug``. A failed check (missing
    or invalid token, unknown, disabled or locked user, revoked session,
    unknown, foreign or archived organization) closes the socket with 1008 and
    a reason, then raises RuntimeError.
    """
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing authentication token")
        raise RuntimeError("WebSocket authentication failed: missing token")

    try:
        payload = decode_token(token)
    except PyJWTError as e:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=f"Invalid token: {str(e)}")
        raise RuntimeError(f"WebSocket authentication failed: {str(e)}") from e

    user_id_str: str | None = payload.get("sub")
    if not user_id_str:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token payload")
        raise RuntimeError("WebSocket authentication failed: missing user ID in token")

    try:
        user_id = UUID(user_id_str)
    except ValueError as e:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Malformed user ID")
        raise RuntimeError("WebSocket authentication failed: malformed user ID") from e

    # Unscoped: the organization is checked below, against the slug.
    user = await UserRepository.get_by_id_unscoped(db, user_id)

    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="User not found")
        raise RuntimeError("WebSocket authentication failed: user not found")

    if not user.is_active:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="User account disabled")
        raise RuntimeError("WebSocket authentication failed: user account disabled")

    if user.locked_until and is_account_locked(user):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=f"Account locked until {user.locked_until.isoformat()}",
        )
        raise RuntimeError(f"WebSocket authentication failed: account locked until {user.locked_until.isoformat()}")

    # A token minted before the account's last password change must not open a
    # WebSocket. A token without ``sv`` reads as 0, valid until the account's
    # first revocation. The rule also refuses a restricted token (``2fa_setup``).
    if not is_full_current_session(payload, user):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session revoked")
        raise RuntimeError("WebSocket authentication failed: session revoked by a password change")

    # A signed-out session opens no WebSocket. The token carries the id of the
    # session it was minted from.
    session_id = token_session_id(payload, token)
    if is_session_signed_out(session_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session revoked")
        raise RuntimeError("WebSocket authentication failed: session signed out")

    org_repo = OrganizationRepository(db)
    org = await org_repo.get_by_slug(slug)

    if not org:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Organization not found")
        raise RuntimeError("WebSocket authentication failed: organization not found")

    if user.organization_id != org.id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Access denied")
        raise RuntimeError("WebSocket authentication failed: user does not belong to organization")

    # The socket of an archived organization is refused with the same code as
    # every other rejected token.
    if await OrganizationRepository.is_archived(db, org.id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Organization archived")
        raise RuntimeError("WebSocket authentication failed: organization is archived")

    # The token is verified once, here. The keepalive check needs its expiry,
    # because an open socket would otherwise outlive the token.
    websocket.state.token_expires_at = _expiry_of(payload)
    # A sign-out changes nothing in the database, so the keepalive check asks
    # the record of signed-out sessions for this id.
    websocket.state.session_id = session_id

    return user, org


def _expiry_of(payload: dict[str, object]) -> datetime | None:
    """The ``exp`` claim of a decoded token as an aware datetime, or None without one."""
    exp = payload.get("exp")
    if not isinstance(exp, int | float):
        return None
    return datetime.fromtimestamp(exp, tz=UTC)


@dataclass(frozen=True)
class WsGrant:
    """
    What a socket was admitted with, and what ``ws_refusal`` checks again.

    Attributes:
        user_id: The account of the socket
        organization_id: The organization of the URL, which is the account's
        session_version: The account's session version the token carried
        token_expires_at: The expiry of the token, None for a token without one
        run_id: The run of the URL
        team_id: None for the run feed; the team of the URL for a team chat
        staff: The run feed subscribed with the staff channel (coordinator and
            above). A role lowered below coordinator ends such a socket.
        session_id: The session the token belongs to (``token_session_id``).
            A sign-out of that session ends the socket.
    """

    user_id: UUID
    organization_id: UUID
    session_version: int
    token_expires_at: datetime | None
    run_id: UUID
    team_id: UUID | None = None
    staff: bool = False
    session_id: str | None = None


@dataclass(frozen=True)
class WsRefusal:
    """Why a socket may not be open, as the close code and reason it gets."""

    code: int
    reason: str


SESSION_REVOKED = WsRefusal(4000, "session revoked")
ROLE_CHANGED = WsRefusal(4000, "role changed")
TOKEN_EXPIRED = WsRefusal(4002, "token expired")
RUN_NOT_FOUND = WsRefusal(status.WS_1008_POLICY_VIOLATION, "Run not found")
TEAM_NOT_FOUND = WsRefusal(status.WS_1008_POLICY_VIOLATION, "Team not found or does not belong to this run")
GUEST_WITHOUT_TEAM = WsRefusal(4003, "Not a member of a team in this run")
LEFT_THE_TEAM = WsRefusal(4003, "Left the team")
# Not recoverable (the client's NON_RECOVERABLE_CODES): a reconnect does not
# reopen the window. This code must not be 1008, which the client answers with
# a reconnect loop.
ASSIST_WINDOW_CLOSED = WsRefusal(4003, "Coordinator assist window is closed")
NOT_ADMITTED_TO_CHAT = WsRefusal(
    status.WS_1008_POLICY_VIOLATION,
    "Must be team member, or coordinator after the team's assistance request, to access team chat",
)


async def ws_refusal(
    db: AsyncSession,
    grant: WsGrant,
    *,
    connecting: bool = False,
    now: datetime | None = None,
) -> WsRefusal | None:
    """
    Say why the socket of ``grant`` may not be open, or None if it may.

    The one rule for the life of a socket. The account exists, is active, is
    not locked and belongs to the organization. The organization is neither
    deleted nor archived. The session version is the token's, the session is
    not signed out and the token has not expired. The channel's admission rule
    holds.

    A sign-out is not in the database. It records the session id in this
    process (``tokens.sign_out_session``), and this check asks the record
    without a read. So a socket that was accepted and not yet subscribed when
    the sign-out closed the account's channel ends at its check after the
    subscriptions.

    Run feed: the run exists in the organization. A guest needs a team in this
    run. A guest account is created with its team membership and bound to that
    run, so a guest without a team here left, was removed (and may not rejoin)
    or belongs to another run. A registered account without a team is
    admitted: every role of the organization may view the feed, a registered
    participant may join a team again, and the waiting step of the join opens
    this socket before a team exists. It gets the broadcasts only, because an
    event for one team goes to that team's members (``pg_notify``).
    ``routers/feed.py`` follows the same rule.

    Team chat: the team exists in the run, and the account is a member of it,
    or is coordinator or above while the team's assist window is open (mirrors
    ``chat.py::_verify_team_access``).

    Everything is one SELECT, so a keepalive costs one read. ``db`` must be a
    short session, never one held for the life of the socket. ``connecting``
    is True before the socket is accepted: a non-member is then refused with
    1008, while an open socket whose membership ended closes with 4003.
    ``now`` is the moment to judge the token and the lock at (default: now).
    """
    now = now or datetime.now(UTC)
    if grant.token_expires_at is not None and grant.token_expires_at <= now:
        return TOKEN_EXPIRED
    if is_session_signed_out(grant.session_id, now):
        return SESSION_REVOKED

    membership = (
        select(TeamMember.id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id == grant.user_id, Team.run_id == grant.run_id, Team.is_deleted.is_(False))
    )
    if grant.team_id is not None:
        membership = membership.where(Team.id == grant.team_id)

    row = (
        await db.execute(
            select(
                User.session_version,
                User.is_active,
                User.is_deleted,
                User.locked_until,
                User.role,
                Organization.is_deleted,
                Organization.archived_at,
                Run.id,
                Team.id,
                Team.coordinator_chat_opened_at,
                exists(membership),
            )
            .select_from(User)
            .outerjoin(Organization, Organization.id == User.organization_id)
            .outerjoin(
                Run,
                and_(
                    Run.id == grant.run_id,
                    Run.organization_id == User.organization_id,
                    Run.is_deleted.is_(False),
                ),
            )
            .outerjoin(
                Team,
                and_(
                    Team.id == grant.team_id,
                    Team.run_id == Run.id,
                    Team.organization_id == User.organization_id,
                    Team.is_deleted.is_(False),
                ),
            )
            .where(User.id == grant.user_id, User.organization_id == grant.organization_id)
        )
    ).one_or_none()
    if row is None:
        # The account is gone or left the organization.
        return SESSION_REVOKED
    (
        session_version,
        is_active,
        user_deleted,
        locked_until,
        role,
        org_deleted,
        org_archived_at,
        run_id,
        team_id,
        window_opened_at,
        is_member,
    ) = row

    if not is_active or user_deleted or session_version != grant.session_version:
        return SESSION_REVOKED
    if locked_until is not None and locked_until > now:
        return SESSION_REVOKED
    if org_deleted is not False or org_archived_at is not None:
        return SESSION_REVOKED

    if run_id is None:
        return RUN_NOT_FOUND
    is_staff = RoleLevel[role.value.upper()] >= RoleLevel.COORDINATOR

    if grant.team_id is None:
        if grant.staff and not is_staff:
            # The socket subscribed to the staff channel of the feed.
            return ROLE_CHANGED
        if role == UserRole.GUEST and not is_member:
            return GUEST_WITHOUT_TEAM
        return None

    if team_id is None:
        return TEAM_NOT_FOUND
    if is_member:
        return None
    if is_staff:
        return None if window_opened_at is not None else ASSIST_WINDOW_CLOSED
    return NOT_ADMITTED_TO_CHAT if connecting else LEFT_THE_TEAM
