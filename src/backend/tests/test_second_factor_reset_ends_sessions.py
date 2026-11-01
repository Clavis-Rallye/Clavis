"""
A reset of the second factor ends the open sessions of that account.

``instance-admin reset-totp`` rotates the operator's TOTP secret and ends every open session of that
operator. It answers a lost or exposed authenticator, so a session opened with the old one ends with it.
The old session answers 401 ``SESSION_REVOKED``, the code the console turns into its session-ended notice.

When somebody else removes the second factor of an organization account, its session version rises.
The rise sits in ``anonymize_user``, so the immediate erasure by an org_admin and the daily retention
sweep both raise it.

A person who removes or renews the own second factor in the own session keeps that session.
"""

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.instance import create_instance_admin, reset_instance_admin_totp
from app.auth.tokens import INSTANCE_ADMIN_COOKIE_NAME
from app.models.user import UserRole
from app.services.gdpr import SYSTEM_RETENTION_ACTOR, anonymize_user
from tests.factories import create_org_admin_user, create_test_user, get_auth_cookies, get_auth_headers

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "OperatorPass123!"
USER_PASSWORD = "SecurePass123!"


async def _signed_in_operator(client: AsyncClient, db_session: AsyncSession):  # noqa: ANN202 — InstanceAdmin
    """Create an operator, confirm the TOTP setup and leave the session cookie on the client."""
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    assert admin.totp_secret is not None
    confirm = await client.post(
        "/api/instance/auth/totp/confirm",
        json={
            "email": OPERATOR_EMAIL,
            "password": OPERATOR_PASSWORD,
            "totp_code": pyotp.TOTP(admin.totp_secret).now(),
        },
    )
    assert confirm.status_code == 200
    assert INSTANCE_ADMIN_COOKIE_NAME in client.cookies
    return admin


# ============================================================================
# reset-totp ends the operator's open sessions
# ============================================================================


@pytest.mark.asyncio
async def test_reset_totp_ends_the_open_operator_session(client: AsyncClient, db_session: AsyncSession):
    """The session from before the reset answers 401 SESSION_REVOKED."""
    await _signed_in_operator(client, db_session)

    before = await client.get("/api/instance/auth/me")
    assert before.status_code == 200

    await reset_instance_admin_totp(db_session, OPERATOR_EMAIL)

    after = await client.get("/api/instance/auth/me")
    assert after.status_code == 401
    assert after.json()["error_code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_sign_in_after_reset_totp_enrolls_again_and_holds(client: AsyncClient, db_session: AsyncSession):
    """The reset ends the old sessions only. The new enrollment opens a session that holds."""
    await _signed_in_operator(client, db_session)
    reset = await reset_instance_admin_totp(db_session, OPERATOR_EMAIL)
    assert reset.totp_secret is not None
    client.cookies.clear()

    login = await client.post("/api/instance/auth/login", json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD})
    assert login.status_code == 200
    assert login.json()["provisioning_uri"] is not None

    confirm = await client.post(
        "/api/instance/auth/totp/confirm",
        json={
            "email": OPERATOR_EMAIL,
            "password": OPERATOR_PASSWORD,
            "totp_code": pyotp.TOTP(reset.totp_secret).now(),
        },
    )
    assert confirm.status_code == 200

    me = await client.get("/api/instance/auth/me")
    assert me.status_code == 200


# ============================================================================
# Somebody else removes the second factor of an organization account
# ============================================================================


@pytest.mark.asyncio
async def test_immediate_erasure_by_an_admin_raises_the_session_version(client: AsyncClient, db_session: AsyncSession):
    """The erasure clears the second factor of another account, so its session version rises."""
    admin, org = await create_org_admin_user(db_session, org_slug="sf-reset-erasure")
    target = await create_test_user(
        db_session,
        email="target@example.com",
        password=USER_PASSWORD,
        role=UserRole.COORDINATOR,
        organization=org,
        totp_enabled=True,
        totp_secret=pyotp.random_base32(),
        totp_recovery_codes={"codes": []},
    )
    version_before = target.session_version
    target_headers = get_auth_headers(target)
    assert (await client.get("/api/auth/me", headers=target_headers)).status_code == 200

    response = await client.delete(
        f"/api/orgs/{org.slug}/users/{target.id}?mode=immediate",
        headers=get_auth_headers(admin),
    )
    assert response.status_code == 204

    await db_session.refresh(target)
    assert target.totp_secret is None
    assert target.session_version == version_before + 1

    after = await client.get("/api/auth/me", headers=target_headers)
    assert after.status_code == 401


@pytest.mark.asyncio
async def test_anonymize_user_ends_the_session_of_an_account_with_a_passkey(
    client: AsyncClient, db_session: AsyncSession
):
    """
    ``anonymize_user`` raises the session version of a passkey account, and that version alone refuses its token.

    The call is the one of the retention sweep, with a system actor. The anonymization also deletes and
    deactivates the account, and each refuses the token before the session version is read. The test
    lifts both, so the last request meets only the session version and answers 401 ``SESSION_REVOKED``.
    """
    user = await create_test_user(
        db_session,
        email="swept@example.com",
        password=USER_PASSWORD,
        webauthn_credentials={
            "credentials": [{"id": "passkey-1", "public_key": "cHVibGlj", "sign_count": 3, "name": "Laptop"}]
        },
    )
    version_before = user.session_version
    headers = get_auth_headers(user)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200

    assert await anonymize_user(db_session, user.id, actor=dict(SYSTEM_RETENTION_ACTOR)) is True
    await db_session.commit()

    await db_session.refresh(user)
    assert user.webauthn_credentials is None
    assert user.totp_secret is None
    assert user.totp_recovery_codes is None
    assert user.session_version == version_before + 1
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401

    # Without the deletion and the deactivation the old token is still refused: by its version.
    user.is_deleted = False
    user.deleted_at = None
    user.is_active = True
    await db_session.commit()

    refused = await client.get("/api/auth/me", headers=headers)
    assert refused.status_code == 401
    assert refused.json()["error_code"] == "SESSION_REVOKED"


# ============================================================================
# The own second factor, in the own session
# ============================================================================


@pytest.mark.asyncio
async def test_disabling_the_own_totp_keeps_the_own_session(client: AsyncClient, db_session: AsyncSession):
    """``DELETE /api/auth/2fa/totp`` leaves the session version alone, and the session holds."""
    user = await create_test_user(
        db_session,
        email="own-disable@example.com",
        password=USER_PASSWORD,
        totp_enabled=True,
        totp_secret=pyotp.random_base32(),
        totp_recovery_codes={"codes": []},
    )
    version_before = user.session_version
    cookies = get_auth_cookies(user)

    response = await client.request("DELETE", "/api/auth/2fa/totp", json={"password": USER_PASSWORD}, cookies=cookies)
    assert response.status_code == 200

    await db_session.refresh(user)
    assert user.totp_secret is None
    assert user.session_version == version_before

    me = await client.get("/api/auth/me", cookies=cookies)
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_renewing_the_own_pending_totp_secret_keeps_the_own_session(
    client: AsyncClient, db_session: AsyncSession
):
    """A second ``POST /api/auth/2fa/totp/setup`` replaces the pending secret, and the session holds."""
    user = await create_test_user(db_session, email="own-renew@example.com", password=USER_PASSWORD)
    version_before = user.session_version
    cookies = get_auth_cookies(user)

    first = await client.post("/api/auth/2fa/totp/setup", cookies=cookies)
    assert first.status_code == 200
    second = await client.post("/api/auth/2fa/totp/setup", cookies=cookies)
    assert second.status_code == 200
    assert second.json()["secret"] != first.json()["secret"]

    await db_session.refresh(user)
    assert user.totp_secret == second.json()["secret"]
    assert user.session_version == version_before

    me = await client.get("/api/auth/me", cookies=cookies)
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_enabling_the_own_totp_keeps_the_own_session(client: AsyncClient, db_session: AsyncSession):
    """Setup plus verify in the own session leaves the session version alone."""
    user = await create_test_user(db_session, email="own-enable@example.com", password=USER_PASSWORD)
    version_before = user.session_version
    cookies = get_auth_cookies(user)

    setup = await client.post("/api/auth/2fa/totp/setup", cookies=cookies)
    assert setup.status_code == 200
    verify = await client.post(
        "/api/auth/2fa/totp/verify",
        json={"code": pyotp.TOTP(setup.json()["secret"]).now()},
        cookies=cookies,
    )
    assert verify.status_code == 200

    await db_session.refresh(user)
    assert user.totp_enabled is True
    assert user.session_version == version_before

    me = await client.get("/api/auth/me", cookies=cookies)
    assert me.status_code == 200
