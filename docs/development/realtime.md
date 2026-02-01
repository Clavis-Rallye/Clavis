# Real-Time Communication

## Architecture

Real-time updates pass a pipeline of four steps:

```
1. API handler writes to DB
2. pg_notify() sends Postgres NOTIFY with JSON payload
3. PgNotifyListener receives NOTIFY, dispatches to ConnectionManager
4. ConnectionManager broadcasts to subscribed WebSocket clients
```

This design means write operations and broadcast operations are decoupled — the API handler doesn't need to know about WebSocket connections. Any code path that writes a feed event, chat message, or run state change just calls the appropriate `notify_*` helper after committing. Two events bypass `pg_notify` entirely and are broadcast straight from the handler — see [Direct broadcasts](#direct-broadcasts-no-pg_notify) below.

## Components

### ConnectionManager (`app/services/ws_manager.py`)

Singleton that manages WebSocket connections grouped by channel. Thread-safe via `asyncio.Lock`.

**Data structures:**
- `_channels: dict[str, set[WebSocket]]` — maps channel names to subscriber sets
- `_ws_channels: dict[int, set[str]]` — reverse index, maps WebSocket IDs to their subscribed channels

**Operations:**

| Method | Description |
|--------|-------------|
| `subscribe(ws, channel)` | Add WebSocket to a channel |
| `unsubscribe(ws, channel?)` | Remove from one or all channels |
| `broadcast(channel, message)` | Send JSON to all subscribers; silently cleans up dead connections |
| `close_channel(channel, code, reason)` | Close every subscriber of a channel and remove each from all its channels (see below) |
| `disconnect(ws)` | Remove WebSocket from all channels and clean up |
| `get_subscriber_count(channel)` | Return number of active subscribers |

Broadcasting releases the lock before sending to avoid holding it during I/O. Dead connections discovered during broadcast are cleaned up afterward.

### PgNotifyListener (`app/services/pg_notify.py`)

Background listener that bridges Postgres NOTIFY events to the ConnectionManager.

**Key design decision:** Uses a raw `asyncpg` connection, not SQLAlchemy, to avoid blocking the connection pool with long-running LISTEN operations.

**Channels listened on:**

| Postgres Channel | WebSocket Channel | Message Type |
|-----------------|-------------------|--------------|
| `feed_events` | `feed:{run_id}` | `feed_event` |
| `chat_messages` | `chat:{team_id}` | `chat_message` |
| `run_state_changes` | `run_state:{run_id}` | `run_state_change` |

The listener's callback (`_on_notification`) is invoked synchronously by asyncpg. It parses the JSON payload and schedules an async dispatch task via `asyncio.create_task()` to avoid blocking the notification reception loop.

**Lifecycle:**
- Started during app startup (skipped when `TESTING=true`)
- Stopped during shutdown (before database disposal)

### Notify Helpers (`app/services/notify.py`)

Three functions that send Postgres NOTIFY from within request handlers:

```python
await notify_feed_event(db, event)
await notify_chat_message(db, message)
await notify_run_state_change(db, run, previous_state)
```

Each serializes the model to JSON and sends it via `SELECT pg_notify('channel', :payload)`.

**Critical rule:** These must be called AFTER `db.commit()`. The notify happens within a new transaction (implicit from the `text()` execution), but the data being referenced should already be committed so clients can fetch it if needed.

**Payload size limit:** Postgres NOTIFY payloads are limited to ~8000 bytes. The helpers serialize only essential fields (IDs, content, timestamps) to stay within this limit.

### Direct Broadcasts (no pg_notify)

Three events are sent straight through the in-process
ConnectionManager, not via `pg_notify`:

- **`team_member_moved`** — after the move commits (`teams.py`), on both teams'
  chat channels (`chat:{from_team_id}`, `chat:{to_team_id}`) **and** on
  `run_state:{run_id}`. Open clients follow the move without a reload: the chat
  rosters update on both sides, and a participant's own client detects "I was
  moved" on the run channel and switches its team context (chat, progress,
  leaderboard). Payload: run id, member id, user id (nullable for guests),
  display name, from and to team ids.
- **`join_blocked`** (`notify_join_blocked` in `notify.py`) — on
  `run_state:{run_id}`, sent right before a join or team creation is refused
  with `RUN_NOT_STARTED` or `RUN_PAUSED`; every other error code is silent (a
  run that has ENDED has no start to wait for). The coordination dashboard
  counts the refused attempts live since the page opened. Payload: run id and
  reason only — no personal data, and nothing is stored.
- **`team_members_changed`** (`notify_team_members_changed` in `notify.py`): on `run_state:{run_id}`, after the commit of every join (guest
  join, join by code, join by id), leave and removal. The run page of the
  Spielleitung reloads its team list at once, so a new member's name shows in
  the team row without the 15 s poll. Payload: run id and team id only. The
  names come from the team list, which carries them for coordinator+ only.

`join_blocked` cannot ride on `pg_notify`: the refusal raises right after the
broadcast, so its transaction rolls back, and Postgres would never deliver a
notification sent inside a rolled-back transaction.

**Single-process caveat:** a direct broadcast reaches only the clients
connected to the same API process. The API runs as one process — the
Dockerfile starts a single uvicorn worker with no `--workers` — which is what
makes both events, and the session-revoked socket close below, reach every
client. A second worker process would need a `pg_notify` of its own, sent on
its own connection, for these events to reach the clients of the other worker.

## The User Channel and the Session-Revoked Close

Next to its content channels, every authenticated WebSocket subscribes to the
account's own channel, `user:{user.id}`. Nothing is ever broadcast on it — it
exists so the server can close the account's open sockets in one place.

When an account's password changes or is reset (`routers/auth.py`), the
handler — after the commit that increments the account's session version —
calls `manager.close_channel(f"user:{user.id}", code=4000,
reason="session revoked")`. Every socket of the account closes with code
4000, including the ones of the session that made the change; a socket that
is already closed is tolerated and simply forgotten. The close matters
because an open connection keeps delivering chat messages and run events:
without it, a session someone else holds would keep reading until it
disconnects on its own.

**Why 4000, and what the client does with it.** The client keeps a list of
non-recoverable close codes — 4001 (authentication failed) and 4003
(forbidden); reconnecting cannot fix those, so it gives up — and reconnects
on any other code. 4000 is deliberately not in that list: a closed socket
reconnects and fetches a fresh WebSocket token, and that fetch is the
checkpoint. For a session revoked by the password change the fetch answers
401 `SESSION_REVOKED` and the client goes to its login page; for the session
that made the change the new token carries the new version and the socket
simply comes back. The session-version mechanics are in
[authentication.md](authentication.md).

Like the direct broadcasts, the close runs through the in-process
ConnectionManager, so it relies on the same single-process caveat above: this
process's manager knows every open socket.

## WebSocket Endpoints

### Run Feed & State: `/api/orgs/{slug}/runs/{run_id}/ws`

- **Auth:** participant+ role required
- **Subscribes to:** `feed:{run_id}`, `run_state:{run_id}`, `user:{user.id}` (the account's own channel — see [The User Channel and the Session-Revoked Close](#the-user-channel-and-the-session-revoked-close))
- **Receives:** feed events (announcements, countdowns, reminders, alerts), run state changes (active/paused/ended), and the directly broadcast `team_member_moved`, `join_blocked` and `team_members_changed` events (see above)

### Team Chat: `/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/ws`

- **Auth:** team member OR coordinator+ role required
- **Subscribes to:** `chat:{team_id}`, `user:{user.id}` (the account's own channel — see [The User Channel and the Session-Revoked Close](#the-user-channel-and-the-session-revoked-close))
- **Receives:** chat messages for this team, and `team_member_moved` when a member moves into or out of it

### Common Behavior

Both endpoints follow the same pattern:

1. Authenticate via `?token=<jwt>` query parameter
2. Verify org membership and role
3. Verify the referenced run/team exists and belongs to the org
4. Accept the WebSocket connection
5. Subscribe to appropriate channels
6. Enter keepalive loop (client sends `"ping"` text → server responds `"pong"`)
7. On disconnect: clean up all subscriptions via `manager.disconnect(ws)`

### Message Envelope

All messages sent to clients follow this format:

```json
{
  "type": "feed_event" | "chat_message" | "run_state_change" | "team_member_moved" | "join_blocked" | "team_members_changed",
  "data": { ... },
  "timestamp": "2026-02-14T12:00:00+00:00"
}
```

The last two types arrive as direct broadcasts, not through the pg_notify
pipeline.

## Data Flow Example

Here's what happens when a coordinator posts a feed announcement:

```
1. POST /api/orgs/{slug}/runs/{run_id}/feed
   → Router validates auth (coordinator+), resolves org
   → Creates FeedEvent in database
   → session.commit()

2. notify_feed_event(db, event)
   → Serializes event to JSON
   → SELECT pg_notify('feed_events', '{"id":"...","run_id":"...","event_type":"announcement",...}')

3. PgNotifyListener._on_notification(channel="feed_events", payload=...)
   → Parses JSON
   → asyncio.create_task(_dispatch("feed_events", data))

4. PgNotifyListener._dispatch("feed_events", data)
   → Constructs ws_channel = "feed:{run_id}"
   → manager.broadcast("feed:{run_id}", {"type":"feed_event","data":{...},"timestamp":"..."})

5. ConnectionManager.broadcast
   → For each WebSocket subscribed to "feed:{run_id}":
     → ws.send_json(message)
   → Dead connections cleaned up
```

## Testing

WebSocket tests use `httpx.AsyncClient` with `ASGITransport` for the initial connection, but the PgNotifyListener is not started in test mode (`TESTING=true`). Instead, tests that need real-time behavior either:

- Call `manager.broadcast()` directly to simulate notifications
- Test the components individually (ConnectionManager subscribe/broadcast, notify helpers, PgNotifyListener dispatch logic)
