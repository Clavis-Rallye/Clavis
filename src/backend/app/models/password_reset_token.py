"""
Password reset token model.

Persists password reset tokens. Only the SHA-256 hash of a token is stored, with an
expiry. The table is not tenant-scoped, because user IDs are globally unique and the
password reset is a cross-tenant auth flow.

There is no ``SoftDeleteMixin``: a token is either used (``used_at``) or cleaned up
when it has expired.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PasswordResetToken(Base):
    """
    A password reset token, stored as a hash.

    The raw token goes to the user by email. Only its hash reaches the database, so a
    database read cannot reset a password. ``used_at`` is null while the token is unused.
    """

    __tablename__ = "password_reset_tokens"

    # Index names as migration 007 created them, so create_all and alembic agree.
    __table_args__ = (
        Index("ix_password_reset_tokens_user", "user_id"),
        Index("ix_password_reset_tokens_hash", "token_hash"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    token_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="password_reset_tokens")
