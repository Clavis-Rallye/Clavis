"""
Integration tests for user invitations and transactional email.

Covers invite → mail → accept → log in with the right role, plus the states a link
can be in when it no longer works, and the guarantee that a broken mail server
never breaks the request that triggered it.

Mail never leaves the process: the ``recording_mail`` fixture installs a
``LoggingTransport`` so assertions run against the composed messages.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.instance import create_instance_admin
from app.auth.tokens import create_instance_admin_token
from app.models.user import UserRole
from app.models.user_invitation import UserInvitation
from app.repositories.invitation import InvitationRepository
from app.services import mail
from app.services.mail.transport import LoggingTransport, MailTransportError
from tests.factories import (
    create_test_user,
    create_test_user_with_org,
    get_auth_headers,
)


@pytest_asyncio.fixture
async def recording_mail():
    """Install a transport that records messages instead of sending them."""
    transport = LoggingTransport()
    mail.set_transport(transport)
    yield transport
    mail.set_transport(None)


async def _invite(
    client: AsyncClient,
    org_slug: str,
    headers: dict[str, str],
    *,
    email: str = "neue.kollegin@augsburg.example",
    role: str = "coordinator",
    language: str = "de",
    display_name: str | None = None,
):
    body: dict[str, object] = {"email": email, "role": role, "language": language}
    if display_name is not None:
        body["display_name"] = display_name
    return await client.post(f"/api/orgs/{org_slug}/invitations/", headers=headers, json=body)


async def _token_for(db_session: AsyncSession, email: str) -> str:
    """Re-issue a known token for an invitation.

    The raw token only ever exists inside the mail, so a test that needs to
    follow the link mints a fresh one through the same repository the endpoint
    uses. This keeps the token-hashing path under test instead of bypassing it.
    """
    result = await db_session.execute(select(UserInvitation).where(UserInvitation.email == email))
    invitation = result.scalar_one()
    return await InvitationRepository(db_session).rotate_token(invitation, expiry_days=7)


# ============================================================================
# Invite by email and role
# ============================================================================


@pytest.mark.asyncio
async def test_invite_sends_mail_and_creates_pending_invitation(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """An invitation is stored and exactly one mail is composed for the invitee."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="stadt-augsburg"
    )

    response = await _invite(client, org.slug, get_auth_headers(admin), display_name="Neue Kollegin")

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "neue.kollegin@augsburg.example"
    assert data["role"] == "coordinator"
    assert data["display_name"] == "Neue Kollegin"

    assert len(recording_mail.sent) == 1
    message = recording_mail.sent[0]
    assert message.to == "neue.kollegin@augsburg.example"
    assert message.kind == "invitation"
    # The link must be absolute and carry a token.
    assert "/accept-invite?token=" in message.text_body
    assert "/accept-invite?token=" in message.html_body


@pytest.mark.asyncio
async def test_accept_creates_user_with_invited_role(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """Redeeming a link produces a working account with the invited role."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin2@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-2"
    )
    await _invite(client, org.slug, get_auth_headers(admin), email="creator@augsburg.example", role="creator")
    token = await _token_for(db_session, "creator@augsburg.example")

    preview = await client.get(f"/api/invitations/{token}")
    assert preview.status_code == 200
    assert preview.json()["email"] == "creator@augsburg.example"
    assert preview.json()["role"] == "creator"
    assert preview.json()["organization_name"] == org.name

    accept = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Neuer Ersteller", "password": "SicheresPasswort123!"},
    )
    assert accept.status_code == 200
    assert accept.json()["role"] == "creator"

    login = await client.post(
        "/api/auth/login",
        json={"email": "creator@augsburg.example", "password": "SicheresPasswort123!"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_invitation_token_is_single_use(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """The same link cannot mint a second account."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin3@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-3"
    )
    await _invite(client, org.slug, get_auth_headers(admin), email="once@augsburg.example")
    token = await _token_for(db_session, "once@augsburg.example")

    first = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Erste", "password": "SicheresPasswort123!"},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Zweite", "password": "AnderesPasswort123!"},
    )
    assert second.status_code == 410
    assert second.json()["detail"] == "invitation_already_accepted"


@pytest.mark.asyncio
async def test_accept_after_the_address_got_an_account_answers_a_coded_409(
    client: AsyncClient, db_session: AsyncSession
):
    """An account made between invitation and click refuses the accept with a code.

    The code lets the accept page explain the situation in German instead of
    showing a raw English text.
    """
    admin, org = await create_test_user_with_org(
        db_session, email="admin-race@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-race"
    )
    await _invite(client, org.slug, get_auth_headers(admin), email="dazwischen@augsburg.example")
    token = await _token_for(db_session, "dazwischen@augsburg.example")
    await create_test_user(db_session, email="dazwischen@augsburg.example", organization=org)

    response = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Zu spät", "password": "SicheresPasswort123!"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "EMAIL_HAS_ACCOUNT"


@pytest.mark.asyncio
async def test_invite_existing_user_email_is_rejected(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """An address that already has an account cannot be invited, and no mail goes out."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin4@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-4"
    )
    await create_test_user(
        db_session,
        email="besetzt@augsburg.example",
        display_name="Besetzt",
        role=UserRole.PARTICIPANT,
        organization=org,
    )

    response = await _invite(client, org.slug, get_auth_headers(admin), email="besetzt@augsburg.example")

    assert response.status_code == 409
    assert recording_mail.sent == []


@pytest.mark.asyncio
async def test_guest_role_cannot_be_invited(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """Guests exist only inside a run and are never invited by mail."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin5@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-5"
    )

    response = await _invite(client, org.slug, get_auth_headers(admin), role="guest")

    assert response.status_code == 422
    assert recording_mail.sent == []


@pytest.mark.asyncio
async def test_accept_enforces_password_policy(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """The accept form applies the same policy as registration."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin6@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-6"
    )
    await _invite(client, org.slug, get_auth_headers(admin), email="schwach@augsburg.example")
    token = await _token_for(db_session, "schwach@augsburg.example")

    response = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Schwach", "password": "kurz"},
    )

    assert response.status_code in (400, 422)


@pytest.mark.asyncio
async def test_org_admin_can_be_invited(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """A second administrator can be added.

    Bootstrap registration is shut after the first organisation, so without an
    org_admin invitation there is no path to a second administrator at all.
    """
    admin, org = await create_test_user_with_org(
        db_session, email="admin7@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-7"
    )
    response = await _invite(
        client, org.slug, get_auth_headers(admin), email="zweiter@augsburg.example", role="org_admin"
    )
    assert response.status_code == 201

    token = await _token_for(db_session, "zweiter@augsburg.example")
    accept = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Zweiter Admin", "password": "SicheresPasswort123!"},
    )
    assert accept.status_code == 200
    assert accept.json()["role"] == "org_admin"


# ============================================================================
# Managing outstanding invitations
# ============================================================================


@pytest.mark.asyncio
async def test_list_shows_pending_invitations_only(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """Accepted invitations drop out of the pending list."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin8@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-8"
    )
    headers = get_auth_headers(admin)
    await _invite(client, org.slug, headers, email="offen@augsburg.example")
    await _invite(client, org.slug, headers, email="wird-akzeptiert@augsburg.example")

    listing = await client.get(f"/api/orgs/{org.slug}/invitations/", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 2

    token = await _token_for(db_session, "wird-akzeptiert@augsburg.example")
    await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Akzeptiert", "password": "SicheresPasswort123!"},
    )

    listing = await client.get(f"/api/orgs/{org.slug}/invitations/", headers=headers)
    emails = [item["email"] for item in listing.json()["items"]]
    assert emails == ["offen@augsburg.example"]


@pytest.mark.asyncio
async def test_resend_invalidates_the_previous_link(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """Resending must not leave two working links in the world."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin9@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-9"
    )
    headers = get_auth_headers(admin)
    await _invite(client, org.slug, headers, email="nochmal@augsburg.example")
    old_token = await _token_for(db_session, "nochmal@augsburg.example")

    listing = await client.get(f"/api/orgs/{org.slug}/invitations/", headers=headers)
    invitation_id = listing.json()["items"][0]["id"]

    recording_mail.clear()
    resend = await client.post(f"/api/orgs/{org.slug}/invitations/{invitation_id}/resend", headers=headers)
    assert resend.status_code == 200
    assert len(recording_mail.sent) == 1

    stale = await client.get(f"/api/invitations/{old_token}")
    assert stale.status_code == 404


@pytest.mark.asyncio
async def test_revoke_kills_the_link(client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport):
    """A withdrawn invitation reports itself as revoked, not merely missing."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin10@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-10"
    )
    headers = get_auth_headers(admin)
    await _invite(client, org.slug, headers, email="zurueckgezogen@augsburg.example")
    token = await _token_for(db_session, "zurueckgezogen@augsburg.example")

    listing = await client.get(f"/api/orgs/{org.slug}/invitations/", headers=headers)
    invitation_id = listing.json()["items"][0]["id"]

    revoke = await client.delete(f"/api/orgs/{org.slug}/invitations/{invitation_id}", headers=headers)
    assert revoke.status_code == 204

    response = await client.get(f"/api/invitations/{token}")
    assert response.status_code == 410
    assert response.json()["detail"] == "invitation_revoked"


@pytest.mark.asyncio
async def test_expired_invitation_says_so(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """An expired link is distinguishable from an unknown one."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin11@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-11"
    )
    await _invite(client, org.slug, get_auth_headers(admin), email="abgelaufen@augsburg.example")
    token = await _token_for(db_session, "abgelaufen@augsburg.example")

    result = await db_session.execute(
        select(UserInvitation).where(UserInvitation.email == "abgelaufen@augsburg.example")
    )
    invitation = result.scalar_one()
    invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    response = await client.get(f"/api/invitations/{token}")
    assert response.status_code == 410
    assert response.json()["detail"] == "invitation_expired"


@pytest.mark.asyncio
async def test_reinviting_an_expired_address_works(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """The partial unique index must not lock an address out forever.

    An expired invitation still occupies the one-live-invitation-per-address
    slot. Inviting again has to rotate it rather than hit the index.
    """
    admin, org = await create_test_user_with_org(
        db_session, email="admin12@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-12"
    )
    headers = get_auth_headers(admin)
    await _invite(client, org.slug, headers, email="nochmal2@augsburg.example")

    result = await db_session.execute(select(UserInvitation).where(UserInvitation.email == "nochmal2@augsburg.example"))
    invitation = result.scalar_one()
    invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    again = await _invite(client, org.slug, headers, email="nochmal2@augsburg.example", role="creator")
    assert again.status_code == 201
    assert again.json()["role"] == "creator"


@pytest.mark.asyncio
async def test_invitations_are_scoped_to_the_admins_organization(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """An admin cannot see or revoke another organisation's invitations."""
    admin_a, org_a = await create_test_user_with_org(
        db_session, email="admin-a2@example.com", role=UserRole.ORG_ADMIN, org_slug="org-alpha"
    )
    admin_b, org_b = await create_test_user_with_org(
        db_session, email="admin-b2@example.com", role=UserRole.ORG_ADMIN, org_slug="org-beta"
    )
    await _invite(client, org_a.slug, get_auth_headers(admin_a), email="nur-fuer-a@example.com")

    listing_b = await client.get(f"/api/orgs/{org_b.slug}/invitations/", headers=get_auth_headers(admin_b))
    assert listing_b.json()["total"] == 0

    listing_a = await client.get(f"/api/orgs/{org_a.slug}/invitations/", headers=get_auth_headers(admin_a))
    invitation_id = listing_a.json()["items"][0]["id"]

    cross = await client.delete(
        f"/api/orgs/{org_b.slug}/invitations/{invitation_id}", headers=get_auth_headers(admin_b)
    )
    assert cross.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_invite(client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport):
    """Only org_admins may hand out accounts."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="koordination@augsburg.example", role=UserRole.COORDINATOR, org_slug="augsburg-13"
    )

    response = await _invite(client, org.slug, get_auth_headers(coordinator))

    assert response.status_code == 403
    assert recording_mail.sent == []


# ============================================================================
# Robustness — a broken mail server must not break the application
# ============================================================================


@pytest.mark.asyncio
async def test_invitation_survives_a_failing_mail_server(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """A transport error is logged, not propagated: the invitation still exists.

    The admin can then press "resend" once the mail server is back, instead of
    discovering that half the invitations were silently lost.
    """

    class ExplodingTransport(LoggingTransport):
        async def send(self, message, settings):  # type: ignore[no-untyped-def]
            raise MailTransportError("SMTPAuthenticationError: 535 authentication failed")

    mail.set_transport(ExplodingTransport())
    try:
        admin, org = await create_test_user_with_org(
            db_session, email="admin14@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-14"
        )
        headers = get_auth_headers(admin)

        response = await _invite(client, org.slug, headers, email="trotzdem@augsburg.example")
        assert response.status_code == 201

        listing = await client.get(f"/api/orgs/{org.slug}/invitations/", headers=headers)
        assert listing.json()["total"] == 1
    finally:
        mail.set_transport(None)


# ============================================================================
# Retention — an unaccepted invitation is an email address with no purpose left
# ============================================================================


@pytest.mark.asyncio
async def test_retention_sweeps_dead_invitations_but_spares_live_ones(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """Expired, revoked and accepted invitations go; a pending one stays."""
    from app.services.retention_tasks import DEAD_INVITATION_GRACE_DAYS, sweep_dead_invitations

    admin, org = await create_test_user_with_org(
        db_session, email="admin-ret@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-ret"
    )
    headers = get_auth_headers(admin)
    long_ago = datetime.now(UTC) - timedelta(days=DEAD_INVITATION_GRACE_DAYS + 1)

    await _invite(client, org.slug, headers, email="laengst-abgelaufen@augsburg.example")
    await _invite(client, org.slug, headers, email="noch-offen@augsburg.example")

    result = await db_session.execute(
        select(UserInvitation).where(UserInvitation.email == "laengst-abgelaufen@augsburg.example")
    )
    stale = result.scalar_one()
    stale.expires_at = long_ago
    await db_session.commit()

    swept = await sweep_dead_invitations(db_session)
    await db_session.commit()

    assert swept == 1
    remaining = await db_session.execute(select(UserInvitation.email))
    assert sorted(remaining.scalars().all()) == ["noch-offen@augsburg.example"]


@pytest.mark.asyncio
async def test_retention_keeps_a_recently_expired_invitation(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """The grace window exists so an admin can still see that it lapsed."""
    from app.services.retention_tasks import sweep_dead_invitations

    admin, org = await create_test_user_with_org(
        db_session, email="admin-ret2@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-ret2"
    )
    await _invite(client, org.slug, get_auth_headers(admin), email="gerade-abgelaufen@augsburg.example")

    result = await db_session.execute(
        select(UserInvitation).where(UserInvitation.email == "gerade-abgelaufen@augsburg.example")
    )
    invitation = result.scalar_one()
    invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    assert await sweep_dead_invitations(db_session) == 0


@pytest.mark.asyncio
async def test_retention_sweep_is_idempotent(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """A second pass over the same state sweeps nothing — the sweeper contract."""
    from app.services.retention_tasks import DEAD_INVITATION_GRACE_DAYS, sweep_dead_invitations

    admin, org = await create_test_user_with_org(
        db_session, email="admin-ret3@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-ret3"
    )
    await _invite(client, org.slug, get_auth_headers(admin), email="weg-damit@augsburg.example")

    result = await db_session.execute(
        select(UserInvitation).where(UserInvitation.email == "weg-damit@augsburg.example")
    )
    invitation = result.scalar_one()
    invitation.expires_at = datetime.now(UTC) - timedelta(days=DEAD_INVITATION_GRACE_DAYS + 1)
    await db_session.commit()

    assert await sweep_dead_invitations(db_session) == 1
    await db_session.commit()
    assert await sweep_dead_invitations(db_session) == 0


@pytest.mark.asyncio
async def test_inviting_a_soft_deleted_users_address_explains_itself(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """A deleted-but-restorable account still owns its address.

    ``ix_users_email_not_null`` covers soft-deleted rows, so the INSERT would
    fail on a constraint the admin cannot see in the user list. The answer has
    to name the situation and point at restore, not just say "already exists".
    """
    admin, org = await create_test_user_with_org(
        db_session, email="admin-del@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-del"
    )
    gone = await create_test_user(
        db_session,
        email="ausgeschieden@augsburg.example",
        display_name="Ausgeschieden",
        role=UserRole.COORDINATOR,
        organization=org,
    )
    gone.is_deleted = True
    gone.deleted_at = datetime.now(UTC)
    await db_session.commit()

    response = await _invite(client, org.slug, get_auth_headers(admin), email="ausgeschieden@augsburg.example")

    assert response.status_code == 409
    assert "deleted account" in response.json()["detail"]
    assert response.json()["error_code"] == "EMAIL_BELONGS_TO_DELETED_USER"
    assert recording_mail.sent == []


@pytest.mark.asyncio
async def test_inviting_an_active_same_org_address_carries_the_user_id(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """An address already active in the *same* organization gets a remedy.

    The dialog can offer to send that account a password reset link, so the
    answer carries the user id.
    """
    admin, org = await create_test_user_with_org(
        db_session, email="admin-active@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-active"
    )
    existing = await create_test_user(
        db_session,
        email="schon-da@augsburg.example",
        role=UserRole.COORDINATOR,
        organization=org,
    )

    response = await _invite(client, org.slug, get_auth_headers(admin), email="schon-da@augsburg.example")

    assert response.status_code == 409
    data = response.json()
    assert data["error_code"] == "EMAIL_BELONGS_TO_USER"
    assert data["detail"] == str(existing.id)
    assert recording_mail.sent == []


@pytest.mark.asyncio
async def test_inviting_a_deactivated_same_org_address_gets_its_own_code(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """A deactivated account in the same organization is named, but carries no id.

    It is a distinct case from an active account (no "send a reset link" remedy
    makes sense for a deactivated account) and from a different organization's
    account.
    """
    admin, org = await create_test_user_with_org(
        db_session, email="admin-inactive@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-inactive"
    )
    await create_test_user(
        db_session,
        email="deaktiviert@augsburg.example",
        role=UserRole.COORDINATOR,
        organization=org,
        is_active=False,
    )

    response = await _invite(client, org.slug, get_auth_headers(admin), email="deaktiviert@augsburg.example")

    assert response.status_code == 409
    data = response.json()
    assert data["error_code"] == "EMAIL_BELONGS_TO_INACTIVE_USER"
    assert recording_mail.sent == []


@pytest.mark.asyncio
async def test_inviting_an_address_from_another_org_is_neutral(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """An address that belongs to an account in another organization must not
    leak that organization's membership: a neutral text, no user id.
    """
    admin, org = await create_test_user_with_org(
        db_session, email="admin-neutral@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-neutral"
    )
    _, other_org = await create_test_user_with_org(
        db_session, email="admin-other@muenchen.de", role=UserRole.ORG_ADMIN, org_slug="muenchen-other"
    )
    existing = await create_test_user(
        db_session,
        email="woanders@muenchen.de",
        role=UserRole.COORDINATOR,
        organization=other_org,
    )

    response = await _invite(client, org.slug, get_auth_headers(admin), email="woanders@muenchen.de")

    assert response.status_code == 409
    data = response.json()
    assert data["error_code"] == "EMAIL_BELONGS_TO_USER"
    assert data["detail"] != str(existing.id)
    assert recording_mail.sent == []


@pytest.mark.asyncio
async def test_list_does_not_claim_delivery_when_mail_is_off(
    client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport
):
    """A mail-less deployment must not show every invitation as delivered."""
    admin, org = await create_test_user_with_org(
        db_session, email="admin-nomail@augsburg.example", role=UserRole.ORG_ADMIN, org_slug="augsburg-nomail"
    )
    headers = get_auth_headers(admin)
    await _invite(client, org.slug, headers, email="kommt-nicht-an@augsburg.example")

    listing = await client.get(f"/api/orgs/{org.slug}/invitations/", headers=headers)

    # The recording transport stands in for "no SMTP host configured".
    assert listing.json()["items"][0]["mail_delivered"] is False


# ============================================================================
# An operator turns the invitation into a takeover at the same moment
# ============================================================================


async def _takeover_while_the_admin_holds_the_old_row(
    concurrent_client: AsyncClient, db_session: AsyncSession, monkeypatch, read: str, slug: str
):
    """An ordinary invitation, and an operator takeover that commits right after the admin read it.

    The interleaving: the admin's request reads the
    ordinary invitation through ``InvitationRepository.<read>``; before it
    writes, the operator's request turns the same row into a takeover and
    commits. The order is steered by the read itself, never by a sleep.

    Returns the admin, the organization, the invitation id and a dict that
    holds the row as the operator left it (``state``) once the read ran.
    """
    admin, org = await create_test_user_with_org(
        db_session, email=f"admin@{slug}.example", role=UserRole.ORG_ADMIN, org_slug=slug
    )
    operator = await create_instance_admin(db_session, f"op@{slug}.example", "OperatorPass123!")
    invitation, _raw = await InvitationRepository(db_session).create(
        organization_id=org.id,
        email=f"successor@{slug}.example",
        role=UserRole.PARTICIPANT,
        invited_by_user_id=admin.id,
        invited_by_name=admin.display_name,
        expiry_days=7,
    )
    await db_session.commit()
    operator_headers = {"Authorization": f"Bearer {create_instance_admin_token(operator.id)}"}
    after_operator: dict[str, tuple] = {}

    original = getattr(InvitationRepository, read)
    fired = False

    async def read_then_takeover(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal fired
        result = await original(*args, **kwargs)
        if not fired:
            fired = True
            taken = await concurrent_client.post(
                f"/api/instance/organizations/{org.slug}/admin-invitations",
                headers=operator_headers,
                json={"email": invitation.email, "replace_admin_ids": [str(admin.id)]},
            )
            assert taken.status_code == 201, taken.text
            after_operator["state"] = await _invitation_state(db_session, invitation.id)
        return result

    monkeypatch.setattr(InvitationRepository, read, read_then_takeover)
    return admin, org, invitation.id, after_operator


async def _invitation_state(db_session: AsyncSession, invitation_id) -> tuple:  # noqa: ANN001
    row = (
        await db_session.execute(
            select(UserInvitation).where(UserInvitation.id == invitation_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    state = (
        row.role,
        row.token_hash,
        row.expires_at,
        list(row.replaces_user_ids or []),
        row.invited_by_user_id,
        row.revoked_at,
    )
    # End the read transaction, so the next look sees what was committed since.
    await db_session.rollback()
    return state


@pytest.mark.asyncio
async def test_renewal_does_not_overwrite_a_takeover_committed_after_its_read(
    concurrent_client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport, monkeypatch
):
    """The admin's renewal read an ordinary invitation; the takeover committed before its UPDATE."""
    admin, org, invitation_id, after_operator = await _takeover_while_the_admin_holds_the_old_row(
        concurrent_client, db_session, monkeypatch, "get_open_for_email", "race-renew"
    )

    admin_id = str(admin.id)

    renewed = await _invite(
        concurrent_client, org.slug, get_auth_headers(admin), email="successor@race-renew.example", role="coordinator"
    )

    # The answer of the route for an existing takeover.
    assert renewed.status_code == 409, renewed.text
    state = await _invitation_state(db_session, invitation_id)
    assert state == after_operator["state"]
    assert state[0] == UserRole.ORG_ADMIN
    assert state[3] == [admin_id]
    # Only the operator's mail went out; the admin got no token.
    assert len(recording_mail.sent) == 1


@pytest.mark.asyncio
async def test_resend_does_not_rotate_a_takeover_committed_after_its_read(
    concurrent_client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport, monkeypatch
):
    """The resend read an ordinary invitation; token and expiry of the takeover stay the operator's."""
    admin, org, invitation_id, after_operator = await _takeover_while_the_admin_holds_the_old_row(
        concurrent_client, db_session, monkeypatch, "get_by_id_in_org", "race-resend"
    )

    resent = await concurrent_client.post(
        f"/api/orgs/{org.slug}/invitations/{invitation_id}/resend", headers=get_auth_headers(admin)
    )

    assert resent.status_code == 404, resent.text
    assert await _invitation_state(db_session, invitation_id) == after_operator["state"]
    assert len(recording_mail.sent) == 1


@pytest.mark.asyncio
async def test_revoke_does_not_withdraw_a_takeover_committed_after_its_read(
    concurrent_client: AsyncClient, db_session: AsyncSession, recording_mail: LoggingTransport, monkeypatch
):
    """The revocation read an ordinary invitation; the takeover stays open."""
    admin, org, invitation_id, after_operator = await _takeover_while_the_admin_holds_the_old_row(
        concurrent_client, db_session, monkeypatch, "get_by_id_in_org", "race-revoke"
    )

    revoked = await concurrent_client.delete(
        f"/api/orgs/{org.slug}/invitations/{invitation_id}", headers=get_auth_headers(admin)
    )

    assert revoked.status_code == 404, revoked.text
    state = await _invitation_state(db_session, invitation_id)
    assert state == after_operator["state"]
    assert state[5] is None
