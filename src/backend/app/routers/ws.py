"""
WebSocket endpoints for real-time communication.

1. Run feed and state: /api/orgs/{slug}/runs/{run_id}/ws
   - Admits: every role of the organization. A guest needs a team in the run.
   - Channels: feed:{run_id}, feed_staff:{run_id} or feed_member:{run_id}:{user.id}, run_state:{run_id}
   - Receives: feed events, run state changes
2. Team chat: /api/orgs/{slug}/runs/{run_id}/teams/{team_id}/ws
   - Admits: a team member, or coordinator+ while the assist window of the team is open
   - Channels: chat:{team_id}
   - Receives: chat messages

Both endpoints also subscribe to the account's channel user:{user.id}.

An open socket ends when its right ends. One rule says whether a socket may be open,
ws_dependencies.ws_refusal: the account is active, its session current, its token not expired, and
the channel's admission rule holds. A sign-out changes nothing else in the database, so it records
the session, and the token of a socket names the session it was minted from (tokens.sign_out_session).
Each endpoint asks the rule:

- before accept(),
- after its subscriptions (_admit): a password change or reset closes the sockets of user:{user.id}
  with 4000 (ws_manager.close_channel), but only those subscribed at that moment, so a revocation
  between the first check and the subscriptions would otherwise be missed,
- every KEEPALIVE_SECONDS, one read each.

The handlers that end a right also close at once (manager.close_channel, close_shared,
recheck_channel). The keepalive check is the net under them, so a missed or raced close lasts one
interval at most.

Never put a database session on a socket's lifetime. It keeps one pooled connection per open socket,
and about 30 open apps exhaust pool_size plus max_overflow, so every HTTP request waits 30 s. The checks
before accept() run in a short session that closes before accept(), and DB work inside the loop opens
its own short one.

The client sends the JWT as ?token=<jwt> and "ping" as keepalive, and the server answers "pong".
The subscriptions are dropped on disconnect.
"""

import asyncio
import contextlib
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.auth.rbac import RoleLevel
from app.auth.ws_dependencies import WsGrant, WsRefusal, ws_authenticate, ws_refusal
from app.database import SessionOpener, get_session_opener
from app.services.ws_manager import SocketCheck, manager

logger = structlog.get_logger(__name__)

router = APIRouter()

# How often the server asks an open socket's admission rule again. It is the
# client's ping interval (useWebSocket.ts). The server keeps the time itself,
# so a client that sends no ping is checked all the same.
KEEPALIVE_SECONDS = 25.0


async def _close(websocket: WebSocket, refusal: WsRefusal) -> None:
    """Drop the socket's subscriptions, then close it with the refusal's code."""
    await manager.disconnect(websocket)
    # Another close may have been first (an explicit close and the keepalive)
    with contextlib.suppress(Exception):
        await websocket.close(code=refusal.code, reason=refusal.reason)


def _check_for(websocket: WebSocket, open_session: SessionOpener, grant: WsGrant) -> SocketCheck:
    """
    Build the check of one socket: ask ``ws_refusal`` again, close if it refuses.

    It runs after the subscriptions, at every keepalive and on ``manager.recheck_channel``,
    each time in a short session of its own with one read.
    """

    async def check() -> bool:
        async with open_session() as db:
            refusal = await ws_refusal(db, grant)
        if refusal is None:
            return False
        await _close(websocket, refusal)
        return True

    return check


async def _watch(check: SocketCheck) -> None:
    """Ask the socket's check every ``KEEPALIVE_SECONDS`` until it closes the socket."""
    while True:
        await asyncio.sleep(KEEPALIVE_SECONDS)
        try:
            if await check():
                return
        except Exception:
            # The database did not answer. The socket stays for one more
            # interval, and the next check decides.
            logger.warning("ws_keepalive_check_failed", exc_info=True)


async def _admit(websocket: WebSocket, open_session: SessionOpener, grant: WsGrant) -> SocketCheck | None:
    """
    Register the check of a subscribed socket and ask it once.

    The admission ran before accept(), the subscription came after it, and a
    revocation between the two closed the account's channel when this socket
    was not on it yet. Returns the check, or None if it closed the socket.
    """
    check = _check_for(websocket, open_session, grant)
    await manager.register_check(websocket, check)
    if await check():
        return None
    return check


async def _serve(websocket: WebSocket, check: SocketCheck) -> None:
    """
    Run an admitted socket until it ends, then drop its subscriptions.

    The check runs at every keepalive, and a pong answers each ping of the client.
    """
    watcher = asyncio.create_task(_watch(check))
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError: the check closed the socket while this loop answered
        pass
    finally:
        watcher.cancel()
        await manager.disconnect(websocket)


@router.websocket("/api/orgs/{slug}/runs/{run_id}/ws")
async def run_feed_ws(
    websocket: WebSocket,
    slug: str,
    run_id: UUID,
    open_session: SessionOpener = Depends(get_session_opener),
) -> None:
    """Stream the feed events and the state changes of a run over a WebSocket.

    Open to every signed-in role of the organization, guests included. The
    token comes in the query parameter `token`. A guest needs a team in the
    run, and a registered account is admitted without one.

    The socket receives:
    - feed events of the run (announcements, countdowns, reminders, alerts)
    - feed events that target one team: coordinators and above get all of them,
      a guest or participant gets those of their own team
    - state changes of the run (active, paused, ended)

    A client sends the text `ping` as keepalive and gets `pong`. The server
    sends JSON messages of the form
    `{"type": "feed_event" | "run_state_change", "data": {...}, "timestamp": "..."}`.

    The server checks again every 25 seconds that the socket may stay open, and
    closes it with a code in these cases, among others: the session ends or the
    account or organization is locked, deactivated, archived or deleted (4000),
    the role of a staff socket dropped below coordinator (4000), the token
    expired (4002), a guest has no team in the run (4003), or the run is gone
    (1008).
    """
    # Every check runs in this session. It closes before accept(), so the
    # socket waits in its loop without a database connection.
    async with open_session() as db:
        try:
            user, org = await ws_authenticate(websocket, slug, db)
        except RuntimeError:
            # ws_authenticate already closed the connection with the right code.
            return
        user_id = user.id
        # Guests are admitted: they need this socket for the feed and the state changes of the run,
        # for example the redirect to /feedback at the end.
        is_staff = RoleLevel[user.role.value.upper()] >= RoleLevel.COORDINATOR
        grant = WsGrant(
            user_id=user_id,
            organization_id=org.id,
            session_version=user.session_version,
            token_expires_at=websocket.state.token_expires_at,
            run_id=run_id,
            staff=is_staff,
            session_id=websocket.state.session_id,
        )
        # The run exists in the organization, and a guest has a team in it.
        # The rule is ws_refusal.
        refusal = await ws_refusal(db, grant, connecting=True)
        if refusal is not None:
            await websocket.close(code=refusal.code, reason=refusal.reason)
            return

    await websocket.accept()

    await manager.subscribe(websocket, f"feed:{run_id}")
    # An event for one team is not sent on feed:{run_id}. Staff get it on the
    # staff channel, a member of that team on their own member channel.
    if is_staff:
        await manager.subscribe(websocket, f"feed_staff:{run_id}")
    else:
        await manager.subscribe(websocket, f"feed_member:{run_id}:{user_id}")
    await manager.subscribe(websocket, f"run_state:{run_id}")
    # The account's own channel: a password change or reset closes every open
    # socket of the account with 4000 "session revoked". Closing relies on the
    # API running as a single process: its connection manager knows every open
    # socket, and no other process holds any.
    await manager.subscribe(websocket, f"user:{user_id}")

    check = await _admit(websocket, open_session, grant)
    if check is not None:
        await _serve(websocket, check)


@router.websocket("/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/ws")
async def team_chat_ws(
    websocket: WebSocket,
    slug: str,
    run_id: UUID,
    team_id: UUID,
    open_session: SessionOpener = Depends(get_session_opener),
) -> None:
    """Stream the chat messages of a team over a WebSocket.

    Open to the members of the team. Coordinators and above are admitted only
    while the assist window of the team is open. The token comes in the query
    parameter `token`.

    A client sends the text `ping` as keepalive and gets `pong`. The server
    sends JSON messages of the form
    `{"type": "chat_message", "data": {...}, "timestamp": "..."}`.

    The server checks again every 25 seconds that the socket may stay open, and
    closes it with a code in these cases, among others: the session ends or the
    account or organization is locked, deactivated, archived or deleted (4000),
    the token expired (4002), the member left the team or the assist window
    closed (4003), or the run or team is gone (1008). A caller who may not join
    the chat is refused with 1008 at connect time.
    """
    # Every check runs in this session, which closes before accept().
    async with open_session() as db:
        try:
            user, org = await ws_authenticate(websocket, slug, db)
        except RuntimeError:
            # ws_authenticate already closed the connection with the right code.
            return
        user_id = user.id
        grant = WsGrant(
            user_id=user_id,
            organization_id=org.id,
            session_version=user.session_version,
            token_expires_at=websocket.state.token_expires_at,
            run_id=run_id,
            team_id=team_id,
            session_id=websocket.state.session_id,
        )
        # The run, the team in it, and the right to its chat: team member, or
        # coordinator+ with an open assist window (coordinators join on
        # request, not by default). The rule is ws_refusal, and it mirrors
        # chat.py::_verify_team_access.
        refusal = await ws_refusal(db, grant, connecting=True)
        if refusal is not None:
            await websocket.close(code=refusal.code, reason=refusal.reason)
            return

    await websocket.accept()

    # The account's channel lets a password change or reset close the socket with 4000. The close
    # relies on a single process, see run_feed_ws above.
    #
    # Subscription and first check run inside one delivery of the chat (manager.delivery). The end
    # of the assist window rechecks the sockets the chat has at that moment. Either the socket is on
    # the channel with its check before the end takes the lock, or its first check reads after the
    # end committed and closes it. No message of the chat reaches the socket while that check reads.
    async with manager.delivery(f"chat:{team_id}"):
        await manager.subscribe(websocket, f"chat:{team_id}")
        await manager.subscribe(websocket, f"user:{user_id}")
        check = await _admit(websocket, open_session, grant)
    if check is not None:
        await _serve(websocket, check)
