"""WebSocket connections grouped by channel, for real-time broadcasts.

The asyncio locks order concurrent tasks of one event loop. They do not guard
against other threads.
"""

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import WebSocket
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

# Asks a socket's admission rule again and closes the socket if the rule
# refuses it. Returns True if it closed the socket.
SocketCheck = Callable[[], Awaitable[bool]]


class _ChannelLock:
    """One asyncio lock of the delivery table, with the number of users holding or awaiting it."""

    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


class ConnectionManager:
    """WebSocket connections grouped by channel, with subscriptions and broadcasts.

    Safe for concurrent tasks of one event loop, not for several threads.
    ``_ws_channels`` is the reverse index of ``_channels``, by socket id.
    ``_checks`` holds each socket's admission check, by socket id. ``_lock``
    orders the changes of these three tables.
    """

    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = defaultdict(set)
        self._ws_channels: dict[int, set[str]] = defaultdict(set)
        self._checks: dict[int, SocketCheck] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        # Closes scheduled from a commit hook. Held so they are not collected.
        self._pending_closes: set[asyncio.Task[None]] = set()
        # One delivery lock per channel with a delivery open or queued. An entry
        # goes when its last user leaves, so the table does not grow with the
        # number of channels seen.
        self._delivery_locks: dict[str, _ChannelLock] = {}

    @contextlib.asynccontextmanager
    async def delivery(self, channel: str) -> AsyncIterator[None]:
        """Deliver to one channel, one delivery at a time.

        A delivery that ends a right holds the channel for two steps: the
        message that ends the coordinator's help on ``chat:{team_id}`` and the
        recheck after it. No later message of the channel reaches a socket
        while the recheck still reads the database. Every chat message is sent
        inside it, by the request that ends the help (before its commit) and by
        the listener. A chat socket subscribes and asks its first check inside
        it. Deliveries to other channels do not wait.

        Not reentrant: do not open it again for the same channel inside it.
        The lock is per process, like the sockets it orders.
        """
        entry = self._delivery_locks.get(channel)
        if entry is None:
            entry = _ChannelLock()
            self._delivery_locks[channel] = entry
        entry.users += 1
        try:
            await entry.lock.acquire()
        except BaseException:
            self._leave_delivery(channel, entry)
            raise
        try:
            yield
        finally:
            entry.lock.release()
            self._leave_delivery(channel, entry)

    def _leave_delivery(self, channel: str, entry: _ChannelLock) -> None:
        entry.users -= 1
        if entry.users == 0 and self._delivery_locks.get(channel) is entry:
            del self._delivery_locks[channel]

    async def subscribe(self, ws: WebSocket, channel: str) -> None:
        async with self._lock:
            self._channels[channel].add(ws)
            self._ws_channels[id(ws)].add(channel)

    async def unsubscribe(self, ws: WebSocket, channel: str | None = None) -> None:
        """Remove a WebSocket from ``channel``, or from all its channels when ``channel`` is None."""
        async with self._lock:
            if channel is not None:
                self._channels[channel].discard(ws)
                self._ws_channels[id(ws)].discard(channel)

                if not self._channels[channel]:
                    del self._channels[channel]
            else:
                self._drop(ws)

    async def broadcast(self, channel: str, message: dict[str, Any]) -> None:
        """Send a JSON message to every WebSocket of a channel.

        A socket whose send fails is removed from its channels silently.
        """
        dead_connections: list[WebSocket] = []

        # Copy the subscribers, so no send runs under the lock.
        async with self._lock:
            connections = list(self._channels.get(channel, set()))

        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead_connections.append(ws)

        for ws in dead_connections:
            await self.disconnect(ws)

    async def broadcast_feed_changed(self, run_id: UUID | str) -> None:
        """Tell every socket of a run that its feed changed in more than one new entry.

        A ``feed_event`` can only add an entry. The publish of a result replaces
        the earlier result notice and removes the interim one, and the
        withdrawal removes the notice. After the commit of either, this goes out
        on ``feed:{run_id}``, which every socket of the run is on, and the page
        reads its feed again. The payload names only the run, in the
        ``{type, data, timestamp}`` envelope of every other broadcast.
        """
        await self.broadcast(
            f"feed:{run_id}",
            {
                "type": "feed_changed",
                "data": {"run_id": str(run_id)},
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    async def disconnect(self, ws: WebSocket) -> None:
        """Remove a WebSocket from all its channels and forget its check."""
        async with self._lock:
            self._drop(ws)

    def _drop(self, ws: WebSocket) -> None:
        """Remove a WebSocket from all its channels and forget its check. The caller must hold the lock."""
        self._checks.pop(id(ws), None)
        for channel in self._ws_channels.pop(id(ws), set()):
            self._channels[channel].discard(ws)

            if not self._channels[channel]:
                del self._channels[channel]

    async def close_channel(self, channel: str, code: int, reason: str) -> None:
        """Close every socket subscribed to a channel and forget each of them.

        Ends live connections for good, for example after a password change:
        every socket on the account's ``user:{id}`` channel is closed with 4000.
        Each closed socket leaves all its channels. A socket that is already
        closed raises on close, and is only removed.

        Only the sockets subscribed now are closed. A socket that authenticated
        before the revocation and subscribes after this call reads its session
        again after subscribing and closes itself (``routers/ws.py``).
        """
        async with self._lock:
            for ws in list(self._channels.get(channel, set())):
                with contextlib.suppress(Exception):
                    await ws.close(code=code, reason=reason)
                self._drop(ws)

    async def close_shared(self, channel: str, other_channel: str, code: int, reason: str) -> None:
        """Close only the sockets subscribed to both channels and forget each of them.

        Ends one account's sockets on one channel. For example, a member who
        left a team loses the team chat (``chat:{team_id}`` and ``user:{id}``),
        while the team's other members and the account's other sockets stay. As
        in :meth:`close_channel`, an already closed socket is only removed.
        """
        async with self._lock:
            shared = self._channels.get(channel, set()) & self._channels.get(other_channel, set())
            for ws in list(shared):
                with contextlib.suppress(Exception):
                    await ws.close(code=code, reason=reason)
                self._drop(ws)

    async def register_check(self, ws: WebSocket, check: SocketCheck) -> None:
        """Remember how to ask a socket's admission rule again.

        The handler registers it once the socket is subscribed. It is
        forgotten with the socket's subscriptions.
        """
        async with self._lock:
            self._checks[id(ws)] = check

    async def recheck_channel(self, channel: str) -> None:
        """Ask the admission rule of every socket on a channel again, now.

        For a change that ends the right of some sockets on a channel and not
        of others, for example a lowered role (``user:{id}``) or a closed assist
        window (``chat:{team_id}``). Each socket's own check decides and closes
        with the code of its rule, so a socket whose right holds stays open.
        Call it after the commit of the change. The keepalive asks the same
        check, so this call only spares the wait for it.

        The checks run outside the lock: each reads the database and closes
        through :meth:`disconnect`.
        """
        async with self._lock:
            checks = [self._checks[id(ws)] for ws in self._channels.get(channel, set()) if id(ws) in self._checks]
        for check in checks:
            # One socket's failing check must not keep the others open.
            with contextlib.suppress(Exception):
                await check()

    async def close_accounts(self, user_ids: Iterable[UUID]) -> None:
        """Close every socket on the ``user:{id}`` channel of each account with 4000 "session revoked"."""
        for user_id in user_ids:
            await self.close_channel(f"user:{user_id}", code=4000, reason="session revoked")

    def close_accounts_after_commit(self, session: AsyncSession, user_ids: Iterable[UUID]) -> None:
        """Close the sockets of accounts once the session's transaction commits.

        For a function that removes accounts and leaves the commit to its
        caller (``guest_cleanup.delete_run_guests``). Closing before the
        commit would let a socket reconnect while the account still exists. A
        rollback drops the close.
        """
        ids = list(user_ids)
        settled = False

        def on_commit(_session: Session) -> None:
            nonlocal settled
            if settled:
                return
            settled = True
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No loop to close on. The keepalive check ends the sockets.
                return
            task = loop.create_task(self.close_accounts(ids))
            self._pending_closes.add(task)
            task.add_done_callback(self._pending_closes.discard)

        def on_rollback(_session: Session) -> None:
            nonlocal settled
            settled = True

        event.listen(session.sync_session, "after_commit", on_commit)
        event.listen(session.sync_session, "after_rollback", on_rollback)

    def get_subscriber_count(self, channel: str) -> int:
        return len(self._channels.get(channel, set()))


manager = ConnectionManager()
