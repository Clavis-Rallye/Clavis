"""
Takeover invitations belong to the operator.

The guard that list, resend and revoke carry also holds for create: an organization
admin cannot renew an operator takeover invitation and so change its role, token
or replacement list. A replacement list only applies to an invitation for the
admin role, so no acceptance leaves the organization without an active admin.
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
from tests.factories import create_test_user_with_org, get_auth_headers


async def _operator_headers(db_session: AsyncSession) -> dict[str, str]:
    operator = await create_instance_admin(db_session, f"op-{uuid4().hex[:6]}@example.com", "OperatorPass123!")
    return {"Authorization": f"Bearer {create_instance_admin_token(operator.id)}"}


async def _takeover(client: AsyncClient, db_session: AsyncSession, email: str) -> str:
    """Mint a known raw token for an invitation the way the mail path does."""
    invitation = (await db_session.execute(select(UserInvitation).where(UserInvitation.email == email))).scalars().one()
    token = await InvitationRepository(db_session).rotate_token(invitation, expiry_days=7)
    await db_session.commit()
    return token


@pytest.mark.asyncio
async def test_an_admin_cannot_renew_an_operator_takeover_invitation(client: AsyncClient, db_session: AsyncSession):
    """The create route refuses to renew an open invitation that carries a replacement list."""
    gone, org = await create_test_user_with_org(
        db_session, email="abgesetzt@stadt.example", role=UserRole.ORG_ADMIN, org_slug="takeover-guard-c1"
    )
    operator = await _operator_headers(db_session)
    invited = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=operator,
        json={"email": "nachfolger@stadt.example", "replace_admin_ids": [str(gone.id)]},
    )
    assert invited.status_code == 201, invited.text

    takeover = (
        (await db_session.execute(select(UserInvitation).where(UserInvitation.email == "nachfolger@stadt.example")))
        .scalars()
        .one()
    )
    before = (takeover.role, takeover.token_hash, list(takeover.replaces_user_ids or []), takeover.invited_by_user_id)
    before_expires = takeover.expires_at

    recreated = await client.post(
        f"/api/orgs/{org.slug}/invitations/",
        headers=get_auth_headers(gone),
        json={"email": "nachfolger@stadt.example", "role": "coordinator", "language": "de"},
    )
    assert recreated.status_code in (403, 409), recreated.text

    db_session.expire_all()
    after_row = (
        (await db_session.execute(select(UserInvitation).where(UserInvitation.email == "nachfolger@stadt.example")))
        .scalars()
        .one()
    )
    assert (
        after_row.role,
        after_row.token_hash,
        list(after_row.replaces_user_ids or []),
        after_row.invited_by_user_id,
    ) == before
    assert after_row.expires_at == before_expires
    assert after_row.revoked_at is None


@pytest.mark.asyncio
async def test_a_replacement_list_only_applies_to_the_admin_role(client: AsyncClient, db_session: AsyncSession):
    """Accepting a non-admin invitation deactivates nobody, even with a replacement list set."""
    only_admin, org = await create_test_user_with_org(
        db_session, email="letzte@stadt.example", role=UserRole.ORG_ADMIN, org_slug="takeover-guard-c2"
    )
    invitation, _ = await InvitationRepository(db_session).create(
        organization_id=org.id,
        email="koordinator@stadt.example",
        role=UserRole.COORDINATOR,
        invited_by_user_id=None,
        invited_by_name="Clavis",
        expiry_days=7,
        language="de",
    )
    admin_id, org_id = only_admin.id, org.id
    invitation.replaces_user_ids = [str(admin_id)]
    await db_session.commit()

    token = await _takeover(client, db_session, "koordinator@stadt.example")
    accepted = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Koordination", "password": "SicheresPasswort123!"},
    )
    assert accepted.status_code == 200, accepted.text

    db_session.expire_all()
    admin = (await db_session.execute(select(User).where(User.id == admin_id))).scalar_one()
    assert admin.is_active is True
    newcomers = (
        (await db_session.execute(select(User).where(User.email == "koordinator@stadt.example"))).scalars().one()
    )
    assert newcomers.role == UserRole.COORDINATOR
    # The organization still has its active admin, whatever was accepted.
    active_admins = (
        (
            await db_session.execute(
                select(User).where(
                    User.organization_id == org_id, User.role == UserRole.ORG_ADMIN, User.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    assert [a.id for a in active_admins] == [admin_id]


@pytest.mark.asyncio
async def test_the_last_admin_takeover_leaves_an_active_admin(client: AsyncClient, db_session: AsyncSession):
    """Accepting an admin takeover that replaces the only admin leaves one active admin."""
    only_admin, org = await create_test_user_with_org(
        db_session, email="letzter@stadt.example", role=UserRole.ORG_ADMIN, org_slug="takeover-guard-c2b"
    )
    operator = await _operator_headers(db_session)
    invited = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=operator,
        json={"email": "rette@stadt.example", "replace_admin_ids": [str(only_admin.id)]},
    )
    assert invited.status_code == 201, invited.text

    old_id = only_admin.id
    token = await _takeover(client, db_session, "rette@stadt.example")
    accepted = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": "Rettung", "password": "SicheresPasswort123!"},
    )
    assert accepted.status_code == 200, accepted.text

    db_session.expire_all()
    old = (await db_session.execute(select(User).where(User.id == old_id))).scalar_one()
    assert old.is_active is False
    new = (await db_session.execute(select(User).where(User.email == "rette@stadt.example"))).scalars().one()
    assert new.role == UserRole.ORG_ADMIN and new.is_active is True
