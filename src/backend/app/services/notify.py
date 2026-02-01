"""
Real-time announcements to the WebSocket clients.

Most helpers send a Postgres NOTIFY, which the PgNotifyListener broadcasts. A
few broadcast directly to this process's sockets.

Call ``notify_feed_event``, ``notify_chat_message`` and
``notify_run_state_change`` AFTER the commit of the data they announce, so that
only committed data is broadcast. They run ``pg_notify`` in the session's next
transaction, and Postgres delivers it when that transaction commits.
``notify_team_dissolved`` is the exception and runs before the commit.
"""

import json
from collections import OrderedDict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ws_manager as ws_connections
from app.services.ws_manager import manager as ws_manager

if TYPE_CHECKING:
    from app.models.chat_message import ChatMessage
    from app.models.feed_event import FeedEvent
    from app.models.run import Run

# Only these refusal reasons are reported live. A run that has ENDED no longer
# has a start to wait for, so RUN_ENDED stays silent.
JOIN_BLOCKED_REASONS = frozenset({"RUN_NOT_STARTED", "RUN_PAUSED"})


async def notify_join_blocked(run_id: UUID, error_code: str) -> None:
    """Broadcast a `join_blocked` event on the run's coordination channel.

    Called right before a join or team creation is refused with RUN_NOT_STARTED
    or RUN_PAUSED. Every other error code is silent.

    A direct WebSocket broadcast, not a pg_notify: the refusal rolls its
    transaction back, so Postgres would never deliver a notification sent in
    it. Nothing is stored, and the payload carries only the run and the reason,
    no personal data. The coordination's run dashboard receives it on
    `run_state:{run_id}` and counts the attempts since the page opened.
    """
    if error_code not in JOIN_BLOCKED_REASONS:
        return
    await ws_manager.broadcast(
        channel=f"run_state:{run_id}",
        message={
            "type": "join_blocked",
            "data": {
                "run_id": str(run_id),
                "reason": error_code,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


async def notify_team_members_changed(run_id: UUID, team_id: UUID) -> None:
    """Broadcast a `team_members_changed` event on the run's channel.

    Called after the commit of every join, leave and removal, so the
    coordination's run page reloads its team list without waiting for the next
    poll. A move has its own event (`team_member_moved`).

    A direct broadcast like `notify_join_blocked`. The payload carries only the
    run and the team, no personal data: a participant on the same channel
    learns no name from it, and the staff reload the names through the list
    endpoint, which shows them to coordinator+ only.
    """
    await ws_manager.broadcast(
        channel=f"run_state:{run_id}",
        message={
            "type": "team_members_changed",
            "data": {
                "run_id": str(run_id),
                "team_id": str(team_id),
            },
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


# What a dissolution tells the open connections. 4003 is one of the two codes
# the app does not reconnect on (useWebSocket.ts): a reconnect to the chat of a
# dissolved team would be refused anyway. The reason is its own.
TEAM_DISSOLVED_PG_CHANNEL = "team_dissolutions"
TEAM_DISSOLVED_CLOSE_CODE = 4003
TEAM_DISSOLVED_CLOSE_REASON = "Team dissolved"

# The teams this process has already closed and announced, by id, since a team
# dissolves once. The cap only bounds the memory of a long-lived process.
_DISSOLVED_SEEN_MAX = 4096
_dissolved_seen: OrderedDict[str, None] = OrderedDict()


async def notify_team_dissolved(db: AsyncSession, run_id: UUID, team_id: UUID) -> None:
    """Send Postgres NOTIFY for a dissolved team, inside the transaction that dissolves it.

    ``dissolve_team`` calls it before the commit and does not commit itself.
    Postgres delivers the notification with the commit and drops it with a
    rollback, so every listening process hears of a dissolution exactly when it
    holds: also on the paths that commit elsewhere (the final erasure of an
    account), and never for an attempt that lost a deadlock.

    The payload names the run and the team by id. It carries no team name,
    because the run channel reaches the members of the other teams.
    """
    payload = json.dumps({"run_id": str(run_id), "team_id": str(team_id)})
    await db.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": TEAM_DISSOLVED_PG_CHANNEL, "payload": payload},
    )


async def dispatch_team_dissolved(data: dict[str, Any]) -> None:
    """Close the chat of a dissolved team and tell its run, in this process.

    ``data`` is the payload of ``notify_team_dissolved``. Every socket on
    ``chat:{team_id}`` is closed, members and staff alike, and
    ``run_state:{run_id}`` gets one ``team_dissolved`` event, on which the run
    page, the scoreboard and the leaderboard of the other teams load again.

    Runs at most once per team and process, whoever calls first: the request
    that dissolved the team calls it after its commit, and the same notice may
    reach the process again through the database.
    """
    run_id, team_id = str(data["run_id"]), str(data["team_id"])
    if team_id in _dissolved_seen:
        return
    _dissolved_seen[team_id] = None
    if len(_dissolved_seen) > _DISSOLVED_SEEN_MAX:
        _dissolved_seen.popitem(last=False)

    await ws_connections.manager.close_channel(
        f"chat:{team_id}", code=TEAM_DISSOLVED_CLOSE_CODE, reason=TEAM_DISSOLVED_CLOSE_REASON
    )
    await ws_manager.broadcast(
        channel=f"run_state:{run_id}",
        message={
            "type": "team_dissolved",
            "data": {"run_id": run_id, "team_id": team_id},
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


# The system message that ends the coordinator's help. Frontends localize by
# the marker, and the listener closes the sockets the end of the help no longer
# admits after it has delivered this message.
COORDINATOR_LEFT_MARKER = "[coordinator_left]"

# The ids of the chat messages this process has already sent to its own sockets.
# Their notice comes back through the database as well, and the listener skips
# them. The cap only bounds the memory of a long-lived process.
_CHAT_SENT_HERE_MAX = 4096
_chat_sent_here: OrderedDict[str, None] = OrderedDict()


def remember_chat_message_sent_here(message_id: UUID | str) -> None:
    """Note that this process sends a chat message to its sockets itself.

    Call it before the notice of the message can come back, that is before the
    commit that delivers the ``pg_notify``: the listener of this process then
    does not deliver the same message a second time.
    """
    _chat_sent_here[str(message_id)] = None
    if len(_chat_sent_here) > _CHAT_SENT_HERE_MAX:
        _chat_sent_here.popitem(last=False)


def chat_message_sent_here(message_id: object) -> bool:
    """Whether this process has already sent the chat message to its sockets directly."""
    return str(message_id) in _chat_sent_here


def is_coordinator_left_message(data: dict[str, Any]) -> bool:
    """Whether a ``chat_messages`` payload is the system message that ends the help.

    Only the system message counts: a member may type the marker into the
    chat, and that changes no right to the channel.
    """
    return bool(data.get("is_system_message")) and str(data.get("content") or "").startswith(COORDINATOR_LEFT_MARKER)


async def notify_feed_event(db: AsyncSession, event: "FeedEvent") -> None:
    """Send Postgres NOTIFY on 'feed_events' for a new feed event.

    The PgNotifyListener dispatches it to `feed:{run_id}`, or, for an event with
    a target team, to the staff and that team's members. The session needs an
    active transaction, and the event needs its refreshed ``created_at``.
    """
    # A NOTIFY payload must stay under 8000 bytes.
    # created_at is a datetime object at runtime despite the Mapped[DateTime] type annotation
    created_at_value: datetime | None = event.created_at  # type: ignore[assignment]
    payload = json.dumps(
        {
            "id": str(event.id),
            "run_id": str(event.run_id),
            "organization_id": str(event.organization_id),
            "event_type": event.event_type.value if hasattr(event.event_type, "value") else event.event_type,
            "content": event.content,
            "author_id": str(event.author_id) if event.author_id else None,
            # The listener routes by this field: an event with a target goes
            # to the staff sockets and to that team's members only. The client
            # checks it once more.
            "target_team_id": str(event.target_team_id) if event.target_team_id else None,
            "created_at": created_at_value.isoformat() if created_at_value else None,
        }
    )

    # pg_notify channel name limit: 63 chars
    await db.execute(
        text("SELECT pg_notify('feed_events', :payload)"),
        {"payload": payload},
    )


async def notify_chat_message(db: AsyncSession, message: "ChatMessage", author_display_name: str | None = None) -> None:
    """Send Postgres NOTIFY on 'chat_messages' for a new chat message.

    The PgNotifyListener dispatches it to `chat:{team_id}`. The session needs an
    active transaction, and the message needs its refreshed ``created_at``.
    ``author_display_name`` is the author's current name, passed in because the
    caller already holds the User row. It is None for a system message or when
    the caller has no name.
    """
    # A NOTIFY payload must stay under 8000 bytes.
    # created_at is a datetime object at runtime despite the Mapped[DateTime] type annotation
    created_at_value: datetime | None = message.created_at  # type: ignore[assignment]
    payload = json.dumps(
        {
            "id": str(message.id),
            "team_id": str(message.team_id),
            "organization_id": str(message.organization_id),
            "author_id": str(message.author_id) if message.author_id else None,
            "content": message.content,
            "is_system_message": message.is_system_message,
            "author_role": message.author_role,
            "author_display_name": author_display_name,
            "created_at": created_at_value.isoformat() if created_at_value else None,
        }
    )

    await db.execute(
        text("SELECT pg_notify('chat_messages', :payload)"),
        {"payload": payload},
    )


async def notify_run_state_change(
    db: AsyncSession,
    run: "Run",
    previous_state: str,
) -> None:
    """Send Postgres NOTIFY on 'run_state_changes' for a run state transition.

    The PgNotifyListener dispatches it to `run_state:{run_id}`. The session needs
    an active transaction, ``run`` is the instance after the transition and
    ``previous_state`` the state before it, for example "configured".
    """
    # A NOTIFY payload must stay under 8000 bytes.
    payload = json.dumps(
        {
            "run_id": str(run.id),
            "organization_id": str(run.organization_id),
            "previous_state": previous_state,
            "new_state": run.state.value if hasattr(run.state, "value") else run.state,
            "changed_at": run.updated_at.isoformat() if run.updated_at else None,
        }
    )

    await db.execute(
        text("SELECT pg_notify('run_state_changes', :payload)"),
        {"payload": payload},
    )
