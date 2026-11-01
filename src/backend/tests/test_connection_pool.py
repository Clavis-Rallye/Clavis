"""
An open WebSocket or a running download holds no database connection.

A socket that kept its request session would pin one pooled connection
until the phone went away. About 30 open apps would take all of pool_size
plus max_overflow, and every HTTP request of everyone would wait 30 s.
The handlers therefore check in a short session that closes before
accept(). The file and media streams end their read transaction before
the first byte, because a request session lives until the last one. These
tests run on real sessions of the test engine, like production, and count
the checked-out connections while sockets are open or bytes flow.
"""

import contextlib
import random
from collections.abc import AsyncGenerator, Iterator
from io import BytesIO

import pytest
from httpx import AsyncClient
from httpx_ws import aconnect_ws
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.tokens import create_access_token
from app.config import settings
from app.database import get_db, get_session_opener
from app.main import app
from app.routers import files as files_module
from app.storage import ensure_bucket, get_minio_client
from tests.journey_helpers import activate_run, create_team_and_join, setup_journey_environment


def _use_real_sessions(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """From here on, every request and every socket gets its own real session."""
    maker = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def real_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = real_get_db
    app.dependency_overrides[get_session_opener] = lambda: maker
    return maker


@pytest.mark.asyncio
async def test_open_sockets_hold_no_database_connection(
    db_session: AsyncSession,
    client: AsyncClient,
    ws_client: AsyncClient,
    test_engine: AsyncEngine,
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-no-pinned-connection")
    coordinator = env.auth_headers["coordinator"]
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/", headers=coordinator, json={"rallye_id": str(env.rallye_id), "name": "Lauf"}
    )
    run_id = created.json()["id"]
    await activate_run(client, env.slug, run_id, coordinator)
    team = await create_team_and_join(
        client, env.slug, run_id, env.auth_headers["participant1"], "Team Pool", "#FF0000"
    )
    participant = env.users["participant1"]
    token = create_access_token(
        user_id=participant.id, role=participant.role.value, organization_id=participant.organization_id
    )

    _use_real_sessions(test_engine)
    await db_session.commit()
    baseline = test_engine.pool.checkedout()

    feed_url = f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}"
    chat_url = f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}/ws?token={token}"
    async with contextlib.AsyncExitStack() as stack:
        sockets = [await stack.enter_async_context(aconnect_ws(feed_url, ws_client)) for _ in range(4)]
        sockets.append(await stack.enter_async_context(aconnect_ws(chat_url, ws_client)))
        for ws in sockets:
            # A pong proves the handler passed its checks and waits in its loop.
            await ws.send_text("ping")
            assert await ws.receive_text(timeout=5.0) == "pong"

        assert test_engine.pool.checkedout() == baseline

        # A normal request still answers while the sockets stay open.
        me = await client.get("/api/auth/me", headers=env.auth_headers["participant1"])
        assert me.status_code == 200


@pytest.mark.asyncio
async def test_a_file_stream_holds_no_database_connection(
    db_session: AsyncSession,
    client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team member's photo streams after the membership check let go of its connection."""
    ensure_bucket()
    env = await setup_journey_environment(db_session, client, org_slug="stream-no-pinned-connection")
    coordinator = env.auth_headers["coordinator"]
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/", headers=coordinator, json={"rallye_id": str(env.rallye_id), "name": "Lauf"}
    )
    run_id = created.json()["id"]
    await activate_run(client, env.slug, run_id, coordinator)
    member = env.auth_headers["participant1"]
    await create_team_and_join(client, env.slug, run_id, member, "Team Stream", "#00FF00")
    # A real, noisy PNG: the upload route re-encodes images and refuses bytes
    # it cannot decode, and noise keeps the stored object well above
    # one 64 KiB stream chunk.
    noise = random.Random(203).randbytes(256 * 256 * 3)
    buffer = BytesIO()
    Image.frombytes("RGB", (256, 256), noise).save(buffer, format="PNG")
    content = buffer.getvalue()
    upload = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/upload/",
        headers=member,
        files={"file": ("stream.png", BytesIO(content), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    object_key = upload.json()["object_key"]
    stored_object = get_minio_client().get_object(settings.storage.bucket, object_key)
    try:
        stored = stored_object.read()
    finally:
        stored_object.close()
        stored_object.release_conn()

    # Count the checked-out connections at every chunk the stream sends.
    seen: list[int] = []
    real_stat_and_get = files_module._stat_and_get

    async def counting(object_name: str):  # type: ignore[no-untyped-def]
        fetched = await real_stat_and_get(object_name)
        if fetched is None:
            return None
        stat, obj = fetched
        original = obj.stream

        def stream(amt: int) -> Iterator[bytes]:
            for chunk in original(amt):
                seen.append(test_engine.pool.checkedout())
                yield chunk

        obj.stream = stream  # type: ignore[method-assign]
        return stat, obj

    monkeypatch.setattr(files_module, "_stat_and_get", counting)
    _use_real_sessions(test_engine)
    await db_session.commit()
    baseline = test_engine.pool.checkedout()

    served = await client.get(f"/api/files/{object_key}", headers=member)
    assert served.status_code == 200
    assert served.content == stored
    assert len(seen) > 1
    assert set(seen) == {baseline}
