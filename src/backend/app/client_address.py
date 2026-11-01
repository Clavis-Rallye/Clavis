"""The client address of a request or a WebSocket: the one the server resolved.

uvicorn resolves it before the App sees the connection. Its proxy header
handling trusts the forwarding header only from the peers that
``FORWARDED_ALLOW_IPS`` names and takes the first untrusted entry from the
right, so a client behind a proxy that appends cannot choose its own address.
With ``FORWARDED_ALLOW_IPS=*`` every peer is trusted and the first entry wins
(``config.startup_warnings`` says so at start).

The audit lines, every limiter key, the team-code admission and the log context
use this one address, so they never disagree for one request. The App reads no
proxy header and parses nothing of its own: a second reading of the header
would let a client choose the address that one of them records.
"""

from starlette.requests import HTTPConnection


def client_address(connection: HTTPConnection) -> str:
    """Return ``connection.client.host``, the address the server resolved, or ``"unknown"`` without a client."""
    return connection.client.host if connection.client else "unknown"
