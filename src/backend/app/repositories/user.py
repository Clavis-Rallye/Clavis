"""User repository: the accounts of one organization, and the unscoped reads of the authentication flows."""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.models.user import User, UserRole
from app.repositories.base import TenantScopedRepository


class UserRepository(TenantScopedRepository[User]):
    """Repository for User entities, filtered by organization and soft-delete."""

    model = User

    # ============================================================================
    # UNSCOPED METHODS (Security-sensitive exceptions)
    # ============================================================================
    # These methods bypass organization scoping for the authentication flows.
    # Use with extreme caution.

    @staticmethod
    async def get_by_id_unscoped(session: AsyncSession, user_id: UUID) -> User | None:
        """
        Get a user by id WITHOUT organization scoping.

        SECURITY WARNING: this bypasses multi-tenant isolation. Use it only where the
        organization is not yet known, such as the JWT validation in
        ``get_current_user``. Soft-deleted users are not found.
        """
        stmt = select(User).where(
            User.id == user_id,
            User.is_deleted == False,  # noqa: E712
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email_unscoped(session: AsyncSession, email: str) -> User | None:
        """
        Get a user by email address WITHOUT organization scoping.

        SECURITY WARNING: this bypasses multi-tenant isolation. Use it only where the
        organization is derived from the user, such as the login lookup and the email
        uniqueness check of the registration. Soft-deleted users are not found.

        Emails match case-insensitively. Request schemas lowercase them already, and
        ``lower()`` also covers a caller that bypasses the schemas and rows stored before
        migration 015 normalized the emails.
        """
        stmt = select(User).where(
            func.lower(User.email) == email.lower(),
            User.is_deleted == False,  # noqa: E712
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email_including_deleted(session: AsyncSession, email: str) -> User | None:
        """Find a user by email, soft-deleted rows included.

        ``ix_users_email_not_null`` is unique over every non-NULL email, soft-deleted or
        not. A caller about to INSERT needs this lookup, not ``get_by_email_unscoped``, or the
        constraint answers with an IntegrityError.
        """
        stmt = select(User).where(func.lower(User.email) == email.lower())
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def raise_session_version(session: AsyncSession, user: User) -> int:
        """
        End every session of an account: raise ``session_version`` by one.

        This method and its guarded variant ``raise_session_version_from`` are the only
        code that raises the session version of an organization account. The database
        computes the new value in the UPDATE, under the row lock, so two revocations at the
        same moment raise the version twice. A version computed from ``user.session_version``
        comes from a read without a lock: two requests write the same number, and a session
        issued between them outlives the second one.

        No organization or soft-delete filter: the callers hold the account already
        (password change and reset, takeover, anonymization, the last also for deleted
        rows). The caller commits. ``user.session_version`` is set to the new value without
        marking the object changed. Return the new version.
        """
        result = await session.execute(
            update(User)
            .where(User.id == user.id)
            .values(session_version=User.session_version + 1)
            .returning(User.session_version)
            .execution_options(synchronize_session=False)
        )
        new_version: int = result.scalar_one()
        set_committed_value(user, "session_version", new_version)
        return new_version

    @staticmethod
    async def raise_session_version_from(
        session: AsyncSession, user: User, verified_session_version: int
    ) -> int | None:
        """
        Raise ``session_version`` by one, but only from the version the caller verified.

        For a change that keeps the caller's session alive on a new cookie (the password
        change). The caller was authenticated with a token of ``verified_session_version``.
        A reset or another revocation that committed since has ended that session. The
        UPDATE then matches no row, nothing is written and the caller mints nothing. A later
        revocation waits for this row lock and raises the version above the one the caller
        mints. The caller commits.

        ``user.session_version`` is set to the new value without marking the object changed.
        Return the new version, or None if the version changed since the token was accepted.
        """
        result = await session.execute(
            update(User)
            .where(User.id == user.id, User.session_version == verified_session_version)
            .values(session_version=User.session_version + 1)
            .returning(User.session_version)
            .execution_options(synchronize_session=False)
        )
        new_version: int | None = result.scalar_one_or_none()
        if new_version is None:
            return None
        set_committed_value(user, "session_version", new_version)
        return new_version

    # ============================================================================
    # CUSTOM SCOPED METHODS
    # ============================================================================

    async def reset_failed_login_attempts(self, user_id: UUID) -> bool:
        """Reset the failed login counter and the lock after a login. Return False when the user is not found."""
        user = await self.get_by_id(user_id)
        if user is None:
            return False

        user.failed_login_attempts = 0
        user.locked_until = None

        await self.session.flush()

        return True

    async def increment_failed_login_attempts(self, user_id: UUID) -> int:
        """Count one failed login. Return the new count, or -1 when the user is not found."""
        user = await self.get_by_id(user_id)
        if user is None:
            return -1

        user.failed_login_attempts += 1

        await self.session.flush()

        return user.failed_login_attempts

    async def list_by_org(
        self,
        skip: int = 0,
        limit: int = 50,
        role_filter: UserRole | None = None,
        active_filter: bool | None = None,
        include_deleted: bool = False,
    ) -> list[User]:
        """
        List users of this organization, newest first. ``include_deleted`` also returns
        users in the grace window and anonymized users.
        """
        if include_deleted:
            stmt = select(User).where(User.organization_id == self.organization_id)
        else:
            stmt = self._base_query()

        if role_filter is not None:
            stmt = stmt.where(User.role == role_filter)

        if active_filter is not None:
            stmt = stmt.where(User.is_active == active_filter)

        stmt = stmt.order_by(User.created_at.desc()).offset(skip).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_org(
        self,
        role_filter: UserRole | None = None,
        active_filter: bool | None = None,
        include_deleted: bool = False,
    ) -> int:
        """Count users of this organization with the filters of ``list_by_org``."""
        stmt = select(func.count(User.id)).where(User.organization_id == self.organization_id)
        if not include_deleted:
            stmt = stmt.where(User.is_deleted == False)  # noqa: E712

        if role_filter is not None:
            stmt = stmt.where(User.role == role_filter)

        if active_filter is not None:
            stmt = stmt.where(User.is_active == active_filter)

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_by_id_in_org(self, user_id: UUID, include_deleted: bool = False) -> User | None:
        """Like ``get_by_id``. ``include_deleted`` also finds a soft-deleted user, for the restore flow."""
        if not include_deleted:
            return await self.get_by_id(user_id)
        stmt = select(User).where(
            User.id == user_id,
            User.organization_id == self.organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
