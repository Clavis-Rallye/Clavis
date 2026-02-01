"""
Integration tests for two-factor authentication (TOTP).

Tests cover:
- TOTP setup (secret generation, provisioning URI, recovery codes)
- TOTP verification (enabling 2FA)
- TOTP authentication (login with 2FA)
- Wrong TOTP code rejection
- Recovery code single-use
"""

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.totp import hash_recovery_code
from tests.factories import create_test_user, get_auth_cookies

# ============================================================================
# TOTP Setup Tests
# ============================================================================


@pytest.mark.asyncio
async def test_totp_setup_returns_secret_and_uri(client: AsyncClient, db_session: AsyncSession):
    """Setup returns a secret, an otpauth URI and eight recovery codes.

    It stores the secret and the codes, and does not enable the factor yet.
    """
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=False,
    )

    cookies = get_auth_cookies(user)

    response = await client.post(
        "/api/auth/2fa/totp/setup",
        cookies=cookies,
    )

    assert response.status_code == 200
    data = response.json()

    assert "secret" in data
    assert len(data["secret"]) == 32
    assert data["secret"].isalnum()  # Base32

    assert "provisioning_uri" in data
    assert data["provisioning_uri"].startswith("otpauth://totp/")
    import urllib.parse

    assert urllib.parse.quote(user.email, safe="") in data["provisioning_uri"]

    assert "recovery_codes" in data
    assert len(data["recovery_codes"]) == 8
    assert all(len(code) == 8 for code in data["recovery_codes"])
    assert all(code.isalnum() for code in data["recovery_codes"])

    assert "Save recovery codes securely" in data["message"]

    await db_session.refresh(user)
    assert user.totp_secret is not None
    assert user.totp_enabled is False
    assert user.totp_recovery_codes is not None
    assert len(user.totp_recovery_codes["codes"]) == 8


@pytest.mark.asyncio
async def test_totp_setup_fails_if_already_enabled(client: AsyncClient, db_session: AsyncSession):
    """Setup answers 409 when the factor is already enabled."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=True,
        totp_secret=secret,
    )

    cookies = get_auth_cookies(user)

    response = await client.post(
        "/api/auth/2fa/totp/setup",
        cookies=cookies,
    )

    assert response.status_code == 409
    data = response.json()
    assert "already enabled" in data["detail"].lower()


@pytest.mark.asyncio
async def test_totp_setup_requires_auth(client: AsyncClient, db_session: AsyncSession):
    """Setup needs a signed-in user."""
    response = await client.post("/api/auth/2fa/totp/setup")

    assert response.status_code == 401


# ============================================================================
# TOTP Verification Tests
# ============================================================================


@pytest.mark.asyncio
async def test_totp_verify_enables_2fa(client: AsyncClient, db_session: AsyncSession):
    """A correct code enables the factor with the secret of the setup."""
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=False,
    )

    cookies = get_auth_cookies(user)

    setup_response = await client.post("/api/auth/2fa/totp/setup", cookies=cookies)
    assert setup_response.status_code == 200
    setup_data = setup_response.json()
    secret = setup_data["secret"]

    totp = pyotp.TOTP(secret)
    code = totp.now()

    verify_response = await client.post(
        "/api/auth/2fa/totp/verify",
        json={"code": code},
        cookies=cookies,
    )

    assert verify_response.status_code == 200
    data = verify_response.json()
    assert "successfully" in data["message"].lower()
    assert "required" in data["message"].lower()

    await db_session.refresh(user)
    assert user.totp_enabled is True
    assert user.totp_secret == secret


@pytest.mark.asyncio
async def test_totp_verify_rejects_wrong_code(client: AsyncClient, db_session: AsyncSession):
    """A wrong code answers 400 and leaves the factor disabled."""
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=False,
    )

    cookies = get_auth_cookies(user)

    setup_response = await client.post("/api/auth/2fa/totp/setup", cookies=cookies)
    assert setup_response.status_code == 200

    verify_response = await client.post(
        "/api/auth/2fa/totp/verify",
        json={"code": "000000"},
        cookies=cookies,
    )

    assert verify_response.status_code == 400
    data = verify_response.json()
    assert "invalid" in data["detail"].lower()

    await db_session.refresh(user)
    assert user.totp_enabled is False


@pytest.mark.asyncio
async def test_totp_verify_fails_if_setup_not_initiated(client: AsyncClient, db_session: AsyncSession):
    """Verify answers 400 when no setup was started."""
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=False,
        totp_secret=None,
    )

    cookies = get_auth_cookies(user)

    verify_response = await client.post(
        "/api/auth/2fa/totp/verify",
        json={"code": "123456"},
        cookies=cookies,
    )

    assert verify_response.status_code == 400
    data = verify_response.json()
    assert "setup not initiated" in data["detail"].lower()


# ============================================================================
# TOTP Disable Tests
# ============================================================================


@pytest.mark.asyncio
async def test_totp_disable_with_correct_password(client: AsyncClient, db_session: AsyncSession):
    """The correct password disables the factor and deletes its secret and recovery codes."""
    secret = pyotp.random_base32()
    hashed_code = hash_recovery_code("ABC12345")
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": [hashed_code]},
    )

    cookies = get_auth_cookies(user)

    response = await client.request(
        "DELETE",
        "/api/auth/2fa/totp",
        json={"password": "SecurePass123!"},
        cookies=cookies,
    )

    assert response.status_code == 200
    data = response.json()
    assert "disabled" in data["message"].lower()

    await db_session.refresh(user)
    assert user.totp_enabled is False
    assert user.totp_secret is None
    assert user.totp_recovery_codes is None


@pytest.mark.asyncio
async def test_totp_disable_with_wrong_password(client: AsyncClient, db_session: AsyncSession):
    """A wrong password answers 403 and keeps the factor."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    cookies = get_auth_cookies(user)

    response = await client.request(
        "DELETE",
        "/api/auth/2fa/totp",
        json={"password": "WrongPassword123!"},
        cookies=cookies,
    )

    assert response.status_code == 403
    data = response.json()
    assert "invalid password" in data["detail"].lower()

    await db_session.refresh(user)
    assert user.totp_enabled is True
    assert user.totp_secret == secret


@pytest.mark.asyncio
async def test_totp_disable_when_not_enabled(client: AsyncClient, db_session: AsyncSession):
    """Disabling a factor that is not enabled answers 422."""
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=False,
        totp_secret=None,
    )

    cookies = get_auth_cookies(user)

    response = await client.request(
        "DELETE",
        "/api/auth/2fa/totp",
        json={"password": "SecurePass123!"},
        cookies=cookies,
    )

    assert response.status_code == 422
    data = response.json()
    assert "not enabled" in data["detail"].lower()


@pytest.mark.asyncio
async def test_totp_disable_requires_auth(client: AsyncClient, db_session: AsyncSession):
    """Disabling needs a signed-in user."""
    response = await client.request(
        "DELETE",
        "/api/auth/2fa/totp",
        json={"password": "SomePassword123!"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_no_longer_requires_2fa_after_disable(client: AsyncClient, db_session: AsyncSession):
    """After the factor is disabled, the login no longer asks for it."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    login_response1 = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )
    assert login_response1.status_code == 200
    assert login_response1.json()["status"] == "2fa_required"

    cookies = get_auth_cookies(user)
    disable_response = await client.request(
        "DELETE",
        "/api/auth/2fa/totp",
        json={"password": "SecurePass123!"},
        cookies=cookies,
    )
    assert disable_response.status_code == 200

    login_response2 = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )
    assert login_response2.status_code == 200
    assert login_response2.json()["status"] == "success"


# ============================================================================
# TOTP Authentication Tests
# ============================================================================


@pytest.mark.asyncio
async def test_totp_authenticate_with_correct_code(client: AsyncClient, db_session: AsyncSession):
    """A correct code completes the sign-in.

    A session cookie is set, last_login is written and the failed attempts are zero.
    """
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    totp = pyotp.TOTP(secret)
    code = totp.now()

    # The password step sets the proof cookie that the second factor needs.
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200
    assert login.json()["status"] == "2fa_required"

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={
            "email": user.email,
            "code": code,
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["email"] == user.email
    assert data["user_id"] == str(user.id)
    assert "successful" in data["message"].lower()

    assert "access_token" in response.cookies

    await db_session.refresh(user)
    assert user.last_login is not None
    assert user.failed_login_attempts == 0


@pytest.mark.asyncio
async def test_totp_authenticate_rejects_wrong_code(client: AsyncClient, db_session: AsyncSession):
    """A wrong code answers 401 and sets no session cookie."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    # The password step sets the proof cookie that the second factor needs.
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200
    assert login.json()["status"] == "2fa_required"

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={
            "email": user.email,
            "code": "000000",
        },
    )

    assert response.status_code == 401
    data = response.json()
    assert "invalid" in data["detail"].lower()

    assert "access_token" not in response.cookies


# ============================================================================
# Recovery Code Tests
# ============================================================================


@pytest.mark.asyncio
async def test_recovery_code_works_once(client: AsyncClient, db_session: AsyncSession):
    """A recovery code signs in once and is then removed from the account.

    A second attempt with it is refused already at the missing password step.
    """
    secret = pyotp.random_base32()
    recovery_code = "ABC12345"
    hashed_code = hash_recovery_code(recovery_code)

    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": [hashed_code]},
    )

    # The password step sets the proof cookie that the second factor needs.
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200
    assert login.json()["status"] == "2fa_required"

    response1 = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={
            "email": user.email,
            "code": recovery_code,
        },
    )

    assert response1.status_code == 200
    data1 = response1.json()

    assert data1["status"] == "success"
    assert data1["recovery_code_used"] is True
    assert data1["remaining_recovery_codes"] == 0
    assert "no recovery codes remaining" in data1["message"].lower()

    assert "access_token" in response1.cookies

    await db_session.refresh(user)
    assert len(user.totp_recovery_codes["codes"]) == 0

    # A second attempt is refused before the code is checked: the successful
    # sign-in used up the proof of the password step.
    response2 = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={
            "email": user.email,
            "code": recovery_code,
        },
    )

    assert response2.status_code == 401
    data2 = response2.json()
    assert "password verification required" in data2["detail"].lower()


@pytest.mark.asyncio
async def test_recovery_code_warns_when_low(client: AsyncClient, db_session: AsyncSession):
    """A sign-in with a recovery code reports the codes left, and warns when one is left."""
    secret = pyotp.random_base32()
    recovery_code1 = "ABC12345"
    recovery_code2 = "DEF67890"
    hashed_codes = [hash_recovery_code(recovery_code1), hash_recovery_code(recovery_code2)]

    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="SecurePass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": hashed_codes},
    )

    # The password step sets the proof cookie that the second factor needs.
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200
    assert login.json()["status"] == "2fa_required"

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={
            "email": user.email,
            "code": recovery_code1,
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["recovery_code_used"] is True
    assert data["remaining_recovery_codes"] == 1
    assert "only 1 recovery codes remaining" in data["message"].lower()


# ============================================================================
# 2FA Enforcement Tests (for org_admin)
# ============================================================================


@pytest.mark.asyncio
async def test_org_admin_login_without_totp_returns_2fa_setup_required(client: AsyncClient, db_session: AsyncSession):
    """An organization admin without a second factor gets a setup token and no session."""
    from app.models.user import UserRole

    user = await create_test_user(
        db_session,
        email="admin@example.com",
        password="SecurePass123!",
        role=UserRole.ORG_ADMIN,
        totp_enabled=False,
        totp_secret=None,
    )

    response = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "2fa_setup_required"
    assert "setup_token" in data
    assert data["setup_url"] == "/api/auth/2fa/totp"
    assert "must enable 2fa" in data["message"].lower()

    # The setup token comes in the body. No session cookie is set.
    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_non_admin_login_without_totp_works_normally(client: AsyncClient, db_session: AsyncSession):
    """A participant without a second factor signs in directly."""
    from app.models.user import UserRole

    participant = await create_test_user(
        db_session,
        email="participant@example.com",
        password="SecurePass123!",
        role=UserRole.PARTICIPANT,
        totp_enabled=False,
    )

    response = await client.post(
        "/api/auth/login",
        json={"email": participant.email, "password": "SecurePass123!"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["email"] == participant.email
    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_coordinator_login_without_totp_works_normally(client: AsyncClient, db_session: AsyncSession):
    """A coordinator without a second factor signs in directly."""
    from app.models.user import UserRole

    coordinator = await create_test_user(
        db_session,
        email="coordinator@example.com",
        password="SecurePass123!",
        role=UserRole.COORDINATOR,
        totp_enabled=False,
    )

    response = await client.post(
        "/api/auth/login",
        json={"email": coordinator.email, "password": "SecurePass123!"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["email"] == coordinator.email


@pytest.mark.asyncio
async def test_creator_login_without_totp_works_normally(client: AsyncClient, db_session: AsyncSession):
    """A creator without a second factor signs in directly."""
    from app.models.user import UserRole

    creator = await create_test_user(
        db_session,
        email="creator@example.com",
        password="SecurePass123!",
        role=UserRole.CREATOR,
        totp_enabled=False,
    )

    response = await client.post(
        "/api/auth/login",
        json={"email": creator.email, "password": "SecurePass123!"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["email"] == creator.email


@pytest.mark.asyncio
async def test_setup_token_only_works_for_2fa_endpoints(client: AsyncClient, db_session: AsyncSession):
    """The setup token reaches the setup route and is refused with 403 elsewhere."""
    from app.models.user import UserRole

    user = await create_test_user(
        db_session,
        email="admin@example.com",
        password="SecurePass123!",
        role=UserRole.ORG_ADMIN,
        totp_enabled=False,
        totp_secret=None,
    )

    login_response = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )

    assert login_response.status_code == 200
    setup_token = login_response.json()["setup_token"]

    setup_response = await client.post(
        "/api/auth/2fa/totp/setup",
        cookies={"access_token": setup_token},
    )
    assert setup_response.status_code == 200

    me_response = await client.get(
        "/api/auth/me",
        cookies={"access_token": setup_token},
    )
    assert me_response.status_code == 403
    assert "complete 2fa setup first" in me_response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_after_totp_setup_org_admin_gets_normal_token(client: AsyncClient, db_session: AsyncSession):
    """An organization admin who completes the setup with the setup token gets a normal session."""
    from app.models.user import UserRole

    user = await create_test_user(
        db_session,
        email="admin@example.com",
        password="SecurePass123!",
        role=UserRole.ORG_ADMIN,
        totp_enabled=False,
        totp_secret=None,
    )

    login_response = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )

    assert login_response.status_code == 200
    login_data = login_response.json()
    assert login_data["status"] == "2fa_setup_required"
    setup_token = login_data["setup_token"]

    setup_response = await client.post(
        "/api/auth/2fa/totp/setup",
        cookies={"access_token": setup_token},
    )

    assert setup_response.status_code == 200
    setup_data = setup_response.json()
    secret = setup_data["secret"]

    totp = pyotp.TOTP(secret)
    code = totp.now()

    verify_response = await client.post(
        "/api/auth/2fa/totp/verify",
        json={"code": code},
        cookies={"access_token": setup_token},
    )

    assert verify_response.status_code == 200
    verify_data = verify_response.json()

    assert verify_data["status"] == "success"
    assert verify_data["email"] == user.email
    assert verify_data["user_id"] == str(user.id)
    assert verify_data["role"] == "org_admin"
    assert "enabled successfully" in verify_data["message"].lower()

    assert "access_token" in verify_response.cookies

    me_response = await client.get(
        "/api/auth/me",
        cookies=verify_response.cookies,
    )

    assert me_response.status_code == 200
    me_data = me_response.json()
    assert me_data["email"] == user.email
    assert me_data["role"] == "org_admin"


@pytest.mark.asyncio
async def test_org_admin_with_totp_already_enabled_login_normally(client: AsyncClient, db_session: AsyncSession):
    """An organization admin with a second factor is asked for the code, not for a setup."""
    from app.models.user import UserRole

    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="admin@example.com",
        password="SecurePass123!",
        role=UserRole.ORG_ADMIN,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    response = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "2fa_required"
    assert data.get("requires_2fa") is True
    # The field may be present, but it has no value.
    assert data.get("setup_token") is None
    assert "totp code" in data["message"].lower()


@pytest.mark.asyncio
async def test_2fa_setup_token_on_restricted_path_returns_coded_403(client: AsyncClient, db_session: AsyncSession):
    """A setup token outside setup and verify gets 403 with error_code 2FA_SETUP_REQUIRED.

    The frontend branches on that code to send the user back to the setup with
    an explanation, so the shape of the body is part of the contract.
    """
    from app.models.user import UserRole

    user = await create_test_user(
        db_session,
        email="admin@example.com",
        password="SecurePass123!",
        role=UserRole.ORG_ADMIN,
        totp_enabled=False,
        totp_secret=None,
    )

    login_response = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "SecurePass123!"},
    )
    assert login_response.status_code == 200
    setup_token = login_response.json()["setup_token"]

    me_response = await client.get(
        "/api/auth/me",
        cookies={"access_token": setup_token},
    )

    assert me_response.status_code == 403
    body = me_response.json()
    assert body["detail"] == "Complete 2FA setup first"
    assert body["error_code"] == "2FA_SETUP_REQUIRED"


@pytest.mark.asyncio
async def test_login_with_new_password_after_change_completes_2fa_step(client: AsyncClient, db_session: AsyncSession):
    """After a password change the new password signs in again, through the second factor."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="changed@example.com",
        password="OldPassword123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    change = await client.post(
        "/api/auth/password-change",
        cookies=get_auth_cookies(user),
        json={"old_password": "OldPassword123!", "new_password": "NewPassword456!"},
    )
    assert change.status_code == 200

    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "NewPassword456!"},
    )
    assert login.status_code == 200
    assert login.json()["status"] == "2fa_required"

    totp_step = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
    )
    assert totp_step.status_code == 200
    assert totp_step.json()["status"] == "success"
    assert "access_token" in totp_step.cookies


# ============================================================================
# The second-factor sign-in against a password reset that commits meanwhile
# ============================================================================
#
# Each test orders the two requests by an event, not by a sleep: the password
# reset runs inside a read the sign-in awaits, so it has committed before the
# sign-in goes on with the account state it verified earlier.


def _run_once_after(monkeypatch, owner, name: str, action) -> None:  # noqa: ANN001
    """Run ``action`` once, right after the first call of the static read ``owner.name`` returned."""
    original = getattr(owner, name)
    fired = False

    async def read(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal fired
        result = await original(*args, **kwargs)
        if not fired:
            fired = True
            await action()
        return result

    monkeypatch.setattr(owner, name, staticmethod(read))


async def _password_reset_by_mail_link(concurrent_client: AsyncClient, db_session: AsyncSession, user):  # noqa: ANN001, ANN202
    """A reset link for ``user`` and the request that redeems it (it raises the session version)."""
    from app.repositories.password_reset import PasswordResetRepository

    raw_token = await PasswordResetRepository(db_session).create_token(user.id)
    await db_session.commit()

    async def reset() -> None:
        response = await concurrent_client.post(
            "/api/auth/password-reset",
            json={"token": raw_token, "new_password": "NewPassword456!"},
        )
        assert response.status_code == 200

    return reset


async def _assert_session_version(db_session: AsyncSession, user, expected: int) -> None:  # noqa: ANN001
    await db_session.refresh(user)
    assert user.session_version == expected


@pytest.mark.asyncio
async def test_totp_sign_in_gets_no_session_when_a_reset_commits_after_the_code_was_checked(
    concurrent_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A password reset that commits after the code was checked leaves the sign-in without a session.

    The refused sign-in is not recorded.
    """
    from app.repositories.organization import OrganizationRepository

    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="totp-vs-reset@example.com",
        password="OldPassword123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )
    await db_session.commit()
    reset = await _password_reset_by_mail_link(concurrent_client, db_session, user)

    login = await concurrent_client.post("/api/auth/login", json={"email": user.email, "password": "OldPassword123!"})
    assert login.json()["status"] == "2fa_required"

    # is_archived is the read between the check of the code and the minting.
    _run_once_after(monkeypatch, OrganizationRepository, "is_archived", reset)
    response = await concurrent_client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "PASSWORD_STEP_REQUIRED"
    assert "access_token" not in response.cookies
    await _assert_session_version(db_session, user, 1)
    # The refused sign-in is not recorded as one.
    assert user.last_login is None


@pytest.mark.asyncio
async def test_totp_setup_sign_in_gets_no_session_when_a_reset_commits_meanwhile(
    concurrent_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A password reset that commits during the setup leaves no session and the factor disabled."""
    from app.models.user import UserRole
    from app.repositories.user import UserRepository

    user = await create_test_user(
        db_session,
        email="setup-vs-reset@example.com",
        password="OldPassword123!",
        role=UserRole.ORG_ADMIN,
        totp_enabled=False,
        totp_secret=None,
    )
    await db_session.commit()
    reset = await _password_reset_by_mail_link(concurrent_client, db_session, user)

    login = await concurrent_client.post("/api/auth/login", json={"email": user.email, "password": "OldPassword123!"})
    setup_token = login.json()["setup_token"]
    concurrent_client.cookies.clear()
    setup = await concurrent_client.post("/api/auth/2fa/totp/setup", cookies={"access_token": setup_token})
    assert setup.status_code == 200

    # The read of get_current_user: the request holds the account from before the reset.
    _run_once_after(monkeypatch, UserRepository, "get_by_id_unscoped", reset)
    response = await concurrent_client.post(
        "/api/auth/2fa/totp/verify",
        json={"code": pyotp.TOTP(setup.json()["secret"]).now()},
        cookies={"access_token": setup_token},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "SESSION_REVOKED"
    assert "access_token" not in response.cookies
    await _assert_session_version(db_session, user, 1)
    assert user.totp_enabled is False
    assert user.last_login is None
