"""
The second factor completes only a login that the password started.

``POST /api/auth/2fa/totp/authenticate`` takes an email plus a code and issues
a session. Without
proof that the password step of the login happened, a phished or guessed
six-digit code alone would be enough to sign in.

When ``POST /api/auth/login`` has checked the password of an account with a
second factor, it sets a short-lived signed proof in an httpOnly cookie
(``second_factor_proof``, at most ten minutes, bound to the user id, own
purpose claim). Both second-factor endpoints demand that proof for the account
named in the request and clear it on success.
"""

from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from httpx import AsyncClient
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import decode_second_factor_proof, decode_token
from tests.factories import create_test_user

PASSWORD = "SecurePass123!"


async def _login_with_password(client: AsyncClient, email: str, password: str = PASSWORD):
    """Run the password step of the login; returns the raw response."""
    return await client.post("/api/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_correct_totp_code_without_password_proof_is_refused(client: AsyncClient, db_session: AsyncSession):
    """
    A correct TOTP code alone completes no login.

    The code is valid, but no /api/auth/login preceded it on this client, so
    there is no proof cookie. The endpoint answers 401 and sets no session
    cookie.
    """
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="c1-totp@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
    )

    assert response.status_code == 401
    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_correct_recovery_code_without_password_proof_is_refused(client: AsyncClient, db_session: AsyncSession):
    """Recovery-code variant: a valid recovery code alone is not enough."""
    from app.auth.totp import hash_recovery_code

    user = await create_test_user(
        db_session,
        email="c1-recovery@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=pyotp.random_base32(),
        totp_recovery_codes={"codes": [hash_recovery_code("ABC12345")]},
    )

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": "ABC12345"},
    )

    assert response.status_code == 401
    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_login_sets_short_lived_proof_bound_to_the_account(client: AsyncClient, db_session: AsyncSession):
    """
    The password step sets the proof cookie.

    After /api/auth/login checked the password of a 2FA account, the response
    carries a ``second_factor_proof`` cookie: httpOnly, SameSite=lax, a life
    of at most ten minutes, and a payload bound to the user id with its own
    purpose claim.
    """
    user = await create_test_user(
        db_session,
        email="c2-proof@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=pyotp.random_base32(),
        totp_recovery_codes={"codes": []},
    )

    login = await _login_with_password(client, user.email)
    assert login.status_code == 200
    assert login.json()["status"] == "2fa_required"

    proof_cookie = login.cookies.get("second_factor_proof")
    assert proof_cookie, "login must set the second-factor proof cookie"

    set_cookie = next(h for h in login.headers.get_list("set-cookie") if "second_factor_proof" in h)
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()

    # The proof is signed with its own key: the session decoder must reject it.
    with pytest.raises(InvalidTokenError):
        decode_token(proof_cookie)

    payload = decode_second_factor_proof(proof_cookie)
    assert payload is not None
    assert payload["sub"] == str(user.id)
    assert payload["purpose"] == "2fa_proof"
    assert payload["exp"] - payload["iat"] <= 10 * 60


@pytest.mark.asyncio
async def test_same_browser_completes_login_and_proof_is_used_up(client: AsyncClient, db_session: AsyncSession):
    """
    The same browser completes the login with the code — exactly once.

    After the password step, the TOTP code completes the login and sets the
    session cookie; the proof cookie is cleared. A second attempt with
    another valid code and no fresh password step is refused.
    """
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="c2-flow@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    login = await _login_with_password(client, user.email)
    assert login.json()["status"] == "2fa_required"

    first = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "success"
    assert "access_token" in first.cookies
    assert not first.cookies.get("second_factor_proof"), "success must clear the proof"

    # The proof was used up: replaying the second factor without a new
    # password step completes no second login.
    replay = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
    )
    assert replay.status_code == 401
    assert "access_token" not in replay.cookies


@pytest.mark.asyncio
async def test_proof_of_another_account_does_not_count(client: AsyncClient, db_session: AsyncSession):
    """The proof is bound to the account: a proof minted for another
    user's password step does not satisfy this request."""
    secret_a = pyotp.random_base32()
    user_a = await create_test_user(
        db_session,
        email="c2-bound-a@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret_a,
        totp_recovery_codes={"codes": []},
    )
    secret_b = pyotp.random_base32()
    user_b = await create_test_user(
        db_session,
        email="c2-bound-b@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret_b,
        totp_recovery_codes={"codes": []},
    )

    login_a = await _login_with_password(client, user_a.email)
    assert login_a.json()["status"] == "2fa_required"

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user_b.email, "code": pyotp.TOTP(secret_b).now()},
    )
    assert response.status_code == 401
    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_proof_token_is_not_a_session(client: AsyncClient, db_session: AsyncSession):
    """The proof is a cookie for one step, not a session: presented as
    access_token (cookie or Bearer) it must not authenticate."""
    user = await create_test_user(
        db_session,
        email="c2-not-a-session@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=pyotp.random_base32(),
        totp_recovery_codes={"codes": []},
    )

    login = await _login_with_password(client, user.email)
    proof = login.cookies.get("second_factor_proof")
    assert proof

    me_via_cookie = await client.get("/api/auth/me", cookies={"access_token": proof})
    assert me_via_cookie.status_code == 401

    me_via_bearer = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {proof}"})
    assert me_via_bearer.status_code == 401


@pytest.mark.asyncio
async def test_wrong_code_counts_toward_the_same_lock_as_wrong_passwords(client: AsyncClient, db_session: AsyncSession):
    """A wrong code after the password step increments the same
    failed-attempt counter a wrong password increments."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="c3-count@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    await _login_with_password(client, user.email)
    wrong = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": "000000"},
    )
    assert wrong.status_code == 401

    await db_session.refresh(user)
    assert user.failed_login_attempts == 1


@pytest.mark.asyncio
async def test_locked_account_is_refused_before_any_code_is_checked(client: AsyncClient, db_session: AsyncSession):
    """A locked account gets 423 from the second-factor endpoint, even
    with a valid proof and a correct code."""
    from app.repositories.user import UserRepository

    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="c3-locked@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    login = await _login_with_password(client, user.email)
    assert login.json()["status"] == "2fa_required"

    # Lock the account directly (a wrong password would have done the same).
    user_repo = UserRepository(db_session, user.organization_id)
    await user_repo.update(user.id, locked_until=datetime.now(UTC) + timedelta(minutes=10))
    await db_session.commit()

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == 423
    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_enough_wrong_codes_lock_the_account(client: AsyncClient, db_session: AsyncSession):
    """Reaching the failed-attempt maximum with wrong codes locks the
    account. It is the same lock, and the locking attempt answers 423."""
    from app.config import get_settings

    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="c3-lockout@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )
    max_attempts = get_settings().auth.max_login_attempts

    await _login_with_password(client, user.email)
    last_status = None
    for _ in range(max_attempts):
        wrong = await client.post(
            "/api/auth/2fa/totp/authenticate",
            json={"email": user.email, "code": "000000"},
        )
        last_status = wrong.status_code

    # The attempt that reaches the threshold is answered with the lock (423),
    # like a wrong password that locks the account.
    assert last_status == 423
    await db_session.refresh(user)
    assert user.locked_until is not None


@pytest.mark.asyncio
async def test_totp_authenticate_is_rate_limited_per_client_address(client: AsyncClient, db_session: AsyncSession):
    """The endpoint has the same per-address rate limit as login."""
    # Unknown email keeps every request on the cheap 401 branch, so the
    # account lockout never interferes with counting the requests.
    for _ in range(10):
        response = await client.post(
            "/api/auth/2fa/totp/authenticate",
            json={"email": "nobody-rate@example.com", "code": "000000"},
        )
        assert response.status_code == 401

    eleventh = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": "nobody-rate@example.com", "code": "000000"},
    )
    assert eleventh.status_code == 429


@pytest.mark.asyncio
async def test_retained_proof_cookie_cannot_complete_a_second_login(client: AsyncClient, db_session: AsyncSession):
    """
    Deleting the cookie is not consumption. A client that keeps a copy of
    the proof cookie and replays it after a successful login — with another
    valid code — must be refused: the proof is single-use on the server.
    """
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="f1-replay@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    login = await _login_with_password(client, user.email)
    proof = login.cookies.get("second_factor_proof")
    assert proof

    first = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
    )
    assert first.status_code == 200

    replay = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
        cookies={"second_factor_proof": proof},
    )
    assert replay.status_code == 401
    assert "access_token" not in replay.cookies


@pytest.mark.asyncio
async def test_two_concurrent_logins_on_one_proof_yield_exactly_one_session(
    concurrent_client: AsyncClient, db_session: AsyncSession
):
    """
    Two requests race on the same proof — exactly one may get a session.
    The consumption is an atomic compare-and-swap on users.last_login, so the
    loser's UPDATE matches no row and it answers 401.
    """
    import asyncio

    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="f1-race@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    login = await _login_with_password(concurrent_client, user.email)
    proof = login.cookies.get("second_factor_proof")
    assert proof

    code = pyotp.TOTP(secret).now()
    first, second = await asyncio.gather(
        concurrent_client.post(
            "/api/auth/2fa/totp/authenticate",
            json={"email": user.email, "code": code},
            cookies={"second_factor_proof": proof},
        ),
        concurrent_client.post(
            "/api/auth/2fa/totp/authenticate",
            json={"email": user.email, "code": code},
            cookies={"second_factor_proof": proof},
        ),
    )

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 401]
    sessions = [r for r in (first, second) if "access_token" in r.cookies]
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_two_concurrent_recovery_code_logins_on_one_proof_yield_exactly_one_session(
    concurrent_client: AsyncClient, db_session: AsyncSession
):
    """
    The recovery-code path must not refresh the user object between proof validation
    and proof consumption. Otherwise the loser of the race adopts the winner's
    last_login and consumes the same proof a second time.

    Two concurrent requests with the same proof and the same recovery code yield
    exactly one session, and the loser does not burn the recovery code either.
    """
    import asyncio

    from app.auth.totp import hash_recovery_code

    user = await create_test_user(
        db_session,
        email="f1-race-recovery@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=pyotp.random_base32(),
        totp_recovery_codes={"codes": [hash_recovery_code("ZZZ98765")]},
    )

    login = await _login_with_password(concurrent_client, user.email)
    proof = login.cookies.get("second_factor_proof")
    assert proof

    first, second = await asyncio.gather(
        concurrent_client.post(
            "/api/auth/2fa/totp/authenticate",
            json={"email": user.email, "code": "ZZZ98765"},
            cookies={"second_factor_proof": proof},
        ),
        concurrent_client.post(
            "/api/auth/2fa/totp/authenticate",
            json={"email": user.email, "code": "ZZZ98765"},
            cookies={"second_factor_proof": proof},
        ),
    )

    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 401], f"both requests must not succeed: {statuses}"
    sessions = [r for r in (first, second) if "access_token" in r.cookies]
    assert len(sessions) == 1

    # The loser was rolled back whole: the recovery code survives its attempt
    # and is only gone because the winner used it.
    from app.repositories.user import UserRepository

    refreshed = await UserRepository.get_by_email_unscoped(db_session, user.email)
    assert refreshed is not None
    assert refreshed.totp_recovery_codes == {"codes": []}


@pytest.mark.asyncio
async def test_proof_cookie_authenticates_no_guest_join(client: AsyncClient, db_session: AsyncSession):
    """
    Presenting the proof as the session cookie to POST /api/join/<code>
    must not resolve the 2FA account — the join answers with a fresh guest
    identity, never with a session for the account.
    """
    from app.models.run import Run, RunState

    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="f2-join@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    run = Run(
        organization_id=user.organization_id,
        rallye_id=None,
        name="Proof Join Run",
        state=RunState.ACTIVE,
        join_code="PRFJN1",
        settings={},
        content_languages=["de"],
    )
    db_session.add(run)
    await db_session.commit()

    login = await _login_with_password(client, user.email)
    proof = login.cookies.get("second_factor_proof")
    assert proof

    join = await client.post(
        "/api/join/PRFJN1",
        json={"mode": "create", "display_name": "Guest", "team_name": "Proof Team"},
        cookies={"access_token": proof},
    )
    assert join.status_code == 201
    join_data = join.json()
    assert join_data["user_id"] != str(user.id), "the proof must not resolve the 2FA account"

    from app.models.user import UserRole

    payload = decode_token(join_data["token"])
    assert payload["sub"] != str(user.id)
    assert payload["role"] == UserRole.GUEST.value


@pytest.mark.asyncio
async def test_proof_token_opens_no_websocket(
    client: AsyncClient,
    db_session: AsyncSession,
    ws_client: AsyncClient,
):
    """The proof token passed as ?token= authenticates no WebSocket."""
    from httpx_ws import WebSocketDisconnect, aconnect_ws

    from app.models.run import Run, RunState

    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="f2-ws@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    run = Run(
        organization_id=user.organization_id,
        rallye_id=None,
        name="Proof WS Run",
        state=RunState.ACTIVE,
        join_code="PRFWS1",
        settings={},
        content_languages=["de"],
    )
    db_session.add(run)
    await db_session.commit()

    login = await _login_with_password(client, user.email)
    proof = login.cookies.get("second_factor_proof")
    assert proof

    slug = user.organization.slug
    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(
            f"/api/orgs/{slug}/runs/{run.id}/ws?token={proof}",
            ws_client,
        ):
            pass

    assert exc_info.value.code == 1008
    assert "Invalid token" in exc_info.value.reason
