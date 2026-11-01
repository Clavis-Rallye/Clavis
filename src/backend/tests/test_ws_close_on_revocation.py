"""
Open WebSockets close when the account loses its rights.

The socket checks the token only when it connects. A password change closes the
account's sockets. The same close must follow the other ways an account loses
its rights:

- deactivation, removal from the organization (grace and immediate), the
  takeover that replaces an admin, and the archive of the organization
- leaving a team, or being removed from it, closes that member's socket
  to the team's chat, and only that one

A socket that passed its authentication before a revocation and subscribes after
it does not stay open. The revocation closed the account's channel when this
socket was not on it yet, so the socket reads its session once more after the
subscription, on the run feed and on the team chat.

An open socket ends when its right ends:

- The handlers that end a right close the socket at once: a move to
  another team, the end of the assist window, a lowered role, a sign-out, the
  deletion of an organization and the cleanup of a guest. Those tests run with
  the real keepalive interval, so only the explicit close can end the socket
  within the test.
- One rule, asked again at every keepalive, ends the socket without any
  explicit close. Those tests change the database directly, where no handler
  closes anything, and shorten the interval (``fast_keepalive``).
- A socket does not outlive its token.
- A guest without a team in the run loses the run feed.
"""

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer

from app.auth.instance import create_instance_admin
from app.auth.tokens import create_access_token, create_instance_admin_token, decode_token, sign_out_session
from app.auth.ws_dependencies import WsGrant, ws_refusal
from app.database import get_session_opener
from app.main import app
from app.models.organization import Organization
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.models.user_invitation import UserInvitation
from app.repositories.invitation import InvitationRepository
from app.routers import ws as ws_router
from app.services.guest_cleanup import delete_run_guests
from app.services.org_deletion import DELETION_GRACE_DAYS, sweep_due_organizations
from app.services.ws_manager import manager
from tests.conftest import await_ws_subscribed
from tests.factories import create_test_user, get_auth_headers
from tests.journey_helpers import JourneyEnv, create_team_and_join, leave_as_last_member, setup_journey_environment


def _token(user: User) -> str:
    return create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        session_version=user.session_version,
    )


async def _active_run(client: AsyncClient, env: JourneyEnv) -> UUID:
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        json={
            "name": "Revocation Run",
            "rallye_id": str(env.rallye_id),
            "start_time": "2026-07-15T10:00:00Z",
            "end_time": "2026-07-15T16:00:00Z",
        },
        headers=env.auth_headers["coordinator"],
    )
    assert created.status_code == 201, created.text
    run_id = UUID(created.json()["id"])
    started = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/start", headers=env.auth_headers["coordinator"])
    assert started.status_code == 200, started.text
    return run_id


def _feed_url(env: JourneyEnv, run_id: UUID, user: User) -> str:
    return f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={_token(user)}"


async def _assert_alive(ws) -> None:  # noqa: ANN001
    """The socket still answers; events that arrive first (member changes) are skipped."""
    await ws.send_text("ping")
    for _ in range(5):
        if await ws.receive_text(timeout=5.0) == "pong":
            return
    raise AssertionError("no pong")


async def _assert_closed(ws, *, code: int = 4000) -> None:  # noqa: ANN001
    """The server closes the socket; events that arrive first (member changes) are skipped."""
    with pytest.raises(WebSocketDisconnect) as exc_info:
        for _ in range(10):
            await ws.receive_text(timeout=5.0)
    assert exc_info.value.code == code


# ============================================================================
# Deactivation, removal, takeover, archive
# ============================================================================


@pytest.mark.asyncio
async def test_deactivation_closes_the_open_socket(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-deactivate")
    run_id = await _active_run(client, env)
    target, bystander = env.users["participant1"], env.users["participant2"]

    async with (
        aconnect_ws(_feed_url(env, run_id, target), ws_client) as ws_target,
        aconnect_ws(_feed_url(env, run_id, bystander), ws_client) as ws_bystander,
    ):
        await await_ws_subscribed(ws_target)
        await await_ws_subscribed(ws_bystander)

        response = await client.put(
            f"/api/orgs/{env.slug}/users/{target.id}",
            json={"is_active": False},
            headers=env.auth_headers["org_admin"],
        )
        assert response.status_code == 200, response.text

        await _assert_closed(ws_target)
        await _assert_alive(ws_bystander)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["grace", "immediate"])
async def test_removal_from_the_organization_closes_the_open_socket(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, mode: str
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug=f"ws-rev-delete-{mode}")
    run_id = await _active_run(client, env)
    target = env.users["participant1"]

    async with aconnect_ws(_feed_url(env, run_id, target), ws_client) as ws_target:
        await await_ws_subscribed(ws_target)

        response = await client.delete(
            f"/api/orgs/{env.slug}/users/{target.id}?mode={mode}",
            headers=env.auth_headers["org_admin"],
        )
        assert response.status_code == 204, response.text

        await _assert_closed(ws_target)


@pytest.mark.asyncio
async def test_takeover_closes_the_socket_of_the_replaced_admin(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-takeover")
    run_id = await _active_run(client, env)
    old_admin = await create_test_user(
        db_session,
        email="old-admin@ws-rev-takeover.test",
        role=UserRole.ORG_ADMIN,
        organization=env.org,
    )
    bystander = env.users["participant1"]

    invitation, _ = await InvitationRepository(db_session).create(
        organization_id=env.org.id,
        email="successor@ws-rev-takeover.test",
        role=UserRole.ORG_ADMIN,
        invited_by_user_id=None,
        invited_by_name="Clavis",
        expiry_days=7,
        language="de",
    )
    invitation.replaces_user_ids = [str(old_admin.id)]
    await db_session.commit()
    stored = (
        (await db_session.execute(select(UserInvitation).where(UserInvitation.email == invitation.email)))
        .scalars()
        .one()
    )
    token = await InvitationRepository(db_session).rotate_token(stored, expiry_days=7)
    await db_session.commit()

    async with (
        aconnect_ws(_feed_url(env, run_id, old_admin), ws_client) as ws_old,
        aconnect_ws(_feed_url(env, run_id, bystander), ws_client) as ws_bystander,
    ):
        await await_ws_subscribed(ws_old)
        await await_ws_subscribed(ws_bystander)

        accepted = await client.post(
            f"/api/invitations/{token}/accept",
            json={"display_name": "Nachfolge", "password": "SicheresPasswort123!"},
        )
        assert accepted.status_code == 200, accepted.text

        await _assert_closed(ws_old)
        await _assert_alive(ws_bystander)


@pytest.mark.asyncio
async def test_archiving_the_organization_closes_every_member_socket(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-archive")
    run_id = await _active_run(client, env)
    p1, p2 = env.users["participant1"], env.users["participant2"]
    operator = await create_instance_admin(db_session, f"op-{uuid4().hex[:6]}@example.com", "OperatorPass123!")
    operator_headers = {"Authorization": f"Bearer {create_instance_admin_token(operator.id)}"}

    async with (
        aconnect_ws(_feed_url(env, run_id, p1), ws_client) as ws_1,
        aconnect_ws(_feed_url(env, run_id, p2), ws_client) as ws_2,
    ):
        await await_ws_subscribed(ws_1)
        await await_ws_subscribed(ws_2)

        archived = await client.post(f"/api/instance/organizations/{env.slug}/archive", headers=operator_headers)
        assert archived.status_code == 200, archived.text

        await _assert_closed(ws_1)
        await _assert_closed(ws_2)


# ============================================================================
# Leaving or being removed from a team closes the team chat socket
# ============================================================================


async def _two_member_team(client: AsyncClient, env: JourneyEnv, run_id: UUID) -> str:
    team = await create_team_and_join(
        client, env.slug, run_id, env.auth_headers["participant1"], "Revocation Team", "#FF0000"
    )
    joined = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team['id']}/join", headers=env.auth_headers["participant2"]
    )
    assert joined.status_code == 204, joined.text
    return str(team["id"])


def _chat_url(env: JourneyEnv, run_id: UUID, team_id: str, user: User) -> str:
    return f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={_token(user)}"


@pytest.mark.asyncio
async def test_leaving_a_team_closes_that_members_chat_socket(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-leave")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    leaver, stayer = env.users["participant2"], env.users["participant1"]

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, leaver), ws_client) as ws_leaver,
        aconnect_ws(_chat_url(env, run_id, team_id, stayer), ws_client) as ws_stayer,
        aconnect_ws(_feed_url(env, run_id, leaver), ws_client) as ws_leaver_feed,
    ):
        await await_ws_subscribed(ws_leaver)
        await await_ws_subscribed(ws_stayer)
        await await_ws_subscribed(ws_leaver_feed)

        left = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/leave", headers=env.auth_headers["participant2"]
        )
        assert left.status_code == 204, left.text

        await _assert_closed(ws_leaver, code=4003)
        await _assert_alive(ws_stayer)
        await _assert_alive(ws_leaver_feed)


@pytest.mark.asyncio
async def test_removing_a_team_member_closes_that_members_chat_socket(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-remove")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    removed, stayer = env.users["participant2"], env.users["participant1"]
    member_id = (
        await db_session.execute(
            select(TeamMember.id).where(TeamMember.team_id == UUID(team_id), TeamMember.user_id == removed.id)
        )
    ).scalar_one()

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, removed), ws_client) as ws_removed,
        aconnect_ws(_chat_url(env, run_id, team_id, stayer), ws_client) as ws_stayer,
    ):
        await await_ws_subscribed(ws_removed)
        await await_ws_subscribed(ws_stayer)

        response = await client.delete(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/members/{member_id}",
            headers=get_auth_headers(env.users["coordinator"]),
        )
        assert response.status_code == 204, response.text

        await _assert_closed(ws_removed, code=4003)
        await _assert_alive(ws_stayer)


# ============================================================================
# Authenticated before the revocation, subscribed after it
# ============================================================================

PARTICIPANT_PASSWORD = "ParticipantPass123!"


def _revoke_between_authentication_and_subscription(monkeypatch, client: AsyncClient, headers: dict[str, str]) -> None:  # noqa: ANN001
    """Change the account's password at the moment the socket is about to subscribe.

    The handler has authenticated and accepted by then. The change commits the
    new session version and closes the account's channel, which this socket is
    not on yet. The order comes from the handler's own first subscription, not
    from a sleep.
    """
    original = manager.subscribe
    fired = False

    async def subscribe(ws, channel: str) -> None:  # noqa: ANN001
        nonlocal fired
        if not fired:
            fired = True
            change = await client.post(
                "/api/auth/password-change",
                json={"old_password": PARTICIPANT_PASSWORD, "new_password": "NewPassword456!"},
                headers=headers,
            )
            assert change.status_code == 200, change.text
        await original(ws, channel)

    monkeypatch.setattr(manager, "subscribe", subscribe)


@pytest.mark.asyncio
async def test_run_feed_socket_subscribing_after_a_revocation_is_closed(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, monkeypatch
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-late-feed")
    run_id = await _active_run(client, env)
    target = env.users["participant1"]
    url = _feed_url(env, run_id, target)

    _revoke_between_authentication_and_subscription(monkeypatch, client, env.auth_headers["participant1"])
    async with aconnect_ws(url, ws_client) as ws:
        await _assert_closed(ws)

    assert manager.get_subscriber_count(f"user:{target.id}") == 0
    assert manager.get_subscriber_count(f"feed:{run_id}") == 0


@pytest.mark.asyncio
async def test_team_chat_socket_subscribing_after_a_revocation_is_closed(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, monkeypatch
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-late-chat")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    target = env.users["participant1"]
    url = _chat_url(env, run_id, team_id, target)

    _revoke_between_authentication_and_subscription(monkeypatch, client, env.auth_headers["participant1"])
    async with aconnect_ws(url, ws_client) as ws:
        await _assert_closed(ws)

    assert manager.get_subscriber_count(f"user:{target.id}") == 0
    assert manager.get_subscriber_count(f"chat:{team_id}") == 0


# ============================================================================
# An open socket ends when its right ends
# ============================================================================


@pytest.fixture
def fast_keepalive(monkeypatch, client: AsyncClient, test_engine: AsyncEngine) -> None:  # noqa: ANN001
    """Check open sockets every 0.1 s, each check in a session of its own.

    The ``client`` fixture hands the WebSocket handlers the test's one session.
    A check that runs on a timer would use it while the test does, so here the
    handlers open their own, as they do in production.
    """
    app.dependency_overrides[get_session_opener] = lambda: async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(ws_router, "KEEPALIVE_SECONDS", 0.1)


async def _participant(db_session: AsyncSession, env: JourneyEnv, name: str) -> tuple[User, dict[str, str]]:
    user = await create_test_user(
        db_session, email=f"{name}@{env.slug}.test", role=UserRole.PARTICIPANT, organization=env.org
    )
    return user, get_auth_headers(user)


async def _member_id(db_session: AsyncSession, team_id: str, user: User) -> UUID:
    return (
        await db_session.execute(
            select(TeamMember.id).where(TeamMember.team_id == UUID(team_id), TeamMember.user_id == user.id)
        )
    ).scalar_one()


async def _open_assist_window(client: AsyncClient, env: JourneyEnv, run_id: UUID, team_id: str) -> None:
    requested = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/request-coordinator",
        headers=env.auth_headers["participant1"],
    )
    assert requested.status_code == 200, requested.text


async def _guest_in_new_run(client: AsyncClient, env: JourneyEnv) -> tuple[UUID, str, UUID, str]:
    """An active run and a guest who joined it with a team: run, team, guest id, guest token."""
    run_id = await _active_run(client, env)
    run = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}", headers=env.auth_headers["coordinator"])
    joined = await client.post(
        f"/api/join/{run.json()['join_code']}",
        json={"mode": "create", "display_name": "Gast", "team_name": "Gaeste"},
    )
    assert joined.status_code == 201, joined.text
    # The join answers with the session cookie; the tests authenticate by header
    client.cookies.clear()
    data = joined.json()
    return run_id, data["team_id"], UUID(data["user_id"]), data["token"]


# --- A move to another team -------------------------------------------------


@pytest.mark.asyncio
async def test_moving_a_member_closes_that_members_socket_to_the_old_teams_chat(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-move")
    run_id = await _active_run(client, env)
    old_team = await _two_member_team(client, env, run_id)
    _, third_headers = await _participant(db_session, env, "third")
    new_team = await create_team_and_join(client, env.slug, run_id, third_headers, "Target Team", "#00FF00")
    moved, stayer = env.users["participant2"], env.users["participant1"]
    member_id = await _member_id(db_session, old_team, moved)

    async with (
        aconnect_ws(_chat_url(env, run_id, old_team, moved), ws_client) as ws_moved,
        aconnect_ws(_chat_url(env, run_id, old_team, stayer), ws_client) as ws_stayer,
        aconnect_ws(_feed_url(env, run_id, moved), ws_client) as ws_moved_feed,
    ):
        await await_ws_subscribed(ws_moved)
        await await_ws_subscribed(ws_stayer)
        await await_ws_subscribed(ws_moved_feed)

        response = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{old_team}/members/{member_id}/move",
            json={"target_team_id": new_team["id"]},
            headers=env.auth_headers["coordinator"],
        )
        assert response.status_code == 204, response.text

        await _assert_closed(ws_moved, code=4003)
        await _assert_alive(ws_stayer)
        await _assert_alive(ws_moved_feed)

    async with aconnect_ws(_chat_url(env, run_id, new_team["id"], moved), ws_client) as ws_new:
        await await_ws_subscribed(ws_new)


# --- The end of the assist window -------------------------------------------


@pytest.mark.asyncio
async def test_closing_the_assist_window_closes_the_chat_socket_of_a_coordinator_who_is_no_member(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-assist")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)
    coordinator, member = env.users["coordinator"], env.users["participant1"]

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, coordinator), ws_client) as ws_coordinator,
        aconnect_ws(_chat_url(env, run_id, team_id, member), ws_client) as ws_member,
    ):
        await await_ws_subscribed(ws_coordinator)
        await await_ws_subscribed(ws_member)

        left = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/leave-coordinator",
            headers=env.auth_headers["coordinator"],
        )
        assert left.status_code == 200, left.text

        await _assert_closed(ws_coordinator, code=4003)
        # A member's right to the chat does not depend on the window
        await _assert_alive(ws_member)


async def _next_chat_message(ws) -> dict:  # noqa: ANN001
    """The next ``chat_message`` envelope on a chat socket; pongs and other events are skipped."""
    for _ in range(10):
        received = await ws.receive_text(timeout=5.0)
        if received == "pong":
            continue
        envelope = json.loads(received)
        if envelope.get("type") == "chat_message":
            return envelope
    raise AssertionError("no chat_message")


@pytest.mark.asyncio
async def test_ending_the_help_from_another_staff_account_tells_the_coordinator_before_its_socket_closes(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    """The coordinator's chat page learns that the help has ended.

    The coordinator is no member of the team and has the chat open. A second
    staff account ends the help. The coordinator's socket receives the
    ``[coordinator_left]`` message first and is closed after it with the code
    of the recheck. A member's socket receives the message once and
    stays open; the team's next message reaches the member and not the
    coordinator.
    """
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-assist-left")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)
    coordinator, member = env.users["coordinator"], env.users["participant1"]

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, coordinator), ws_client) as ws_coordinator,
        aconnect_ws(_chat_url(env, run_id, team_id, member), ws_client) as ws_member,
    ):
        await await_ws_subscribed(ws_coordinator)
        await await_ws_subscribed(ws_member)

        left = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/leave-coordinator",
            headers=env.auth_headers["org_admin"],
        )
        assert left.status_code == 200, left.text

        # The message arrives on the coordinator's socket, then the close
        to_coordinator = await _next_chat_message(ws_coordinator)
        assert to_coordinator["data"]["content"].startswith("[coordinator_left]")
        assert to_coordinator["data"]["is_system_message"] is True
        await _assert_closed(ws_coordinator, code=4003)

        # The member receives it once, in the envelope of every chat message
        to_member = await _next_chat_message(ws_member)
        assert to_member == to_coordinator
        sent = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
            json={"content": "after the help"},
            headers=env.auth_headers["participant1"],
        )
        assert sent.status_code == 201, sent.text
        after = await _next_chat_message(ws_member)
        # A second copy of the left message would arrive before this one
        assert after["data"]["content"] == "after the help"
        assert set(after["data"]) == set(to_member["data"])
        await _assert_alive(ws_member)


class _ChatInbox:
    """A second database connection on ``chat_messages``, where another API process listens."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self._arrived = asyncio.Event()

    def __call__(self, _conn, _pid: int, _channel: str, payload: str) -> None:  # noqa: ANN001
        self.payloads.append(json.loads(payload))
        self._arrived.set()

    async def wait_for(self, prefix: str) -> dict:
        """The first notice whose content starts with ``prefix``."""
        for _ in range(10):
            for payload in self.payloads:
                if str(payload.get("content", "")).startswith(prefix):
                    return payload
            self._arrived.clear()
            await asyncio.wait_for(self._arrived.wait(), timeout=5.0)
        raise AssertionError(f"no notice starting with {prefix}")


@pytest.mark.asyncio
async def test_ending_the_help_sends_the_notice_and_the_serving_process_delivers_it_once(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, postgres_container: PostgresContainer
) -> None:
    """The notice leaves through the database again, and the serving process,
    whose real listener hears it too, delivers the message once.

    ``ws_client`` runs the production ``PgNotifyListener`` of this process. The
    request sends the message directly; the notice comes back to the same
    process and is skipped there by the message id.
    """
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-left-notice")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)
    coordinator, member = env.users["coordinator"], env.users["participant1"]

    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    other_process = await asyncpg.connect(dsn)
    inbox = _ChatInbox()
    try:
        await other_process.add_listener("chat_messages", inbox)
        async with (
            aconnect_ws(_chat_url(env, run_id, team_id, coordinator), ws_client) as ws_coordinator,
            aconnect_ws(_chat_url(env, run_id, team_id, member), ws_client) as ws_member,
        ):
            await await_ws_subscribed(ws_coordinator)
            await await_ws_subscribed(ws_member)

            left = await client.post(
                f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/leave-coordinator",
                headers=env.auth_headers["org_admin"],
            )
            assert left.status_code == 200, left.text

            # The notice carries the message, by the id the sockets see
            notice = await inbox.wait_for("[coordinator_left]")
            to_coordinator = await _next_chat_message(ws_coordinator)
            assert to_coordinator["data"]["id"] == notice["id"]
            assert to_coordinator["data"]["content"] == notice["content"]
            await _assert_closed(ws_coordinator, code=4003)

            # The member receives it once. The team's next message travels
            # behind the notice of the left message, so a second copy would
            # arrive before it.
            to_member = await _next_chat_message(ws_member)
            assert to_member["data"]["id"] == notice["id"]
            sent = await client.post(
                f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
                json={"content": "after the notice"},
                headers=env.auth_headers["participant1"],
            )
            assert sent.status_code == 201, sent.text
            after = await _next_chat_message(ws_member)
            assert after["data"]["content"] == "after the notice"
            await _assert_alive(ws_member)
    finally:
        await other_process.close()


class _NoDirectSend:
    """The connection manager of the serving process, seen from a second one: its sends reach no socket here."""

    async def broadcast(self, _channel: str, _message: dict) -> None:
        return None

    async def recheck_channel(self, _channel: str) -> None:
        return None

    def delivery(self, _channel: str) -> contextlib.nullcontext[None]:
        # The serving process orders its own deliveries; that touches no socket here
        return contextlib.nullcontext()


@pytest.mark.asyncio
async def test_a_second_process_delivers_the_end_of_the_help_once_and_closes_the_coordinator_after_it(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process that only hears the notice delivers the message to its sockets
    once, and only then closes the socket of a coordinator who is no member.

    The request's direct send and recheck reach no socket of this process, and
    the request leaves no memory of the message here: what the sockets see
    comes from the production listener alone.
    """
    import app.routers.chat as chat_router

    monkeypatch.setattr(chat_router, "ws_manager", _NoDirectSend())
    monkeypatch.setattr(chat_router, "remember_chat_message_sent_here", lambda _message_id: None)

    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-left-second")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)
    coordinator, member = env.users["coordinator"], env.users["participant1"]

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, coordinator), ws_client) as ws_coordinator,
        aconnect_ws(_chat_url(env, run_id, team_id, member), ws_client) as ws_member,
    ):
        await await_ws_subscribed(ws_coordinator)
        await await_ws_subscribed(ws_member)

        left = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/leave-coordinator",
            headers=env.auth_headers["org_admin"],
        )
        assert left.status_code == 200, left.text

        # The real keepalive interval is 25 s: within the 5 s of the test only
        # the listener's recheck can close the socket, and only after the message.
        to_coordinator = await _next_chat_message(ws_coordinator)
        assert to_coordinator["data"]["content"].startswith("[coordinator_left]")
        await _assert_closed(ws_coordinator, code=4003)

        to_member = await _next_chat_message(ws_member)
        assert to_member == to_coordinator
        sent = await client.post(
            f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/",
            json={"content": "after the help"},
            headers=env.auth_headers["participant1"],
        )
        assert sent.status_code == 201, sent.text
        after = await _next_chat_message(ws_member)
        assert after["data"]["content"] == "after the help"
        await _assert_alive(ws_member)


class _FakeSocket:
    """A socket as the connection manager sees it; it records what happens to it, in order."""

    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    async def send_json(self, message: dict) -> None:
        self.log.append(f"{self.name} got {message['data']['content']}")


@pytest.mark.asyncio
async def test_the_listener_skips_a_message_sent_here_and_rechecks_only_after_the_end_of_the_help() -> None:
    """The listener does not deliver again a message id this process sent directly.

    The end of the help is delivered first and rechecked after. Any other chat
    message is delivered once and asks no admission rule.
    """
    from app.services.notify import remember_chat_message_sent_here
    from app.services.pg_notify import PgNotifyListener

    team_id = str(uuid4())
    log: list[str] = []
    socket = _FakeSocket(log, "socket")
    await manager.subscribe(socket, f"chat:{team_id}")  # type: ignore[arg-type]

    async def check() -> None:
        log.append("recheck")

    await manager.register_check(socket, check)  # type: ignore[arg-type]
    listener = PgNotifyListener("postgresql://unused")

    def payload(content: str, *, system: bool = True) -> dict:
        return {
            "id": str(uuid4()),
            "team_id": team_id,
            "organization_id": str(uuid4()),
            "author_id": None,
            "content": content,
            "is_system_message": system,
            "author_role": None,
            "author_display_name": None,
            "created_at": datetime.now(UTC).isoformat(),
        }

    try:
        sent_here = payload("[coordinator_left] The coordinator has left the team chat.")
        remember_chat_message_sent_here(sent_here["id"])
        await listener._dispatch("chat_messages", sent_here)
        assert log == []

        heard = payload("[coordinator_left] The coordinator has left the team chat.")
        await listener._dispatch("chat_messages", heard)
        assert log == [f"socket got {heard['content']}", "recheck"]

        log.clear()
        await listener._dispatch("chat_messages", payload("[coordinator_joined] A coordinator has joined."))
        await listener._dispatch("chat_messages", payload("[coordinator_left] typed by a member", system=False))
        assert log == [
            "socket got [coordinator_joined] A coordinator has joined.",
            "socket got [coordinator_left] typed by a member",
        ]
    finally:
        await manager.disconnect(socket)  # type: ignore[arg-type]


def _chat_payload(team_id: str, content: str, *, system: bool = True) -> dict:
    """A ``chat_messages`` notice as notify_chat_message writes it."""
    return {
        "id": str(uuid4()),
        "team_id": team_id,
        "organization_id": str(uuid4()),
        "author_id": None,
        "content": content,
        "is_system_message": system,
        "author_role": None,
        "author_display_name": None,
        "created_at": datetime.now(UTC).isoformat(),
    }


class _HeldRecheck:
    """The admission check of a coordinator's socket that holds until released, then closes it."""

    def __init__(self, socket: _FakeSocket) -> None:
        self.socket = socket
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self) -> bool:
        self.entered.set()
        await self.release.wait()
        self.socket.log.append(f"{self.socket.name} closed")
        await manager.disconnect(self.socket)  # type: ignore[arg-type]
        return True


async def _let_tasks_run() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_no_later_message_of_the_chat_reaches_the_coordinator_while_the_listener_rechecks() -> None:
    """After the end of the help, the listener delivers no later message of the
    same chat before its recheck has closed the coordinator's socket.

    A message of another chat does not wait.
    """
    from app.services.pg_notify import PgNotifyListener

    team_id, other_team_id = str(uuid4()), str(uuid4())
    log: list[str] = []
    coordinator, member, elsewhere = (
        _FakeSocket(log, "coordinator"),
        _FakeSocket(log, "member"),
        _FakeSocket(log, "elsewhere"),
    )
    await manager.subscribe(coordinator, f"chat:{team_id}")  # type: ignore[arg-type]
    await manager.subscribe(member, f"chat:{team_id}")  # type: ignore[arg-type]
    await manager.subscribe(elsewhere, f"chat:{other_team_id}")  # type: ignore[arg-type]
    held = _HeldRecheck(coordinator)
    await manager.register_check(coordinator, held)  # type: ignore[arg-type]

    async def member_stays() -> bool:
        return False

    await manager.register_check(member, member_stays)  # type: ignore[arg-type]
    listener = PgNotifyListener("postgresql://unused")

    try:
        left = _chat_payload(team_id, "[coordinator_left] The coordinator has left the team chat.")
        left_task = asyncio.create_task(listener._dispatch("chat_messages", left))
        await asyncio.wait_for(held.entered.wait(), timeout=5.0)

        after_task = asyncio.create_task(
            listener._dispatch("chat_messages", _chat_payload(team_id, "after the help", system=False))
        )
        await asyncio.wait_for(
            listener._dispatch("chat_messages", _chat_payload(other_team_id, "another chat", system=False)),
            timeout=5.0,
        )
        await _let_tasks_run()
        assert "coordinator got after the help" not in log
        assert "elsewhere got another chat" in log

        held.release.set()
        await asyncio.wait_for(asyncio.gather(left_task, after_task), timeout=5.0)
        assert "coordinator got after the help" not in log
        assert log.index("coordinator closed") < log.index("member got after the help")
    finally:
        held.release.set()
        for socket in (coordinator, member, elsewhere):
            await manager.disconnect(socket)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_later_message_of_the_chat_reaches_the_coordinator_while_the_serving_process_rechecks(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """In the process that served the leave, its listener delivers no later
    message of the chat between the direct send of the end of the help and the
    end of the recheck that follows it."""
    from app.services.pg_notify import PgNotifyListener

    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-left-serving-lock")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)

    log: list[str] = []
    coordinator, member = _FakeSocket(log, "coordinator"), _FakeSocket(log, "member")
    await manager.subscribe(coordinator, f"chat:{team_id}")  # type: ignore[arg-type]
    await manager.subscribe(member, f"chat:{team_id}")  # type: ignore[arg-type]
    held = _HeldRecheck(coordinator)
    await manager.register_check(coordinator, held)  # type: ignore[arg-type]

    async def member_stays() -> bool:
        return False

    await manager.register_check(member, member_stays)  # type: ignore[arg-type]
    listener = PgNotifyListener("postgresql://unused")

    try:
        leave_task = asyncio.create_task(
            client.post(
                f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/leave-coordinator",
                headers=env.auth_headers["org_admin"],
            )
        )
        await asyncio.wait_for(held.entered.wait(), timeout=10.0)
        assert any(entry.startswith("coordinator got [coordinator_left]") for entry in log)

        after_task = asyncio.create_task(
            listener._dispatch("chat_messages", _chat_payload(team_id, "after the help", system=False))
        )
        await _let_tasks_run()
        assert "coordinator got after the help" not in log

        held.release.set()
        left = await asyncio.wait_for(leave_task, timeout=10.0)
        assert left.status_code == 200, left.text
        await asyncio.wait_for(after_task, timeout=5.0)
        assert "coordinator got after the help" not in log
        assert log.index("coordinator closed") < log.index("member got after the help")
    finally:
        held.release.set()
        for socket in (coordinator, member):
            await manager.disconnect(socket)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_later_message_of_the_chat_reaches_the_coordinator_while_the_end_of_the_help_commits(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the process that served the leave, while the commit that releases the
    notice of the end of the help has not returned, its listener delivers no
    later message of the chat. The coordinator's socket is closed first."""
    import app.routers.chat as chat_router
    from app.services.pg_notify import PgNotifyListener

    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-left-serving-commit")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)

    log: list[str] = []
    coordinator, member = _FakeSocket(log, "coordinator"), _FakeSocket(log, "member")
    await manager.subscribe(coordinator, f"chat:{team_id}")  # type: ignore[arg-type]
    await manager.subscribe(member, f"chat:{team_id}")  # type: ignore[arg-type]

    async def coordinator_leaves() -> bool:
        log.append("coordinator closed")
        await manager.disconnect(coordinator)  # type: ignore[arg-type]
        return True

    async def member_stays() -> bool:
        return False

    await manager.register_check(coordinator, coordinator_leaves)  # type: ignore[arg-type]
    await manager.register_check(member, member_stays)  # type: ignore[arg-type]

    # The commit that carries the notice is held after it has committed: the
    # notice is on its way to every listener, the request has not gone on.
    notice_written = False
    notify_chat_message = chat_router.notify_chat_message

    async def notify(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        nonlocal notice_written
        await notify_chat_message(*args, **kwargs)
        notice_written = True

    monkeypatch.setattr(chat_router, "notify_chat_message", notify)
    committed, release = asyncio.Event(), asyncio.Event()
    commit = db_session.commit

    async def held_commit() -> None:
        await commit()
        if notice_written and not committed.is_set():
            committed.set()
            await release.wait()

    monkeypatch.setattr(db_session, "commit", held_commit)
    listener = PgNotifyListener("postgresql://unused")

    try:
        leave_task = asyncio.create_task(
            client.post(
                f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/chat/leave-coordinator",
                headers=env.auth_headers["org_admin"],
            )
        )
        await asyncio.wait_for(committed.wait(), timeout=10.0)

        after_task = asyncio.create_task(
            listener._dispatch("chat_messages", _chat_payload(team_id, "after the help", system=False))
        )
        await _let_tasks_run()
        assert "coordinator got after the help" not in log

        release.set()
        left = await asyncio.wait_for(leave_task, timeout=10.0)
        assert left.status_code == 200, left.text
        await asyncio.wait_for(after_task, timeout=5.0)
        assert "coordinator got after the help" not in log
        got_left = next(i for i, entry in enumerate(log) if entry.startswith("coordinator got [coordinator_left]"))
        assert got_left < log.index("coordinator closed") < log.index("member got after the help")
    finally:
        release.set()
        for socket in (coordinator, member):
            await manager.disconnect(socket)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_coordinator_socket_subscribing_after_the_end_of_the_help_gets_no_later_message(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coordinator's chat socket admitted while the window was open, and
    subscribed after the help has ended, is closed by its own first check.

    While that check reads, no later message of the chat reaches it.
    """
    from app.services.pg_notify import PgNotifyListener

    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-left-late-socket")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)
    coordinator = env.users["coordinator"]

    # Admitted with the window open; the help ends at once after that (another
    # process served the leave, before this socket was on the channel). The
    # socket's first check after its subscription is held.
    entered, release = asyncio.Event(), asyncio.Event()
    refusal_of = ws_router.ws_refusal

    async def refusal(db: AsyncSession, grant: WsGrant, **kwargs):  # noqa: ANN003, ANN202
        if kwargs.get("connecting"):
            admitted = await refusal_of(db, grant, **kwargs)
            await db.execute(update(Team).where(Team.id == UUID(team_id)).values(coordinator_chat_opened_at=None))
            return admitted
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await refusal_of(db, grant, **kwargs)

    monkeypatch.setattr(ws_router, "ws_refusal", refusal)
    listener = PgNotifyListener("postgresql://unused")

    try:
        async with aconnect_ws(_chat_url(env, run_id, team_id, coordinator), ws_client) as ws_coordinator:
            await asyncio.wait_for(entered.wait(), timeout=5.0)
            after_task = asyncio.create_task(
                listener._dispatch("chat_messages", _chat_payload(team_id, "after the help", system=False))
            )
            await _let_tasks_run()
            release.set()
            await asyncio.wait_for(after_task, timeout=5.0)

            received: list[str] = []
            with pytest.raises(WebSocketDisconnect) as exc_info:
                for _ in range(10):
                    received.append(await ws_coordinator.receive_text(timeout=5.0))
            assert exc_info.value.code == 4003
            assert not any("after the help" in text for text in received)
    finally:
        release.set()


# --- A lowered role ---------------------------------------------------------


@pytest.mark.asyncio
async def test_lowering_a_role_closes_every_socket_the_new_role_does_not_allow(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-demote")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)
    coordinator, member = env.users["coordinator"], env.users["participant1"]

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, coordinator), ws_client) as ws_chat,
        aconnect_ws(_feed_url(env, run_id, coordinator), ws_client) as ws_staff_feed,
        aconnect_ws(_chat_url(env, run_id, team_id, member), ws_client) as ws_member,
    ):
        await await_ws_subscribed(ws_chat)
        await await_ws_subscribed(ws_staff_feed)
        await await_ws_subscribed(ws_member)

        response = await client.put(
            f"/api/orgs/{env.slug}/users/{coordinator.id}",
            json={"role": "participant"},
            headers=env.auth_headers["org_admin"],
        )
        assert response.status_code == 200, response.text

        # A participant who is no member has no right to the team's chat
        await _assert_closed(ws_chat, code=4003)
        # The feed socket was on the staff channel. 4000 lets the client
        # reconnect, and it is then subscribed as a participant.
        await _assert_closed(ws_staff_feed, code=4000)
        await _assert_alive(ws_member)

    assert manager.get_subscriber_count(f"feed_staff:{run_id}") == 0


@pytest.mark.asyncio
async def test_lowering_a_role_keeps_the_sockets_the_new_role_allows(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-demote-keep")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)
    creator = env.users["creator"]

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, creator), ws_client) as ws_chat,
        aconnect_ws(_feed_url(env, run_id, creator), ws_client) as ws_feed,
    ):
        await await_ws_subscribed(ws_chat)
        await await_ws_subscribed(ws_feed)

        response = await client.put(
            f"/api/orgs/{env.slug}/users/{creator.id}",
            json={"role": "coordinator"},
            headers=env.auth_headers["org_admin"],
        )
        assert response.status_code == 200, response.text

        # A coordinator may be in an assisted chat and on the staff feed
        await _assert_alive(ws_chat)
        await _assert_alive(ws_feed)


# --- Sign-out and the expiry of the token -----------------------------------


@pytest.mark.asyncio
async def test_sign_out_closes_the_sockets_of_that_account_only(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-logout")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    target, bystander = env.users["participant1"], env.users["participant2"]

    async with (
        aconnect_ws(_feed_url(env, run_id, target), ws_client) as ws_feed,
        aconnect_ws(_chat_url(env, run_id, team_id, target), ws_client) as ws_chat,
        aconnect_ws(_feed_url(env, run_id, bystander), ws_client) as ws_bystander,
    ):
        await await_ws_subscribed(ws_feed)
        await await_ws_subscribed(ws_chat)
        await await_ws_subscribed(ws_bystander)

        signed_out = await client.post("/api/auth/logout", headers=env.auth_headers["participant1"])
        assert signed_out.status_code == 200, signed_out.text

        # A socket does not know which session opened it (its token carries no
        # session id), so the sign-out closes the account's sockets. 4000 lets
        # another device of the account reconnect with its own session.
        await _assert_closed(ws_feed)
        await _assert_closed(ws_chat)
        await _assert_alive(ws_bystander)


async def _ws_token(client: AsyncClient, headers: dict[str, str]) -> str:
    """The short token the app opens its sockets with, minted from the session of ``headers``."""
    response = await client.get("/api/auth/ws-token", headers=headers)
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


def _sign_out_between_accept_and_subscription(
    monkeypatch, client: AsyncClient, headers: dict[str, str]
) -> asyncio.Event:  # noqa: ANN001
    """Sign the session out while its socket is accepted and not subscribed yet.

    The handler has authenticated and accepted by then, and holds before its
    first subscription. The sign-out closes the account's channel, which this
    socket is not on yet, and changes nothing in the database. The order comes
    from events, not from a sleep: the handler holds until the sign-out
    answered, and the returned event says that it did.
    """
    original = manager.subscribe
    held = asyncio.Event()
    signed_out = asyncio.Event()

    async def sign_out() -> None:
        await held.wait()
        response = await client.post("/api/auth/logout", headers=headers)
        assert response.status_code == 200, response.text
        signed_out.set()

    task = asyncio.get_running_loop().create_task(sign_out())

    async def subscribe(ws, channel: str) -> None:  # noqa: ANN001
        if not held.is_set():
            held.set()
            await task
        await original(ws, channel)

    monkeypatch.setattr(manager, "subscribe", subscribe)
    return signed_out


@pytest.mark.asyncio
async def test_run_feed_socket_subscribing_after_a_sign_out_is_closed(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, monkeypatch
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-late-out-feed")
    run_id = await _active_run(client, env)
    target = env.users["participant1"]
    headers = env.auth_headers["participant1"]
    url = f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={await _ws_token(client, headers)}"

    signed_out = _sign_out_between_accept_and_subscription(monkeypatch, client, headers)
    async with aconnect_ws(url, ws_client) as ws:
        await _assert_closed(ws)

    assert signed_out.is_set()
    assert manager.get_subscriber_count(f"user:{target.id}") == 0
    assert manager.get_subscriber_count(f"feed:{run_id}") == 0


@pytest.mark.asyncio
async def test_team_chat_socket_subscribing_after_a_sign_out_is_closed(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, monkeypatch
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-late-out-chat")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    target = env.users["participant1"]
    headers = env.auth_headers["participant1"]
    url = f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}/ws?token={await _ws_token(client, headers)}"

    signed_out = _sign_out_between_accept_and_subscription(monkeypatch, client, headers)
    async with aconnect_ws(url, ws_client) as ws:
        await _assert_closed(ws)

    assert signed_out.is_set()
    assert manager.get_subscriber_count(f"user:{target.id}") == 0
    assert manager.get_subscriber_count(f"chat:{team_id}") == 0


@pytest.mark.asyncio
async def test_a_signed_out_session_opens_no_socket_and_another_session_of_the_account_does(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-out-other")
    run_id = await _active_run(client, env)
    target = env.users["participant1"]
    this_device = env.auth_headers["participant1"]
    other_device = get_auth_headers(target)
    stale = await _ws_token(client, this_device)

    signed_out = await client.post("/api/auth/logout", headers=this_device)
    assert signed_out.status_code == 200, signed_out.text

    # The token minted from the signed-out session is refused before accept()
    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={stale}", ws_client) as ws:
            await ws.receive_text(timeout=5.0)
    assert exc_info.value.code == 1008

    # A sign-out does not end the person's session on another device
    fresh = await _ws_token(client, other_device)
    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={fresh}", ws_client) as ws:
        await await_ws_subscribed(ws)
        await _assert_alive(ws)


@pytest.mark.asyncio
async def test_keepalive_closes_a_socket_whose_session_signed_out_in_another_way(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    """The record alone ends the socket: nothing closes the account's channel here."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-out-keepalive")
    run_id = await _active_run(client, env)
    await db_session.commit()
    token = await _ws_token(client, env.auth_headers["participant1"])

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={token}", ws_client) as ws:
        await await_ws_subscribed(ws)
        await _assert_alive(ws)

        session_id = decode_token(token)["sid"]
        sign_out_session(session_id, datetime.now(UTC) + timedelta(minutes=5))

        await _assert_closed(ws)


@pytest.mark.asyncio
async def test_a_socket_does_not_outlive_its_token(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-expiry")
    run_id = await _active_run(client, env)
    target, bystander = env.users["participant1"], env.users["participant2"]
    short_token = create_access_token(
        user_id=target.id,
        role=target.role.value,
        organization_id=target.organization_id,
        expires_delta=timedelta(seconds=2),
        session_version=target.session_version,
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=2)

    async with (
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={short_token}", ws_client) as ws_target,
        aconnect_ws(_feed_url(env, run_id, bystander), ws_client) as ws_bystander,
    ):
        await await_ws_subscribed(ws_target)

        # 4002 is recoverable: the client fetches a new token and reconnects
        await _assert_closed(ws_target, code=4002)
        closed_at = datetime.now(UTC)
        # Not before the expiry, and within a few keepalive intervals of it
        assert closed_at >= expires_at - timedelta(seconds=1)
        assert closed_at <= expires_at + timedelta(seconds=2)
        await _assert_alive(ws_bystander)


# --- The deletion of an organization and the cleanup of a guest -------------


@pytest.mark.asyncio
async def test_deleting_the_organization_closes_every_member_socket(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-org-delete")
    run_id = await _active_run(client, env)
    p1, coordinator = env.users["participant1"], env.users["coordinator"]

    async with (
        aconnect_ws(_feed_url(env, run_id, p1), ws_client) as ws_1,
        aconnect_ws(_feed_url(env, run_id, coordinator), ws_client) as ws_2,
    ):
        await await_ws_subscribed(ws_1)
        await await_ws_subscribed(ws_2)

        deleted = await client.delete(f"/api/orgs/{env.slug}", headers=env.auth_headers["org_admin"])
        assert deleted.status_code == 200, deleted.text

        await _assert_closed(ws_1)
        await _assert_closed(ws_2)


@pytest.mark.asyncio
async def test_the_hard_deletion_of_an_organization_closes_every_member_socket(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-org-sweep")
    run_id = await _active_run(client, env)
    p1 = env.users["participant1"]
    other = await setup_journey_environment(db_session, client, org_slug="ws-rev-org-sweep-other")
    other_run_id = await _active_run(client, other)
    now = datetime.now(UTC)

    async with (
        aconnect_ws(_feed_url(env, run_id, p1), ws_client) as ws_1,
        aconnect_ws(_feed_url(other, other_run_id, other.users["participant1"]), ws_client) as ws_other,
    ):
        await await_ws_subscribed(ws_1)
        await await_ws_subscribed(ws_other)

        # Due for deletion, written directly: the archive endpoint would close
        # the sockets itself, and this test is about the sweep.
        await db_session.execute(
            update(Organization)
            .where(Organization.id == env.org.id)
            .values(archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1))
        )
        await db_session.commit()
        assert await sweep_due_organizations(db_session, reference_date=now) == 1

        await _assert_closed(ws_1)
        await _assert_alive(ws_other)


@pytest.mark.asyncio
async def test_the_cleanup_of_a_guest_closes_the_guests_socket_after_the_commit(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-guest-cleanup")
    run_id, _team_id, guest_id, guest_token = await _guest_in_new_run(client, env)
    bystander = env.users["participant1"]

    async with (
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={guest_token}", ws_client) as ws_guest,
        aconnect_ws(_feed_url(env, run_id, bystander), ws_client) as ws_bystander,
    ):
        await await_ws_subscribed(ws_guest)
        await await_ws_subscribed(ws_bystander)

        assert await delete_run_guests(db_session, run_id, env.org.id, {run_id}) == 1
        # Nothing is closed before the commit: the account still exists
        assert manager.get_subscriber_count(f"user:{guest_id}") == 1
        await db_session.commit()

        await _assert_closed(ws_guest)
        await _assert_alive(ws_bystander)


@pytest.mark.asyncio
async def test_a_rolled_back_cleanup_of_a_guest_closes_nothing(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-rev-guest-rollback")
    run_id, _team_id, guest_id, guest_token = await _guest_in_new_run(client, env)

    async with aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={guest_token}", ws_client) as ws_guest:
        await await_ws_subscribed(ws_guest)

        assert await delete_run_guests(db_session, run_id, env.org.id, {run_id}) == 1
        await db_session.rollback()
        # A later commit of the same session does not close them either
        await db_session.commit()
        await asyncio.sleep(0.1)

        assert manager.get_subscriber_count(f"user:{guest_id}") == 1
        await _assert_alive(ws_guest)


# --- The one rule, asked again at every keepalive ---------------------------
#
# These tests write the change to the database directly. No handler runs, so
# no explicit close runs: only the keepalive check can end the socket.


@pytest.mark.asyncio
async def test_keepalive_keeps_a_socket_whose_right_holds(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-holds")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    member = env.users["participant1"]

    async with (
        aconnect_ws(_feed_url(env, run_id, member), ws_client) as ws_feed,
        aconnect_ws(_chat_url(env, run_id, team_id, member), ws_client) as ws_chat,
        aconnect_ws(_feed_url(env, run_id, env.users["coordinator"]), ws_client) as ws_staff,
    ):
        await await_ws_subscribed(ws_feed)
        # Several keepalive intervals pass
        await asyncio.sleep(0.5)

        await _assert_alive(ws_feed)
        await _assert_alive(ws_chat)
        await _assert_alive(ws_staff)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [{"is_active": False}, {"session_version": 7}, {"is_deleted": True}],
    ids=["deactivated", "session-version", "deleted"],
)
async def test_keepalive_closes_a_socket_whose_account_or_session_ended(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None, values: dict
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-account")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    target, bystander = env.users["participant1"], env.users["participant2"]

    async with (
        aconnect_ws(_feed_url(env, run_id, target), ws_client) as ws_feed,
        aconnect_ws(_chat_url(env, run_id, team_id, target), ws_client) as ws_chat,
        aconnect_ws(_feed_url(env, run_id, bystander), ws_client) as ws_bystander,
    ):
        await await_ws_subscribed(ws_feed)
        await await_ws_subscribed(ws_chat)

        await db_session.execute(update(User).where(User.id == target.id).values(**values))
        await db_session.commit()

        await _assert_closed(ws_feed)
        await _assert_closed(ws_chat)
        await _assert_alive(ws_bystander)


@pytest.mark.asyncio
async def test_keepalive_closes_the_sockets_of_an_archived_organization(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-archived")
    run_id = await _active_run(client, env)

    async with aconnect_ws(_feed_url(env, run_id, env.users["participant1"]), ws_client) as ws_feed:
        await await_ws_subscribed(ws_feed)

        await db_session.execute(
            update(Organization).where(Organization.id == env.org.id).values(archived_at=datetime.now(UTC))
        )
        await db_session.commit()

        await _assert_closed(ws_feed)


@pytest.mark.asyncio
async def test_keepalive_closes_the_chat_socket_of_a_member_whose_membership_ended(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-member")
    run_id = await _active_run(client, env)
    old_team = await _two_member_team(client, env, run_id)
    _, third_headers = await _participant(db_session, env, "third")
    new_team = await create_team_and_join(client, env.slug, run_id, third_headers, "Target Team", "#00FF00")
    moved, stayer = env.users["participant2"], env.users["participant1"]

    async with (
        aconnect_ws(_chat_url(env, run_id, old_team, moved), ws_client) as ws_moved,
        aconnect_ws(_chat_url(env, run_id, old_team, stayer), ws_client) as ws_stayer,
        aconnect_ws(_feed_url(env, run_id, moved), ws_client) as ws_moved_feed,
    ):
        await await_ws_subscribed(ws_moved)
        await await_ws_subscribed(ws_stayer)

        await db_session.execute(
            update(TeamMember).where(TeamMember.user_id == moved.id).values(team_id=UUID(new_team["id"]))
        )
        await db_session.commit()

        await _assert_closed(ws_moved, code=4003)
        await _assert_alive(ws_stayer)
        # Still a member of a team of the run: the run feed stays
        await _assert_alive(ws_moved_feed)


@pytest.mark.asyncio
async def test_keepalive_closes_the_chat_socket_of_a_coordinator_when_the_assist_window_closed(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-assist")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, env.users["coordinator"]), ws_client) as ws_coordinator,
        aconnect_ws(_chat_url(env, run_id, team_id, env.users["participant1"]), ws_client) as ws_member,
    ):
        await await_ws_subscribed(ws_coordinator)
        await await_ws_subscribed(ws_member)

        await db_session.execute(update(Team).where(Team.id == UUID(team_id)).values(coordinator_chat_opened_at=None))
        await db_session.commit()

        await _assert_closed(ws_coordinator, code=4003)
        await _assert_alive(ws_member)


@pytest.mark.asyncio
async def test_keepalive_closes_what_a_lowered_role_does_not_allow(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-role")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    await _open_assist_window(client, env, run_id, team_id)
    coordinator = env.users["coordinator"]

    async with (
        aconnect_ws(_chat_url(env, run_id, team_id, coordinator), ws_client) as ws_chat,
        aconnect_ws(_feed_url(env, run_id, coordinator), ws_client) as ws_staff_feed,
    ):
        await await_ws_subscribed(ws_chat)
        await await_ws_subscribed(ws_staff_feed)

        await db_session.execute(update(User).where(User.id == coordinator.id).values(role=UserRole.PARTICIPANT))
        await db_session.commit()

        await _assert_closed(ws_chat, code=4003)
        await _assert_closed(ws_staff_feed, code=4000)


@pytest.mark.asyncio
async def test_keepalive_checks_a_socket_that_sends_no_ping(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    """The server keeps the time: a client that never pings is checked all the same."""
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-silent")
    run_id = await _active_run(client, env)
    target = env.users["participant1"]

    async with aconnect_ws(_feed_url(env, run_id, target), ws_client) as ws_silent:
        # No ping at all; wait for the subscription on the server side
        for _ in range(100):
            if manager.get_subscriber_count(f"user:{target.id}") == 1:
                break
            await asyncio.sleep(0.02)

        await db_session.execute(update(User).where(User.id == target.id).values(is_active=False))
        await db_session.commit()

        await _assert_closed(ws_silent)


@pytest.mark.asyncio
async def test_the_keepalive_check_costs_one_read(
    db_session: AsyncSession, client: AsyncClient, test_engine: AsyncEngine
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-one-read")
    run_id = await _active_run(client, env)
    team_id = await _two_member_team(client, env, run_id)
    member = env.users["participant1"]
    feed = WsGrant(
        user_id=member.id,
        organization_id=env.org.id,
        session_version=member.session_version,
        token_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        run_id=run_id,
    )
    chat = WsGrant(
        user_id=member.id,
        organization_id=env.org.id,
        session_version=member.session_version,
        token_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        run_id=run_id,
        team_id=UUID(team_id),
    )
    await db_session.commit()

    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", count)
    try:
        assert await ws_refusal(db_session, feed) is None
        assert len(statements) == 1, statements
        assert await ws_refusal(db_session, chat) is None
        assert len(statements) == 2, statements
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", count)


# --- A guest without a team in the run loses the run feed -------------


@pytest.mark.asyncio
async def test_keepalive_closes_the_run_feed_of_a_guest_who_left_the_team(
    db_session: AsyncSession, client: AsyncClient, ws_client: AsyncClient, fast_keepalive: None
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="ws-keep-guest-left")
    run_id, team_id, _guest_id, guest_token = await _guest_in_new_run(client, env)
    guest_headers = {"Authorization": f"Bearer {guest_token}"}
    registered, registered_headers = await _participant(db_session, env, "registered")
    team = await create_team_and_join(client, env.slug, run_id, registered_headers, "Registered", "#0000FF")

    async with (
        aconnect_ws(f"/api/orgs/{env.slug}/runs/{run_id}/ws?token={guest_token}", ws_client) as ws_guest,
        aconnect_ws(_feed_url(env, run_id, registered), ws_client) as ws_registered,
    ):
        await await_ws_subscribed(ws_guest)
        await await_ws_subscribed(ws_registered)

        for headers, left_team in ((guest_headers, team_id), (registered_headers, team["id"])):
            left = await leave_as_last_member(client, env.slug, run_id, left_team, headers)
            assert left.status_code == 204, left.text

        # The guest is out of the run. 4003: reconnecting would be refused.
        await _assert_closed(ws_guest, code=4003)
        # A registered account without a team keeps the run's broadcasts
        await _assert_alive(ws_registered)
