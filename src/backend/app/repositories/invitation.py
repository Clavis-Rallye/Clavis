"""
User invitation repository.

Follows the token rule of :mod:`app.repositories.password_reset`: the raw token
is generated here, returned once for the mail, and never stored. Only its SHA-256
hash goes to the database.

Not a ``TenantScopedRepository``: acceptance happens before the recipient has
any identity at all, so the token lookup must run unscoped and carry the
organization with it.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Text, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from app.models.user_invitation import UserInvitation


def _is_no_takeover() -> ColumnElement[bool]:
    """The row carries no replacement list: SQL NULL, JSON ``null`` or an empty list.

    The same reading as ``not invitation.replaces_user_ids`` on a loaded row.
    Anything else counts as a takeover.
    """
    column = UserInvitation.replaces_user_ids
    return or_(column.is_(None), cast(column, Text).in_(["null", "[]"]))


def hash_token(raw_token: str) -> str:
    """SHA-256 of a raw token, hex-encoded."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


class InvitationRepository:
    """Create, look up, and retire invitations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        organization_id: UUID,
        email: str,
        role: UserRole,
        invited_by_user_id: UUID | None,
        invited_by_name: str,
        expiry_days: int,
        language: str = "de",
        display_name_suggestion: str | None = None,
    ) -> tuple[UserInvitation, str]:
        """Create an invitation and return it together with the raw token.

        The raw token is returned once. It is readable only here and in the mail
        that follows.
        """
        raw_token = secrets.token_urlsafe(32)
        invitation = UserInvitation(
            organization_id=organization_id,
            email=email,
            role=role,
            display_name_suggestion=display_name_suggestion,
            language=language,
            token_hash=hash_token(raw_token),
            invited_by_user_id=invited_by_user_id,
            invited_by_name=invited_by_name,
            expires_at=datetime.now(UTC) + timedelta(days=expiry_days),
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation, raw_token

    async def get_by_token(self, raw_token: str) -> UserInvitation | None:
        """Find an invitation by raw token, regardless of its state.

        Expired and already-consumed invitations are returned too: the caller
        needs to tell the recipient *why* their link no longer works.
        """
        stmt = select(UserInvitation).where(UserInvitation.token_hash == hash_token(raw_token))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_in_org(self, invitation_id: UUID, organization_id: UUID) -> UserInvitation | None:
        """Scoped lookup: an admin may touch only the invitations of their own organization."""
        stmt = select(UserInvitation).where(
            UserInvitation.id == invitation_id,
            UserInvitation.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_pending(self, organization_id: UUID) -> list[UserInvitation]:
        """Pending, unexpired invitations for an organization, newest first."""
        now = datetime.now(UTC)
        stmt = (
            select(UserInvitation)
            .where(
                UserInvitation.organization_id == organization_id,
                UserInvitation.accepted_at.is_(None),
                UserInvitation.revoked_at.is_(None),
                UserInvitation.expires_at > now,
            )
            .order_by(UserInvitation.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_open_for_email(self, email: str) -> UserInvitation | None:
        """The open invitation for an address, expired or not.

        Ignores ``expires_at`` on purpose, to match the partial unique index
        ``ix_user_invitations_pending_email``. An *expired* invitation still
        occupies that slot, so inviting the same address again must find and
        rotate it, not insert a duplicate and hit the index.

        Not scoped by organization, because an address may exist only once across
        the whole deployment. The users table enforces the same rule.
        """
        stmt = select(UserInvitation).where(
            UserInvitation.email == email,
            UserInvitation.accepted_at.is_(None),
            UserInvitation.revoked_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def count_pending_org_admin_invitations_by_organization(self) -> dict[UUID, int]:
        """Open org_admin invitations, counted per organization in one grouped query.

        Same predicate as :meth:`list_pending`, restricted to the org_admin role.
        The operator's organization list needs a count, not the rows.
        """
        now = datetime.now(UTC)
        stmt = (
            select(UserInvitation.organization_id, func.count(UserInvitation.id))
            .where(
                UserInvitation.role == UserRole.ORG_ADMIN,
                UserInvitation.accepted_at.is_(None),
                UserInvitation.revoked_at.is_(None),
                UserInvitation.expires_at > now,
            )
            .group_by(UserInvitation.organization_id)
        )
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())

    async def rotate_token(self, invitation: UserInvitation, expiry_days: int) -> str:
        """Issue a fresh token and restart the clock; the old link dies immediately."""
        raw_token = secrets.token_urlsafe(32)
        invitation.token_hash = hash_token(raw_token)
        invitation.expires_at = datetime.now(UTC) + timedelta(days=expiry_days)
        await self.session.flush()
        return raw_token

    async def renew_unless_takeover(
        self,
        invitation: UserInvitation,
        *,
        expiry_days: int,
        **values: Any,
    ) -> str | None:
        """Renew an invitation of the organization: fresh token, fresh clock, ``values``.

        One UPDATE that carries the condition that the row is no takeover. The
        caller read the row without a lock, and an operator can turn it into a
        takeover in between (``invite_org_admin``). The database then decides
        on the row as it is at the UPDATE: a statement that waited for the
        operator's commit checks the condition again and matches nothing.

        Returns:
            The raw token, or None when the row is a takeover by now and
            nothing was changed.
        """
        raw_token = secrets.token_urlsafe(32)
        result = await self.session.execute(
            update(UserInvitation)
            .where(UserInvitation.id == invitation.id, _is_no_takeover())
            .values(
                token_hash=hash_token(raw_token),
                expires_at=datetime.now(UTC) + timedelta(days=expiry_days),
                **values,
            )
            .returning(UserInvitation.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return None
        await self.session.refresh(invitation)
        return raw_token

    async def revoke_unless_takeover(self, invitation: UserInvitation) -> bool:
        """Withdraw an invitation of the organization, unless it is a takeover by now.

        Same conditional UPDATE as :meth:`renew_unless_takeover`.

        Returns:
            False when the row is a takeover by now and nothing was changed.
        """
        result = await self.session.execute(
            update(UserInvitation)
            .where(UserInvitation.id == invitation.id, _is_no_takeover())
            .values(revoked_at=datetime.now(UTC))
            .returning(UserInvitation.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return False
        await self.session.refresh(invitation)
        return True

    async def mark_accepted(self, invitation: UserInvitation, user_id: UUID) -> None:
        invitation.accepted_at = datetime.now(UTC)
        invitation.accepted_user_id = user_id
        await self.session.flush()

    async def revoke(self, invitation: UserInvitation) -> None:
        invitation.revoked_at = datetime.now(UTC)
        await self.session.flush()
