"""
The sign-in tells an account's state only to someone who proved its password.

``POST /api/auth/2fa/totp/authenticate`` gives one answer to every request
without a valid proof of the password step. ``POST /api/auth/login`` gives a
disabled account the answer of any wrong password and says ``ACCOUNT_DISABLED``
only after the right one. ``ACCOUNT_LOCKED`` stays before the password check.
The audit events keep their reasons: only the answers to the client change.
"""

import importlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user import UserRepository
from tests.factories import create_test_user

PASSWORD = "SecurePass123!"
WRONG_PASSWORD = "WrongPass123!"
TOTP_URL = "/api/auth/2fa/totp/authenticate"
LOGIN_URL = "/api/auth/login"


def _answer(response: Response) -> tuple[int, Any, Any]:
    """What the client learns from a refusal: status, detail and error code."""
    body = response.json()
    return response.status_code, body.get("detail"), body.get("error_code")


def _record_failed_logins(monkeypatch: pytest.MonkeyPatch, module: str) -> list[str]:
    """The reasons of the user_login_failed audit events the route emits."""
    reasons: list[str] = []
    original: Callable[..., Any] = importlib.import_module(module).audit_user_login_failed

    def record(**kwargs: Any) -> Any:
        reasons.append(kwargs["reason"])
        return original(**kwargs)

    monkeypatch.setattr(f"{module}.audit_user_login_failed", record)
    return reasons


async def _totp_user(db_session: AsyncSession, email: str, *, is_active: bool = True) -> tuple[User, str]:
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email=email,
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
        is_active=is_active,
    )
    return user, secret


async def _lock(db_session: AsyncSession, user: User) -> None:
    await UserRepository(db_session, user.organization_id).update(
        user.id, locked_until=datetime.now(UTC) + timedelta(minutes=10)
    )
    await db_session.commit()


# --- totp/authenticate --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_refusals_without_a_valid_proof_give_one_answer(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Without a valid proof, the route answers alike for every account, and the audit keeps each reason."""
    reasons = _record_failed_logins(monkeypatch, "app.routers.two_factor")
    await create_test_user(db_session, email="tn-no-totp@example.com", password=PASSWORD)
    _, disabled_secret = await _totp_user(db_session, "tn-disabled@example.com", is_active=False)
    active, active_secret = await _totp_user(db_session, "tn-active@example.com")
    other, _ = await _totp_user(db_session, "tn-other@example.com")

    answers: dict[str, tuple[int, Any, Any]] = {}

    unknown = await client.post(TOTP_URL, json={"email": "tn-nobody@example.com", "code": "000000"})
    answers["unknown email"] = _answer(unknown)

    no_totp = await client.post(TOTP_URL, json={"email": "tn-no-totp@example.com", "code": "000000"})
    answers["account without TOTP"] = _answer(no_totp)

    disabled = await client.post(
        TOTP_URL, json={"email": "tn-disabled@example.com", "code": pyotp.TOTP(disabled_secret).now()}
    )
    answers["disabled account"] = _answer(disabled)

    no_proof = await client.post(TOTP_URL, json={"email": active.email, "code": pyotp.TOTP(active_secret).now()})
    answers["no proof cookie"] = _answer(no_proof)

    # The password step of another account sets a proof for that account only.
    login_other = await client.post(LOGIN_URL, json={"email": other.email, "password": PASSWORD})
    assert login_other.json()["status"] == "2fa_required"
    foreign = await client.post(TOTP_URL, json={"email": active.email, "code": pyotp.TOTP(active_secret).now()})
    answers["proof of another account"] = _answer(foreign)

    assert len(set(answers.values())) == 1, answers
    status_code, _, error_code = answers["no proof cookie"]
    assert status_code == 401
    assert error_code == "PASSWORD_STEP_REQUIRED"
    for response in (unknown, no_totp, disabled, no_proof, foreign):
        assert "access_token" not in response.cookies

    assert reasons == [
        "2fa_user_not_found",
        "2fa_not_enabled",
        "2fa_account_disabled",
        "2fa_missing_password_proof",
        "2fa_missing_password_proof",
    ]


@pytest.mark.asyncio
async def test_totp_locked_account_without_a_proof_gives_the_same_answer(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """A locked account without a proof answers like any request without a proof; the audit says it is locked."""
    reasons = _record_failed_logins(monkeypatch, "app.routers.two_factor")
    locked, secret = await _totp_user(db_session, "tn-locked@example.com")
    await _lock(db_session, locked)

    unknown = await client.post(TOTP_URL, json={"email": "tn-nobody-2@example.com", "code": "000000"})
    response = await client.post(TOTP_URL, json={"email": locked.email, "code": pyotp.TOTP(secret).now()})

    assert _answer(response) == _answer(unknown)
    assert reasons == ["2fa_user_not_found", "2fa_account_locked"]


@pytest.mark.asyncio
async def test_totp_with_a_valid_proof_a_right_code_signs_in(client: AsyncClient, db_session: AsyncSession):
    user, secret = await _totp_user(db_session, "tn-right-code@example.com")

    login = await client.post(LOGIN_URL, json={"email": user.email, "password": PASSWORD})
    assert login.json()["status"] == "2fa_required"

    response = await client.post(TOTP_URL, json={"email": user.email, "code": pyotp.TOTP(secret).now()})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_totp_with_a_valid_proof_a_wrong_code_counts_a_failed_attempt(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    reasons = _record_failed_logins(monkeypatch, "app.routers.two_factor")
    user, _ = await _totp_user(db_session, "tn-wrong-code@example.com")

    login = await client.post(LOGIN_URL, json={"email": user.email, "password": PASSWORD})
    assert login.json()["status"] == "2fa_required"

    response = await client.post(TOTP_URL, json={"email": user.email, "code": "000000"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid TOTP code"
    assert "access_token" not in response.cookies

    await db_session.refresh(user)
    assert user.failed_login_attempts == 1
    assert reasons == ["2fa_invalid_totp_code"]


# --- login --------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_wrong_password_answers_alike_for_a_missing_and_a_disabled_account(
    client: AsyncClient, db_session: AsyncSession
):
    await create_test_user(db_session, email="tn-login-disabled@example.com", password=PASSWORD, is_active=False)

    missing = await client.post(LOGIN_URL, json={"email": "tn-login-nobody@example.com", "password": WRONG_PASSWORD})
    disabled = await client.post(LOGIN_URL, json={"email": "tn-login-disabled@example.com", "password": WRONG_PASSWORD})

    assert _answer(disabled) == _answer(missing)
    assert _answer(missing)[0] == 401
    assert _answer(missing)[2] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_wrong_password_counts_on_a_disabled_account_as_on_an_active_one(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """A wrong password on a disabled account takes the path of any wrong password."""
    reasons = _record_failed_logins(monkeypatch, "app.routers.auth")
    active = await create_test_user(db_session, email="tn-count-active@example.com", password=PASSWORD)
    disabled = await create_test_user(
        db_session, email="tn-count-disabled@example.com", password=PASSWORD, is_active=False
    )

    await client.post(LOGIN_URL, json={"email": active.email, "password": WRONG_PASSWORD})
    await client.post(LOGIN_URL, json={"email": disabled.email, "password": WRONG_PASSWORD})

    await db_session.refresh(active)
    await db_session.refresh(disabled)
    assert disabled.failed_login_attempts == active.failed_login_attempts == 1
    assert reasons == ["invalid_password", "invalid_password"]


@pytest.mark.asyncio
async def test_login_right_password_on_a_disabled_account_says_disabled(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    reasons = _record_failed_logins(monkeypatch, "app.routers.auth")
    user = await create_test_user(db_session, email="tn-right-disabled@example.com", password=PASSWORD, is_active=False)

    response = await client.post(LOGIN_URL, json={"email": user.email, "password": PASSWORD})

    assert response.status_code == 401
    assert response.json()["error_code"] == "ACCOUNT_DISABLED"
    assert "access_token" not in response.cookies
    assert "second_factor_proof" not in response.cookies
    assert reasons == ["account_disabled"]


@pytest.mark.asyncio
async def test_login_locked_account_is_refused_before_the_password_check(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """The lock answers 423 with a right and a wrong password alike, and a wrong one counts nothing."""
    reasons = _record_failed_logins(monkeypatch, "app.routers.auth")
    user = await create_test_user(db_session, email="tn-locked-login@example.com", password=PASSWORD)
    await _lock(db_session, user)

    right = await client.post(LOGIN_URL, json={"email": user.email, "password": PASSWORD})
    wrong = await client.post(LOGIN_URL, json={"email": user.email, "password": WRONG_PASSWORD})

    for response in (right, wrong):
        assert response.status_code == 423
        assert response.json()["error_code"] == "ACCOUNT_LOCKED"
        assert "access_token" not in response.cookies
    await db_session.refresh(user)
    assert user.failed_login_attempts == 0
    assert reasons == ["account_locked", "account_locked"]
