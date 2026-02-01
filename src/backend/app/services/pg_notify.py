"""
Postgres LISTEN/NOTIFY listener for real-time WebSocket broadcasts.

A background listener hears NOTIFY on four channels and dispatches each notice
to the WebSocket ConnectionManager. It uses its own raw asyncpg connection, not
SQLAlchemy, so the long-running LISTEN blocks no connection of the pool.
"""

import asyncio
import json
import logging
from typing import Any

import asyncpg

from app.services.notify import (
    TEAM_DISSOLVED_PG_CHANNEL,
    chat_message_sent_here,
    dispatch_team_dissolved,
    is_coordinator_left_message,
)
from app.services.ws_manager import manager

logger = logging.getLogger(__name__)


class PgNotifyListener:
    """
    Background listener for Postgres NOTIFY events.

    The Postgres channels, and where each notice goes:
        - feed_events: `feed:{run_id}`, or for an event with a target team
          `feed_staff:{run_id}` and `feed_member:{run_id}:{user_id}` of each member
        - chat_messages: `chat:{team_id}`, except a message this process already
          sent there directly. After the message that ends the coordinator's help
          the channel's sockets are checked again.
        - run_state_changes: `run_state:{run_id}`
        - team_dissolutions: every socket on `chat:{team_id}` is closed and
          `run_state:{run_id}` gets one `team_dissolved` event
    """

    def __init__(self, database_url: str) -> None:
        # asyncpg does not know the SQLAlchemy driver suffix of the URL scheme.
        self._dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._conn: asyncpg.Connection | None = None
        # Feed events are dispatched one at a time, in the order they arrived. A
        # targeted event waits for a query, and without the lock a broadcast sent
        # after it could overtake it.
        self._feed_lock = asyncio.Lock()

    async def start(self) -> None:
        """Connect to Postgres and listen on the four channels. A failure is logged and raised."""
        try:
            logger.info("PgNotifyListener starting - connecting to database")
            self._conn = await asyncpg.connect(self._dsn)

            await self._conn.add_listener("feed_events", self._on_notification)
            await self._conn.add_listener("chat_messages", self._on_notification)
            await self._conn.add_listener("run_state_changes", self._on_notification)
            await self._conn.add_listener(TEAM_DISSOLVED_PG_CHANNEL, self._on_notification)

            logger.info(
                "PgNotifyListener started successfully - listening on feed_events, chat_messages, "
                f"run_state_changes, {TEAM_DISSOLVED_PG_CHANNEL}"
            )
        except Exception as e:
            logger.error(f"PgNotifyListener failed to start: {e}")
            raise

    async def stop(self) -> None:
        """Remove the listeners and close the connection. Safe to call when not started or already stopped."""
        if self._conn is None:
            logger.warning("PgNotifyListener.stop() called but connection is None")
            return

        try:
            logger.info("PgNotifyListener stopping - removing listeners")

            await self._conn.remove_listener("feed_events", self._on_notification)
            await self._conn.remove_listener("chat_messages", self._on_notification)
            await self._conn.remove_listener("run_state_changes", self._on_notification)
            await self._conn.remove_listener(TEAM_DISSOLVED_PG_CHANNEL, self._on_notification)

            await self._conn.close()
            logger.info("PgNotifyListener stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping PgNotifyListener: {e}")
        finally:
            self._conn = None

    def _on_notification(
        self,
        _connection: asyncpg.Connection,
        _pid: int,
        channel: str,
        payload: str,
    ) -> None:
        """Schedule the dispatch of one NOTIFY.

        asyncpg calls this synchronously, so the dispatch runs as a task and
        does not block the listener.
        """
        try:
            data = json.loads(payload)

            asyncio.create_task(self._dispatch(channel, data))
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse NOTIFY payload from channel '{channel}': {e}")
        except Exception as e:
            logger.error(f"Error in _on_notification for channel '{channel}': {e}")

    async def _team_member_ids(self, team_id: str) -> list[str]:
        """
        The user ids of a team's members, read on the listener's own connection.

        Fails closed: when the lookup fails the list is empty, and a targeted
        event reaches the staff sockets only. The members still find it in the
        feed list, which filters in the database.
        """
        if not self._conn:
            return []
        try:
            # The caller holds the feed lock, so this is the only query on the
            # connection. The timeout keeps a half-dead connection from holding
            # that lock, and with it every later feed event, forever.
            rows = await self._conn.fetch(
                "SELECT user_id FROM team_members WHERE team_id = $1::uuid", team_id, timeout=5.0
            )
            return [str(row["user_id"]) for row in rows]
        except Exception as e:
            logger.error(f"Could not read the members of team {team_id} for a targeted feed event: {e}")
            return []

    async def _dispatch(self, pg_channel: str, data: dict[str, Any]) -> None:
        """
        Dispatch one NOTIFY to its WebSocket channels, as the class docstring lists.

        Message envelope:
            {
                "type": "feed_event" | "chat_message" | "run_state_change",
                "data": {...},  # The original payload
                "timestamp": "2026-02-14T12:34:56Z"
            }

        The team_dissolved envelope is built in services/notify.py.
        """
        try:
            if pg_channel == "feed_events":
                async with self._feed_lock:
                    message = {
                        "type": "feed_event",
                        "data": data,
                        "timestamp": data.get("created_at"),
                    }
                    target_team_id = data.get("target_team_id")
                    if not target_team_id:
                        # A broadcast: every socket of the run.
                        ws_channel = f"feed:{data['run_id']}"
                        await manager.broadcast(ws_channel, message)
                        logger.debug(f"Dispatched feed_event to {ws_channel}")
                    else:
                        # An event for one team (an alarm, a help request, an
                        # automatic score) goes to the staff sockets of the run
                        # and to that team's members, never to another team.
                        # The members are read at dispatch time, so a member
                        # moved a minute ago is right.
                        await manager.broadcast(f"feed_staff:{data['run_id']}", message)
                        for user_id in await self._team_member_ids(target_team_id):
                            await manager.broadcast(f"feed_member:{data['run_id']}:{user_id}", message)
                        logger.debug(f"Dispatched targeted feed_event of run {data['run_id']}")

            elif pg_channel == "chat_messages":
                ws_channel = f"chat:{data['team_id']}"
                if chat_message_sent_here(data.get("id")):
                    # The request that wrote it already sent it to this
                    # process's sockets and rechecked the channel after it.
                    logger.debug(f"Skipped chat_message {data.get('id')} already sent to {ws_channel}")
                    return
                message = {
                    "type": "chat_message",
                    "data": data,
                    "timestamp": data.get("created_at"),
                }
                # One delivery of the channel at a time: a later message waits
                # until the recheck after the end of the help has closed the
                # coordinator's socket. Each notice is its own task, so without
                # the lock it would pass while the recheck reads the database.
                async with manager.delivery(ws_channel):
                    await manager.broadcast(ws_channel, message)
                    logger.debug(f"Dispatched chat_message to {ws_channel}")
                    if is_coordinator_left_message(data):
                        # The help has ended. A process that hears of it only
                        # here closes the socket of a coordinator who is no
                        # member now, after the message and not before it.
                        await manager.recheck_channel(ws_channel)

            elif pg_channel == "run_state_changes":
                ws_channel = f"run_state:{data['run_id']}"
                message = {
                    "type": "run_state_change",
                    "data": data,
                    "timestamp": data.get("changed_at"),
                }
                await manager.broadcast(ws_channel, message)
                logger.debug(f"Dispatched run_state_change to {ws_channel}")

            elif pg_channel == TEAM_DISSOLVED_PG_CHANNEL:
                # notify.py holds the one copy of the close and the broadcast. It
                # runs once per team and process, so a dissolution this process
                # already dispatched does nothing here.
                await dispatch_team_dissolved(data)
                logger.debug(f"Dispatched team_dissolved of team {data['team_id']}")

            else:
                logger.warning(f"Received NOTIFY from unknown channel: {pg_channel}")

        except KeyError as e:
            logger.error(f"Missing required field in NOTIFY payload from {pg_channel}: {e}")
        except Exception as e:
            logger.error(f"Error dispatching NOTIFY from {pg_channel}: {e}")
