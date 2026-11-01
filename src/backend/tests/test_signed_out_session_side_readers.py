"""
A signed-out session is no session for the routes that read the cookie themselves.

A sign-out clears the cookie, and the token it held stays a valid signature
until it expires. The shared dependency and the WebSockets ask the record of
signed-out sessions. The readers below decode the cookie themselves, so each
one is sent the kept cookie of a signed-out session:

- the guest entry treats the caller as a new visitor, the public route of the
  own team answers as for no team, the public route of the run state answers 404
- the realm check of the operator routes, and the second look at the token in
  the TOTP confirmation
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pyotp
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import (
    create_access_token,
    decode_token,
    is_session_that_may_act,
    sign_out_session,
    token_session_id,
)
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from tests.factories import create_test_user
from tests.journey_helpers import JourneyEnv, activate_run, setup_journey_environment

PASSWORD = "TestPassword123!"


def _set_cookies_for(response: Response, name: str) -> list[str]:
    """Every ``Set-Cookie`` header of the response that sets ``name``, in order."""
    return [header for header in response.headers.get_list("set-cookie") if header.startswith(f"{name}=")]


async def _active_run(client: AsyncClient, db_session: AsyncSession, slug: str) -> tuple[JourneyEnv, str, str]:
    """An organization with one active run: the environment, the run id and the join code."""
    env = await setup_journey_environment(db_session, client, org_slug=slug)
    headers = env.auth_headers["coordinator"]
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/", headers=headers, json={"rallye_id": str(env.rallye_id), "name": "Side readers"}
    )
    assert created.status_code == 201, created.text
    run = created.json()
    await activate_run(client, env.slug, run["id"], headers)
    client.cookies.clear()
    return env, run["id"], run["join_code"]


async def _sign_out(client: AsyncClient, token: str) -> None:
    """Sign the session of ``token`` out through the route; the caller keeps the token."""
    signed_out = await client.post("/api/auth/logout", cookies={"access_token": token})
    assert signed_out.status_code == 200, signed_out.text
    client.cookies.clear()


async def _memberships(db_session: AsyncSession, user_id: Any) -> int:
    counted = await db_session.execute(
        select(func.count()).select_from(TeamMember).where(TeamMember.user_id == user_id)
    )
    return counted.scalar_one()


def test_one_function_says_whether_a_session_may_act() -> None:
    """A session may act when it is full, current and not signed out."""
    user = User(id=uuid4(), email="one-rule@example.com", role=UserRole.PARTICIPANT, session_version=3)
    user.locked_until = None
    organization_id = uuid4()

    def token(**claims: Any) -> str:
        return create_access_token(user_id=user.id, role="participant", organization_id=organization_id, **claims)

    live = token(session_version=3)
    assert is_session_that_may_act(decode_token(live), live, user)

    stale = token(session_version=2)
    assert not is_session_that_may_act(decode_token(stale), stale, user)

    restricted = token(session_version=3, purpose="2fa_setup")
    assert not is_session_that_may_act(decode_token(restricted), restricted, user)
    assert is_session_that_may_act(decode_token(restricted), restricted, user, allow_purposes=("2fa_setup",))

    signed_out = token(session_version=3)
    sign_out_session(token_session_id(decode_token(signed_out), signed_out), datetime.now(UTC) + timedelta(minutes=5))
    assert not is_session_that_may_act(decode_token(signed_out), signed_out, user)
    # Only that session: another one of the account still acts.
    assert is_session_that_may_act(decode_token(live), live, user)


@pytest.mark.asyncio
async def test_guest_entry_treats_a_signed_out_account_session_as_a_new_visitor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A signed-out account session is a new visitor at the guest entry: no cookie, no token and no membership for the old account."""
    env, _run_id, join_code = await _active_run(client, db_session, "side-guest-entry")
    account = await create_test_user(
        db_session, email="side-entry@example.com", password=PASSWORD, organization=env.org
    )
    login = await client.post("/api/auth/login", json={"email": "side-entry@example.com", "password": PASSWORD})
    assert login.status_code == 200, login.text
    kept = login.cookies["access_token"]
    client.cookies.clear()
    await _sign_out(client, kept)

    joined = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Visitor", "team_name": "Visitors"},
        cookies={"access_token": kept},
    )

    assert joined.status_code == 201, joined.text
    assert joined.json()["user_id"] != str(account.id)
    assert decode_token(joined.json()["token"])["sub"] != str(account.id)
    set_cookies = _set_cookies_for(joined, "access_token")
    assert len(set_cookies) == 1  # the new visitor's own session
    assert decode_token(set_cookies[0].split(";", 1)[0].split("=", 1)[1])["sub"] == joined.json()["user_id"]
    assert await _memberships(db_session, account.id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["guest", "account"])
async def test_guest_entry_answers_a_reused_account_with_a_token_of_the_session_that_asked(
    client: AsyncClient, db_session: AsyncSession, who: str
) -> None:
    """A token from the guest entry's answer is refused once the session that asked is signed out."""
    env, _run_id, join_code = await _active_run(client, db_session, f"side-answer-token-{who}")
    if who == "guest":
        first = await client.post(
            f"/api/join/{join_code}", json={"mode": "create", "display_name": "Guest", "team_name": "Answer team"}
        )
        assert first.status_code == 201, first.text
        session_a = first.cookies["access_token"]
    else:
        await create_test_user(db_session, email="side-answer@example.com", password=PASSWORD, organization=env.org)
        login = await client.post("/api/auth/login", json={"email": "side-answer@example.com", "password": PASSWORD})
        assert login.status_code == 200, login.text
        session_a = login.cookies["access_token"]
    client.cookies.clear()

    joined = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Guest", "team_name": "Answer team"},
        cookies={"access_token": session_a},
    )
    assert joined.status_code == 201, joined.text
    client.cookies.clear()
    kept = joined.json()["token"]

    # The account was reused: no cookie, and the token names the session that asked.
    presented, answered = decode_token(session_a), decode_token(kept)
    assert _set_cookies_for(joined, "access_token") == []
    assert answered["sub"] == presented["sub"] == joined.json()["user_id"]
    assert answered["sid"] == token_session_id(presented, session_a)
    assert answered["sv"] == presented["sv"]

    # While session A lives, its token from the answer is a session like the cookie.
    bearer = {"Authorization": f"Bearer {kept}"}
    assert (await client.get("/api/auth/me", headers=bearer)).status_code == 200
    client.cookies.clear()

    await _sign_out(client, session_a)

    for path in ("/api/auth/me", "/api/auth/ws-token"):
        for attempt in ({"headers": bearer}, {"cookies": {"access_token": kept}}):
            refused = await client.get(path, **attempt)
            assert refused.status_code == 401, (path, attempt, refused.text)
            assert refused.json()["error_code"] == "SESSION_REVOKED"
            client.cookies.clear()


@pytest.mark.asyncio
async def test_guest_entry_answers_a_reused_account_with_the_session_version_that_was_accepted(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A password change that commits before the answer's token is minted does not make that token current."""
    env, _run_id, join_code = await _active_run(client, db_session, "side-answer-version")
    account = await create_test_user(
        db_session, email="side-version@example.com", password=PASSWORD, organization=env.org
    )
    login = await client.post("/api/auth/login", json={"email": "side-version@example.com", "password": PASSWORD})
    assert login.status_code == 200, login.text
    session_a = login.cookies["access_token"]
    client.cookies.clear()

    # The version rises after the entry accepted the session and before it
    # reads the account again for the answer: at its commit.
    original_commit = AsyncSession.commit
    raised = {"done": False}

    async def commit(session: AsyncSession) -> None:
        if not raised["done"] and any(isinstance(row, TeamMember) for row in session.new):
            raised["done"] = True
            await session.execute(
                update(User).where(User.id == account.id).values(session_version=User.session_version + 1)
            )
        await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)

    joined = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Member", "team_name": "Version team"},
        cookies={"access_token": session_a},
    )
    monkeypatch.undo()
    assert joined.status_code == 201, joined.text
    assert raised["done"]
    client.cookies.clear()
    kept = joined.json()["token"]

    assert decode_token(kept)["sv"] == decode_token(session_a)["sv"]
    for path in ("/api/auth/me", "/api/auth/ws-token"):
        refused = await client.get(path, headers={"Authorization": f"Bearer {kept}"})
        assert refused.status_code == 401, (path, refused.text)
        assert refused.json()["error_code"] == "SESSION_REVOKED"
        client.cookies.clear()


@pytest.mark.asyncio
async def test_guest_entry_gives_a_new_guest_a_cookie_and_a_token_of_a_new_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A new guest gets a cookie and a token of one new session, and both authenticate."""
    _env, _run_id, join_code = await _active_run(client, db_session, "side-answer-new-guest")

    joined = await client.post(
        f"/api/join/{join_code}", json={"mode": "create", "display_name": "Guest", "team_name": "New team"}
    )

    assert joined.status_code == 201, joined.text
    client.cookies.clear()
    token = joined.json()["token"]
    set_cookies = _set_cookies_for(joined, "access_token")
    assert len(set_cookies) == 1
    cookie = set_cookies[0].split(";", 1)[0].split("=", 1)[1]
    assert cookie == token
    claims = decode_token(token)
    assert claims["sub"] == joined.json()["user_id"]
    assert isinstance(claims["sid"], str) and claims["sid"]
    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code == 200
    client.cookies.clear()
    assert (await client.get("/api/auth/ws-token", cookies={"access_token": cookie})).status_code == 200


@pytest.mark.asyncio
async def test_guest_entry_does_not_reuse_the_guest_of_a_signed_out_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The guest who signed out is not recognized again: the entry makes a new guest and a new team."""
    _env, _run_id, join_code = await _active_run(client, db_session, "side-guest-again")
    first = await client.post(
        f"/api/join/{join_code}", json={"mode": "create", "display_name": "Guest", "team_name": "First team"}
    )
    assert first.status_code == 201, first.text
    kept = first.cookies["access_token"]
    client.cookies.clear()

    replay = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Guest", "team_name": "First team"},
        cookies={"access_token": kept},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["user_id"] == first.json()["user_id"]
    client.cookies.clear()
    await _sign_out(client, kept)

    again = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": "Guest", "team_name": "Second team"},
        cookies={"access_token": kept},
    )

    assert again.status_code == 201, again.text
    assert again.json()["user_id"] != first.json()["user_id"]
    assert again.json()["team_id"] != first.json()["team_id"]
    assert decode_token(again.json()["token"])["sub"] != first.json()["user_id"]
    for header in _set_cookies_for(again, "access_token"):
        assert decode_token(header.split(";", 1)[0].split("=", 1)[1])["sub"] != first.json()["user_id"]
    assert await _memberships(db_session, first.json()["user_id"]) == 1  # the one from before the sign-out


@pytest.mark.asyncio
async def test_public_routes_answer_a_signed_out_session_as_a_stale_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The own team answers as for no team, the run state answers 404."""
    _env, run_id, join_code = await _active_run(client, db_session, "side-public")
    joined = await client.post(
        f"/api/join/{join_code}", json={"mode": "create", "display_name": "Member", "team_name": "Members"}
    )
    assert joined.status_code == 201, joined.text
    kept = joined.cookies["access_token"]
    client.cookies.clear()

    my_team = await client.get(f"/api/public/runs/{join_code}/my-team", cookies={"access_token": kept})
    assert my_team.status_code == 200
    assert my_team.json()["team_id"] == joined.json()["team_id"]
    state = await client.get(f"/api/public/runs/{run_id}/state", cookies={"access_token": kept})
    assert state.status_code == 200
    client.cookies.clear()
    await _sign_out(client, kept)

    my_team = await client.get(f"/api/public/runs/{join_code}/my-team", cookies={"access_token": kept})
    assert my_team.status_code == 200
    assert my_team.json()["team_id"] is None
    assert my_team.json()["team_name"] is None
    client.cookies.clear()

    state = await client.get(f"/api/public/runs/{run_id}/state", cookies={"access_token": kept})
    assert state.status_code == 404
    assert state.json()["detail"] == "Run not found"


@pytest.mark.asyncio
async def test_operator_routes_take_a_signed_out_user_cookie_for_no_credential(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A live user cookie is the wrong realm (403). A signed-out one is no token at all (401)."""
    await create_test_user(db_session, email="side-realm@example.com", password=PASSWORD)
    login = await client.post("/api/auth/login", json={"email": "side-realm@example.com", "password": PASSWORD})
    assert login.status_code == 200, login.text
    kept = login.cookies["access_token"]
    client.cookies.clear()

    wrong_realm = await client.get("/api/instance/auth/me", cookies={"access_token": kept})
    assert wrong_realm.status_code == 403
    client.cookies.clear()
    await _sign_out(client, kept)

    nobody = await client.get("/api/instance/auth/me", cookies={"access_token": kept})
    assert nobody.status_code == 401
    assert nobody.json()["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_totp_confirmation_reads_the_token_that_authenticated_the_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A setup cookie of a signed-out session beside a Bearer session mints no session.

    The shared dependency takes the Bearer token before the cookie. The
    confirmation must read the same token to learn whether it completes a
    sign-in. A cookie nobody has checked must not decide that a new session
    cookie is set.
    """
    secret = pyotp.random_base32()
    user = await create_test_user(db_session, email="side-totp@example.com", password=PASSWORD, totp_secret=secret)
    bearer = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        session_version=user.session_version,
    )
    setup_cookie = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        session_version=user.session_version,
        purpose="2fa_setup",
    )
    sign_out_session(
        token_session_id(decode_token(setup_cookie), setup_cookie), datetime.now(UTC) + timedelta(minutes=5)
    )

    confirmed = await client.post(
        "/api/auth/2fa/totp/verify",
        json={"code": pyotp.TOTP(secret).now()},
        headers={"Authorization": f"Bearer {bearer}"},
        cookies={"access_token": setup_cookie},
    )

    assert confirmed.status_code == 200, confirmed.text
    assert _set_cookies_for(confirmed, "access_token") == []
    assert confirmed.json().get("status") != "success"  # the answer of a plain confirmation, not of a sign-in
