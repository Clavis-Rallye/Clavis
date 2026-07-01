"""Integration tests for the coordinator assist window of a team chat.

Coordinators join a team chat on the team's request, with the full history,
and can leave again at any time.

- A request opens the window, and the coordinator gains read and write access.
- A closed window answers 403 for coordinator, creator and org_admin.
- Leaving closes the window, is idempotent, and works on paused runs.
- A re-request after a leave re-opens the window.
- A coordinator cannot open the window through /request-coordinator.
- `coordinator_chat_open` is exposed on team responses.
- Repeated requests keep the window open and keep emitting messages.

"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers
from tests.test_coordinator_request import _setup_run


async def _setup_with_member(client, db_session, org, coordinator):
    """Create a run with a team and one joined participant. Returns (run_id, team_id, member_headers)."""
    run_id, team_id = await _setup_run(client, db_session, org.slug, coordinator, org)
    participant = await create_test_user(
        db_session, email=f"member-{team_id[:8]}@test.com", role=UserRole.PARTICIPANT, organization=org
    )
    member_headers = get_auth_headers(participant)
    join = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join",
        headers=member_headers,
    )
    assert join.status_code == 204
    return run_id, team_id, member_headers


@pytest.mark.asyncio
async def test_closed_window_denies_all_coordinator_plus_roles(client: AsyncClient, db_session: AsyncSession):
    """Without a request, coordinator, creator and org_admin all get 403, with no admin carve-out."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    creator = await create_test_user(db_session, email="creator@test.com", role=UserRole.CREATOR, organization=org)
    org_admin = await create_test_user(db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, organization=org)

    run_id, team_id, _member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    for user in (coordinator, creator, org_admin):
        headers = get_auth_headers(user)
        assert (await client.get(base, headers=headers)).status_code == 403
        assert (await client.post(base, headers=headers, json={"content": "hi"})).status_code == 403


@pytest.mark.asyncio
async def test_request_opens_window_and_leave_closes_it(client: AsyncClient, db_session: AsyncSession):
    """A request opens the window for coordinator read and write, a leave closes it again, and a re-request re-opens it."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200

    assert (await client.get(base, headers=coord_headers)).status_code == 200
    assert (await client.post(base, headers=coord_headers, json={"content": "How can I help?"})).status_code == 201

    leave = await client.post(f"{base}leave-coordinator", headers=coord_headers)
    assert leave.status_code == 200
    assert (await client.get(base, headers=coord_headers)).status_code == 403

    listing = await client.get(base, headers=member_headers)
    assert listing.status_code == 200
    system_msgs = [i["content"] for i in listing.json()["items"] if i["is_system_message"]]
    assert any("[coordinator_left]" in c for c in system_msgs)

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200
    assert (await client.get(base, headers=coord_headers)).status_code == 200


@pytest.mark.asyncio
async def test_leave_is_idempotent(client: AsyncClient, db_session: AsyncSession):
    """A leave on a closed window is a no-op 200 without a duplicate system message."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    assert (await client.post(f"{base}leave-coordinator", headers=coord_headers)).status_code == 200

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200
    assert (await client.post(f"{base}leave-coordinator", headers=coord_headers)).status_code == 200
    assert (await client.post(f"{base}leave-coordinator", headers=coord_headers)).status_code == 200

    listing = await client.get(base, headers=member_headers)
    left_msgs = [i for i in listing.json()["items"] if "[coordinator_left]" in i["content"]]
    assert len(left_msgs) == 1


@pytest.mark.asyncio
async def test_leave_works_on_paused_run(client: AsyncClient, db_session: AsyncSession):
    """A coordinator can leave at any time: leave has no run-state guard, while a request on a paused run is refused."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200

    pause = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=coord_headers)
    assert pause.status_code == 200

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 422
    assert (await client.post(f"{base}leave-coordinator", headers=coord_headers)).status_code == 200


@pytest.mark.asyncio
async def test_coordinator_cannot_self_open_window(client: AsyncClient, db_session: AsyncSession):
    """A non-member coordinator calling /request-coordinator gets 403, so the window cannot be self-granted."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, _member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    assert (await client.post(f"{base}request-coordinator", headers=coord_headers)).status_code == 403
    assert (await client.get(base, headers=coord_headers)).status_code == 403


@pytest.mark.asyncio
async def test_team_response_exposes_coordinator_chat_open(client: AsyncClient, db_session: AsyncSession):
    """The team list carries coordinator_chat_open, reflecting the window state."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    teams_url = f"/api/orgs/{org.slug}/runs/{run_id}/teams/"
    chat_base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    def team_flag(payload):
        return next(t["coordinator_chat_open"] for t in payload["items"] if t["id"] == team_id)

    listing = await client.get(teams_url, headers=coord_headers)
    assert listing.status_code == 200
    assert team_flag(listing.json()) is False

    assert (await client.post(f"{chat_base}request-coordinator", headers=member_headers)).status_code == 200
    assert team_flag((await client.get(teams_url, headers=coord_headers)).json()) is True

    assert (await client.post(f"{chat_base}leave-coordinator", headers=coord_headers)).status_code == 200
    assert team_flag((await client.get(teams_url, headers=coord_headers)).json()) is False


@pytest.mark.asyncio
async def test_coordinator_first_message_emits_joined_once(client: AsyncClient, db_session: AsyncSession):
    """[coordinator_joined] mirrors [coordinator_left].

    It is emitted once when a coordinator first posts to an open window, not on every message,
    and again when a new window opens.

    """
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200

    # Two coordinator messages: the join notice appears exactly once, before the first.
    assert (await client.post(base, headers=coord_headers, json={"content": "Hi!"})).status_code == 201
    assert (await client.post(base, headers=coord_headers, json={"content": "Still there?"})).status_code == 201

    listing = await client.get(base, headers=member_headers)
    items = listing.json()["items"]
    joined_msgs = [i for i in items if "[coordinator_joined]" in i["content"]]
    assert len(joined_msgs) == 1
    joined_idx = next(idx for idx, i in enumerate(items) if i["id"] == joined_msgs[0]["id"])
    first_real_msg_idx = next(idx for idx, i in enumerate(items) if i["content"] == "Hi!")
    assert joined_idx < first_real_msg_idx

    # Leave + re-request + coordinator posts again: notifies once more (new window).
    assert (await client.post(f"{base}leave-coordinator", headers=coord_headers)).status_code == 200
    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200
    assert (await client.post(base, headers=coord_headers, json={"content": "Back again"})).status_code == 201

    listing2 = await client.get(base, headers=member_headers)
    assert len([i for i in listing2.json()["items"] if "[coordinator_joined]" in i["content"]]) == 2


@pytest.mark.asyncio
async def test_repeated_requests_keep_window_open_and_keep_notifying(client: AsyncClient, db_session: AsyncSession):
    """Repeated requests keep the window open, and each one still emits a message."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    for _ in range(3):
        assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200

    assert (await client.get(base, headers=coord_headers)).status_code == 200
    listing = await client.get(base, headers=member_headers)
    request_msgs = [i for i in listing.json()["items"] if "[coordinator_request]" in i["content"]]
    assert len(request_msgs) == 3


@pytest.mark.asyncio
async def test_leave_broadcasts_the_left_message(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """The [coordinator_left] message must be broadcast, not merely written.

    A written row alone is invisible to anyone already in the chat until a reload,
    so a team would see the coordinator arrive but never leave.
    The participant chat also derives its coordinator-open state from this broadcast.
    A test that reads the message back through the listing endpoint cannot catch a missing broadcast.

    The join notice goes out through notify_chat_message (the listener).
    The left notice goes out through it too, for the other API processes.
    It is also sent directly to this process's sockets, before the recheck closes
    a coordinator's socket. Its delivery is tested in test_ws_close_on_revocation.py.

    """
    import app.routers.chat as chat_router

    broadcast: list[str] = []

    original = chat_router.notify_chat_message

    async def spy(db, message, author_display_name=None):
        broadcast.append(message.content)
        return await original(db, message, author_display_name=author_display_name)

    monkeypatch.setattr(chat_router, "notify_chat_message", spy)

    original_broadcast = chat_router.ws_manager.broadcast

    async def broadcast_spy(channel, message):
        if message.get("type") == "chat_message":
            broadcast.append(message["data"]["content"])
        return await original_broadcast(channel, message)

    monkeypatch.setattr(chat_router.ws_manager, "broadcast", broadcast_spy)

    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200
    assert (await client.post(base, headers=coord_headers, json={"content": "How can I help?"})).status_code == 201
    assert (await client.post(f"{base}leave-coordinator", headers=coord_headers)).status_code == 200

    assert any("[coordinator_joined]" in c for c in broadcast), "join notice was not broadcast"
    assert any("[coordinator_left]" in c for c in broadcast), "leave notice was not broadcast"


@pytest.mark.asyncio
async def test_closed_window_send_returns_coded_error(client: AsyncClient, db_session: AsyncSession):
    """A coordinator send that reaches the API on a closed window is 403 with error_code CHAT_WINDOW_CLOSED.

    The frontend can then show the "team has to request help again" sentence
    instead of the generic retry banner.

    """
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    never_requested = await client.post(base, headers=coord_headers, json={"content": "hi"})
    assert never_requested.status_code == 403
    assert never_requested.json()["error_code"] == "CHAT_WINDOW_CLOSED"

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200
    assert (await client.post(f"{base}leave-coordinator", headers=coord_headers)).status_code == 200

    after_leave = await client.post(base, headers=coord_headers, json={"content": "still here?"})
    assert after_leave.status_code == 403
    assert after_leave.json()["error_code"] == "CHAT_WINDOW_CLOSED"


@pytest.mark.asyncio
async def test_coordinator_joined_audit_event_emitted_once(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """The first coordinator message into an open window emits exactly one chat.coordinator_joined audit event.

    It comes next to the chat.coordinator_requested event.

    """
    import app.routers.chat as chat_router

    events: list[str] = []
    original = chat_router.record_audit_event

    def spy(*, event, **kwargs):
        events.append(event)
        return original(event=event, **kwargs)

    monkeypatch.setattr(chat_router, "record_audit_event", spy)

    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    coord_headers = get_auth_headers(coordinator)
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    assert (await client.post(f"{base}request-coordinator", headers=member_headers)).status_code == 200

    assert (await client.post(base, headers=coord_headers, json={"content": "Hi!"})).status_code == 201
    assert (await client.post(base, headers=coord_headers, json={"content": "Still there?"})).status_code == 201

    joined_events = [e for e in events if e == "chat.coordinator_joined"]
    assert len(joined_events) == 1
    assert "chat.coordinator_requested" in events


@pytest.mark.asyncio
async def test_latest_window_returns_the_newest_messages_still_oldest_first(
    client: AsyncClient, db_session: AsyncSession
):
    """A chat is read at its end, so opening one must not land on page 1 of its history.

    With latest=true the window holds the newest messages, still ordered oldest first.
    Without it, skip=0 and limit=N give the oldest window.

    """
    coordinator, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    run_id, team_id, member_headers = await _setup_with_member(client, db_session, org, coordinator)
    base = f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/chat/"

    for i in range(6):
        assert (await client.post(base, headers=member_headers, json={"content": f"m{i}"})).status_code == 201

    oldest = await client.get(f"{base}?limit=3", headers=member_headers)
    assert [i["content"] for i in oldest.json()["items"]] == ["m0", "m1", "m2"]

    latest = await client.get(f"{base}?limit=3&latest=true", headers=member_headers)
    assert [i["content"] for i in latest.json()["items"]] == ["m3", "m4", "m5"]
