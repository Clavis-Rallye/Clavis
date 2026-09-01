"""
Repository of the operator accounts (``instance_admins``).

Not a ``SystemRepository``: the table has no soft-delete columns, so the ``is_deleted``
filters of the base classes do not apply.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instance_admin import InstanceAdmin


class InstanceAdminRepository:
    """There is no tenant scope: operators sit above all organizations."""

    model = InstanceAdmin

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, admin_id: UUID) -> InstanceAdmin | None:
        result = await self.session.execute(select(InstanceAdmin).where(InstanceAdmin.id == admin_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> InstanceAdmin | None:
        """The caller must lowercase the email first."""
        result = await self.session.execute(select(InstanceAdmin).where(InstanceAdmin.email == email))
        return result.scalar_one_or_none()

    async def list_all(self) -> list[InstanceAdmin]:
        """All operators, ordered by email. No paging: only the command line creates operators, so they are few."""
        result = await self.session.execute(select(InstanceAdmin).order_by(InstanceAdmin.email))
        return list(result.scalars().all())

    async def create(self, **kwargs: Any) -> InstanceAdmin:
        """Create an operator account from the model fields (email, password_hash, totp_secret)."""
        admin = InstanceAdmin(**kwargs)
        self.session.add(admin)
        await self.session.flush()
        await self.session.refresh(admin)
        return admin

    async def update(self, admin_id: UUID, **kwargs: Any) -> InstanceAdmin | None:
        """Return None if there is no such operator. An explicit None value is applied."""
        admin = await self.get_by_id(admin_id)
        if admin is None:
            return None

        for key, value in kwargs.items():
            if hasattr(admin, key):
                setattr(admin, key, value)

        await self.session.flush()
        await self.session.refresh(admin)
        return admin

    async def raise_session_version(self, admin_id: UUID, **values: Any) -> InstanceAdmin | None:
        """
        End every session of an operator: raise ``session_version`` by one.

        This method and its guarded variant ``raise_session_version_from`` are the only code
        that raises an operator's session version. The database computes the new value in
        the UPDATE, under the row lock, so two revocations at the same moment raise the
        version twice. A version computed from a row read earlier would write the same
        number twice, and a session issued between the two would outlive the second.

        ``values`` are further columns of the same UPDATE (the new password hash, the
        rotated TOTP secret). Return the operator as the row is after the UPDATE, or None
        if there is no such operator.
        """
        result = await self.session.execute(
            update(InstanceAdmin)
            .where(InstanceAdmin.id == admin_id)
            .values(session_version=InstanceAdmin.session_version + 1, **values)
            .returning(InstanceAdmin.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self._reload(admin_id)

    async def raise_session_version_from(
        self, admin_id: UUID, verified_session_version: int, **values: Any
    ) -> InstanceAdmin | None:
        """
        Raise ``session_version`` by one, but only from the version the caller verified.

        For a change that keeps the caller's session alive on a new cookie (the password
        change). The caller was authenticated with a token of the verified version. A
        reset that committed since has ended that session. The UPDATE then matches no row,
        nothing is written and the caller mints nothing. A later reset waits for this row
        lock and raises the version above the one the caller mints.

        ``values`` are further columns of the same UPDATE (the new password hash). Return
        the operator after the UPDATE, or None if the version changed since the token was
        accepted or there is no such operator.
        """
        result = await self.session.execute(
            update(InstanceAdmin)
            .where(InstanceAdmin.id == admin_id, InstanceAdmin.session_version == verified_session_version)
            .values(session_version=InstanceAdmin.session_version + 1, **values)
            .returning(InstanceAdmin.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self._reload(admin_id)

    async def complete_sign_in(
        self,
        admin_id: UUID,
        *,
        verified_session_version: int,
        verified_totp_secret: str | None,
        verified_totp_confirmed_at: datetime | None,
        **values: Any,
    ) -> InstanceAdmin | None:
        """
        Record a sign-in step, but only on the account state the step verified.

        A sign-in reads the operator row, proves the password and the code against it, and
        writes ``last_login_at`` afterwards. A reset that commits in between rotates the
        secret and raises the session version. So the WHERE clause names the session
        version and the second-factor state the caller verified. It matches no row when
        they changed, and the caller refuses the sign-in. A later reset waits for this row
        lock and raises the version above the one the caller mints its cookie with.

        The ``verified_*`` arguments are the session version, TOTP secret and confirmation
        time of the row the caller read. ``values`` are the columns to set
        (``last_login_at``, ``totp_confirmed_at``). Return the operator after the UPDATE,
        or None if the state changed since the caller read it or there is no such operator.
        """
        result = await self.session.execute(
            update(InstanceAdmin)
            .where(
                InstanceAdmin.id == admin_id,
                InstanceAdmin.session_version == verified_session_version,
                InstanceAdmin.totp_secret.is_not_distinct_from(verified_totp_secret),
                InstanceAdmin.totp_confirmed_at.is_not_distinct_from(verified_totp_confirmed_at),
            )
            .values(**values)
            .returning(InstanceAdmin.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self._reload(admin_id)

    async def _reload(self, admin_id: UUID) -> InstanceAdmin | None:
        """The operator as the row is now, also when this session loaded it before."""
        admin = await self.get_by_id(admin_id)
        if admin is not None:
            await self.session.refresh(admin)
        return admin
