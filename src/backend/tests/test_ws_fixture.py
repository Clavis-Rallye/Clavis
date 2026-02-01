"""Smoke tests for the ``ws_client`` fixture and the ``ws_connect`` helper."""

import pytest
from httpx import AsyncClient
from httpx_ws.transport import ASGIWebSocketTransport

from tests.conftest import ws_connect


@pytest.mark.asyncio
async def test_ws_client_fixture_exists(ws_client: AsyncClient) -> None:
    """``ws_client`` talks WebSocket through ``ASGIWebSocketTransport``, without a real server."""
    assert ws_client is not None
    assert isinstance(ws_client, AsyncClient)
    assert ws_client._transport is not None
    assert isinstance(ws_client._transport, ASGIWebSocketTransport)


@pytest.mark.asyncio
async def test_ws_connect_helper_exists() -> None:
    """``ws_connect`` is importable and callable."""
    assert ws_connect is not None
    assert callable(ws_connect)
