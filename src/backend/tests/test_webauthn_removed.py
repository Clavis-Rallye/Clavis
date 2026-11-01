"""
The App serves no WebAuthn route, and the column ``users.webauthn_credentials`` keeps working.

Covered here:

- The four paths under ``/api/auth/2fa/webauthn/`` answer 404, and the generated OpenAPI schema holds no
  path with ``webauthn`` and no schema whose name starts with ``WebAuthn``.
- The user detail reports ``webauthn_enabled`` from the column, true with a stored credential, false without.
- Login works for an account with a stored credential: without TOTP the password alone signs in, with TOTP
  the TOTP route completes the sign-in. Neither path changes the column.

The erasure and the reset of the second factors are proven by
``tests/test_second_factor_reset_ends_sessions.py``, the PII filter by ``tests/test_pii_filter.py``.
"""

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.user import UserRole
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers

PASSWORD = "SecurePass123!"

REMOVED_PATHS = [
    "/api/auth/2fa/webauthn/register-options",
    "/api/auth/2fa/webauthn/register-verify",
    "/api/auth/2fa/webauthn/authenticate-options",
    "/api/auth/2fa/webauthn/authenticate-verify",
]

STORED_CREDENTIALS = {
    "credentials": [
        {
            "credential_id": "stored-credential-id",
            "public_key": "stored-public-key",
            "sign_count": 4,
            "transports": ["usb"],
            "created_at": "2026-02-12T00:00:00Z",
        }
    ]
}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", REMOVED_PATHS)
async def test_webauthn_path_answers_404(client: AsyncClient, db_session: AsyncSession, path: str):
    """Each of the four paths answers 404, also to a signed-in user."""
    user = await create_test_user(db_session, email="no-webauthn@example.com", password=PASSWORD)

    anonymous = await client.post(path, json={"email": user.email})
    signed_in = await client.post(path, json={"email": user.email}, headers=get_auth_headers(user))

    assert anonymous.status_code == 404
    assert signed_in.status_code == 404


def test_openapi_schema_has_no_webauthn_path_or_schema():
    """The generated OpenAPI schema names no WebAuthn path and no WebAuthn schema."""
    schema = app.openapi()

    paths = [path for path in schema["paths"] if "webauthn" in path.lower()]
    schemas = [name for name in schema.get("components", {}).get("schemas", {}) if name.startswith("WebAuthn")]

    assert paths == []
    assert schemas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("credentials", "expected"), [(STORED_CREDENTIALS, True), (None, False)])
async def test_user_detail_reports_webauthn_enabled_from_the_column(
    client: AsyncClient, db_session: AsyncSession, credentials: dict | None, expected: bool
):
    """The user detail says ``webauthn_enabled`` true exactly when the column holds a credential."""
    admin, org = await create_test_user_with_org(
        db_session,
        email="detail-admin@example.com",
        role=UserRole.ORG_ADMIN,
        org_slug="webauthn-detail-org",
    )
    user = await create_test_user(
        db_session,
        email="detail-user@example.com",
        organization=org,
        webauthn_credentials=credentials,
    )

    response = await client.get(f"/api/orgs/{org.slug}/users/{user.id}", headers=get_auth_headers(admin))

    assert response.status_code == 200
    assert response.json()["webauthn_enabled"] is expected


@pytest.mark.asyncio
async def test_login_without_totp_ignores_a_stored_credential(client: AsyncClient, db_session: AsyncSession):
    """An account with a stored credential and no TOTP signs in with the password alone."""
    user = await create_test_user(
        db_session,
        email="stored-credential-login@example.com",
        password=PASSWORD,
        webauthn_credentials=STORED_CREDENTIALS,
    )

    response = await client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "access_token" in response.cookies
    await db_session.refresh(user)
    assert user.webauthn_credentials == STORED_CREDENTIALS


@pytest.mark.asyncio
async def test_login_with_totp_completes_beside_a_stored_credential(client: AsyncClient, db_session: AsyncSession):
    """An account with TOTP and a stored credential asks for the second factor, and TOTP completes the sign-in."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="stored-credential-totp@example.com",
        password=PASSWORD,
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
        webauthn_credentials=STORED_CREDENTIALS,
    )

    login = await client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["status"] == "2fa_required"

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": pyotp.TOTP(secret).now()},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "access_token" in response.cookies
    await db_session.refresh(user)
    assert user.webauthn_credentials == STORED_CREDENTIALS
