"""
Transactional email tests.

Two layers:

- the composed messages: bilingual copy, escaping, both MIME parts, links built
  from the configured base URL and not from a request header;
- the flows that send: password reset, the admin-triggered reset,
  the change confirmation, the operator's delivery probe.

Nothing here touches a network. ``recording_mail`` installs the logging
transport. The SMTP path is not exercised in CI.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MailSettings
from app.models.password_reset_token import PasswordResetToken
from app.models.user import UserRole
from app.services import mail
from app.services.mail import templates
from app.services.mail.transport import LoggingTransport, MailTransportError, OutgoingMessage
from tests.factories import create_test_user_with_org, get_auth_headers


@pytest_asyncio.fixture
async def recording_mail():
    transport = LoggingTransport()
    mail.set_transport(transport)
    yield transport
    mail.set_transport(None)


# ============================================================================
# Message composition
# ============================================================================


@pytest.mark.parametrize("language", ["de", "en"])
def test_every_message_type_exists_in_both_languages(language: str):
    """No message may be missing a translation."""
    messages = [
        templates.invitation_message(
            to="a@b.de",
            language=language,
            organization_name="Stadt Augsburg",
            role="coordinator",
            inviter_name="Aaron",
            accept_url="https://app.clavis-rallye.de/accept-invite?token=t",
            expires_in_days=7,
        ),
        templates.password_reset_message(
            to="a@b.de",
            language=language,
            organization_name="Stadt Augsburg",
            display_name="Test",
            reset_url="https://app.clavis-rallye.de/reset-password?token=t",
            expires_in_minutes=60,
        ),
        templates.password_changed_message(
            to="a@b.de",
            language=language,
            organization_name="Stadt Augsburg",
            display_name="Test",
            changed_at="27.07.2026, 19:00 UTC",
        ),
        templates.role_changed_message(
            to="a@b.de",
            language=language,
            organization_name="Stadt Augsburg",
            display_name="Test",
            new_role="creator",
        ),
        templates.test_message(
            to="a@b.de", language=language, organization_name="Stadt Augsburg", triggered_by="Aaron"
        ),
    ]
    for message in messages:
        assert message.subject.strip(), f"empty subject for {message.kind}/{language}"
        assert message.text_body.strip(), f"empty text part for {message.kind}/{language}"
        assert message.html_body.strip(), f"empty html part for {message.kind}/{language}"
        assert f'<html lang="{language}"' in message.html_body


def test_unknown_language_falls_back_to_german():
    """A locale we have no copy for must not produce an empty mail."""
    assert templates.normalize_language("fr") == "de"
    assert templates.normalize_language(None) == "de"
    assert templates.normalize_language("en-GB") == "en"
    assert templates.normalize_language("DE_de") == "de"


def test_interpolated_values_are_html_escaped():
    """A display name is user-controlled input."""
    message = templates.invitation_message(
        to="a@b.de",
        language="de",
        organization_name='Stadt "><img src=x onerror=alert(1)>',
        role="coordinator",
        inviter_name="<script>alert(1)</script>",
        accept_url="https://app.clavis-rallye.de/accept-invite?token=t",
        expires_in_days=7,
    )
    # What matters is that no payload survives as *markup*. The words remain as
    # visible text — that is escaping working, not escaping failing.
    assert "<script" not in message.html_body
    assert "<img" not in message.html_body
    assert "&lt;script&gt;" in message.html_body
    assert "&lt;img src=x onerror=alert(1)&gt;" in message.html_body
    assert "&quot;&gt;" in message.html_body


def test_text_part_carries_the_link_too():
    """A plain-text reader must not get a mail whose only action is missing."""
    url = "https://app.clavis-rallye.de/accept-invite?token=abc123"
    message = templates.invitation_message(
        to="a@b.de",
        language="de",
        organization_name="Stadt Augsburg",
        role="participant",
        inviter_name="Aaron",
        accept_url=url,
        expires_in_days=7,
    )
    assert url in message.text_body
    assert url in message.html_body


def test_mime_message_has_both_parts_and_a_reply_to():
    """The rendered MIME must be multipart/alternative with a usable Reply-To."""
    settings = MailSettings(
        smtp_host="mail.example.org",
        mail_from_address="noreply@clavis-rallye.de",
        mail_from_name="Clavis",
        mail_reply_to="team@clavis-rallye.de",
    )
    message = OutgoingMessage(to="a@b.de", subject="Test", text_body="text", html_body="<p>html</p>", kind="test")
    mime = message.to_mime(settings)

    assert mime["From"] == "Clavis <noreply@clavis-rallye.de>"
    assert mime["Reply-To"] == "team@clavis-rallye.de"
    assert mime["Auto-Submitted"] == "auto-generated"
    subtypes = {part.get_content_type() for part in mime.walk()}
    assert "text/plain" in subtypes
    assert "text/html" in subtypes


def test_links_come_from_configuration_not_from_a_header(monkeypatch):
    """Host-header poisoning must not be able to redirect a reset link."""
    # Patch where the name is looked up, not where it is re-exported: the URL
    # builders resolve ``mail_settings`` in their own module namespace.
    from app.services.mail import service as mail_service

    monkeypatch.setattr(
        mail_service,
        "mail_settings",
        lambda: MailSettings(public_base_url="https://app.clavis-rallye.de"),
    )
    assert mail.invitation_url("tok") == "https://app.clavis-rallye.de/accept-invite?token=tok"
    assert mail.password_reset_url("tok") == "https://app.clavis-rallye.de/reset-password?token=tok"


def test_public_base_url_rejects_a_relative_value():
    """A misconfigured base URL must fail at startup, not silently produce broken links."""
    with pytest.raises(ValueError, match="must start with http"):
        MailSettings(public_base_url="app.clavis-rallye.de")


def test_trailing_slash_does_not_produce_a_double_slash():
    settings = MailSettings(public_base_url="https://app.clavis-rallye.de/")
    assert settings.public_base_url == "https://app.clavis-rallye.de"


def test_mail_is_disabled_without_a_host():
    """An empty SMTP host means "log, do not send"."""
    assert MailSettings(smtp_host="", mail_from_address="noreply@example.org").enabled is False
    assert MailSettings(smtp_host="mail.example.org", mail_from_address="noreply@example.org").enabled is True


def test_mail_is_disabled_without_a_sender_address():
    """The sender address has no default: without it the app logs instead of sending."""
    assert MailSettings(smtp_host="mail.example.org", mail_from_address="").enabled is False
    assert MailSettings(smtp_host="mail.example.org", mail_from_address="  ").enabled is False


def test_mail_defaults_name_no_real_mailbox():
    """An installation that sets nothing must not send as, or point answers at, somebody else's address."""
    settings = MailSettings(_env_file=None)
    assert settings.mail_from_address == ""
    assert settings.mail_reply_to == ""


# ============================================================================
# Password reset
# ============================================================================


@pytest.mark.asyncio
async def test_password_forgot_sends_a_reset_mail(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    user, _org = await create_test_user_with_org(
        db_session, email="vergesslich@augsburg.example", role=UserRole.COORDINATOR, org_slug="reset-org-1"
    )

    response = await client.post("/api/auth/password-forgot", json={"email": user.email})

    assert response.status_code == 200
    assert len(recording_mail.sent) == 1
    message = recording_mail.sent[0]
    assert message.kind == "password_reset"
    assert message.to == "vergesslich@augsburg.example"
    assert "/reset-password?token=" in message.text_body


@pytest.mark.asyncio
async def test_password_forgot_for_an_unknown_address_sends_nothing(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """No mail, and a response identical to the known-address case."""
    await create_test_user_with_org(
        db_session, email="existiert@augsburg.example", role=UserRole.COORDINATOR, org_slug="reset-org-2"
    )

    known = await client.post("/api/auth/password-forgot", json={"email": "existiert@augsburg.example"})
    recording_mail.clear()
    unknown = await client.post("/api/auth/password-forgot", json={"email": "gibt-es-nicht@augsburg.example"})

    assert unknown.status_code == known.status_code
    assert unknown.json() == known.json()
    assert recording_mail.sent == []


@pytest.mark.asyncio
async def test_reset_token_is_never_written_to_the_log(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport, caplog
):
    """A reset link in the application log is a standing takeover primitive."""
    user, _org = await create_test_user_with_org(
        db_session, email="kein-log@augsburg.example", role=UserRole.COORDINATOR, org_slug="reset-org-3"
    )

    with caplog.at_level("INFO"):
        await client.post("/api/auth/password-forgot", json={"email": user.email})

    token = recording_mail.sent[0].text_body.split("token=")[1].split()[0]
    assert token not in caplog.text


@pytest.mark.asyncio
async def test_password_reset_confirms_by_mail(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """Completing a reset notifies the account owner."""
    user, _org = await create_test_user_with_org(
        db_session, email="bestaetigung@augsburg.example", role=UserRole.COORDINATOR, org_slug="reset-org-4"
    )
    await client.post("/api/auth/password-forgot", json={"email": user.email})
    token = recording_mail.sent[0].text_body.split("token=")[1].split()[0]
    recording_mail.clear()

    response = await client.post(
        "/api/auth/password-reset", json={"token": token, "new_password": "GanzNeuesPasswort123!"}
    )

    assert response.status_code == 200
    assert [m.kind for m in recording_mail.sent] == ["password_changed"]

    login = await client.post("/api/auth/login", json={"email": user.email, "password": "GanzNeuesPasswort123!"})
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_send_a_reset_link_for_a_user(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """The admin triggers the mail but never sees the link."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin-reset@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="reset-org-5"
    )
    from tests.factories import create_test_user

    user = await create_test_user(
        db_session,
        email="hilfe@augsburg.example",
        display_name="Braucht Hilfe",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    response = await client.post(
        f"/api/orgs/{org.slug}/users/{user.id}/send-password-reset", headers=get_auth_headers(admin)
    )

    assert response.status_code == 202
    assert [m.to for m in recording_mail.sent] == ["hilfe@augsburg.example"]
    # The link is in the mail, not in the API response.
    assert "token=" not in response.text

    tokens = await db_session.execute(select(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    assert len(list(tokens.scalars().all())) == 1


@pytest.mark.asyncio
async def test_password_change_confirms_by_mail(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    user, _org = await create_test_user_with_org(
        db_session,
        email="selbst@augsburg.example",
        password="AltesPasswort123!",
        role=UserRole.COORDINATOR,
        org_slug="reset-org-6",
    )

    response = await client.post(
        "/api/auth/password-change",
        headers=get_auth_headers(user),
        json={"old_password": "AltesPasswort123!", "new_password": "NeuesPasswort456!"},
    )

    assert response.status_code == 200
    assert [m.kind for m in recording_mail.sent] == ["password_changed"]


@pytest.mark.asyncio
async def test_failed_password_change_sends_nothing(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """A rejected change must not tell the owner their password moved."""
    user, _org = await create_test_user_with_org(
        db_session,
        email="falsch@augsburg.example",
        password="AltesPasswort123!",
        role=UserRole.COORDINATOR,
        org_slug="reset-org-7",
    )

    response = await client.post(
        "/api/auth/password-change",
        headers=get_auth_headers(user),
        json={"old_password": "DasIstFalsch123!", "new_password": "NeuesPasswort456!"},
    )

    assert response.status_code == 401
    assert recording_mail.sent == []


# ============================================================================
# Operator probe
# ============================================================================


@pytest.mark.asyncio
async def test_mail_status_reports_disabled_without_configuration(client: AsyncClient, db_session: AsyncSession):
    admin, org = await create_test_user_with_org(
        db_session, email="admin-status@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="mail-org-1"
    )

    response = await client.get(f"/api/orgs/{org.slug}/admin/mail/status", headers=get_auth_headers(admin))

    assert response.status_code == 200
    assert response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_mail_test_reports_the_transport_error(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """The probe's whole purpose is surfacing the error — and never the password."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin-test@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="mail-org-2"
    )

    class ExplodingTransport(LoggingTransport):
        async def send(self, message, settings):  # type: ignore[no-untyped-def]
            raise MailTransportError("SMTPAuthenticationError: 535 authentication failed")

    monkeypatch.setattr(mail, "is_enabled", lambda: True)
    mail.set_transport(ExplodingTransport())
    try:
        response = await client.post(
            f"/api/orgs/{org.slug}/admin/mail/test",
            headers=get_auth_headers(admin),
            json={"email": "aaron@example.com", "language": "de"},
        )
    finally:
        mail.set_transport(None)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "535" in body["error"]


@pytest.mark.asyncio
async def test_mail_test_succeeds_against_a_working_transport(
    client: AsyncClient, db_session: AsyncSession, monkeypatch, recording_mail: LoggingTransport
):
    admin, org = await create_test_user_with_org(
        db_session, email="admin-ok@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="mail-org-3"
    )
    monkeypatch.setattr(mail, "is_enabled", lambda: True)

    response = await client.post(
        f"/api/orgs/{org.slug}/admin/mail/test",
        headers=get_auth_headers(admin),
        json={"email": "aaron@example.com", "language": "en"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert [m.kind for m in recording_mail.sent] == ["test"]


@pytest.mark.asyncio
async def test_non_admin_cannot_probe_mail(client: AsyncClient, db_session: AsyncSession):
    coordinator, org = await create_test_user_with_org(
        db_session, email="koord@augsburg.example", role=UserRole.COORDINATOR, org_slug="mail-org-4"
    )

    response = await client.post(
        f"/api/orgs/{org.slug}/admin/mail/test",
        headers=get_auth_headers(coordinator),
        json={"email": "aaron@example.com"},
    )

    assert response.status_code == 403
