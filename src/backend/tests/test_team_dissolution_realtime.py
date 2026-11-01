"""Open connections of a dissolved team close, and the run channel says so.

Every socket on ``chat:{team_id}`` is closed
with a code the app does not reconnect on and a reason of its own, and
``run_state:{run_id}`` carries one ``team_dissolved`` event with the run id and
the team id, and nothing else. The same notice leaves through the database
(``pg_notify``) with the commit of the dissolution, and the receiving side
(``dispatch_team_dissolved``) is safe to run more than once in one process.
"""

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.postgres import PostgresContainer

from app.auth.tokens import create_access_token
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.services.gdpr import anonymize_user
from app.services.notify import (
    TEAM_DISSOLVED_CLOSE_CODE,
    TEAM_DISSOLVED_CLOSE_REASON,
    TEAM_DISSOLVED_PG_CHANNEL,
    dispatch_team_dissolved,
)
from app.services.pg_notify import PgNotifyListener
from app.services.team_dissolution import dissolve_team
from app.services.ws_manager import manager as ws_manager
from tests.conftest import await_ws_subscribed
from tests.journey_helpers import JourneyEnv, create_team_and_join, setup_journey_environment

ACTS = ("leave", "remove_member", "move_member", "remove_team")


def _token(user: User) -> str:
    return create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        session_version=user.session_version,
    )


def _run_ws(env: JourneyEnv, run_id: UUID, user: User) -> str:
    return f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={_token(user)}"


def _chat_ws(env: JourneyEnv, run_id: UUID, team_id: str, user: User) -> str:
    return f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={_token(user)}"


async def _active_run(client: AsyncClient, env: JourneyEnv) -> UUID:
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={"name": "Dissolution realtime run", "rallye_id": str(env.rallye_id)},
        headers=env.auth_headers["coordinator"],
    )
    assert created.status_code == 201, created.text
    run_id = UUID(created.json()["id"])
    started = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/start", headers=env.auth_headers["coordinator"])
    assert started.status_code == 200, started.text
    return run_id


async def _two_teams(client: AsyncClient, env: JourneyEnv, run_id: UUID) -> tuple[str, str]:
    """Team One with participant1 alone, Team Two with participant2 alone."""
    one = await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant1"], "Team One", "#FF0000")
    two = await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant2"], "Team Two", "#00FF00")
    return one["id"], two["id"]


async def _member_id(db: AsyncSession, team_id: str, user: User) -> UUID:
    stmt = select(TeamMember.id).where(TeamMember.team_id == UUID(team_id), TeamMember.user_id == user.id)
    return (await db.execute(stmt)).scalar_one()


async def _act(
    act: str, client: AsyncClient, db: AsyncSession, env: JourneyEnv, run_id: UUID, team_id: str, target_team_id: str
) -> None:
    """Take the only member (participant1) out of ``team_id`` by one of the four acts that dissolve a team."""
    base = f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}"
    staff = env.auth_headers["coordinator"]
    if act == "leave":
        response = await client.post(
            f"{base}/leave", headers=env.auth_headers["participant1"], json={"confirm_dissolution": True}
        )
    elif act == "remove_member":
        member_id = await _member_id(db, team_id, env.users["participant1"])
        response = await client.delete(
            f"{base}/members/{member_id}", headers=staff, params={"confirm_dissolution": "true"}
        )
    elif act == "move_member":
        member_id = await _member_id(db, team_id, env.users["participant1"])
        response = await client.post(
            f"{base}/members/{member_id}/move",
            headers=staff,
            json={"target_team_id": target_team_id, "confirm_dissolution": True},
        )
    else:
        response = await client.delete(base, headers=staff, params={"confirm_dissolution": "true"})
    assert response.status_code == 204, response.text


async def _closed_with(ws: Any) -> tuple[int, str]:
    """The close of a socket: its code and its reason."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        while True:
            await ws.receive_text(timeout=5.0)
    return exc_info.value.code, exc_info.value.reason


async def _events_so_far(ws: Any) -> list[dict[str, Any]]:
    """Every event the socket got up to now. The pong of a ping sent here ends the reading, and proves it is open."""
    await ws.send_text("ping")
    events: list[dict[str, Any]] = []
    while True:
        text = await ws.receive_text(timeout=5.0)
        if text == "pong":
            return events
        events.append(json.loads(text))


# ---- closing and announcing through the four acts ----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("act", ACTS)
async def test_dissolution_closes_the_chat_and_tells_the_run_channel(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, act: str
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug=f"dis-rt-{act.replace('_', '-')}")
    run_id = await _active_run(client, env)
    team_id, other_team_id = await _two_teams(client, env, run_id)
    member, other, staff = env.users["participant1"], env.users["participant2"], env.users["coordinator"]

    # The team asks for help, so the coordinator may be on its chat too: a
    # connection on the chat channel that belongs to no member.
    asked = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=env.auth_headers["participant1"],
    )
    assert asked.status_code == 200, asked.text

    async with (
        aconnect_ws(_chat_ws(env, run_id, team_id, member), ws_client) as member_chat,
        aconnect_ws(_chat_ws(env, run_id, team_id, staff), ws_client) as staff_chat,
        aconnect_ws(_chat_ws(env, run_id, other_team_id, other), ws_client) as other_chat,
        aconnect_ws(_run_ws(env, run_id, other), ws_client) as other_run,
        aconnect_ws(_run_ws(env, run_id, staff), ws_client) as staff_run,
    ):
        for ws in (member_chat, staff_chat, other_chat, other_run, staff_run):
            await await_ws_subscribed(ws)

        await _act(act, client, db_session, env, run_id, team_id, other_team_id)

        # Every connection on the chat of the dissolved team is closed. The
        # member's own socket may have been closed a moment earlier by the act
        # itself (leaving, removal), with the same code.
        assert await _closed_with(staff_chat) == (TEAM_DISSOLVED_CLOSE_CODE, TEAM_DISSOLVED_CLOSE_REASON)
        member_code, _reason = await _closed_with(member_chat)
        assert member_code == TEAM_DISSOLVED_CLOSE_CODE
        assert ws_manager.get_subscriber_count(f"chat:{team_id}") == 0

        # One event on the run channel, for the other team and the staff
        # alike. It names the run and the team by id and carries nothing else.
        for ws in (other_run, staff_run):
            dissolved = [event for event in await _events_so_far(ws) if event["type"] == "team_dissolved"]
            assert len(dissolved) == 1
            assert dissolved[0]["data"] == {"run_id": str(run_id), "team_id": team_id}
            assert "timestamp" in dissolved[0]

        # The chat of the other team is untouched.
        assert [e for e in await _events_so_far(other_chat) if e["type"] == "team_dissolved"] == []


def test_the_close_code_is_one_the_app_does_not_reconnect_on() -> None:
    # useWebSocket.ts NON_RECOVERABLE_CODES: 4001 and 4003. Any other code makes
    # the client connect again to a team that no longer exists.
    assert TEAM_DISSOLVED_CLOSE_CODE == 4003
    assert TEAM_DISSOLVED_CLOSE_REASON == "Team dissolved"


@pytest.mark.asyncio
async def test_a_team_that_keeps_a_member_sends_no_dissolution(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="dis-rt-stays")
    run_id = await _active_run(client, env)
    team = await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant1"], "Stays", "#FF0000")
    joined = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}/join", headers=env.auth_headers["participant2"]
    )
    assert joined.status_code == 204, joined.text
    stayer = env.users["participant2"]

    async with (
        aconnect_ws(_chat_ws(env, run_id, team["id"], stayer), ws_client) as stayer_chat,
        aconnect_ws(_run_ws(env, run_id, stayer), ws_client) as stayer_run,
    ):
        await await_ws_subscribed(stayer_chat)
        await await_ws_subscribed(stayer_run)

        left = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}/leave", headers=env.auth_headers["participant1"]
        )
        assert left.status_code == 204, left.text

        assert [e["type"] for e in await _events_so_far(stayer_run)] == ["team_members_changed"]
        assert await _events_so_far(stayer_chat) == []


# ---- the way through the database ---------------------------------------------


class _Inbox:
    """A second connection that listens where another API process would."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self._arrived = asyncio.Event()

    def __call__(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        self.payloads.append(json.loads(payload))
        self._arrived.set()

    async def wait(self) -> None:
        await asyncio.wait_for(self._arrived.wait(), timeout=5.0)


@pytest.mark.asyncio
async def test_the_database_carries_the_notice_with_the_commit_only(
    db_session: AsyncSession, client: AsyncClient, postgres_container: PostgresContainer
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="dis-rt-notify")
    run_id = await _active_run(client, env)
    rolled_back_id, committed_id = await _two_teams(client, env, run_id)

    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    listener = await asyncpg.connect(dsn)
    inbox = _Inbox()
    try:
        await listener.add_listener(TEAM_DISSOLVED_PG_CHANNEL, inbox)

        # A dissolution that is rolled back, as a lost deadlock is, says nothing.
        team = (await db_session.execute(select(Team).where(Team.id == UUID(rolled_back_id)))).scalar_one()
        await dissolve_team(db_session, team)
        await db_session.rollback()

        removed = await client.delete(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{committed_id}",
            headers=env.auth_headers["coordinator"],
            params={"confirm_dissolution": "true"},
        )
        assert removed.status_code == 204, removed.text

        await inbox.wait()
        await asyncio.sleep(0.2)
        # The run and the team by id. No team name, no member, no organization.
        assert inbox.payloads == [{"run_id": str(run_id), "team_id": committed_id}]
    finally:
        await listener.close()


class _Socket:
    """A socket as the connection manager sees it."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed: list[tuple[int, str]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def close(self, code: int, reason: str) -> None:
        self.closed.append((code, reason))


@pytest.mark.asyncio
async def test_a_process_that_only_hears_the_notice_closes_its_connections_once() -> None:
    run_id, team_id = str(uuid4()), str(uuid4())
    chat, run, bystander = _Socket(), _Socket(), _Socket()
    await ws_manager.subscribe(chat, f"chat:{team_id}")  # type: ignore[arg-type]
    await ws_manager.subscribe(run, f"run_state:{run_id}")  # type: ignore[arg-type]
    await ws_manager.subscribe(bystander, f"chat:{uuid4()}")  # type: ignore[arg-type]
    try:
        payload = {"run_id": run_id, "team_id": team_id}
        await dispatch_team_dissolved(payload)
        # The same notice a second time, as it reaches the process that sent it.
        await dispatch_team_dissolved(payload)

        assert chat.closed == [(TEAM_DISSOLVED_CLOSE_CODE, TEAM_DISSOLVED_CLOSE_REASON)]
        assert ws_manager.get_subscriber_count(f"chat:{team_id}") == 0
        assert [message["type"] for message in run.sent] == ["team_dissolved"]
        assert run.sent[0]["data"] == payload
        assert run.closed == []
        assert bystander.closed == [] and bystander.sent == []
    finally:
        for socket in (chat, run, bystander):
            await ws_manager.disconnect(socket)  # type: ignore[arg-type]


# ---- the production listener: the receiver in a running API process ------------
#
# The ``ws_client`` fixture starts the real ``PgNotifyListener``, as the lifespan
# of the app does. These tests take the direct call of the router away, or use a
# path that never had one, so the listener alone must close and announce.


async def _next_dissolution(ws: Any) -> dict[str, Any]:
    """Wait for the ``team_dissolved`` event of a run socket."""
    while True:
        event = json.loads(await ws.receive_text(timeout=5.0))
        if event["type"] == "team_dissolved":
            return dict(event)


async def _ask_for_the_coordinator(client: AsyncClient, env: JourneyEnv, run_id: UUID, team_id: str) -> None:
    """After the request the coordinator may connect to the chat of the team."""
    asked = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=env.auth_headers["participant1"],
    )
    assert asked.status_code == 200, asked.text


@pytest.mark.asyncio
async def test_a_second_process_closes_and_announces_through_the_production_listener(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="dis-rt-second-process")
    run_id = await _active_run(client, env)
    team_id, _other_team_id = await _two_teams(client, env, run_id)
    member, other, staff = env.users["participant1"], env.users["participant2"], env.users["coordinator"]
    await _ask_for_the_coordinator(client, env, run_id, team_id)

    # This process stands for the second one: the request that dissolves the
    # team does not dispatch here. Only the database notification is left.
    async def _no_direct_call(_data: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr("app.routers.teams.dispatch_team_dissolved", _no_direct_call)

    async with (
        aconnect_ws(_chat_ws(env, run_id, team_id, member), ws_client) as member_chat,
        aconnect_ws(_chat_ws(env, run_id, team_id, staff), ws_client) as staff_chat,
        aconnect_ws(_run_ws(env, run_id, other), ws_client) as other_run,
    ):
        for ws in (member_chat, staff_chat, other_run):
            await await_ws_subscribed(ws)

        removed = await client.delete(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
            headers=env.auth_headers["coordinator"],
            params={"confirm_dissolution": "true"},
        )
        assert removed.status_code == 204, removed.text

        # The staff socket has no other reason to close: only the listener's
        # dispatch ends it. The member's own socket is closed a moment earlier
        # by the removal itself, with the same code.
        assert await _closed_with(staff_chat) == (TEAM_DISSOLVED_CLOSE_CODE, TEAM_DISSOLVED_CLOSE_REASON)
        member_code, _reason = await _closed_with(member_chat)
        assert member_code == TEAM_DISSOLVED_CLOSE_CODE
        assert ws_manager.get_subscriber_count(f"chat:{team_id}") == 0

        event = await _next_dissolution(other_run)
        assert event["data"] == {"run_id": str(run_id), "team_id": team_id}
        assert [e for e in await _events_so_far(other_run) if e["type"] == "team_dissolved"] == []


@pytest.mark.asyncio
async def test_the_erasure_of_the_last_member_closes_and_announces_through_the_listener(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="dis-rt-erasure")
    run_id = await _active_run(client, env)
    team_id, _other_team_id = await _two_teams(client, env, run_id)
    member, other, staff = env.users["participant1"], env.users["participant2"], env.users["coordinator"]
    await _ask_for_the_coordinator(client, env, run_id, team_id)

    async with (
        aconnect_ws(_chat_ws(env, run_id, team_id, staff), ws_client) as staff_chat,
        aconnect_ws(_run_ws(env, run_id, other), ws_client) as other_run,
    ):
        await await_ws_subscribed(staff_chat)
        await await_ws_subscribed(other_run)

        # The final erasure of the account: no router, no direct dispatch.
        assert await anonymize_user(db_session, member.id, {"system": "test"}) is True
        await db_session.commit()

        assert await _closed_with(staff_chat) == (TEAM_DISSOLVED_CLOSE_CODE, TEAM_DISSOLVED_CLOSE_REASON)
        assert ws_manager.get_subscriber_count(f"chat:{team_id}") == 0

        event = await _next_dissolution(other_run)
        assert event["data"] == {"run_id": str(run_id), "team_id": team_id}
        assert [e for e in await _events_so_far(other_run) if e["type"] == "team_dissolved"] == []


@pytest.mark.asyncio
async def test_the_direct_call_and_the_listener_together_announce_once(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="dis-rt-once")
    run_id = await _active_run(client, env)
    team_id, _other_team_id = await _two_teams(client, env, run_id)
    other = env.users["participant2"]

    # Watch the listener's side, and let the real dispatch run behind it.
    heard = asyncio.Event()

    async def _listener_side(data: dict[str, Any]) -> None:
        await dispatch_team_dissolved(data)
        if data["team_id"] == team_id:
            heard.set()

    monkeypatch.setattr("app.services.pg_notify.dispatch_team_dissolved", _listener_side)

    async with aconnect_ws(_run_ws(env, run_id, other), ws_client) as other_run:
        await await_ws_subscribed(other_run)

        # The router dispatches after its commit, and the listener hears the same notice.
        removed = await client.delete(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
            headers=env.auth_headers["coordinator"],
            params={"confirm_dissolution": "true"},
        )
        assert removed.status_code == 204, removed.text
        await asyncio.wait_for(heard.wait(), timeout=5.0)

        dissolved = [e for e in await _events_so_far(other_run) if e["type"] == "team_dissolved"]
        assert len(dissolved) == 1
        assert dissolved[0]["data"] == {"run_id": str(run_id), "team_id": team_id}


@pytest.mark.asyncio
async def test_the_listener_is_subscribed_to_the_dissolutions_and_hands_them_on(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    handed: list[dict[str, Any]] = []
    arrived = asyncio.Event()

    async def _record(data: dict[str, Any]) -> None:
        handed.append(data)
        arrived.set()

    monkeypatch.setattr("app.services.pg_notify.dispatch_team_dissolved", _record)

    url = postgres_container.get_connection_url(driver="asyncpg")
    listener = PgNotifyListener(url)
    sender = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        # Not subscribed before the start: the branch of _dispatch alone.
        direct = {"run_id": str(uuid4()), "team_id": str(uuid4())}
        await listener._dispatch(TEAM_DISSOLVED_PG_CHANNEL, direct)
        assert handed == [direct]

        # Subscribed after the start: a notice sent by another connection arrives.
        handed.clear()
        arrived.clear()
        await listener.start()
        sent = {"run_id": str(uuid4()), "team_id": str(uuid4())}
        await sender.execute("SELECT pg_notify($1, $2)", TEAM_DISSOLVED_PG_CHANNEL, json.dumps(sent))
        await asyncio.wait_for(arrived.wait(), timeout=5.0)
        assert handed == [sent]

        # And no longer after the stop.
        await listener.stop()
        handed.clear()
        await sender.execute("SELECT pg_notify($1, $2)", TEAM_DISSOLVED_PG_CHANNEL, json.dumps(sent))
        await asyncio.sleep(0.3)
        assert handed == []
    finally:
        await listener.stop()
        await sender.close()


@pytest.mark.asyncio
async def test_a_broken_dissolution_notice_does_not_stop_the_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = PgNotifyListener("postgresql+asyncpg://unused/unused")
    run_id, team_id = str(uuid4()), str(uuid4())
    run = _Socket()
    await ws_manager.subscribe(run, f"run_state:{run_id}")  # type: ignore[arg-type]
    try:
        # A payload without the team: logged, not raised.
        await listener._dispatch(TEAM_DISSOLVED_PG_CHANNEL, {"run_id": run_id})
        assert run.sent == []

        # A dispatch that fails: logged, not raised.
        async def _fails(_data: dict[str, Any]) -> None:
            raise RuntimeError("the close failed")

        with monkeypatch.context() as patched:
            patched.setattr("app.services.pg_notify.dispatch_team_dissolved", _fails)
            await listener._dispatch(TEAM_DISSOLVED_PG_CHANNEL, {"run_id": run_id, "team_id": team_id})

        # The other channels still go through, and so does the next dissolution.
        await listener._dispatch("run_state_changes", {"run_id": run_id, "changed_at": None})
        await listener._dispatch(TEAM_DISSOLVED_PG_CHANNEL, {"run_id": run_id, "team_id": team_id})
        assert [message["type"] for message in run.sent] == ["run_state_change", "team_dissolved"]
    finally:
        await ws_manager.disconnect(run)  # type: ignore[arg-type]
