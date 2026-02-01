"""
Unit tests for ``ConnectionManager``, with mock WebSocket objects and without a
database or a real connection.
"""

import pytest

from app.services.ws_manager import ConnectionManager


class MockWebSocket:
    """Mock WebSocket for testing.

    Provides send_json and close methods that can be configured to succeed
    or fail.
    """

    def __init__(self, should_fail: bool = False, already_closed: bool = False) -> None:
        """Initialize mock WebSocket.

        Args:
            should_fail: If True, send_json will raise an exception
            already_closed: If True, close will raise like a socket that is
                already closed
        """
        self.should_fail = should_fail
        self.already_closed = already_closed
        self.sent_messages: list[dict] = []
        self.close_calls: list[dict] = []

    async def send_json(self, data: dict) -> None:
        """Mock send_json method.

        Args:
            data: Message to send

        Raises:
            RuntimeError: If should_fail is True
        """
        if self.should_fail:
            raise RuntimeError("Connection failed")
        self.sent_messages.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        """Mock close method, recording the code and reason it was called with.

        Raises:
            RuntimeError: If already_closed is True (like a real socket that
                is already closed)
        """
        if self.already_closed:
            raise RuntimeError("Connection is closed")
        self.close_calls.append({"code": code, "reason": reason})


@pytest.mark.asyncio
async def test_subscribe_adds_to_channel() -> None:
    """Subscribing adds the connection to the channel."""
    manager = ConnectionManager()
    ws = MockWebSocket()
    channel = "test_channel"

    await manager.subscribe(ws, channel)

    assert manager.get_subscriber_count(channel) == 1


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_subscribers() -> None:
    """A broadcast reaches every subscriber of the channel."""
    manager = ConnectionManager()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    ws3 = MockWebSocket()
    channel = "broadcast_channel"

    await manager.subscribe(ws1, channel)
    await manager.subscribe(ws2, channel)
    await manager.subscribe(ws3, channel)

    test_message = {"type": "test", "data": "hello"}
    await manager.broadcast(channel, test_message)

    assert len(ws1.sent_messages) == 1
    assert len(ws2.sent_messages) == 1
    assert len(ws3.sent_messages) == 1
    assert ws1.sent_messages[0] == test_message
    assert ws2.sent_messages[0] == test_message
    assert ws3.sent_messages[0] == test_message


@pytest.mark.asyncio
async def test_disconnect_removes_from_all_channels() -> None:
    """Disconnecting removes the connection from all its channels."""
    manager = ConnectionManager()
    ws = MockWebSocket()
    channel1 = "channel_1"
    channel2 = "channel_2"
    channel3 = "channel_3"

    await manager.subscribe(ws, channel1)
    await manager.subscribe(ws, channel2)
    await manager.subscribe(ws, channel3)

    assert manager.get_subscriber_count(channel1) == 1
    assert manager.get_subscriber_count(channel2) == 1
    assert manager.get_subscriber_count(channel3) == 1

    await manager.disconnect(ws)

    assert manager.get_subscriber_count(channel1) == 0
    assert manager.get_subscriber_count(channel2) == 0
    assert manager.get_subscriber_count(channel3) == 0


@pytest.mark.asyncio
async def test_broadcast_to_empty_channel_is_noop() -> None:
    """Broadcasting to a channel without subscribers does not raise."""
    manager = ConnectionManager()
    channel = "empty_channel"

    test_message = {"type": "test", "data": "hello"}
    await manager.broadcast(channel, test_message)

    assert manager.get_subscriber_count(channel) == 0


@pytest.mark.asyncio
async def test_multiple_channels_per_connection() -> None:
    """One connection can subscribe to several channels and gets each broadcast once."""
    manager = ConnectionManager()
    ws = MockWebSocket()
    channel1 = "multi_channel_1"
    channel2 = "multi_channel_2"
    channel3 = "multi_channel_3"

    await manager.subscribe(ws, channel1)
    await manager.subscribe(ws, channel2)
    await manager.subscribe(ws, channel3)

    assert manager.get_subscriber_count(channel1) == 1
    assert manager.get_subscriber_count(channel2) == 1
    assert manager.get_subscriber_count(channel3) == 1

    await manager.broadcast(channel1, {"type": "test1"})
    assert len(ws.sent_messages) == 1

    await manager.broadcast(channel2, {"type": "test2"})
    assert len(ws.sent_messages) == 2

    await manager.broadcast(channel3, {"type": "test3"})
    assert len(ws.sent_messages) == 3


@pytest.mark.asyncio
async def test_multiple_connections_per_channel() -> None:
    """Several connections can subscribe to the same channel."""
    manager = ConnectionManager()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    ws3 = MockWebSocket()
    ws4 = MockWebSocket()
    channel = "popular_channel"

    await manager.subscribe(ws1, channel)
    await manager.subscribe(ws2, channel)
    await manager.subscribe(ws3, channel)
    await manager.subscribe(ws4, channel)

    assert manager.get_subscriber_count(channel) == 4

    test_message = {"type": "announcement"}
    await manager.broadcast(channel, test_message)

    assert len(ws1.sent_messages) == 1
    assert len(ws2.sent_messages) == 1
    assert len(ws3.sent_messages) == 1
    assert len(ws4.sent_messages) == 1


@pytest.mark.asyncio
async def test_unsubscribe_from_specific_channel() -> None:
    """Unsubscribing leaves the connection on its other channels."""
    manager = ConnectionManager()
    ws = MockWebSocket()
    channel1 = "unsub_channel_1"
    channel2 = "unsub_channel_2"
    channel3 = "unsub_channel_3"

    await manager.subscribe(ws, channel1)
    await manager.subscribe(ws, channel2)
    await manager.subscribe(ws, channel3)

    assert manager.get_subscriber_count(channel1) == 1
    assert manager.get_subscriber_count(channel2) == 1
    assert manager.get_subscriber_count(channel3) == 1

    await manager.unsubscribe(ws, channel2)

    assert manager.get_subscriber_count(channel1) == 1
    assert manager.get_subscriber_count(channel2) == 0
    assert manager.get_subscriber_count(channel3) == 1

    await manager.broadcast(channel1, {"msg": "1"})
    await manager.broadcast(channel2, {"msg": "2"})
    await manager.broadcast(channel3, {"msg": "3"})

    assert len(ws.sent_messages) == 2
    assert ws.sent_messages[0] == {"msg": "1"}
    assert ws.sent_messages[1] == {"msg": "3"}


@pytest.mark.asyncio
async def test_get_subscriber_count() -> None:
    """The subscriber count follows subscribe, unsubscribe and disconnect."""
    manager = ConnectionManager()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    ws3 = MockWebSocket()
    channel = "count_channel"

    assert manager.get_subscriber_count(channel) == 0

    await manager.subscribe(ws1, channel)
    assert manager.get_subscriber_count(channel) == 1

    await manager.subscribe(ws2, channel)
    assert manager.get_subscriber_count(channel) == 2

    await manager.subscribe(ws3, channel)
    assert manager.get_subscriber_count(channel) == 3

    await manager.disconnect(ws2)
    assert manager.get_subscriber_count(channel) == 2

    await manager.unsubscribe(ws1, channel)
    assert manager.get_subscriber_count(channel) == 1

    await manager.disconnect(ws3)
    assert manager.get_subscriber_count(channel) == 0


@pytest.mark.asyncio
async def test_broadcast_handles_disconnected_clients() -> None:
    """A broadcast removes a connection whose send fails and still reaches the others."""
    manager = ConnectionManager()
    ws_working = MockWebSocket(should_fail=False)
    ws_dead = MockWebSocket(should_fail=True)
    channel = "mixed_channel"

    await manager.subscribe(ws_working, channel)
    await manager.subscribe(ws_dead, channel)

    assert manager.get_subscriber_count(channel) == 2

    test_message = {"type": "cleanup_test"}
    await manager.broadcast(channel, test_message)

    assert len(ws_working.sent_messages) == 1
    assert ws_working.sent_messages[0] == test_message

    assert manager.get_subscriber_count(channel) == 1

    await manager.broadcast(channel, {"type": "second_message"})
    assert len(ws_working.sent_messages) == 2
    assert manager.get_subscriber_count(channel) == 1


@pytest.mark.asyncio
async def test_close_channel_closes_subscribers_and_removes_from_every_channel() -> None:
    """close_channel closes each subscriber with the given code and reason and
    removes it from ALL its channels, not just the closed one."""
    manager = ConnectionManager()
    ws_shared = MockWebSocket()
    ws_only_target = MockWebSocket()
    ws_bystander = MockWebSocket()
    target = "user:1"
    other = "feed:run-1"

    # ws_shared sits on the target channel and a run channel; ws_only_target
    # only on the target; ws_bystander only on the run channel.
    await manager.subscribe(ws_shared, target)
    await manager.subscribe(ws_shared, other)
    await manager.subscribe(ws_only_target, target)
    await manager.subscribe(ws_bystander, other)

    await manager.close_channel(target, code=4000, reason="session revoked")

    assert ws_shared.close_calls == [{"code": 4000, "reason": "session revoked"}]
    assert ws_only_target.close_calls == [{"code": 4000, "reason": "session revoked"}]

    assert manager.get_subscriber_count(target) == 0
    assert manager.get_subscriber_count(other) == 1

    assert ws_bystander.close_calls == []


@pytest.mark.asyncio
async def test_close_channel_tolerates_already_closed_socket() -> None:
    """A socket that is already closed raises on close; close_channel swallows
    that and still removes the socket from all its channels."""
    manager = ConnectionManager()
    ws_dead = MockWebSocket(already_closed=True)
    ws_live = MockWebSocket()
    channel = "user:2"
    other = "chat:team-1"

    await manager.subscribe(ws_dead, channel)
    await manager.subscribe(ws_dead, other)
    await manager.subscribe(ws_live, channel)

    # Must not raise although ws_dead.close raises.
    await manager.close_channel(channel, code=4000, reason="session revoked")

    assert manager.get_subscriber_count(channel) == 0
    assert manager.get_subscriber_count(other) == 0
    assert ws_live.close_calls == [{"code": 4000, "reason": "session revoked"}]


@pytest.mark.asyncio
async def test_close_channel_on_empty_channel_is_noop() -> None:
    """Closing a channel nobody subscribes to does not error."""
    manager = ConnectionManager()

    await manager.close_channel("user:nobody", code=4000, reason="session revoked")

    assert manager.get_subscriber_count("user:nobody") == 0


@pytest.mark.asyncio
async def test_close_shared_closes_only_sockets_on_both_channels() -> None:
    """close_shared closes the sockets subscribed to both channels (one
    account's chat socket) and leaves the rest of either channel alone."""
    manager = ConnectionManager()
    ws_both = MockWebSocket()
    ws_chat_only = MockWebSocket()
    ws_account_only = MockWebSocket()
    chat = "chat:team-1"
    account = "user:1"

    await manager.subscribe(ws_both, chat)
    await manager.subscribe(ws_both, account)
    await manager.subscribe(ws_chat_only, chat)
    await manager.subscribe(ws_account_only, account)

    await manager.close_shared(chat, account, code=4003, reason="Left the team")

    assert ws_both.close_calls == [{"code": 4003, "reason": "Left the team"}]
    assert ws_chat_only.close_calls == []
    assert ws_account_only.close_calls == []
    assert manager.get_subscriber_count(chat) == 1
    assert manager.get_subscriber_count(account) == 1


@pytest.mark.asyncio
async def test_close_shared_on_unknown_channels_is_noop() -> None:
    manager = ConnectionManager()

    await manager.close_shared("chat:none", "user:none", code=4003, reason="Left the team")

    assert manager.get_subscriber_count("chat:none") == 0
