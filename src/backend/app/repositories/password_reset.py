"""
Password reset token repository. It extends neither base repository, because a token
expires by time and has no soft delete and no tenant scope.
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset_token import PasswordResetToken
from app.repositories.invitation import hash_token


class PasswordResetRepository:
    """
    A token is valid for ``DEFAULT_EXPIRY_HOURS`` by default and single use (``used_at`` is set when
    it is consumed). ``cleanup_expired`` deletes expired and used tokens.

    Security: the raw token goes to the user by email and is never stored. Only its
    SHA-256 hash is stored. Tokens come from a cryptographically secure generator.
    """

    DEFAULT_EXPIRY_HOURS = 1

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_token(
        self,
        user_id: UUID,
        expiry_hours: int | None = None,
    ) -> str:
        """
        Create a reset token for a user and return the raw token (64 hex chars). The raw
        token is returned once and never stored, only its SHA-256 hash.
        """
        if expiry_hours is None:
            expiry_hours = self.DEFAULT_EXPIRY_HOURS

        raw_token = secrets.token_hex(32)
        token_hash = hash_token(raw_token)

        expires_at = datetime.now(UTC) + timedelta(hours=expiry_hours)

        token = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        self.session.add(token)
        await self.session.flush()

        return raw_token

    async def get_by_hash(self, raw_token: str) -> PasswordResetToken | None:
        """Look up a token by its raw value through the hash. Return it only while it is unexpired and unused."""
        token_hash = hash_token(raw_token)
        now = datetime.now(UTC)

        stmt = select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.expires_at > now,
            PasswordResetToken.used_at.is_(None),
        )

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_used(self, token_id: UUID) -> bool:
        """
        Set ``used_at``, so the token cannot be used again. Call it after a successful
        password change. Return False if the token does not exist.
        """
        stmt = select(PasswordResetToken).where(PasswordResetToken.id == token_id)
        result = await self.session.execute(stmt)
        token = result.scalar_one_or_none()

        if token is None:
            return False

        token.used_at = datetime.now(UTC)
        await self.session.flush()

        return True

    async def cleanup_expired(self) -> int:
        """
        Delete the expired and the used tokens and return their number. The caller must
        trigger it, as the auth router does after a completed password reset.
        """
        now = datetime.now(UTC)

        stmt = delete(PasswordResetToken).where(
            (PasswordResetToken.expires_at <= now) | (PasswordResetToken.used_at.is_not(None))
        )

        result = await self.session.execute(stmt)
        await self.session.flush()

        return result.rowcount or 0  # type: ignore[attr-defined]

    async def get_active_for_user(self, user_id: UUID) -> list[PasswordResetToken]:
        """The active (unexpired, unused) tokens of a user."""
        now = datetime.now(UTC)

        stmt = select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.expires_at > now,
            PasswordResetToken.used_at.is_(None),
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def invalidate_for_user(self, user_id: UUID) -> int:
        """
        Delete the active tokens of a user and return their number. Called before a new reset
        token is created and when an admin force-resets a password.
        """
        now = datetime.now(UTC)

        stmt = delete(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.expires_at > now,
            PasswordResetToken.used_at.is_(None),
        )

        result = await self.session.execute(stmt)
        await self.session.flush()

        return result.rowcount or 0  # type: ignore[attr-defined]
