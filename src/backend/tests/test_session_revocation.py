"""
Session revocation: a password change ends the account's other sessions.

Every token carries the account's ``session_version`` (claim ``sv``). Every password change raises the
version, so a token minted before it gets 401 ``SESSION_REVOKED`` at its next request. This holds for
an organization account's change, a reset by mail link, the operator console change and
``instance-admin set-password``. A WebSocket token is refused when it connects. The changing session
survives on the cookie of the change response. A token without the ``sv`` claim stays valid while the
account's version is 0.

A revoked session stays revoked under concurrent requests:

- Two revocations of one account at the same moment raise the version twice, so a session issued
  between them ends with the second one.
- A sign-in that proved the old password gets no session when a reset by mail link commits while it runs.
- A password change by a session that a reset revoked while the change ran answers 401
  ``SESSION_REVOKED`` and sets no cookie, for an operator and for an organization account.

A signed-out session is refused on ordinary requests.

These tests order the two requests by an event, never by a sleep. The first revocation runs inside
the read the second request waits for, so the second one holds the row from before the first.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pyotp
import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.instance import create_instance_admin, reset_instance_admin_totp, set_instance_admin_password
from app.auth.passwords import verify_password
from app.auth.tokens import create_access_token, create_instance_admin_token, decode_token
from app.config import get_settings
from app.models.instance_admin import InstanceAdmin
from app.models.user import User, UserRole
from app.repositories.instance_admin import InstanceAdminRepository
from app.repositories.invitation import InvitationRepository
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.user import UserRepository
from app.routers.invitations import _take_over
from app.services.gdpr import SYSTEM_RETENTION_ACTOR, anonymize_user
from tests.factories import create_test_user, create_test_user_with_org

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "OperatorPass123!"


def _session_token(user) -> str:  # noqa: ANN001 — User model, no import cycle wanted here
    """A normal access token for the user, minted at the account's current version."""
    return create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        session_version=user.session_version,
    )


def _token_without_sv_claim(user) -> str:  # noqa: ANN001
    """A token without the ``sv`` claim, shaped like one that older releases issued."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "org_id": str(user.organization_id),
        "exp": now + timedelta(minutes=30),
        "iat": now,
    }
    return jwt.encode(payload, get_settings().auth.jwt_secret_key, algorithm="HS256")


async def _confirmed_operator(client: AsyncClient, db_session: AsyncSession, email: str = OPERATOR_EMAIL):  # noqa: ANN201
    """Create an operator and walk them through TOTP confirmation + login."""
    admin = await create_instance_admin(db_session, email, OPERATOR_PASSWORD)
    login = await client.post("/api/instance/auth/login", json={"email": email, "password": OPERATOR_PASSWORD})
    assert login.status_code == 200
    assert admin.totp_secret is not None
    confirm = await client.post(
        "/api/instance/auth/totp/confirm",
        json={"email": email, "password": OPERATOR_PASSWORD, "totp_code": pyotp.TOTP(admin.totp_secret).now()},
    )
    assert confirm.status_code == 200
    return admin


def _set_cookies_for(response, name: str) -> list[str]:  # noqa: ANN001
    """Every ``Set-Cookie`` header of the response that sets ``name``, in order."""
    return [header for header in response.headers.get_list("set-cookie") if header.startswith(f"{name}=")]


# ============================================================================
# An organization account's password change ends its other session
# ============================================================================


@pytest.mark.asyncio
async def test_password_change_revokes_other_session_of_same_account(client: AsyncClient, db_session: AsyncSession):
    """After one session changes the password, the other gets 401 SESSION_REVOKED.

    The changing one keeps working on the cookie of the change response, which carries the new version.
    """
    user = await create_test_user(db_session, email="two-sessions@example.com", password="OldPassword123!")

    session_a = _session_token(user)
    session_b = _session_token(user)

    change = await client.post(
        "/api/auth/password-change",
        cookies={"access_token": session_b},
        json={"old_password": "OldPassword123!", "new_password": "NewPassword456!"},
    )
    assert change.status_code == 200

    other = await client.get("/api/auth/me", cookies={"access_token": session_a})
    assert other.status_code == 401
    assert other.json()["error_code"] == "SESSION_REVOKED"
    assert other.headers["WWW-Authenticate"] == "Bearer"

    # The access_token Set-Cookie of the change comes last, after the sliding-window cookie
    # that get_current_user set earlier in the same request.
    access_token_cookies = _set_cookies_for(change, "access_token")
    assert len(access_token_cookies) >= 2
    sliding = decode_token(access_token_cookies[0].split(";")[0].removeprefix("access_token="))
    replacement = decode_token(access_token_cookies[-1].split(";")[0].removeprefix("access_token="))
    assert sliding["sv"] == 0
    assert replacement["sv"] == 1

    me = await client.get("/api/auth/me", cookies=change.cookies)
    assert me.status_code == 200
    assert me.json()["email"] == "two-sessions@example.com"


# ============================================================================
# A reset by mail link ends every session issued before it
# ============================================================================


@pytest.mark.asyncio
async def test_password_reset_by_mail_link_revokes_prior_sessions(client: AsyncClient, db_session: AsyncSession):
    user = await create_test_user(db_session, email="reset-revokes@example.com", password="OldPassword123!")
    prior_session = _session_token(user)

    # The repository creates the token that the reset mail renders into its link.
    reset_repo = PasswordResetRepository(db_session)
    raw_token = await reset_repo.create_token(user.id)
    await db_session.commit()

    response = await client.post(
        "/api/auth/password-reset",
        json={"token": raw_token, "new_password": "NewPassword456!"},
    )
    assert response.status_code == 200

    me = await client.get("/api/auth/me", cookies={"access_token": prior_session})
    assert me.status_code == 401
    assert me.json()["error_code"] == "SESSION_REVOKED"


# ============================================================================
# Operator sessions: console change and set-password
# ============================================================================


@pytest.mark.asyncio
async def test_operator_console_password_change_revokes_other_session(client: AsyncClient, db_session: AsyncSession):
    admin = await _confirmed_operator(client, db_session)
    session_a = client.cookies.get("instance_token")
    assert session_a is not None
    session_b = create_instance_admin_token(admin.id, session_version=admin.session_version)

    change = await client.post(
        "/api/instance/auth/password",
        cookies={"instance_token": session_b},
        json={"old_password": OPERATOR_PASSWORD, "new_password": "BrandNewPass456!"},
    )
    assert change.status_code == 200

    other = await client.get("/api/instance/auth/me", cookies={"instance_token": session_a})
    assert other.status_code == 401
    assert other.json()["error_code"] == "SESSION_REVOKED"

    me = await client.get("/api/instance/auth/me", cookies=change.cookies)
    assert me.status_code == 200
    assert me.json()["email"] == OPERATOR_EMAIL


@pytest.mark.asyncio
async def test_operator_set_password_command_revokes_all_sessions(client: AsyncClient, db_session: AsyncSession):
    """``instance-admin set-password`` ends every session of the operator."""
    admin = await _confirmed_operator(client, db_session)
    # A second session beyond the cookie in the client jar.
    bearer_session = create_instance_admin_token(admin.id, session_version=admin.session_version)

    await set_instance_admin_password(db_session, OPERATOR_EMAIL, "BrandNewPass456!")

    me_cookie = await client.get("/api/instance/auth/me")
    assert me_cookie.status_code == 401
    assert me_cookie.json()["error_code"] == "SESSION_REVOKED"

    me_bearer = await client.get(
        "/api/instance/auth/me",
        headers={"Authorization": f"Bearer {bearer_session}"},
    )
    assert me_bearer.status_code == 401
    assert me_bearer.json()["error_code"] == "SESSION_REVOKED"


# ============================================================================
# A WebSocket token minted before the change is refused on connect
# ============================================================================


@pytest.mark.asyncio
async def test_ws_token_minted_before_change_refused_on_connect(
    client: AsyncClient, db_session: AsyncSession, ws_client: AsyncClient
):
    user, org = await create_test_user_with_org(
        db_session,
        email="ws-revoked@example.com",
        password="OldPassword123!",
        org_slug="ws-revoked-org",
    )

    session_cookie = {"access_token": _session_token(user)}
    ws_token_response = await client.get("/api/auth/ws-token", cookies=session_cookie)
    assert ws_token_response.status_code == 200
    ws_token = ws_token_response.json()["token"]

    change = await client.post(
        "/api/auth/password-change",
        cookies=session_cookie,
        json={"old_password": "OldPassword123!", "new_password": "NewPassword456!"},
    )
    assert change.status_code == 200

    # ws_authenticate rejects before the run is resolved, so any run UUID
    # reaches the session check.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(f"/api/orgs/{org.slug}/runs/{uuid4()}/ws?token={ws_token}", ws_client):
            pass

    assert exc_info.value.code == 1008
    assert exc_info.value.reason == "Session revoked"


# ============================================================================
# A token without the sv claim survives until the account's first change
# ============================================================================


@pytest.mark.asyncio
async def test_token_without_sv_claim_valid_until_first_change(client: AsyncClient, db_session: AsyncSession):
    user = await create_test_user(db_session, email="legacy-token@example.com", password="OldPassword123!")
    legacy_token = _token_without_sv_claim(user)

    me = await client.get("/api/auth/me", cookies={"access_token": legacy_token})
    assert me.status_code == 200

    change = await client.post(
        "/api/auth/password-change",
        cookies={"access_token": _session_token(user)},
        json={"old_password": "OldPassword123!", "new_password": "NewPassword456!"},
    )
    assert change.status_code == 200

    refused = await client.get("/api/auth/me", cookies={"access_token": legacy_token})
    assert refused.status_code == 401
    assert refused.json()["error_code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_a_deactivated_account_is_told_by_code(client: AsyncClient, db_session: AsyncSession):
    """A deactivated person's open session gets 401 ACCOUNT_DISABLED, so the app can say why instead of a bare sign-in."""
    user = await create_test_user(db_session, email="leaver@example.com", password="Password123!")
    token = _session_token(user)
    assert (await client.get("/api/auth/me", cookies={"access_token": token})).status_code == 200

    user.is_active = False
    await db_session.commit()

    client.cookies.clear()
    refused = await client.get("/api/auth/me", cookies={"access_token": token})
    assert refused.status_code == 401
    assert refused.json()["error_code"] == "ACCOUNT_DISABLED"
    assert refused.headers["WWW-Authenticate"] == "Bearer"


# ============================================================================
# Two revocations at the same moment
# ============================================================================


def _run_once_after_the_read(monkeypatch, owner, name: str, action, *, static: bool = False) -> None:  # noqa: ANN001
    """Run ``action`` once, right after the first call of the read ``owner.name`` returned.

    The request that made that read goes on with the row from before ``action``. That is the
    interleaving of two requests at the same moment.
    """
    original = getattr(owner, name)
    fired = False

    async def read(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal fired
        result = await original(*args, **kwargs)
        if not fired:
            fired = True
            await action()
        return result

    monkeypatch.setattr(owner, name, staticmethod(read) if static else read)


@pytest.mark.asyncio
async def test_two_operator_resets_at_the_same_moment_end_a_session_issued_between_them(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch
):
    """The second reset read the row before the first committed. It still ends the session issued after the first."""
    admin = await _confirmed_operator(client, db_session)
    client.cookies.clear()
    other_session = async_sessionmaker(test_engine, expire_on_commit=False)
    between: dict[str, str] = {}

    async def first_reset_then_a_new_session() -> None:
        async with other_session() as session:
            updated = await reset_instance_admin_totp(session, OPERATOR_EMAIL)
            between["token"] = create_instance_admin_token(updated.id, session_version=updated.session_version)

    _run_once_after_the_read(monkeypatch, InstanceAdminRepository, "get_by_email", first_reset_then_a_new_session)
    async with other_session() as session:
        await reset_instance_admin_totp(session, OPERATOR_EMAIL)

    # The test session still holds the row from before both resets.
    await db_session.refresh(admin)
    assert admin.session_version == 2
    me = await client.get("/api/instance/auth/me", headers={"Authorization": f"Bearer {between['token']}"})
    assert me.status_code == 401
    assert me.json()["error_code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_operator_set_password_raises_the_version_a_concurrent_reset_already_raised(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch
):
    """``set-password`` read the row before a reset committed, and the version rises twice."""
    admin = await _confirmed_operator(client, db_session)
    other_session = async_sessionmaker(test_engine, expire_on_commit=False)

    async def reset() -> None:
        async with other_session() as session:
            await reset_instance_admin_totp(session, OPERATOR_EMAIL)

    _run_once_after_the_read(monkeypatch, InstanceAdminRepository, "get_by_email", reset)
    async with other_session() as session:
        await set_instance_admin_password(session, OPERATOR_EMAIL, "BrandNewPass456!")

    async with other_session() as session:
        version = (
            await session.execute(select(InstanceAdmin.session_version).where(InstanceAdmin.id == admin.id))
        ).scalar_one()
    assert version == 2


@pytest.mark.asyncio
async def test_operator_console_password_change_by_a_session_revoked_meanwhile_mints_no_session(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch
):
    """The token was accepted for version v, and a reset committed before the change wrote.

    The reset ended that session, and the change gives it no new one."""
    admin_id = (await _confirmed_operator(client, db_session)).id
    other_session = async_sessionmaker(test_engine, expire_on_commit=False)

    async def reset() -> None:
        async with other_session() as session:
            await reset_instance_admin_totp(session, OPERATOR_EMAIL)

    # require_instance_admin reads the operator by id; the reset commits right after it.
    _run_once_after_the_read(monkeypatch, InstanceAdminRepository, "get_by_id", reset)
    change = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": "BrandNewPass456!"},
    )

    assert change.status_code == 401
    assert change.json()["error_code"] == "SESSION_REVOKED"
    assert change.headers.get_list("set-cookie") == []
    async with other_session() as session:
        row = (await session.execute(select(InstanceAdmin).where(InstanceAdmin.id == admin_id))).scalar_one()
    # Only the reset raised the version, and the password is the old one.
    assert row.session_version == 1
    assert verify_password(OPERATOR_PASSWORD, row.password_hash)


@pytest.mark.asyncio
async def test_reset_and_password_change_at_the_same_moment_end_the_session_issued_between_them(
    concurrent_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Organization account: the reset by mail link read the account before a password change committed.

    The change kept its own session alive on a new cookie, and the reset ends it."""
    user = await create_test_user(db_session, email="two-revocations@example.com", password="OldPassword123!")
    raw_token = await PasswordResetRepository(db_session).create_token(user.id)
    await db_session.commit()
    session_cookie = {"access_token": _session_token(user)}
    between: dict[str, str] = {}

    async def password_change() -> None:
        change = await concurrent_client.post(
            "/api/auth/password-change",
            cookies=session_cookie,
            json={"old_password": "OldPassword123!", "new_password": "NewPassword456!"},
        )
        assert change.status_code == 200
        between["token"] = _set_cookies_for(change, "access_token")[-1].split(";")[0].removeprefix("access_token=")

    _run_once_after_the_read(monkeypatch, UserRepository, "get_by_id_unscoped", password_change, static=True)
    reset = await concurrent_client.post(
        "/api/auth/password-reset",
        json={"token": raw_token, "new_password": "ThirdPassword789!"},
    )
    assert reset.status_code == 200
    concurrent_client.cookies.clear()

    me = await concurrent_client.get("/api/auth/me", cookies={"access_token": between["token"]})
    assert me.status_code == 401
    assert me.json()["error_code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_password_change_by_a_session_revoked_meanwhile_mints_no_session(
    concurrent_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """Organization account: the token was accepted for version v, and a reset by mail link committed before the change wrote.

    The change answers 401 and sets no cookie."""
    user = await create_test_user(db_session, email="change-after-reset@example.com", password="OldPassword123!")
    raw_token = await PasswordResetRepository(db_session).create_token(user.id)
    await db_session.commit()

    async def reset() -> None:
        response = await concurrent_client.post(
            "/api/auth/password-reset",
            json={"token": raw_token, "new_password": "ThirdPassword789!"},
        )
        assert response.status_code == 200

    # get_current_user reads the account; the reset commits right after it.
    _run_once_after_the_read(monkeypatch, UserRepository, "get_by_id_unscoped", reset, static=True)
    change = await concurrent_client.post(
        "/api/auth/password-change",
        cookies={"access_token": _session_token(user)},
        json={"old_password": "OldPassword123!", "new_password": "NewPassword456!"},
    )

    assert change.status_code == 401
    assert change.json()["error_code"] == "SESSION_REVOKED"
    assert change.headers.get_list("set-cookie") == []
    await db_session.refresh(user)
    # Only the reset raised the version, and the password is the one the reset set.
    assert user.session_version == 1
    assert verify_password("ThirdPassword789!", user.password_hash)


async def _raise_version_elsewhere(test_engine: AsyncEngine, user_id) -> None:  # noqa: ANN001
    """Another request's revocation: it commits in a session of its own."""
    async with async_sessionmaker(test_engine)() as session:
        await session.execute(update(User).where(User.id == user_id).values(session_version=User.session_version + 1))
        await session.commit()


@pytest.mark.asyncio
async def test_anonymization_raises_the_version_a_concurrent_revocation_already_raised(
    db_session: AsyncSession, test_engine: AsyncEngine
):
    """``anonymize_user`` holds the account from before another revocation, and the version rises twice."""
    user = await create_test_user(db_session, email="swept-twice@example.com", password="OldPassword123!")
    assert user.session_version == 0

    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        held = await session.get(User, user.id)  # this session's read; the reference keeps the row loaded
        assert held is not None and held.session_version == 0
        await _raise_version_elsewhere(test_engine, user.id)
        assert await anonymize_user(session, user.id, actor=dict(SYSTEM_RETENTION_ACTOR)) is True
        await session.commit()

    await db_session.refresh(user)
    assert user.session_version == 2


@pytest.mark.asyncio
async def test_takeover_raises_the_version_a_concurrent_revocation_already_raised(
    db_session: AsyncSession, test_engine: AsyncEngine
):
    """The takeover holds the replaced admin from before another revocation, and the version rises twice."""
    old_admin, org = await create_test_user_with_org(
        db_session, email="replaced@example.com", role=UserRole.ORG_ADMIN, org_slug="takeover-twice"
    )
    invitation, _ = await InvitationRepository(db_session).create(
        organization_id=org.id,
        email="successor@example.com",
        role=UserRole.ORG_ADMIN,
        invited_by_user_id=None,
        invited_by_name="Clavis",
        expiry_days=7,
        language="de",
    )
    invitation.replaces_user_ids = [str(old_admin.id)]
    await db_session.commit()

    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        held = await session.get(User, old_admin.id)  # this session's read; the reference keeps the row loaded
        assert held is not None and held.session_version == 0
        await _raise_version_elsewhere(test_engine, old_admin.id)
        replaced = await _take_over(session, invitation, uuid4())
        await session.commit()
        assert [user.id for user in replaced] == [old_admin.id]

    await db_session.refresh(old_admin)
    assert old_admin.is_active is False
    assert old_admin.session_version == 2


# ============================================================================
# Organization account: a sign-in against a reset
# ============================================================================


@pytest.mark.asyncio
async def test_sign_in_with_the_old_password_gets_no_session_when_a_reset_commits_meanwhile(
    concurrent_client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """The sign-in read the account, a reset by mail link committed, the old password is
    proven against the row from before: no cookie is minted."""
    user = await create_test_user(db_session, email="login-vs-reset@example.com", password="OldPassword123!")
    raw_token = await PasswordResetRepository(db_session).create_token(user.id)
    await db_session.commit()

    async def reset() -> None:
        response = await concurrent_client.post(
            "/api/auth/password-reset",
            json={"token": raw_token, "new_password": "NewPassword456!"},
        )
        assert response.status_code == 200

    _run_once_after_the_read(monkeypatch, UserRepository, "get_by_email_unscoped", reset, static=True)
    login = await concurrent_client.post(
        "/api/auth/login",
        json={"email": "login-vs-reset@example.com", "password": "OldPassword123!"},
    )

    assert login.status_code == 401
    assert login.json()["error_code"] == "INVALID_CREDENTIALS"
    assert "access_token" not in login.cookies


# ============================================================================
# A signed-out session is refused on ordinary requests
# ============================================================================


async def _sign_in(client: AsyncClient, email: str, password: str) -> str:
    """Sign in and return the session cookie; the client's jar is left empty."""
    login = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.cookies["access_token"]
    client.cookies.clear()
    return token


@pytest.mark.asyncio
async def test_a_signed_out_session_is_refused_and_another_session_of_the_account_passes(
    client: AsyncClient, db_session: AsyncSession
):
    """A signed-out token gets 401 as cookie, as Bearer and as its slid copy. Another session of the account stays.

    A sign-out clears the cookie, but the token stays a valid signature. The record of the signed-out
    session is what refuses it. The copy the sliding cookie minted before the sign-out is the same session.
    """
    await create_test_user(db_session, email="signed-out@example.com", password="TestPassword123!")
    kept = await _sign_in(client, "signed-out@example.com", "TestPassword123!")
    other_device = await _sign_in(client, "signed-out@example.com", "TestPassword123!")

    before = await client.get("/api/auth/me", cookies={"access_token": kept})
    assert before.status_code == 200
    slid = before.cookies["access_token"]
    client.cookies.clear()

    signed_out = await client.post("/api/auth/logout", cookies={"access_token": kept})
    assert signed_out.status_code == 200, signed_out.text
    client.cookies.clear()

    for attempt in (
        {"cookies": {"access_token": kept}},
        {"cookies": {"access_token": slid}},
        {"headers": {"Authorization": f"Bearer {kept}"}},
    ):
        refused = await client.get("/api/auth/me", **attempt)
        assert refused.status_code == 401, attempt
        assert refused.json()["error_code"] == "SESSION_REVOKED"
        assert refused.headers["WWW-Authenticate"] == "Bearer"
        assert _set_cookies_for(refused, "access_token") == []  # a refused session is not slid on
        client.cookies.clear()

    still_in = await client.get("/api/auth/me", cookies={"access_token": other_device})
    assert still_in.status_code == 200
