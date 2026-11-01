"""One client address for the audit line, every limit and the log context.

The address of a client is the one the server resolved for the connection.
uvicorn resolves it with its proxy header handling, ``ProxyHeadersMiddleware``,
from ``FORWARDED_ALLOW_IPS``. The App reads ``connection.client.host`` through
one helper and parses no header of its own.

The cases run the real App inside the middleware class uvicorn installs, with
one trusted proxy, ``172.20.0.1``. ``httpx.ASGITransport(client=...)`` stands
for the peer that opened the TCP connection. Each case reads the address of the
audit line, the key the limiter counts under and the ``client_ip`` of the log
context of the same request.
"""

import ast
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import app.routers.instance_auth as instance_auth_router
import app.services.audit as audit_service
from app.auth.instance import create_instance_admin
from app.config import get_settings, startup_warnings
from app.main import app
from app.middleware.rate_limit import limiter

APP_DIR = Path(__file__).resolve().parents[1] / "app"
HELPER = APP_DIR / "client_address.py"

PROXY = "172.20.0.1"
CLIENT = "198.51.100.7"
FORGED = "203.0.113.7"
STRANGER = "192.0.2.99"
PEER_PORT = 4711

USER_LOGIN = "/api/auth/login"
INSTANCE_LOGIN = "/api/instance/auth/login"


class _Recorded:
    """Audit events and the log context the App had when it wrote them."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, original: Callable[..., None]) -> Callable[..., None]:
        def recording(event: str, actor: dict[str, Any] | None = None, **kwargs: Any) -> None:
            self.events.append(
                {
                    "event": event,
                    "actor_ip": (actor or {}).get("ip"),
                    "context_ip": structlog.contextvars.get_contextvars().get("client_ip"),
                }
            )
            original(event, actor=actor, **kwargs)

        return recording

    def of(self, event: str) -> list[dict[str, Any]]:
        return [entry for entry in self.events if entry["event"] == event]


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> _Recorded:
    """Record every audit event of the sign-in routes, as the audit line receives it."""
    recorded = _Recorded()
    monkeypatch.setattr(audit_service, "record_audit_event", recorded.record(audit_service.record_audit_event))
    monkeypatch.setattr(
        instance_auth_router, "record_audit_event", recorded.record(instance_auth_router.record_audit_event)
    )
    return recorded


def _limiter_keys() -> list[str]:
    """The client keys the limiter has counted under, read from its storage.

    A moving window keeps one entry per counter: ``LIMITER/<key>/<route>/<amount>/<n>/<unit>``.
    """
    storage = limiter._storage
    counters = list(getattr(storage, "events", {})) + list(getattr(storage, "storage", {}))
    return sorted({counter.split("/")[1] for counter in counters if counter.startswith("LIMITER/")})


@pytest.fixture
def behind_proxy(client: AsyncClient) -> Callable[[str], Any]:
    """Open a client whose connection comes from ``peer``, through uvicorn's proxy header handling.

    ``client`` holds the App's database override for the test. Only ``PROXY`` is trusted.
    """
    proxied = ProxyHeadersMiddleware(app, trusted_hosts=PROXY)  # type: ignore[arg-type]

    @asynccontextmanager
    async def connect(peer: str) -> AsyncIterator[AsyncClient]:
        transport = ASGITransport(app=proxied, client=(peer, PEER_PORT))  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://testserver") as connection:
            yield connection

    return connect


# (case, peer, X-Forwarded-For or None, the address the audit line and the limiter carry)
CASES = [
    ("1 direct client, forged header", STRANGER, FORGED, STRANGER),
    ("2 appending chain: forged, client", PROXY, f"{FORGED}, {CLIENT}", CLIENT),
    ("3 replacing chain: client", PROXY, CLIENT, CLIENT),
    ("4a trusted last hop", PROXY, f"{FORGED}, {CLIENT}", CLIENT),
    ("4b untrusted last hop, same header", STRANGER, f"{FORGED}, {CLIENT}", STRANGER),
    ("5a IPv6 peer, forged header", "2001:db8::99", FORGED, "2001:db8::99"),
    ("5b IPv6 client behind the proxy", PROXY, f"{FORGED}, 2001:db8::7", "2001:db8::7"),
    ("5c bracketed IPv6 client with a port", PROXY, "[2001:db8::7]:4711", "2001:db8::7"),
    # uvicorn strips the spaces of each entry and splits the port off: 198.51.100.7.
    ("6 spaces and a port", PROXY, f" {CLIENT}:4711 ,{PROXY}", CLIENT),
    ("7a no header, untrusted peer", STRANGER, None, STRANGER),
    ("7b no header, trusted peer", PROXY, None, PROXY),
    # uvicorn takes the malformed entry as the client: the App records the literal text.
    ("8 trusted hop, malformed header", PROXY, "not-an-address", "not-an-address"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("case", "peer", "forwarded_for", "expected"), CASES, ids=[c[0] for c in CASES])
async def test_audit_line_and_limiter_key_carry_the_address_uvicorn_resolved(
    behind_proxy: Callable[[str], Any],
    audit: _Recorded,
    case: str,
    peer: str,
    forwarded_for: str | None,
    expected: str,
) -> None:
    """The audit line, the limiter key and the log context of one sign-in carry the same address."""
    headers = {} if forwarded_for is None else {"X-Forwarded-For": forwarded_for}
    async with behind_proxy(peer) as connection:
        response = await connection.post(
            USER_LOGIN, headers=headers, json={"email": "nobody@example.org", "password": "Wrong-Password-1"}
        )

    assert response.status_code == 401, response.text
    failed = audit.of("user_login_failed")
    assert [entry["actor_ip"] for entry in failed] == [expected], case
    assert [entry["context_ip"] for entry in failed] == [expected], case
    assert _limiter_keys() == [expected], case


@pytest.mark.asyncio
async def test_six_sign_ins_with_six_forged_addresses_behind_an_appending_proxy_hit_the_limit_of_five(
    behind_proxy: Callable[[str], Any], audit: _Recorded, db_session: AsyncSession
) -> None:
    """The instance sign-in allows five a minute per client, whatever first entry the client forges."""
    await create_instance_admin(db_session, "operator@example.org", "Operator-Password-1")

    statuses = []
    async with behind_proxy(PROXY) as connection:
        for attempt in range(6):
            response = await connection.post(
                INSTANCE_LOGIN,
                headers={"X-Forwarded-For": f"203.0.113.{attempt + 1}, {CLIENT}"},
                json={"email": "operator@example.org", "password": "Wrong-Password-1"},
            )
            statuses.append(response.status_code)

    assert statuses == [401, 401, 401, 401, 401, 429]
    assert [entry["actor_ip"] for entry in audit.of("instance.login_failed")] == [CLIENT] * 5
    assert _limiter_keys() == [CLIENT]


@pytest.mark.asyncio
async def test_six_user_sign_ins_with_six_forged_addresses_hit_a_limit_of_five(
    behind_proxy: Callable[[str], Any], audit: _Recorded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user sign-in at a limit of five counts the client, not the forged first entry."""
    monkeypatch.setattr(get_settings().auth, "login_rate_limit", "5/minute")

    statuses = []
    async with behind_proxy(PROXY) as connection:
        for attempt in range(6):
            response = await connection.post(
                USER_LOGIN,
                headers={"X-Forwarded-For": f"203.0.113.{attempt + 1}, {CLIENT}"},
                json={"email": "nobody@example.org", "password": "Wrong-Password-1"},
            )
            statuses.append(response.status_code)

    assert statuses == [401, 401, 401, 401, 401, 429]
    assert [entry["actor_ip"] for entry in audit.of("user_login_failed")] == [CLIENT] * 5
    assert _limiter_keys() == [CLIENT]


def test_client_address_is_the_host_the_server_resolved_for_a_request() -> None:
    """The helper returns ``client.host`` and reads no header."""
    from starlette.requests import Request

    from app.client_address import client_address

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", FORGED.encode()), (b"x-real-ip", FORGED.encode())],
        "client": (CLIENT, PEER_PORT),
    }
    assert client_address(Request(scope)) == CLIENT


def test_client_address_of_a_websocket_is_the_host_the_server_resolved() -> None:
    """The same helper serves a WebSocket handshake."""
    from starlette.websockets import WebSocket

    from app.client_address import client_address

    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [(b"x-forwarded-for", FORGED.encode())],
        "client": ("2001:db8::7", PEER_PORT),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        return None

    assert client_address(WebSocket(scope, receive, send)) == "2001:db8::7"


def test_client_address_without_a_client_is_unknown() -> None:
    """A connection without a client gives ``"unknown"``."""
    from starlette.requests import Request

    from app.client_address import client_address

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", FORGED.encode())]}
    assert client_address(Request(scope)) == "unknown"


# Header names the App must never read itself: uvicorn's proxy handling owns them.
PROXY_HEADERS = {"x-forwarded-for", "x-real-ip", "forwarded", "http_x_forwarded_for", "http_x_real_ip"}


def _app_modules() -> list[Path]:
    return sorted(APP_DIR.rglob("*.py"))


def test_no_code_of_the_app_reads_a_proxy_header_or_the_client_host_itself() -> None:
    """One helper reads the client; a new copy of the old helper fails here.

    Flags a string constant that names a proxy header, ``.client.host`` outside
    ``app/client_address.py``, and any use of slowapi's ``get_remote_address``.
    """
    found: list[str] = []
    for path in _app_modules():
        where = path.relative_to(APP_DIR.parent)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str | bytes):
                text = node.value.decode("latin1") if isinstance(node.value, bytes) else node.value
                if text.strip().lower() in PROXY_HEADERS:
                    found.append(f"{where}:{node.lineno} names the header {text!r}")
            if (
                path != HELPER
                and isinstance(node, ast.Attribute)
                and node.attr == "host"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "client"
            ):
                found.append(f"{where}:{node.lineno} reads .client.host")
            if isinstance(node, ast.alias) and node.name == "get_remote_address":
                found.append(f"{where}:{node.lineno} imports get_remote_address")
            if isinstance(node, ast.Name | ast.Attribute) and "get_remote_address" in (
                node.id if isinstance(node, ast.Name) else node.attr
            ):
                found.append(f"{where}:{node.lineno} uses get_remote_address")

    assert found == [], "\n".join(found)


@pytest.mark.parametrize(
    ("value", "warned"),
    [("*", True), (" * ", True), (PROXY, False), ("172.20.0.0/16", False), (None, False)],
    ids=["star", "star-with-spaces", "named-address", "named-network", "unset"],
)
def test_start_warns_when_forwarded_allow_ips_trusts_every_peer(
    monkeypatch: pytest.MonkeyPatch, value: str | None, warned: bool
) -> None:
    """``FORWARDED_ALLOW_IPS=*`` gives one warning, a named proxy or no variable gives none."""
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    without = startup_warnings(get_settings())
    if value is not None:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", value)

    warnings = startup_warnings(get_settings())

    about = [w for w in warnings if "FORWARDED_ALLOW_IPS" in w]
    if warned:
        assert len(about) == 1, warnings
        assert warnings == [*without, about[0]]
        sentence = about[0]
        assert sentence.startswith("FORWARDED_ALLOW_IPS is *"), sentence
        assert sentence.count(". ") == 0 and ";" not in sentence and sentence.endswith("."), sentence
        assert "every" in sentence and "proxy" in sentence, sentence
    else:
        assert about == []
        assert warnings == without
