"""
User invitation model.

An invitation is an offer of an account, not an account. The user row is created only when
the recipient accepts. That keeps half-provisioned users with empty password hashes out of
the users table, out of the login path and out of the GDPR retention sweep.

Only the SHA-256 hash of the token is stored. The raw token exists in exactly one place:
the mail in the inbox of the recipient.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin
from app.models.user import UserRole

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class UserInvitation(UUIDMixin, TimestampMixin, Base):
    """
    A pending offer of an account in an organization.

    Lifecycle: created, then accepted, revoked or expired. The three end states are
    distinguished on purpose. The accept page tells the recipient which one they hit,
    because "this link no longer works" without a reason is a dead end that generates a
    support call.

    Attributes:
        email: Address the invitation was sent to (also the future login)
        role: Role the accepted user will receive
        display_name_suggestion: Optional pre-fill for the accept form
        language: Language of the invitation mail ('de' | 'en')
        token_hash: SHA-256 of the raw token. The raw value is never stored.
        invited_by_user_id: Admin who issued it (kept for the audit trail)
        expires_at: When the link stops working
        accepted_at: When it was redeemed (null while pending)
        revoked_at: When an admin withdrew it (null while pending)
        accepted_user_id: The user created on acceptance
    """

    __tablename__ = "user_invitations"

    # Mirrors migration 024: one open invitation per address, token hashes unique by constraint.
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_invitations_token_hash"),
        Index(
            "ix_user_invitations_pending_email",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Organization the invitee will join",
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Invited email address (becomes the login)",
    )

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        comment="Role granted on acceptance",
    )

    display_name_suggestion: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Optional name pre-filled on the accept form",
    )

    language: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="de",
        server_default="de",
        comment="Language of the invitation mail (de|en)",
    )

    token_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="SHA-256 hash of the invitation token",
    )

    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        # The invitation outlives the admin who sent it: an admin account that
        # is deleted must not take a colleague's pending invitation with it.
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Admin who issued the invitation",
    )

    invited_by_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
        server_default="",
        comment="Inviter's display name, denormalized so the list survives their deletion",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When the invitation link expires",
    )

    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the invitation was accepted",
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When an admin revoked the invitation",
    )

    accepted_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User created by accepting this invitation",
    )

    replaces_user_ids: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Org admins this invitation takes over from (operator takeover, D7)",
    )

    organization: Mapped["Organization"] = relationship("Organization", lazy="selectin")

    invited_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[invited_by_user_id],
        lazy="selectin",
    )

    @property
    def is_pending(self) -> bool:
        """Whether this invitation can still be accepted (expiry aside)."""
        return self.accepted_at is None and self.revoked_at is None

    def __repr__(self) -> str:
        return f"<UserInvitation(email={self.email}, role={self.role.value}, pending={self.is_pending})>"
