"""Integration tests for the authentication endpoints: registration, login and lockout,
session cookies and tokens, profile update, and password reset and change.
"""

from datetime import UTC, timedelta
from uuid import UUID

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_access_token, decode_token
from app.config import get_settings
from app.routers import auth as auth_router
from tests.factories import create_test_organization, create_test_user


@pytest.mark.asyncio
async def test_register_creates_org_and_user(client: AsyncClient, db_session: AsyncSession):
    """Bootstrap registration creates the organization and its org_admin.

    With ENFORCE_ORG_ADMIN_TOTP on, the test default, it answers with a setup token instead of a session cookie.
    """
    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "ACME Corporation",
            "organization_slug": "acme-corp",
            "email": "admin@acme.com",
            "password": "SecurePass123!",
            "display_name": "Admin User",
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert data["email"] == "admin@acme.com"
    assert data["display_name"] == "Admin User"
    assert data["role"] == "org_admin"
    assert data["message"] == "Registration successful"

    assert "user_id" in data
    assert "organization_id" in data
    UUID(data["user_id"])
    UUID(data["organization_id"])

    assert data["requires_2fa"] is True
    assert data["setup_token"]

    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_register_bootstrap_setup_token_claims_and_totp_flow(client: AsyncClient, db_session: AsyncSession):
    """The setup token of a bootstrap registration is the one login issues.

    It carries purpose 2fa_setup, a 30-minute lifetime and the user's session_version.
    As a bearer it opens the two TOTP routes, and a verify sets the session cookie.
    Any other route, for example GET /api/auth/me, refuses it.

    """
    from app.repositories.user import UserRepository

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Setup Token Corp",
            "organization_slug": "setup-token-corp",
            "email": "admin@setup-token.com",
            "password": "SecurePass123!",
            "display_name": "Admin User",
        },
    )
    assert response.status_code == 201
    setup_token = response.json()["setup_token"]
    assert setup_token

    # Token claims: purpose, 30-minute lifetime, the user's session_version
    payload = decode_token(setup_token)
    user = await UserRepository.get_by_email_unscoped(db_session, "admin@setup-token.com")
    assert user is not None
    assert payload["purpose"] == "2fa_setup"
    assert payload["sv"] == user.session_version
    assert payload["exp"] - payload["iat"] == 30 * 60

    # The setup token works as a bearer on the TOTP setup route
    setup_response = await client.post(
        "/api/auth/2fa/totp/setup",
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert setup_response.status_code == 200
    secret = setup_response.json()["secret"]

    # The setup token is refused on any other route
    me_response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert me_response.status_code == 403
    assert me_response.json()["error_code"] == "2FA_SETUP_REQUIRED"

    # A valid code from the setup secret sets the session cookie
    code = pyotp.TOTP(secret).now()
    verify_response = await client.post(
        "/api/auth/2fa/totp/verify",
        json={"code": code},
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert verify_response.status_code == 200
    assert "access_token" in verify_response.cookies


@pytest.mark.asyncio
async def test_register_bootstrap_without_enforcement_sets_cookie(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """With ENFORCE_ORG_ADMIN_TOTP off, a bootstrap registration sets the cookie and issues no setup token."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings().auth, "enforce_org_admin_totp", False)

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "No Enforcement Corp",
            "organization_slug": "no-enforcement-corp",
            "email": "admin@no-enforcement.com",
            "password": "SecurePass123!",
            "display_name": "Admin User",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["requires_2fa"] is False
    assert data["setup_token"] is None
    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_register_self_signup_gets_cookie_and_no_setup_token(client: AsyncClient, db_session: AsyncSession):
    """Self-signup (no organization_name) gets the cookie and no setup_token, even with ENFORCE_ORG_ADMIN_TOTP on."""
    await create_test_organization(db_session, slug="self-signup-org")

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_slug": "self-signup-org",
            "email": "participant@self-signup.org",
            "password": "SecurePass123!",
            "display_name": "Pat Participant",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "participant"
    assert data["requires_2fa"] is False
    assert data["setup_token"] is None
    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(
    client: AsyncClient, db_session: AsyncSession, open_org_bootstrap: None
):
    """Registration with an existing email is refused with 409."""
    await create_test_user(db_session, email="existing@example.com")

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "New Org",
            "organization_slug": "new-org",
            "email": "existing@example.com",
            "password": "SecurePass123!",
            "display_name": "New User",
        },
    )

    assert response.status_code == 409
    data = response.json()
    assert "Email 'existing@example.com' already registered" in data["detail"]


@pytest.mark.asyncio
async def test_register_soft_deleted_email_returns_409_not_500(
    client: AsyncClient, db_session: AsyncSession, open_org_bootstrap: None
):
    """A soft-deleted account still owns its address, so registration answers 409, not 500.

    The unique index covers deleted rows too, so the lookup must as well.
    The answer is the generic duplicate message, because registration is unauthenticated
    and must not confirm that a deleted account exists.

    """
    deleted_user = await create_test_user(db_session, email="ausgeschieden@example.com")
    deleted_user.is_deleted = True
    await db_session.commit()

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "New Org",
            "organization_slug": "new-org-softdel",
            "email": "ausgeschieden@example.com",
            "password": "SecurePass123!",
            "display_name": "New User",
        },
    )

    assert response.status_code == 409
    data = response.json()
    assert data["detail"] == "Email 'ausgeschieden@example.com' already registered"


@pytest.mark.asyncio
async def test_register_duplicate_slug_returns_409(
    client: AsyncClient, db_session: AsyncSession, open_org_bootstrap: None
):
    """Registration with an existing organization slug is refused with 409."""
    await create_test_organization(db_session, slug="existing-org")

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "New Org",
            "organization_slug": "existing-org",
            "email": "newuser@example.com",
            "password": "SecurePass123!",
            "display_name": "New User",
        },
    )

    assert response.status_code == 409
    data = response.json()
    assert "Organization slug 'existing-org' already exists" in data["detail"]


# ============================================================================
# Self-signup into an existing organisation
# ============================================================================


@pytest.mark.asyncio
async def test_register_self_signup_creates_participant(client: AsyncClient, db_session: AsyncSession):
    """Without `organization_name`, registration creates a participant in the existing organization of `organization_slug`."""
    org = await create_test_organization(db_session, slug="signup-target-org")

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_slug": "signup-target-org",
            "email": "anna@example.org",
            "password": "SecurePass123!",
            "display_name": "Anna",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "anna@example.org"
    assert data["display_name"] == "Anna"
    assert data["role"] == "participant"
    assert data["organization_id"] == str(org.id)
    assert data["requires_2fa"] is False
    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_register_self_signup_unknown_org_returns_404(client: AsyncClient, db_session: AsyncSession):
    """Self-signup into a non-existing org returns 404 — consistent with the
    multi-tenancy guard, no org-existence probe via 409."""
    response = await client.post(
        "/api/auth/register",
        json={
            "organization_slug": "does-not-exist",
            "email": "ghost@example.org",
            "password": "SecurePass123!",
            "display_name": "Ghost",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_register_self_signup_duplicate_email_returns_409(client: AsyncClient, db_session: AsyncSession):
    """Self-signup with an email that already exists (anywhere) returns 409."""
    org = await create_test_organization(db_session, slug="dup-email-org")
    await create_test_user(db_session, email="taken@example.org", organization=org)

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_slug": "dup-email-org",
            "email": "taken@example.org",
            "password": "SecurePass123!",
            "display_name": "Anna",
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_self_signup_soft_deleted_email_returns_409_not_500(
    client: AsyncClient, db_session: AsyncSession
):
    """Self-signup onto a soft-deleted address answers 409, not 500, with the same generic message as any other duplicate."""
    org = await create_test_organization(db_session, slug="dup-email-org-softdel")
    deleted_user = await create_test_user(db_session, email="ausgeschieden@example.org", organization=org)
    deleted_user.is_deleted = True
    await db_session.commit()

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_slug": "dup-email-org-softdel",
            "email": "ausgeschieden@example.org",
            "password": "SecurePass123!",
            "display_name": "Anna",
        },
    )

    assert response.status_code == 409
    data = response.json()
    assert data["detail"] == "Email 'ausgeschieden@example.org' already registered"


@pytest.mark.asyncio
async def test_register_bootstrap_response_includes_requires_2fa(client: AsyncClient, db_session: AsyncSession):
    """The bootstrap response exposes `requires_2fa`."""
    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Bootstrap Org",
            "organization_slug": "bootstrap-2fa-org",
            "email": "bootstrap-2fa@example.org",
            "password": "SecurePass123!",
            "display_name": "Bootstrap Admin",
        },
    )
    assert response.status_code == 201
    assert "requires_2fa" in response.json()


@pytest.mark.asyncio
async def test_login_returns_cookie(client: AsyncClient, db_session: AsyncSession):
    """A successful login returns the user and sets the auth cookie."""
    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="TestPassword123!",
    )

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "user@example.com",
            "password": "TestPassword123!",
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["email"] == "user@example.com"
    assert data["display_name"] == "Test User"
    assert data["role"] == "participant"
    assert data["message"] == "Login successful"

    assert "user_id" in data
    assert data["user_id"] == str(user.id)

    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_login_invalid_credentials_returns_401(client: AsyncClient, db_session: AsyncSession):
    """A wrong password is refused with a generic 401 and sets no cookie."""
    await create_test_user(
        db_session,
        email="user@example.com",
        password="CorrectPassword123!",
    )

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "user@example.com",
            "password": "WrongPassword123!",
        },
    )

    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Invalid credentials"

    assert "access_token" not in response.cookies


@pytest.mark.asyncio
async def test_login_nonexistent_user_returns_401(client: AsyncClient, db_session: AsyncSession):
    """An unknown email gets the same generic 401 message as a wrong password."""
    response = await client.post(
        "/api/auth/login",
        json={
            "email": "nonexistent@example.com",
            "password": "SomePassword123!",
        },
    )

    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_email_case_insensitive(client: AsyncClient, db_session: AsyncSession):
    """Login matches the email case-insensitively.

    The user is stored in lowercase.
    A mixed-case login succeeds because the request schema lowercases the input.

    """
    user = await create_test_user(
        db_session,
        email="case.test@example.com",
        password="TestPassword123!",
    )

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "Case.Test@Example.COM",
            "password": "TestPassword123!",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["user_id"] == str(user.id)
    assert data["email"] == "case.test@example.com"


@pytest.mark.asyncio
async def test_login_finds_legacy_mixed_case_email(client: AsyncClient, db_session: AsyncSession):
    """Login finds users whose stored email has mixed case.

    Rows written before migration 015 can hold mixed-case emails.
    The factory writes directly to the database and bypasses the request schemas.
    The repository's lower() comparison must still match.

    """
    user = await create_test_user(
        db_session,
        email="Legacy.User@Example.com",
        password="TestPassword123!",
    )

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "legacy.user@example.com",
            "password": "TestPassword123!",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["user_id"] == str(user.id)


@pytest.mark.asyncio
async def test_register_normalizes_email_to_lowercase(
    client: AsyncClient, db_session: AsyncSession, open_org_bootstrap: None
):
    """Registration lowercases the email before storing it, and the duplicate check is case-insensitive."""
    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Case Corp",
            "organization_slug": "case-corp",
            "email": "Admin@Case-Corp.DE",
            "password": "SecurePass123!",
            "display_name": "Case Admin",
        },
    )

    assert response.status_code == 201
    assert response.json()["email"] == "admin@case-corp.de"

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Case Corp Two",
            "organization_slug": "case-corp-two",
            "email": "ADMIN@case-corp.de",
            "password": "SecurePass123!",
            "display_name": "Case Admin Two",
        },
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_access_protected_with_cookie(client: AsyncClient, db_session: AsyncSession):
    """A valid auth cookie grants access to the user's own organization."""
    user = await create_test_user(db_session, email="user@example.com")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )

    response = await client.get(
        # User's org slug is derived from email prefix: user@example.com -> org-user
        "/api/orgs/org-user",
        cookies={"access_token": token},
    )

    assert response.status_code == 200
    data = response.json()

    assert "id" in data
    assert data["id"] == str(user.organization_id)


@pytest.mark.asyncio
async def test_access_protected_with_bearer_token(client: AsyncClient, db_session: AsyncSession):
    """A Bearer token works as an alternative to the cookie."""
    user = await create_test_user(db_session, email="user@example.com")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )

    response = await client.get(
        # User's org slug is derived from email prefix: user@example.com -> org-user
        "/api/orgs/org-user",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_access_protected_without_cookie_returns_401(client: AsyncClient, db_session: AsyncSession):
    """A request without auth is refused with 401."""
    # The slug does not matter, auth fails first
    response = await client.get("/api/orgs/test-org")

    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_expired_token_returns_401(client: AsyncClient, db_session: AsyncSession):
    """An expired token is refused with 401."""
    user = await create_test_user(db_session, email="user@example.com")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        expires_delta=timedelta(hours=-1),  # Negative delta = expired
    )

    response = await client.get(
        # User's org slug is derived from email prefix: user@example.com -> org-user
        "/api/orgs/org-user",
        cookies={"access_token": token},
    )

    assert response.status_code == 401
    data = response.json()
    assert "token" in data["detail"].lower() or "expired" in data["detail"].lower()


@pytest.mark.asyncio
async def test_logout_clears_cookie(client: AsyncClient, db_session: AsyncSession):
    """Logout answers 200, clears the cookie, and the server refuses the old cookie afterwards."""
    user = await create_test_user(db_session, email="user@example.com")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )
    assert (await client.get("/api/auth/me", cookies={"access_token": token})).status_code == 200
    client.cookies.clear()  # only the original token is sent, not a refreshed copy

    response = await client.post(
        "/api/auth/logout",
        cookies={"access_token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Logout successful"
    set_cookie = response.headers.get("set-cookie", "")
    assert set_cookie.startswith("access_token=")
    assert "Max-Age=0" in set_cookie

    # A cleared cookie only asks the browser to forget it. The server must refuse a kept copy too.
    client.cookies.clear()
    after = await client.get("/api/auth/me", cookies={"access_token": token})
    assert after.status_code == 401


@pytest.mark.asyncio
async def test_logout_without_auth_returns_401(client: AsyncClient, db_session: AsyncSession):
    """Logout requires authentication."""
    response = await client.post("/api/auth/logout")

    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_register_weak_password_returns_400(client: AsyncClient, db_session: AsyncSession):
    """A password that passes the schema length but fails the policy is refused with 400."""
    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Test Org",
            "organization_slug": "test-org",
            "email": "user@example.com",
            "password": "12345678",  # Long enough for Pydantic but fails policy (no uppercase, special, etc.)
            "display_name": "Test User",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data

    detail = data["detail"]
    if isinstance(detail, dict):
        assert "errors" in detail
        errors = detail["errors"]
        assert len(errors) > 0
    else:
        assert "password" in detail.lower()


@pytest.mark.asyncio
async def test_login_increments_failed_attempts(client: AsyncClient, db_session: AsyncSession):
    """Five failed logins lock the account with 423, and the lock is stored."""
    from app.repositories.user import UserRepository

    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="CorrectPassword123!",
    )

    # The maximum is 5 attempts
    for i in range(5):
        response = await client.post(
            "/api/auth/login",
            json={
                "email": "user@example.com",
                "password": "WrongPassword123!",
            },
        )

        if i < 4:
            assert response.status_code == 401
        else:
            assert response.status_code == 423
            data = response.json()
            assert "locked" in data["detail"].lower()
            assert data["error_code"] == "ACCOUNT_LOCKED"
            assert "locked_until" in data

    # The account is locked in the database
    user_repo = UserRepository(db_session, user.organization_id)
    refreshed_user = await user_repo.get_by_id(user.id)
    assert refreshed_user is not None
    assert refreshed_user.locked_until is not None
    assert refreshed_user.failed_login_attempts >= 5


@pytest.mark.asyncio
async def test_login_errors_carry_codes(client: AsyncClient, db_session: AsyncSession):
    """Every login refusal carries an error code, so the client never shows raw English.

    The unknown address and the wrong password share one code,
    so the code reveals no more than the status does.

    """
    user = await create_test_user(db_session, email="coded@example.com", password="CorrectPassword123!")

    unknown = await client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "Whatever123!"})
    assert unknown.status_code == 401
    assert unknown.json()["error_code"] == "INVALID_CREDENTIALS"

    wrong = await client.post("/api/auth/login", json={"email": "coded@example.com", "password": "WrongPassword123!"})
    assert wrong.status_code == 401
    assert wrong.json()["error_code"] == "INVALID_CREDENTIALS"

    user.is_active = False
    await db_session.commit()
    disabled = await client.post(
        "/api/auth/login", json={"email": "coded@example.com", "password": "CorrectPassword123!"}
    )
    assert disabled.status_code == 401
    assert disabled.json()["error_code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_locked_account_cannot_login(client: AsyncClient, db_session: AsyncSession):
    """A locked account is refused with 423 even with the correct password."""
    from datetime import datetime

    from app.repositories.user import UserRepository

    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="CorrectPassword123!",
    )

    user_repo = UserRepository(db_session, user.organization_id)
    await user_repo.update(
        user.id,
        locked_until=datetime.now(UTC) + timedelta(minutes=15),
        failed_login_attempts=5,
    )
    await db_session.commit()

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "user@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 423
    data = response.json()
    assert "locked" in data["detail"].lower()
    # The client words the lock in its own language from the code and the end time,
    # not from the English detail.
    assert data["error_code"] == "ACCOUNT_LOCKED"
    assert datetime.fromisoformat(data["locked_until"]) > datetime.now(UTC)


@pytest.mark.asyncio
async def test_successful_login_resets_failed_attempts(client: AsyncClient, db_session: AsyncSession):
    """A successful login resets the failed_login_attempts counter."""
    from app.repositories.user import UserRepository

    user = await create_test_user(
        db_session,
        email="user@example.com",
        password="CorrectPassword123!",
    )

    user_repo = UserRepository(db_session, user.organization_id)
    await user_repo.update(user.id, failed_login_attempts=3)
    await db_session.commit()

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "user@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200

    refreshed_user = await user_repo.get_by_id(user.id)
    assert refreshed_user is not None
    assert refreshed_user.failed_login_attempts == 0


# ============================================================================
# Profile Update Tests
# ============================================================================


@pytest.mark.asyncio
async def test_profile_update_display_name(client: AsyncClient, db_session: AsyncSession):
    """A profile update changes the display name and returns the updated user."""
    user = await create_test_user(db_session, email="user@example.com", display_name="Original Name")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )

    response = await client.put(
        "/api/auth/profile",
        json={"display_name": "New Display Name"},
        cookies={"access_token": token},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["display_name"] == "New Display Name"
    assert data["email"] == "user@example.com"
    assert data["message"] == "Profile updated successfully"


@pytest.mark.asyncio
async def test_profile_update_no_changes(client: AsyncClient, db_session: AsyncSession):
    """An update with the current values answers 200 with 'No changes made'."""
    user = await create_test_user(db_session, email="user@example.com", display_name="Same Name")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )

    response = await client.put(
        "/api/auth/profile",
        json={"display_name": "Same Name"},
        cookies={"access_token": token},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["display_name"] == "Same Name"
    assert data["message"] == "No changes made"


@pytest.mark.asyncio
async def test_profile_update_empty_body(client: AsyncClient, db_session: AsyncSession):
    """An empty body answers 200 with 'No changes made'."""
    user = await create_test_user(db_session, email="user@example.com", display_name="Original")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )

    response = await client.put(
        "/api/auth/profile",
        json={},
        cookies={"access_token": token},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["display_name"] == "Original"
    assert data["message"] == "No changes made"


@pytest.mark.asyncio
async def test_profile_update_unauthenticated(client: AsyncClient, db_session: AsyncSession):
    """A profile update requires authentication."""
    response = await client.put(
        "/api/auth/profile",
        json={"display_name": "New Name"},
    )

    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_profile_update_empty_display_name_rejected(client: AsyncClient, db_session: AsyncSession):
    """An empty display_name is rejected with 422."""
    user = await create_test_user(db_session, email="user@example.com")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )

    response = await client.put(
        "/api/auth/profile",
        json={"display_name": ""},
        cookies={"access_token": token},
    )

    assert response.status_code == 422


# ============================================================================
# Password Reset Tests (database-backed tokens)
# ============================================================================


@pytest.mark.asyncio
async def test_password_reset_token_stored_in_db(client: AsyncClient, db_session: AsyncSession):
    """Requesting a reset stores an unused SHA-256 token hash with an expiry."""
    from sqlalchemy import select

    from app.models.password_reset_token import PasswordResetToken

    user = await create_test_user(
        db_session,
        email="resetdb@example.com",
        password="OldPassword123!",
    )

    response = await client.post(
        "/api/auth/password-forgot",
        json={"email": "resetdb@example.com"},
    )

    assert response.status_code == 200

    stmt = select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    result = await db_session.execute(stmt)
    tokens = result.scalars().all()

    assert len(tokens) >= 1
    token_record = tokens[-1]
    assert token_record.token_hash is not None
    assert len(token_record.token_hash) == 64  # SHA-256 hex digest
    assert token_record.expires_at is not None
    assert token_record.used_at is None


@pytest.mark.asyncio
async def test_password_reset_valid_token_succeeds(client: AsyncClient, db_session: AsyncSession):
    """A valid reset token changes the password."""
    from app.repositories.password_reset import PasswordResetRepository

    user = await create_test_user(
        db_session,
        email="resetvalid@example.com",
        password="OldPassword123!",
    )

    reset_repo = PasswordResetRepository(db_session)
    raw_token = await reset_repo.create_token(user.id)
    await db_session.commit()

    response = await client.post(
        "/api/auth/password-reset",
        json={
            "token": raw_token,
            "new_password": "NewPassword456!",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "successfully" in data["message"].lower()

    # The new password logs in
    response = await client.post(
        "/api/auth/login",
        json={
            "email": "resetvalid@example.com",
            "password": "NewPassword456!",
        },
    )
    assert response.status_code == 200

    # The old password no longer logs in
    response = await client.post(
        "/api/auth/login",
        json={
            "email": "resetvalid@example.com",
            "password": "OldPassword123!",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_password_reset_expired_token_rejected(client: AsyncClient, db_session: AsyncSession):
    """An expired reset token is refused with 400."""
    from app.repositories.password_reset import PasswordResetRepository

    user = await create_test_user(
        db_session,
        email="resetexpired@example.com",
        password="OldPassword123!",
    )

    # A zero-hour expiry is expired at once
    reset_repo = PasswordResetRepository(db_session)
    raw_token = await reset_repo.create_token(user.id, expiry_hours=0)
    await db_session.commit()

    response = await client.post(
        "/api/auth/password-reset",
        json={
            "token": raw_token,
            "new_password": "NewPassword456!",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    detail = data["detail"]
    if isinstance(detail, dict):
        assert "invalid" in detail.get("detail", "").lower() or "expired" in detail.get("detail", "").lower()


@pytest.mark.asyncio
async def test_password_reset_used_token_rejected(client: AsyncClient, db_session: AsyncSession):
    """A reset token works once, and a second use is refused with 400."""
    from app.repositories.password_reset import PasswordResetRepository

    user = await create_test_user(
        db_session,
        email="resetused@example.com",
        password="OldPassword123!",
    )

    reset_repo = PasswordResetRepository(db_session)
    raw_token = await reset_repo.create_token(user.id)
    await db_session.commit()

    # The first use succeeds
    response = await client.post(
        "/api/auth/password-reset",
        json={
            "token": raw_token,
            "new_password": "NewPassword456!",
        },
    )
    assert response.status_code == 200

    # The second use of the same token fails
    response = await client.post(
        "/api/auth/password-reset",
        json={
            "token": raw_token,
            "new_password": "AnotherPassword789!",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    detail = data["detail"]
    if isinstance(detail, dict):
        assert "invalid" in detail.get("detail", "").lower()


@pytest.mark.asyncio
async def test_cookie_session_slides_on_activity(client: AsyncClient, db_session: AsyncSession):
    """A cookie-authenticated request re-mints the session cookie with a fresh, full-length expiry.

    The session is sliding, so an actively working user is never logged out a fixed window after login.

    """
    from app.auth.tokens import decode_token

    user = await create_test_user(db_session, email="user@example.com")

    # A token with 2 minutes left stands in for a session that is well into its window.
    near_expiry = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        expires_delta=timedelta(minutes=2),
    )

    response = await client.get("/api/auth/me", cookies={"access_token": near_expiry})

    assert response.status_code == 200
    assert "access_token" in response.cookies
    refreshed = decode_token(response.cookies["access_token"])
    original = decode_token(near_expiry)
    assert refreshed["exp"] > original["exp"]


@pytest.mark.asyncio
async def test_bearer_session_is_not_refreshed(client: AsyncClient, db_session: AsyncSession):
    """Bearer (API/test) clients manage their own tokens, so the server does not re-mint a session cookie for them."""
    user = await create_test_user(db_session, email="user@example.com")
    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )

    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert "access_token" not in response.cookies


# ============================================================================
# Organisation bootstrap is one-time by default
# ============================================================================


@pytest.mark.asyncio
async def test_bootstrap_registration_blocked_once_an_org_exists(client: AsyncClient, db_session: AsyncSession):
    """The unauthenticated bootstrap path must close after the first organisation.

    `POST /api/auth/register` in bootstrap mode creates an organisation AND an
    `org_admin` user without any authentication. Left open, anyone could mint
    themselves an admin account on a live instance. The check is
    server-side on purpose, because the frontend hiding the route is convenience, not
    the control.

    """
    await create_test_organization(db_session, slug="first-org")

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Squatter Org",
            "organization_slug": "squatter-org",
            "email": "squatter@example.org",
            "password": "SecurePass123!",
            "display_name": "Squatter",
        },
    )

    assert response.status_code == 403
    body = response.json()
    # Coded so the frontend can hide/redirect the route cleanly rather than
    # string-matching the message.
    assert body["error_code"] == "ORG_BOOTSTRAP_CLOSED"


@pytest.mark.asyncio
async def test_bootstrap_registration_allowed_on_an_empty_instance(client: AsyncClient, db_session: AsyncSession):
    """The first organisation must still be creatable — that's the whole point."""
    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Genesis Org",
            "organization_slug": "genesis-org",
            "email": "genesis@example.org",
            "password": "SecurePass123!",
            "display_name": "Genesis Admin",
        },
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_bootstrap_registration_allowed_when_operator_opts_in(
    client: AsyncClient, db_session: AsyncSession, open_org_bootstrap: None
):
    """An instance that deliberately offers open organisation signup lets further organisations register via `allow_unlimited_org_registrations`."""
    await create_test_organization(db_session, slug="tenant-one")

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Tenant Two",
            "organization_slug": "tenant-two",
            "email": "tenant2@example.org",
            "password": "SecurePass123!",
            "display_name": "Tenant Two Admin",
        },
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_password_hash_runs_off_the_event_loop(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Registration and login run Argon2 in a worker thread.

    A slow hash then does not stop every other request on the event loop.

    """
    import threading

    from app.routers import auth as auth_router

    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_hash = auth_router.hash_password
    real_verify = auth_router.verify_password

    def spying_hash(password: str) -> str:
        seen["hash"] = threading.get_ident()
        return real_hash(password)

    def spying_verify(password: str, password_hash: str) -> bool:
        seen["verify"] = threading.get_ident()
        return real_verify(password, password_hash)

    monkeypatch.setattr(auth_router, "hash_password", spying_hash)
    monkeypatch.setattr(auth_router, "verify_password", spying_verify)

    org = await create_test_organization(db_session, slug="off-loop-org")
    register = await client.post(
        "/api/auth/register",
        json={
            "organization_slug": org.slug,
            "email": "off-loop@test.com",
            "password": "SecurePass123!",
            "display_name": "Off Loop",
        },
    )
    assert register.status_code == 201, register.text
    login = await client.post("/api/auth/login", json={"email": "off-loop@test.com", "password": "SecurePass123!"})
    assert login.status_code == 200, login.text

    assert seen["hash"] != loop_thread
    assert seen["verify"] != loop_thread


# ============================================================================
# The password change is rate limited before the password work
# ============================================================================


@pytest.mark.asyncio
async def test_password_change_rate_limited_before_the_password_is_verified_and_hashed(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """The limiter of the sign-in: the request over the limit reaches neither Argon2 call."""
    user = await create_test_user(db_session, email="limited-change@example.com", password="OldPassword123!")
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=user.id,
            role=user.role.value,
            organization_id=user.organization_id,
            session_version=user.session_version,
        )
    }
    allowed = int(get_settings().auth.login_rate_limit.split("/")[0])
    calls = {"verify": 0, "hash": 0}

    def counting_verify(password: str, password_hash: str) -> bool:
        calls["verify"] += 1
        return verify_password(password, password_hash)

    def counting_hash(password: str) -> str:
        calls["hash"] += 1
        return hash_password(password)

    monkeypatch.setattr(auth_router, "verify_password", counting_verify)
    monkeypatch.setattr(auth_router, "hash_password", counting_hash)

    for _ in range(allowed):
        wrong = await client.post(
            "/api/auth/password-change",
            headers=headers,
            json={"old_password": "WrongPassword123!", "new_password": "NewPassword456!"},
        )
        assert wrong.status_code == 401
    assert calls == {"verify": allowed, "hash": 0}

    over = await client.post(
        "/api/auth/password-change",
        headers=headers,
        json={"old_password": "OldPassword123!", "new_password": "NewPassword456!"},
    )

    assert over.status_code == 429
    assert calls == {"verify": allowed, "hash": 0}
    await db_session.refresh(user)
    assert verify_password("OldPassword123!", user.password_hash)
