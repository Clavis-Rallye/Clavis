"""
Admin succession.

- The next admin reaches everything an earlier admin left behind: nothing in
  the organization is bound to the person who created or sent it.
- Worst case, no admin can act: the operator's "Admin einladen" takes over
  from chosen admins. On acceptance their accounts are deactivated and their
  sessions end, both logs record it, and the new admin sees what was taken over.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.instance import create_instance_admin
from app.auth.tokens import create_instance_admin_token
from app.models.user import User, UserRole
from app.models.user_invitation import UserInvitation
from app.repositories.invitation import InvitationRepository
from app.routers import instance as instance_module
from app.routers import invitations as invitations_module
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers


class _AuditRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **kwargs: object) -> None:
        self.events.append({"event": event, **kwargs})

    def named(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]


async def _operator_headers(db_session: AsyncSession) -> dict[str, str]:
    operator = await create_instance_admin(db_session, f"op-{uuid4().hex[:6]}@example.com", "OperatorPass123!")
    return {"Authorization": f"Bearer {create_instance_admin_token(operator.id)}"}


async def _accept(client: AsyncClient, db_session: AsyncSession, email: str) -> None:
    invitation = (await db_session.execute(select(UserInvitation).where(UserInvitation.email == email))).scalars().one()
    token = await InvitationRepository(db_session).rotate_token(invitation, expiry_days=7)
    await db_session.commit()
    response = await client.post(
        f"/api/invitations/{token}/accept", json={"display_name": "Nachfolge", "password": "SicheresPasswort123!"}
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_a_second_admin_reaches_what_the_first_one_left(client: AsyncClient, db_session: AsyncSession):
    """An invitation and a station of the first admin are open to the second admin, who can also deactivate the first."""
    first, org = await create_test_user_with_org(
        db_session, email="erste@stadt.example", role=UserRole.ORG_ADMIN, org_slug="succession-a"
    )
    second = await create_test_user(db_session, email="zweite@stadt.example", role=UserRole.ORG_ADMIN, organization=org)
    first_headers, second_headers = get_auth_headers(first), get_auth_headers(second)

    sent = await client.post(
        f"/api/orgs/{org.slug}/invitations/",
        headers=first_headers,
        json={"email": "offen@stadt.example", "role": "coordinator", "language": "de"},
    )
    assert sent.status_code == 201, sent.text
    station = await client.post(
        f"/api/orgs/{org.slug}/stations/",
        headers=first_headers,
        json={
            "title": {"de": "Station der Ersten"},
            "description": {"de": "Text"},
            "location_lat": 48.37,
            "location_lng": 10.89,
        },
    )
    assert station.status_code == 201, station.text

    listed = await client.get(f"/api/orgs/{org.slug}/invitations/", headers=second_headers)
    invitation_id = listed.json()[0]["id"] if isinstance(listed.json(), list) else listed.json()["items"][0]["id"]
    assert (
        await client.post(f"/api/orgs/{org.slug}/invitations/{invitation_id}/resend", headers=second_headers)
    ).status_code == 200
    assert (
        await client.delete(f"/api/orgs/{org.slug}/invitations/{invitation_id}", headers=second_headers)
    ).status_code in (200, 204)
    updated = await client.put(
        f"/api/orgs/{org.slug}/stations/{station.json()['id']}",
        headers=second_headers,
        json={"title": {"de": "Station übernommen"}},
    )
    assert updated.status_code == 200, updated.text
    # The second admin also deactivates the first, who left.
    deactivated = await client.put(
        f"/api/orgs/{org.slug}/users/{first.id}", headers=second_headers, json={"is_active": False}
    )
    assert deactivated.status_code == 200, deactivated.text


@pytest.mark.asyncio
async def test_the_operator_takeover_deactivates_the_replaced_admins_on_acceptance(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """On acceptance of an operator takeover, the replaced admin is deactivated and loses the session. The logs record it."""
    instance_audit, invitation_audit = _AuditRecorder(), _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", instance_audit)
    monkeypatch.setattr(invitations_module, "record_audit_event", invitation_audit)
    gone, org = await create_test_user_with_org(
        db_session, email="weg@stadt.example", role=UserRole.ORG_ADMIN, org_slug="succession-b"
    )
    stays = await create_test_user(db_session, email="bleibt@stadt.example", role=UserRole.ORG_ADMIN, organization=org)
    coordinator = await create_test_user(
        db_session, email="spielleitung@stadt.example", role=UserRole.COORDINATOR, organization=org
    )
    operator = await _operator_headers(db_session)
    gone_headers = get_auth_headers(gone)
    gone_id, stays_id, slug = gone.id, stays.id, org.slug

    admins = await client.get(f"/api/instance/organizations/{org.slug}/admins", headers=operator)
    assert admins.status_code == 200
    assert {a["email"] for a in admins.json()["items"]} == {"weg@stadt.example", "bleibt@stadt.example"}
    assert len(instance_audit.named("instance.org_admins_viewed")) == 1

    # Only an admin of this organization can be replaced.
    refused = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=operator,
        json={"email": "neu@stadt.example", "replace_admin_ids": [str(coordinator.id)]},
    )
    assert refused.status_code == 422
    assert refused.json()["error_code"] == "REPLACE_NOT_ORG_ADMIN"

    invited = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=operator,
        json={"email": "neu@stadt.example", "replace_admin_ids": [str(gone.id)]},
    )
    assert invited.status_code == 201, invited.text
    assert instance_audit.named("instance.org_admin_invited")[-1]["replaces_count"] == 1
    # Before the acceptance nothing changes.
    assert (await client.get("/api/auth/me", headers=gone_headers)).status_code == 200

    await _accept(client, db_session, "neu@stadt.example")

    db_session.expire_all()
    old = (await db_session.execute(select(User).where(User.id == gone_id))).scalar_one()
    kept = (await db_session.execute(select(User).where(User.id == stays_id))).scalar_one()
    assert old.is_active is False and old.is_deleted is False
    assert kept.is_active is True
    # The old session is over.
    assert (await client.get("/api/auth/me", headers=gone_headers)).status_code == 401

    deactivations = invitation_audit.named("user_deactivated")
    assert [e["target"]["id"] for e in deactivations] == [str(gone_id)]
    assert deactivations[0]["reason"] == "operator_takeover"
    assert invitation_audit.named("instance.org_admin_takeover")[0]["replaced_count"] == 1

    # The new admin sees what was taken over.
    new_admin = (await db_session.execute(select(User).where(User.email == "neu@stadt.example"))).scalar_one()
    notice = await client.get(f"/api/orgs/{slug}/invitations/takeover", headers=get_auth_headers(new_admin))
    assert notice.status_code == 200
    assert [r["email"] for r in notice.json()["replaced"]] == ["weg@stadt.example"]
    # An admin who took over nothing sees nothing.
    plain = await client.get(f"/api/orgs/{slug}/invitations/takeover", headers=get_auth_headers(kept))
    assert plain.json()["replaced"] == []


@pytest.mark.asyncio
async def test_the_replaced_admins_cannot_withdraw_their_replacement(client: AsyncClient, db_session: AsyncSession):
    """
    A takeover invitation is the operator's.

    The organization's list does not show it, and an admin of the
    organization can neither withdraw nor resend it. In the hostile case the
    admin being replaced would otherwise block the replacement.
    """
    gone, org = await create_test_user_with_org(
        db_session, email="feindlich@stadt.example", role=UserRole.ORG_ADMIN, org_slug="succession-r4"
    )
    operator = await _operator_headers(db_session)
    invited = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=operator,
        json={"email": "nachfolge@stadt.example", "replace_admin_ids": [str(gone.id)]},
    )
    assert invited.status_code == 201, invited.text
    # The organization's own invitation stays visible and manageable.
    own = await client.post(
        f"/api/orgs/{org.slug}/invitations/",
        headers=get_auth_headers(gone),
        json={"email": "kollegin@stadt.example", "role": "coordinator", "language": "de"},
    )
    assert own.status_code == 201, own.text

    headers = get_auth_headers(gone)
    listed = await client.get(f"/api/orgs/{org.slug}/invitations/", headers=headers)
    items = listed.json()["items"] if isinstance(listed.json(), dict) else listed.json()
    assert [i["email"] for i in items] == ["kollegin@stadt.example"]

    takeover = (
        await db_session.execute(select(UserInvitation).where(UserInvitation.email == "nachfolge@stadt.example"))
    ).scalar_one()
    base = f"/api/orgs/{org.slug}/invitations/{takeover.id}"
    assert (await client.delete(base, headers=headers)).status_code == 404
    assert (await client.post(f"{base}/resend", headers=headers)).status_code == 404

    # The takeover still works.
    await _accept(client, db_session, "nachfolge@stadt.example")
