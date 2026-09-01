"""
Instance-operator auth.

Covers the whole operator flow: CLI-side account creation (the plain async
function), first login with the one-time TOTP provisioning URI, confirmation,
code-required login with the instance-scoped cookie, the token separation
between operator and organization sessions, rate limiting and the audit
events ``instance.login`` / ``instance.login_failed``.
"""

from datetime import timedelta

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.instance import (
    InstanceAdminExistsError,
    InstanceAdminPasswordPolicyError,
    create_instance_admin,
    disable_instance_admin,
    reset_instance_admin_totp,
)
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import INSTANCE_ADMIN_COOKIE_NAME, create_access_token, create_instance_admin_token, decode_token
from app.models.instance_admin import InstanceAdmin
from app.repositories.instance_admin import InstanceAdminRepository
from app.routers import instance_auth as instance_auth_router
from tests.factories import create_test_user_with_org

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "OperatorPass123!"


def _totp_code(admin) -> str:  # noqa: ANN001 — InstanceAdmin model, no import cycle wanted here
    """Current 6-digit code for the admin's stored TOTP secret."""
    assert admin.totp_secret is not None
    return pyotp.TOTP(admin.totp_secret).now()


async def _confirmed_admin(client: AsyncClient, db_session: AsyncSession, email: str = OPERATOR_EMAIL):
    """Create an operator and walk them through TOTP confirmation + login."""
    admin = await create_instance_admin(db_session, email, OPERATOR_PASSWORD)
    login = await client.post("/api/instance/auth/login", json={"email": email, "password": OPERATOR_PASSWORD})
    assert login.status_code == 200
    confirm = await client.post(
        "/api/instance/auth/totp/confirm",
        json={"email": email, "password": OPERATOR_PASSWORD, "totp_code": _totp_code(admin)},
    )
    assert confirm.status_code == 200
    return admin


# ============================================================================
# Account creation (CLI path, never an HTTP route)
# ============================================================================


@pytest.mark.asyncio
async def test_create_instance_admin_argon2_hash_and_pending_totp(db_session: AsyncSession):
    """The creation function stores an Argon2id hash and a pending TOTP setup."""
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert admin.email == OPERATOR_EMAIL
    assert admin.password_hash.startswith("$argon2id")
    assert verify_password(OPERATOR_PASSWORD, admin.password_hash)
    assert admin.totp_secret  # generated at creation so the first login can show the URI
    assert admin.totp_confirmed_at is None  # setup pending
    assert admin.last_login_at is None


@pytest.mark.asyncio
async def test_create_instance_admin_normalizes_email(db_session: AsyncSession):
    """Mixed-case input is stored lowercased, like user emails."""
    admin = await create_instance_admin(db_session, "Operator@Example.COM", OPERATOR_PASSWORD)
    assert admin.email == "operator@example.com"


@pytest.mark.asyncio
async def test_create_instance_admin_refuses_duplicate_email(db_session: AsyncSession):
    await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    with pytest.raises(InstanceAdminExistsError):
        await create_instance_admin(db_session, OPERATOR_EMAIL, "AnotherPass123!")


@pytest.mark.asyncio
async def test_create_instance_admin_enforces_password_policy(db_session: AsyncSession):
    with pytest.raises(InstanceAdminPasswordPolicyError) as exc_info:
        await create_instance_admin(db_session, "weak@example.com", "weak")
    assert exc_info.value.errors  # policy messages carried for the CLI to print


# ============================================================================
# Login flow: provisioning URI once, confirm, code required afterwards
# ============================================================================


@pytest.mark.asyncio
async def test_first_login_returns_provisioning_uri_once(client: AsyncClient, db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    first = await client.post("/api/instance/auth/login", json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD})
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "totp_setup_required"
    assert body["totp_setup_required"] is True
    assert body["provisioning_uri"] is not None
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert pyotp.parse_uri(body["provisioning_uri"]).secret == admin.totp_secret

    # No session is issued while the setup is pending.
    assert "instance_token" not in client.cookies

    # Second login with the setup still unconfirmed: the URI — a standing
    # enrollment secret — is never handed out twice.
    second = await client.post(
        "/api/instance/auth/login", json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD}
    )
    assert second.status_code == 200
    assert second.json()["provisioning_uri"] is None


@pytest.mark.asyncio
async def test_totp_confirm_wrong_code_rejected(client: AsyncClient, db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    response = await client.post(
        "/api/instance/auth/totp/confirm",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": "000000"},
    )
    assert response.status_code == 401
    await db_session.refresh(admin)
    assert admin.totp_confirmed_at is None


@pytest.mark.asyncio
async def test_totp_confirm_wrong_password_rejected(client: AsyncClient, db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    response = await client.post(
        "/api/instance/auth/totp/confirm",
        json={"email": OPERATOR_EMAIL, "password": "WrongPassword123!", "totp_code": _totp_code(admin)},
    )
    assert response.status_code == 401
    await db_session.refresh(admin)
    assert admin.totp_confirmed_at is None


@pytest.mark.asyncio
async def test_totp_confirm_completes_enrollment_and_sets_session(client: AsyncClient, db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    response = await client.post(
        "/api/instance/auth/totp/confirm",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": _totp_code(admin)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["email"] == OPERATOR_EMAIL

    await db_session.refresh(admin)
    assert admin.totp_confirmed_at is not None

    token = client.cookies.get("instance_token")
    assert token is not None
    payload = decode_token(token)
    assert payload["scope"] == "instance"
    assert payload["sub"] == str(admin.id)
    assert "org_id" not in payload
    assert "role" not in payload


@pytest.mark.asyncio
async def test_login_after_confirmation_requires_valid_code(client: AsyncClient, db_session: AsyncSession):
    admin = await _confirmed_admin(client, db_session)

    # Missing code and wrong code answer the same generic 401 — the response
    # must not reveal which factor failed.
    no_code = await client.post(
        "/api/instance/auth/login", json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD}
    )
    wrong_code = await client.post(
        "/api/instance/auth/login",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": "000000"},
    )
    for response in (no_code, wrong_code):
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid credentials"

    success = await client.post(
        "/api/instance/auth/login",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": _totp_code(admin)},
    )
    assert success.status_code == 200
    body = success.json()
    assert body["status"] == "success"
    assert body["email"] == OPERATOR_EMAIL

    payload = decode_token(client.cookies.get("instance_token"))
    assert payload["scope"] == "instance"
    assert payload["sub"] == str(admin.id)
    assert "org_id" not in payload
    assert "role" not in payload


@pytest.mark.asyncio
async def test_login_generic_401_for_unknown_email_and_wrong_password(client: AsyncClient, db_session: AsyncSession):
    """Unknown email, wrong password: same generic 401, no oracle."""
    await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    wrong_password = await client.post(
        "/api/instance/auth/login",
        json={"email": OPERATOR_EMAIL, "password": "WrongPassword123!"},
    )
    unknown_email = await client.post(
        "/api/instance/auth/login",
        json={"email": "nobody@example.com", "password": OPERATOR_PASSWORD},
    )
    for response in (wrong_password, unknown_email):
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid credentials"


# ============================================================================
# me / logout
# ============================================================================


@pytest.mark.asyncio
async def test_me_returns_operator_state(client: AsyncClient, db_session: AsyncSession):
    admin = await _confirmed_admin(client, db_session)

    response = await client.get("/api/instance/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(admin.id)
    assert body["email"] == OPERATOR_EMAIL
    assert body["totp_confirmed"] is True


@pytest.mark.asyncio
async def test_an_active_operator_session_slides(client: AsyncClient, db_session: AsyncSession):
    """Every request with the operator cookie re-mints it.

    The timeout counts from the last activity, like an organization session. A Bearer request does not.
    """
    admin = await _confirmed_admin(client, db_session)

    response = await client.get("/api/instance/auth/me")
    assert response.status_code == 200
    assert INSTANCE_ADMIN_COOKIE_NAME in response.headers.get("set-cookie", "")

    bearer = await client.get(
        "/api/instance/auth/me",
        headers={"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"},
        cookies={},
    )
    assert bearer.status_code == 200
    assert INSTANCE_ADMIN_COOKIE_NAME not in bearer.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_me_without_token_is_401(client: AsyncClient, db_session: AsyncSession):
    await _confirmed_admin(client, db_session)
    client.cookies.clear()

    response = await client.get("/api/instance/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_with_bearer_instance_token(client: AsyncClient, db_session: AsyncSession):
    admin = await _confirmed_admin(client, db_session)
    client.cookies.clear()
    response = await client.get(
        "/api/instance/auth/me",
        headers={"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == OPERATOR_EMAIL


@pytest.mark.asyncio
async def test_logout_clears_only_operator_cookie(client: AsyncClient, db_session: AsyncSession):
    await _confirmed_admin(client, db_session)
    assert client.cookies.get("instance_token") is not None

    response = await client.post("/api/instance/auth/logout")
    assert response.status_code == 200

    assert client.cookies.get("instance_token") is None
    me = await client.get("/api/instance/auth/me")
    assert me.status_code == 401


# ============================================================================
# Token separation: operator vs organization sessions
# ============================================================================


@pytest.mark.asyncio
async def test_instance_token_rejected_on_user_and_org_endpoints(client: AsyncClient, db_session: AsyncSession):
    """An instance token gets 403 on the user dependency and org-scoped routes."""
    admin = await _confirmed_admin(client, db_session)
    user, org = await create_test_user_with_org(db_session, org_slug="sep-org")

    instance_headers = {"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"}

    # User dependency (GET /api/auth/me)
    me = await client.get("/api/auth/me", headers=instance_headers)
    assert me.status_code == 403

    # Org dependency (GET /api/orgs/{slug})
    org_detail = await client.get(f"/api/orgs/{org.slug}", headers=instance_headers)
    assert org_detail.status_code == 403

    # And in the user cookie slot (no Bearer): an instance token delivered as
    # the access_token cookie is still a token for the wrong realm — 403, not
    # the anonymous 401.
    client.cookies.clear()
    client.cookies.set("access_token", create_instance_admin_token(admin.id))
    me_via_cookie = await client.get("/api/auth/me")
    assert me_via_cookie.status_code == 403


@pytest.mark.asyncio
async def test_org_token_rejected_on_instance_endpoints(client: AsyncClient, db_session: AsyncSession):
    user, org = await create_test_user_with_org(db_session, org_slug="sep-org-2")

    from tests.factories import get_auth_headers

    org_headers = get_auth_headers(user)
    me = await client.get("/api/instance/auth/me", headers=org_headers)
    assert me.status_code == 403


@pytest.mark.asyncio
async def test_operator_cookie_alone_is_403_on_org_endpoints(client: AsyncClient, db_session: AsyncSession):
    """A browser holding only the operator cookie reads as wrong realm, not anonymous.

    The operator signs in through the real login (POST /api/instance/auth/login),
    so the session sits in the instance_token cookie — no Bearer, no
    access_token. Organization endpoints must answer 403, the same error an
    instance Bearer token gets, not the anonymous 401.
    """
    await _confirmed_admin(client, db_session)
    user, org = await create_test_user_with_org(db_session, org_slug="cross-realm-org")

    me = await client.get("/api/auth/me")
    assert me.status_code == 403

    org_detail = await client.get(f"/api/orgs/{org.slug}")
    assert org_detail.status_code == 403


@pytest.mark.asyncio
async def test_invalid_or_absent_operator_cookie_is_401_on_org_endpoint(client: AsyncClient, db_session: AsyncSession):
    """An invalid or expired operator cookie counts as no token: 401, not 403."""
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    expired = create_instance_admin_token(admin.id, expires_delta=timedelta(seconds=-1))

    for bad_cookie in ("not-a-jwt", expired):
        client.cookies.clear()
        client.cookies.set("instance_token", bad_cookie)
        me = await client.get("/api/auth/me")
        assert me.status_code == 401

    # No cookie at all: the anonymous 401.
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_member_cookie_alone_is_403_on_instance_me(client: AsyncClient, db_session: AsyncSession):
    """A browser holding only the access_token cookie reads as wrong realm on instance endpoints: 403."""
    user, org = await create_test_user_with_org(db_session, org_slug="cross-realm-org-2")

    from tests.factories import get_auth_cookies

    client.cookies.set("access_token", get_auth_cookies(user)["access_token"])

    me = await client.get("/api/instance/auth/me")
    assert me.status_code == 403


@pytest.mark.asyncio
async def test_invalid_or_absent_member_cookie_is_401_on_instance_me(client: AsyncClient, db_session: AsyncSession):
    """An invalid or expired user cookie counts as no token on instance endpoints: 401, not 403."""
    user, org = await create_test_user_with_org(db_session, org_slug="cross-realm-org-3")
    expired = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        expires_delta=timedelta(seconds=-1),
    )

    for bad_cookie in ("not-a-jwt", expired):
        client.cookies.clear()
        client.cookies.set("access_token", bad_cookie)
        me = await client.get("/api/instance/auth/me")
        assert me.status_code == 401

    # No cookie at all: the anonymous 401.
    client.cookies.clear()
    assert (await client.get("/api/instance/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_operator_and_member_sessions_coexist(client: AsyncClient, db_session: AsyncSession):
    """Separate cookies: an operator who is also an org member keeps both sessions."""
    await _confirmed_admin(client, db_session)
    user, org = await create_test_user_with_org(db_session, org_slug="coexist-org")

    # Operator session via cookie works …
    operator_me = await client.get("/api/instance/auth/me")
    assert operator_me.status_code == 200

    # … and the explicit member Bearer token still authorizes the org route,
    # because Bearer wins over the ambient operator cookie.
    from tests.factories import get_auth_headers

    org_detail = await client.get(f"/api/orgs/{org.slug}", headers=get_auth_headers(user))
    assert org_detail.status_code == 200


# ============================================================================
# Rate limiting
# ============================================================================


@pytest.mark.asyncio
async def test_login_rate_limited_after_five_failed_attempts(client: AsyncClient, db_session: AsyncSession):
    await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    for _ in range(5):
        response = await client.post(
            "/api/instance/auth/login",
            json={"email": OPERATOR_EMAIL, "password": "WrongPassword123!"},
        )
        assert response.status_code == 401

    sixth = await client.post(
        "/api/instance/auth/login",
        json={"email": OPERATOR_EMAIL, "password": "WrongPassword123!"},
    )
    assert sixth.status_code == 429


@pytest.mark.asyncio
async def test_totp_confirm_rate_limited_after_five_failed_attempts(client: AsyncClient, db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)

    for _ in range(5):
        response = await client.post(
            "/api/instance/auth/totp/confirm",
            json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": "000000"},
        )
        assert response.status_code == 401

    sixth = await client.post(
        "/api/instance/auth/totp/confirm",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": _totp_code(admin)},
    )
    assert sixth.status_code == 429


# ============================================================================
# Audit events (instance.login / instance.login_failed)
# ============================================================================


class _AuditRecorder:
    """Spy that stands in for record_audit_event in the router module."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **kwargs: object) -> None:
        self.events.append({"event": event, **kwargs})

    def named(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]


@pytest.mark.asyncio
async def test_login_audits_success_and_failure(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    from app.routers import instance_auth as instance_auth_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_auth_module, "record_audit_event", recorder)

    admin = await _confirmed_admin(client, db_session, email="audited@example.com")
    recorder.events.clear()

    # Failed login (wrong password) → instance.login_failed / invalid_password
    failed_password = await client.post(
        "/api/instance/auth/login",
        json={"email": "audited@example.com", "password": "WrongPassword123!"},
    )
    assert failed_password.status_code == 401

    # Failed login (wrong code) → instance.login_failed / invalid_totp
    failed_code = await client.post(
        "/api/instance/auth/login",
        json={"email": "audited@example.com", "password": OPERATOR_PASSWORD, "totp_code": "000000"},
    )
    assert failed_code.status_code == 401

    failed_events = recorder.named("instance.login_failed")
    assert [e["reason"] for e in failed_events] == ["invalid_password", "invalid_totp"]
    assert all(e["organization_id"] is None for e in failed_events)

    # Successful login → instance.login
    success = await client.post(
        "/api/instance/auth/login",
        json={"email": "audited@example.com", "password": OPERATOR_PASSWORD, "totp_code": _totp_code(admin)},
    )
    assert success.status_code == 200
    login_events = recorder.named("instance.login")
    assert len(login_events) == 1
    assert login_events[0]["actor"]["email"] == "audited@example.com"
    assert login_events[0]["organization_id"] is None

    # Never a password, a TOTP code or the TOTP secret in an audit entry.
    for entry in recorder.events:
        serialized = str(entry)
        assert OPERATOR_PASSWORD not in serialized
        assert admin.totp_secret not in serialized


@pytest.mark.asyncio
async def test_failed_login_audits_unknown_email(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    from app.routers import instance_auth as instance_auth_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_auth_module, "record_audit_event", recorder)

    response = await client.post(
        "/api/instance/auth/login",
        json={"email": "ghost@example.com", "password": "WhateverPass123!"},
    )
    assert response.status_code == 401
    failed_events = recorder.named("instance.login_failed")
    assert len(failed_events) == 1
    assert failed_events[0]["reason"] == "user_not_found"
    assert failed_events[0]["target"]["email"] == "ghost@example.com"


# ============================================================================
# CLI wiring (the DB-touching creation itself is covered above)
# ============================================================================


def test_cli_parses_instance_admin_create():
    from app.cli import build_parser

    args = build_parser().parse_args(["instance-admin", "create", "--email", OPERATOR_EMAIL])
    assert args.command == "instance-admin"
    assert args.subcommand == "create"
    assert args.email == OPERATOR_EMAIL


# ============================================================================
# Operator password change
# ============================================================================


@pytest.mark.asyncio
async def test_password_change_with_correct_current_password(client: AsyncClient, db_session: AsyncSession):
    admin = await _confirmed_admin(client, db_session)
    new_password = "BrandNewPass456!"

    response = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": new_password},
    )
    assert response.status_code == 200
    assert response.json()["message"]

    await db_session.refresh(admin)
    assert verify_password(new_password, admin.password_hash)
    assert not verify_password(OPERATOR_PASSWORD, admin.password_hash)


@pytest.mark.asyncio
async def test_password_change_with_wrong_current_password_rejected(client: AsyncClient, db_session: AsyncSession):
    admin = await _confirmed_admin(client, db_session)

    response = await client.post(
        "/api/instance/auth/password",
        json={"old_password": "WrongCurrentPass123!", "new_password": "BrandNewPass456!"},
    )
    assert response.status_code == 401

    await db_session.refresh(admin)
    assert verify_password(OPERATOR_PASSWORD, admin.password_hash)


@pytest.mark.asyncio
async def test_password_change_enforces_policy(client: AsyncClient, db_session: AsyncSession):
    await _confirmed_admin(client, db_session)

    response = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": "weak"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_password_change_requires_authentication(client: AsyncClient, db_session: AsyncSession):
    await _confirmed_admin(client, db_session)
    client.cookies.clear()

    response = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": "BrandNewPass456!"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_password_change_audits_instance_password_changed(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    from app.routers import instance_auth as instance_auth_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_auth_module, "record_audit_event", recorder)

    admin = await _confirmed_admin(client, db_session)

    response = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": "BrandNewPass456!"},
    )
    assert response.status_code == 200

    events = recorder.named("instance.password_changed")
    assert len(events) == 1
    assert events[0]["actor"]["email"] == admin.email
    assert events[0]["organization_id"] is None


@pytest.mark.asyncio
async def test_password_change_refuses_reuse_of_current_password(client: AsyncClient, db_session: AsyncSession):
    admin = await _confirmed_admin(client, db_session)

    response = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": OPERATOR_PASSWORD},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "INSTANCE_PASSWORD_REUSE_NOT_ALLOWED"

    await db_session.refresh(admin)
    assert verify_password(OPERATOR_PASSWORD, admin.password_hash)


@pytest.mark.asyncio
async def test_password_change_refuses_reuse_of_last_password_then_accepts_a_third(
    client: AsyncClient, db_session: AsyncSession
):
    admin = await _confirmed_admin(client, db_session)
    second_password = "SecondPass456!"
    third_password = "ThirdPass789!"

    first_change = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": second_password},
    )
    assert first_change.status_code == 200

    # The password just replaced (OPERATOR_PASSWORD) is now the "last" one — refused too.
    reuse_last = await client.post(
        "/api/instance/auth/password",
        json={"old_password": second_password, "new_password": OPERATOR_PASSWORD},
    )
    assert reuse_last.status_code == 400
    assert reuse_last.json()["error_code"] == "INSTANCE_PASSWORD_REUSE_NOT_ALLOWED"

    # A genuinely new, third password is accepted.
    third_change = await client.post(
        "/api/instance/auth/password",
        json={"old_password": second_password, "new_password": third_password},
    )
    assert third_change.status_code == 200

    await db_session.refresh(admin)
    assert verify_password(third_password, admin.password_hash)
    assert admin.last_password_hash is not None
    assert verify_password(second_password, admin.last_password_hash)


# ============================================================================
# A disabled operator: login and an already-open session
# ============================================================================


@pytest.mark.asyncio
async def test_disabled_operator_login_rejected(client: AsyncClient, db_session: AsyncSession):
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    await disable_instance_admin(db_session, OPERATOR_EMAIL)

    response = await client.post(
        "/api/instance/auth/login", json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD}
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "INSTANCE_ADMIN_DISABLED"

    await db_session.refresh(admin)
    assert admin.disabled_at is not None


@pytest.mark.asyncio
async def test_disabled_operator_with_pending_enrollment_totp_confirm_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    """A disabled operator with a pending enrollment (e.g. right after instance-admin
    reset-totp) must not be able to complete it through /totp/confirm either."""
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    code = _totp_code(admin)
    await disable_instance_admin(db_session, OPERATOR_EMAIL)

    response = await client.post(
        "/api/instance/auth/totp/confirm",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": code},
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "INSTANCE_ADMIN_DISABLED"

    assert "instance_token" not in client.cookies

    await db_session.refresh(admin)
    assert admin.totp_confirmed_at is None


@pytest.mark.asyncio
async def test_disabled_operator_open_session_rejected_at_next_request(client: AsyncClient, db_session: AsyncSession):
    """An operator disabled mid-session is refused by the next authenticated request."""
    admin = await _confirmed_admin(client, db_session)

    me = await client.get("/api/instance/auth/me")
    assert me.status_code == 200

    await disable_instance_admin(db_session, admin.email)

    me_after_disable = await client.get("/api/instance/auth/me")
    assert me_after_disable.status_code == 403
    assert me_after_disable.json()["error_code"] == "INSTANCE_ADMIN_DISABLED"


def test_cli_reads_password_from_environment(monkeypatch):
    from app import cli

    monkeypatch.setenv("INSTANCE_ADMIN_PASSWORD", "FromEnvironment123!")

    captured: dict[str, str] = {}

    async def fake_create(email: str, password: str) -> None:
        captured["email"] = email
        captured["password"] = password

    monkeypatch.setattr(cli, "_create_instance_admin", fake_create)

    exit_code = cli.main(["instance-admin", "create", "--email", OPERATOR_EMAIL])

    assert exit_code == 0
    assert captured == {"email": OPERATOR_EMAIL, "password": "FromEnvironment123!"}


# ============================================================================
# A sign-in against a reset that commits while it runs
# ============================================================================


def _reset_totp_right_after_the_sign_in_read(monkeypatch, test_engine: AsyncEngine) -> None:  # noqa: ANN001
    """Commit ``instance-admin reset-totp`` in a session of its own, once, at the
    moment the sign-in has read the operator row.

    The sign-in then proves the password and the code against the row it read,
    the state from before the reset. No sleep decides the order: the reset runs
    inside the read the sign-in waits for.
    """
    original = InstanceAdminRepository.get_by_email
    other_session = async_sessionmaker(test_engine, expire_on_commit=False)
    fired = False

    async def get_by_email(self: InstanceAdminRepository, email: str) -> InstanceAdmin | None:
        nonlocal fired
        admin = await original(self, email)
        if not fired:
            fired = True
            async with other_session() as session:
                await reset_instance_admin_totp(session, email)
        return admin

    monkeypatch.setattr(InstanceAdminRepository, "get_by_email", get_by_email)


@pytest.mark.asyncio
async def test_sign_in_with_the_old_factor_gets_no_session_when_a_reset_commits_meanwhile(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch
):
    """The code of the OLD authenticator is proven, a reset commits, no cookie is minted."""
    admin = await _confirmed_admin(client, db_session)
    client.cookies.clear()
    old_code = _totp_code(admin)

    _reset_totp_right_after_the_sign_in_read(monkeypatch, test_engine)
    login = await client.post(
        "/api/instance/auth/login",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": old_code},
    )

    assert login.status_code == 401
    assert INSTANCE_ADMIN_COOKIE_NAME not in login.cookies


@pytest.mark.asyncio
async def test_totp_confirm_of_the_old_secret_does_not_confirm_a_secret_a_reset_rotated_meanwhile(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch
):
    """The confirmation proved the OLD pending secret. The rotated one stays unconfirmed."""
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    old_code = _totp_code(admin)

    _reset_totp_right_after_the_sign_in_read(monkeypatch, test_engine)
    confirm = await client.post(
        "/api/instance/auth/totp/confirm",
        json={"email": OPERATOR_EMAIL, "password": OPERATOR_PASSWORD, "totp_code": old_code},
    )

    assert confirm.status_code == 401
    assert INSTANCE_ADMIN_COOKIE_NAME not in confirm.cookies
    async with async_sessionmaker(test_engine)() as session:
        confirmed_at = (
            await session.execute(select(InstanceAdmin.totp_confirmed_at).where(InstanceAdmin.id == admin.id))
        ).scalar_one()
    assert confirmed_at is None


# ============================================================================
# The password change is rate limited before the password work
# ============================================================================


@pytest.mark.asyncio
async def test_password_change_rate_limited_before_the_password_is_verified_and_hashed(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """The limiter of the sign-in (five a minute): the sixth request reaches neither Argon2 call."""
    admin = await _confirmed_admin(client, db_session)
    calls = {"verify": 0, "hash": 0}

    def counting_verify(password: str, password_hash: str) -> bool:
        calls["verify"] += 1
        return verify_password(password, password_hash)

    def counting_hash(password: str) -> str:
        calls["hash"] += 1
        return hash_password(password)

    monkeypatch.setattr(instance_auth_router, "verify_password", counting_verify)
    monkeypatch.setattr(instance_auth_router, "hash_password", counting_hash)

    for _ in range(5):
        wrong = await client.post(
            "/api/instance/auth/password",
            json={"old_password": "WrongCurrentPass123!", "new_password": "BrandNewPass456!"},
        )
        assert wrong.status_code == 401
    assert calls == {"verify": 5, "hash": 0}

    sixth = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": "BrandNewPass456!"},
    )

    assert sixth.status_code == 429
    assert calls == {"verify": 5, "hash": 0}
    await db_session.refresh(admin)
    assert verify_password(OPERATOR_PASSWORD, admin.password_hash)


# ============================================================================
# A signed-out operator session is no session
# ============================================================================


@pytest.mark.asyncio
async def test_kept_cookie_of_a_signed_out_operator_session_is_refused(client: AsyncClient, db_session: AsyncSession):
    """The sign-out clears the cookie; a copy kept elsewhere is still a valid
    signature. The sign-out records the session, and the operator's dependency
    answers the kept copy as the organization side does: 401 SESSION_REVOKED.
    """
    await _confirmed_admin(client, db_session)
    kept = client.cookies.get(INSTANCE_ADMIN_COOKIE_NAME)
    assert kept is not None

    signed_out = await client.post("/api/instance/auth/logout")
    assert signed_out.status_code == 200
    client.cookies.clear()

    refused = await client.get("/api/instance/auth/me", cookies={INSTANCE_ADMIN_COOKIE_NAME: kept})
    assert refused.status_code == 401
    assert refused.json()["error_code"] == "SESSION_REVOKED"
    assert INSTANCE_ADMIN_COOKIE_NAME not in refused.headers.get("set-cookie", "")

    as_bearer = await client.get("/api/instance/auth/me", headers={"Authorization": f"Bearer {kept}"})
    assert as_bearer.status_code == 401
    assert as_bearer.json()["error_code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_operator_sign_out_ends_every_cookie_the_session_slid_through(
    client: AsyncClient, db_session: AsyncSession
):
    """The sliding cookie mints a new token on every request. All of them are
    one session: a copy from before the slide ends with the sign-out too, and
    so does the cookie a password change minted for the session.
    """
    await _confirmed_admin(client, db_session)
    first = client.cookies.get(INSTANCE_ADMIN_COOKIE_NAME)
    assert first is not None
    sid = decode_token(first).get("sid")
    assert isinstance(sid, str) and sid

    slid = await client.get("/api/instance/auth/me")
    assert slid.status_code == 200
    second = client.cookies.get(INSTANCE_ADMIN_COOKIE_NAME)
    assert second is not None
    assert decode_token(second)["sid"] == sid

    changed = await client.post(
        "/api/instance/auth/password",
        json={"old_password": OPERATOR_PASSWORD, "new_password": "BrandNewPass456!"},
    )
    assert changed.status_code == 200
    third = client.cookies.get(INSTANCE_ADMIN_COOKIE_NAME)
    assert third is not None
    assert decode_token(third)["sid"] == sid

    signed_out = await client.post("/api/instance/auth/logout")
    assert signed_out.status_code == 200
    client.cookies.clear()

    refused = await client.get("/api/instance/auth/me", cookies={INSTANCE_ADMIN_COOKIE_NAME: third})
    assert refused.status_code == 401
    assert refused.json()["error_code"] == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_operator_sign_out_ends_only_its_own_session(client: AsyncClient, db_session: AsyncSession):
    """Another sign-in of the same operator is another session and stays."""
    admin = await _confirmed_admin(client, db_session)
    other_device = create_instance_admin_token(admin.id, session_version=admin.session_version)

    signed_out = await client.post("/api/instance/auth/logout")
    assert signed_out.status_code == 200
    client.cookies.clear()

    still_in = await client.get("/api/instance/auth/me", cookies={INSTANCE_ADMIN_COOKIE_NAME: other_device})
    assert still_in.status_code == 200


@pytest.mark.asyncio
async def test_signed_out_operator_cookie_names_no_realm_on_an_organization_route(
    client: AsyncClient, db_session: AsyncSession
):
    """A live operator cookie alone is the wrong realm on an organization route
    (403). The kept cookie of a signed-out operator session is no token: 401.
    """
    await _confirmed_admin(client, db_session)
    kept = client.cookies.get(INSTANCE_ADMIN_COOKIE_NAME)
    assert kept is not None

    wrong_realm = await client.get("/api/auth/me", cookies={INSTANCE_ADMIN_COOKIE_NAME: kept})
    assert wrong_realm.status_code == 403

    signed_out = await client.post("/api/instance/auth/logout")
    assert signed_out.status_code == 200
    client.cookies.clear()

    nobody = await client.get("/api/auth/me", cookies={INSTANCE_ADMIN_COOKIE_NAME: kept})
    assert nobody.status_code == 401
