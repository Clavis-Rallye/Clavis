"""
Join flow and WebSocket accept only a full, current session.

The optional cookie of POST /api/join/{code} and the WebSocket token are
judged by the one rule ``is_full_current_session``: a token of an older
session version, or one with a restricted purpose (``2fa_setup``), is no
session at all. In the join flow such a token is treated as absent; the
WebSocket refuses it.

- a stale session version is no session in the join flow
- a ``2fa_setup`` token is no session in the join flow, and the WebSocket refuses it
- one shared function decides for all three places
- a guest with a valid session refreshing the page gets the same team
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens as _tokens
from app.auth.tokens import create_access_token
from app.models.user import User
from tests.factories import create_test_user_with_org
from tests.journey_helpers import activate_run, setup_journey_environment


def _token(user: User, *, purpose: str | None = None, session_version: int | None = None) -> str:
    return create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        purpose=purpose,
        session_version=user.session_version if session_version is None else session_version,
    )


async def _active_run(client: AsyncClient, db_session: AsyncSession, org_slug: str) -> tuple[str, str]:
    env = await setup_journey_environment(db_session, client, org_slug=org_slug)
    headers = env.auth_headers["coordinator"]
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(env.rallye_id), "name": "Token State Run"},
    )
    assert created.status_code == 201
    run = created.json()
    await activate_run(client, env.slug, run["id"], headers)
    return run["join_code"], env.slug


async def _guest_joined(client: AsyncClient, db_session: AsyncSession, org_slug: str):  # noqa: ANN202
    """A joined guest: (join_code, first join body, guest User)."""
    join_code, _ = await _active_run(client, db_session, org_slug)
    first = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Team Eins"},
    )
    assert first.status_code == 201, first.text
    guest = await db_session.get(User, first.json()["user_id"])
    assert guest is not None
    client.cookies.clear()
    return join_code, first.json(), guest


@pytest.mark.asyncio
async def test_join_with_stale_session_version_issues_a_fresh_identity(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The cookie's sv is older than the account's — no identity reused, no token for that account."""
    join_code, first, guest = await _guest_joined(client, db_session, "jts-stale")
    stale = _token(guest)
    guest.session_version += 1
    await db_session.commit()

    again = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Zweite", "team_name": "Team Zwei"},
        cookies={"access_token": stale},
    )
    assert again.status_code == 201, again.text
    assert again.json()["user_id"] != first["user_id"]
    assert again.json()["team_id"] != first["team_id"]


@pytest.mark.asyncio
async def test_join_by_code_with_stale_session_version_does_not_reuse_account(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Join by code: a stale token does not make the old account the member."""
    join_code, first, guest = await _guest_joined(client, db_session, "jts-stale-code")
    stale = _token(guest)
    guest.session_version += 1
    await db_session.commit()

    again = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "join", "display_name": "Neu", "team_code": first["team_share_code"]},
        cookies={"access_token": stale},
    )
    assert again.status_code == 201, again.text
    assert again.json()["user_id"] != first["user_id"]


@pytest.mark.asyncio
async def test_join_with_2fa_setup_token_is_treated_as_absent(client: AsyncClient, db_session: AsyncSession) -> None:
    """A restricted 2fa_setup token is no session in the join flow."""
    join_code, first, guest = await _guest_joined(client, db_session, "jts-2fa")
    restricted = _token(guest, purpose="2fa_setup")

    again = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Zweite", "team_name": "Team Zwei"},
        cookies={"access_token": restricted},
    )
    assert again.status_code == 201, again.text
    assert again.json()["user_id"] != first["user_id"]
    assert again.json()["team_id"] != first["team_id"]


@pytest.mark.asyncio
async def test_join_refresh_with_valid_guest_session_keeps_the_team(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A guest with a valid session refreshing the page gets the same team."""
    join_code, first, guest = await _guest_joined(client, db_session, "jts-refresh")

    again = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Erste", "team_name": "Team Eins"},
        cookies={"access_token": _token(guest)},
    )
    assert again.status_code == 201, again.text
    assert again.json()["user_id"] == first["user_id"]
    assert again.json()["team_id"] == first["team_id"]


@pytest.mark.asyncio
async def test_ws_refuses_2fa_setup_token(
    client: AsyncClient, db_session: AsyncSession, ws_client: AsyncClient
) -> None:
    """The WebSocket refuses a restricted 2fa_setup token."""
    user, org = await create_test_user_with_org(
        db_session, email="ws-2fa@example.com", password="Password123!", org_slug="jts-ws-2fa"
    )
    restricted = _token(user, purpose="2fa_setup")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{org.slug}/runs/{uuid4()}/ws?token={restricted}", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert exc_info.value.reason == "Session revoked"


@pytest.mark.asyncio
async def test_is_full_current_session_rule(db_session: AsyncSession) -> None:
    """The one rule — purpose-less, current sv, not locked."""
    user, _ = await create_test_user_with_org(
        db_session, email="rule@example.com", password="Password123!", org_slug="jts-rule"
    )
    full = {"sub": str(user.id), "sv": user.session_version}

    assert _tokens.is_full_current_session(full, user)
    assert _tokens.is_full_current_session({"sub": str(user.id)}, user)  # pre-release token, sv reads as 0
    assert not _tokens.is_full_current_session({**full, "sv": user.session_version + 1}, user)
    assert not _tokens.is_full_current_session({**full, "purpose": "2fa_setup"}, user)
    assert _tokens.is_full_current_session(
        {**full, "purpose": "2fa_setup"}, user, allow_purposes=frozenset({"2fa_setup"})
    )
    assert not _tokens.is_full_current_session(
        {**full, "purpose": "other"}, user, allow_purposes=frozenset({"2fa_setup"})
    )

    user.locked_until = datetime.now(UTC) + timedelta(minutes=5)
    assert not _tokens.is_full_current_session(full, user)
    user.locked_until = datetime.now(UTC) - timedelta(minutes=5)
    assert _tokens.is_full_current_session(full, user)


# --- The instance scope and the lock rule have one home ----------


@pytest.mark.asyncio
async def test_is_full_current_session_rejects_the_instance_scope(db_session: AsyncSession) -> None:
    """The operator's token is no user session for any caller, even with a matching sub and sv."""
    user, _ = await create_test_user_with_org(
        db_session, email="scope@example.com", password="Password123!", org_slug="jts-scope"
    )
    operator = _tokens.decode_token(_tokens.create_instance_admin_token(user.id, session_version=user.session_version))

    assert operator["scope"] == "instance"
    assert not _tokens.is_full_current_session(operator, user)
    assert not _tokens.is_full_current_session(operator, user, allow_purposes=frozenset({"2fa_setup"}))


@pytest.mark.asyncio
async def test_join_treats_an_instance_scope_token_as_absent(client: AsyncClient, db_session: AsyncSession) -> None:
    """An operator token whose sub equals a guest's id does not make that guest the member."""
    join_code, first, guest = await _guest_joined(client, db_session, "jts-scope-join")
    operator = _tokens.create_instance_admin_token(guest.id, session_version=guest.session_version)

    again = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Zweite", "team_name": "Team Zwei"},
        cookies={"access_token": operator},
    )
    assert again.status_code == 201, again.text
    assert again.json()["user_id"] != first["user_id"]


@pytest.mark.asyncio
async def test_is_account_locked_is_the_one_lock_rule(db_session: AsyncSession) -> None:
    """``is_account_locked`` answers the lock question; the shared session rule uses it."""
    user, _ = await create_test_user_with_org(
        db_session, email="lock@example.com", password="Password123!", org_slug="jts-lock"
    )
    now = datetime.now(UTC)

    user.locked_until = None
    assert not _tokens.is_account_locked(user)
    user.locked_until = now + timedelta(minutes=5)
    assert _tokens.is_account_locked(user)
    user.locked_until = now - timedelta(minutes=5)
    assert not _tokens.is_account_locked(user)
    assert _tokens.is_account_locked(user, now=now - timedelta(minutes=10))


@pytest.mark.asyncio
async def test_session_rule_follows_the_lock_helper(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared rule has no lock rule of its own — it asks the helper."""
    user, _ = await create_test_user_with_org(
        db_session, email="lock-follow@example.com", password="Password123!", org_slug="jts-lock-follow"
    )
    full = {"sub": str(user.id), "sv": user.session_version}

    monkeypatch.setattr(_tokens, "is_account_locked", lambda _user, now=None: True)
    assert not _tokens.is_full_current_session(full, user)
