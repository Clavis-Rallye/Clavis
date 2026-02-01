"""Shared fixtures: a real PostgreSQL from testcontainers, test clients on the app, and two organizations."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

# The environment is set before the app imports below, because app.config builds its settings on import.

# TESTING turns off PgNotifyListener and the other production services.
os.environ["TESTING"] = "true"

# Ryuk stays on by default. Export TESTCONTAINERS_RYUK_DISABLED=true to turn it off, for example in WSL.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "false")

# The suite reaches the object store through the Garage that `make test` starts on 127.0.0.1:59000.
# CI starts its own Garage and exports S3_ENDPOINT the same way. The default below
# applies only when pytest runs without that env block. The environment beats
# app.config's .env loading, and setdefault lets an exported endpoint win.
os.environ.setdefault("S3_ENDPOINT", "localhost:9000")

# The suite never reads the developer's App/.env. Every setting a test relies on comes
# from the code defaults or the explicit env block (ci.yml, run script). A local
# ENFORCE_ORG_ADMIN_TOTP=false would fail 2FA tests that pass in CI.
os.environ.setdefault("CLAVIS_ENV_FILE", "none")

# Many test files use POST /teams/{team_id}/join as a setup shortcut.
# The endpoint is closed by default and must stay off in production. This opens it for the suite only.
os.environ.setdefault("ALLOW_TEAM_JOIN_BY_ID", "true")

# One bucket per xdist worker. Several test modules wipe the WHOLE bucket as cleanup.
# Another worker can sit between upload and stat_object during that wipe, and its stat fails with NoSuchKey.
# Each worker process imports conftest on its own, so the buckets are fully apart.
_xdist_worker = os.getenv("PYTEST_XDIST_WORKER")
if _xdist_worker:
    os.environ["S3_BUCKET"] = f"{os.getenv('S3_BUCKET', 'clavis-media')}-{_xdist_worker}"

import contextlib  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from httpx_ws import AsyncWebSocketSession, aconnect_ws  # noqa: E402
from httpx_ws.transport import ASGIWebSocketTransport  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, get_db, get_session_opener  # noqa: E402
from app.main import app  # noqa: E402
from app.storage import ensure_bucket  # noqa: E402


@pytest.fixture(scope="session")
def event_loop_policy():
    """One event loop policy for all async fixtures and tests of the session."""
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(scope="session")
def postgres_container():
    """One PostgreSQL container for the whole session, shared by all tests."""
    container = PostgresContainer(
        image="postgres:16-alpine",
        username="test",
        password="test",
        dbname="test_clavis",
    )
    container.start()

    yield container

    container.stop()


@pytest_asyncio.fixture(scope="function")
async def test_engine(postgres_container: PostgresContainer) -> AsyncEngine:
    """An async engine on the test database. Its tables exist for one test and are dropped after it."""
    connection_url = postgres_container.get_connection_url(driver="asyncpg")

    engine = create_async_engine(
        connection_url,
        echo=False,  # Set to True for SQL debugging
        pool_pre_ping=True,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """A fresh session per test. Uncommitted changes are rolled back after the test."""
    test_session_maker = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async with test_session_maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """A test client whose endpoints use the test session, so they see isolated test data."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # The WebSocket handlers open short sessions of their own. In tests they
    # see the same test session, which stays open for the test.
    @contextlib.asynccontextmanager
    async def test_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session_opener] = lambda: test_session

    transport = ASGITransport(app=app)  # type: ignore
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def concurrent_client(test_engine: AsyncEngine) -> AsyncGenerator[AsyncClient, None]:
    """
    A test client for concurrency tests that gives each request its own session.

    asyncio.gather() can then run concurrent requests without SQLAlchemy's
    "concurrent operations not permitted" error. Data created through this client
    is committed, not rolled back. Set up with `db_session`, then switch to this client.
    """
    _concurrent_session_maker = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with _concurrent_session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)  # type: ignore
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def clean_db(db_session: AsyncSession) -> AsyncSession:
    """Alias of db_session for tests that work on the database without the HTTP client."""
    return db_session


# Two organizations, for tests of tenant isolation


@pytest_asyncio.fixture(scope="function")
async def org_a(db_session: AsyncSession):  # type: ignore[misc]
    """Organization A for cross-tenant tests."""
    from app.models.organization import Organization
    from tests.factories import create_test_organization

    org: Organization = await create_test_organization(
        db_session,
        name="Organization A",
        slug="org-a",
    )
    return org


@pytest_asyncio.fixture(scope="function")
async def org_b(db_session: AsyncSession):  # type: ignore[misc]
    """Organization B for cross-tenant tests."""
    from app.models.organization import Organization
    from tests.factories import create_test_organization

    org: Organization = await create_test_organization(
        db_session,
        name="Organization B",
        slug="org-b",
    )
    return org


@pytest_asyncio.fixture(scope="function")
async def user_in_org_a(db_session: AsyncSession, org_a):  # type: ignore[misc,no-untyped-def]
    """An org_admin user in organization A."""
    from app.models.user import User, UserRole
    from tests.factories import create_test_user

    user: User = await create_test_user(
        db_session,
        email="admin-a@example.com",
        display_name="Admin A",
        role=UserRole.ORG_ADMIN,
        organization=org_a,
    )
    return user


@pytest_asyncio.fixture(scope="function")
async def user_in_org_b(db_session: AsyncSession, org_b):  # type: ignore[misc,no-untyped-def]
    """An org_admin user in organization B."""
    from app.models.user import User, UserRole
    from tests.factories import create_test_user

    user: User = await create_test_user(
        db_session,
        email="admin-b@example.com",
        display_name="Admin B",
        role=UserRole.ORG_ADMIN,
        organization=org_b,
    )
    return user


@pytest_asyncio.fixture(scope="function")
async def client_org_a(client: AsyncClient, user_in_org_a) -> AsyncClient:  # type: ignore[no-untyped-def]
    """The `client` fixture, authenticated as the admin of organization A."""
    from tests.factories import get_auth_headers

    headers = get_auth_headers(user_in_org_a)
    client.headers.update(headers)
    return client


@pytest_asyncio.fixture(scope="function")
async def client_org_b(db_session: AsyncSession, user_in_org_b) -> AsyncGenerator[AsyncClient, None]:  # type: ignore[no-untyped-def]
    """A client authenticated as the admin of organization B, separate so its headers do not clash with client_org_a."""
    from tests.factories import get_auth_headers

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as test_client:
        headers = get_auth_headers(user_in_org_b)
        test_client.headers.update(headers)
        yield test_client

    app.dependency_overrides.clear()


# WebSocket test fixtures


@pytest_asyncio.fixture(scope="function")
async def ws_client(postgres_container: PostgresContainer) -> AsyncGenerator[AsyncClient, None]:
    """
    A client for WebSocket tests on ASGIWebSocketTransport, without a real server.

    It starts a PgNotifyListener, which delivers the broadcasts. It does NOT override
    get_db: use the `client` fixture beside it for the session override.
    """
    from app.services.pg_notify import PgNotifyListener

    database_url = postgres_container.get_connection_url(driver="asyncpg")
    listener = PgNotifyListener(database_url)
    await listener.start()

    # ASGIWebSocketTransport needs __aenter__ to set up its _task_group. The fixture runs that lifecycle
    # by hand and suppresses teardown errors: anyio raises cancel scope errors when
    # pytest-asyncio tears down in a different task.
    # initial_receive_timeout: the handlers in app.routers.ws run four to five DB queries before accept().
    # Under `make test` with two workers that exceeds the 1.0 s default, and the transport raises
    # "WebSocket didn't accept the connection in time". A rejected connection still fails at once,
    # because the server's close message arrives without waiting.
    transport = ASGIWebSocketTransport(app=app, initial_receive_timeout=10.0)  # type: ignore
    await transport.__aenter__()
    test_client = AsyncClient(transport=transport, base_url="http://testserver")
    try:
        yield test_client
    finally:
        # Let pending tasks finish before cleanup.
        await asyncio.sleep(0.2)
        with contextlib.suppress(Exception):
            await test_client.aclose()
        # The timeout bounds the transport teardown: a server task stuck in its task group
        # fails the test instead of hanging the pytest worker forever. The timeout is raised
        # only after the listener cleanup, so the listener always stops.
        stuck_transport: BaseException | None = None
        try:
            async with asyncio.timeout(10):
                await transport.__aexit__(None, None, None)
        except TimeoutError as exc:
            stuck_transport = exc
        except Exception:
            pass
        # A timeout keeps a stuck listener from hanging the test.
        try:
            await asyncio.wait_for(listener.stop(), timeout=2.0)
        except TimeoutError:
            logger.warning("PgNotifyListener.stop() timed out during test cleanup")
        except Exception as e:
            logger.warning(f"Error stopping PgNotifyListener during test cleanup: {e}")
        if stuck_transport is not None:
            raise stuck_transport


async def ws_connect(
    ws_client: AsyncClient,
    path: str,
    token: str | None = None,
):
    """Connect to a WebSocket endpoint with httpx_ws.aconnect_ws, the optional JWT in the query string as ?token=..."""
    if token:
        path = f"{path}?token={token}"
    return aconnect_ws(path, ws_client)


async def await_ws_subscribed(ws: AsyncWebSocketSession) -> None:
    """
    Block until the server has subscribed a freshly connected WebSocket.

    aconnect_ws returns once the handshake is accepted, but the handlers in
    app.routers.ws subscribe to their channels only after that. A test that
    posts its event right after connecting races the subscription and can
    silently miss the broadcast. The handler reaches the keepalive loop, and with
    it the pong, only after both subscribes. So a pong proves the socket is
    subscribed. Call this after every connect that a broadcast follows, or an
    assertion that no broadcast arrives.
    """
    await ws.send_text("ping")
    assert await ws.receive_text(timeout=5.0) == "pong"


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset the rate limiter before each test, because its in-memory state persists across tests."""
    from app.middleware.rate_limit import limiter

    with contextlib.suppress(Exception):
        limiter.reset()


@pytest.fixture(autouse=True)
def reset_ws_manager():
    """
    Give the WebSocket ConnectionManager a fresh lock and empty channel maps before each test.

    The manager is a module-level singleton, but pytest-asyncio gives every
    test a function-scoped event loop. The manager's asyncio.Lock binds to
    the first loop that contends it. Acquired from any later loop, it raises
    and wedges the handler task. Synchronous on purpose: lock creation is
    lazy and must not happen inside a fixture-scoped loop.
    """
    from app.services.ws_manager import manager

    manager._lock = asyncio.Lock()
    manager._channels.clear()
    manager._ws_channels.clear()

    yield

    # Drop the sockets of the loop that just ended.
    manager._channels.clear()
    manager._ws_channels.clear()


@pytest.fixture
def storage_bucket() -> str:
    """
    Make sure the object-store bucket of this worker exists and return its name.

    Every test that writes to the object store asks for this fixture, directly
    or with ``pytest.mark.usefixtures``, and counts on nothing else for its bucket.
    The test clients drive the app without its startup, where production calls
    ``ensure_bucket``. Under ``--dist loadfile`` any file can be the first a worker
    runs against a Garage without a bucket. A file that relies on another for it fails there with NoSuchBucket.

    Function-scoped on purpose: the check is one request, and it makes the bucket
    certain for each test, whatever ran before it.
    """
    ensure_bucket()
    return settings.storage.bucket


@pytest.fixture
def open_org_bootstrap():
    """
    Let the unauthenticated bootstrap registration run when an organisation already exists.

    Bootstrap is one-time by default. Once any organisation exists, `POST /api/auth/register`
    in bootstrap mode returns a coded 403. The path is unauthenticated and would otherwise
    let anyone mint an org_admin account on a live instance.

    Tests of *later* bootstrap-mode behaviour, such as duplicate slug, duplicate email or email
    normalisation, opt into open signup here. `get_settings()` is `lru_cache`d, so the flag
    flips on the cached instance and is restored afterwards.
    """
    from app.config import get_settings

    auth_settings = get_settings().auth
    previous = auth_settings.allow_unlimited_org_registrations
    auth_settings.allow_unlimited_org_registrations = True
    yield
    auth_settings.allow_unlimited_org_registrations = previous
