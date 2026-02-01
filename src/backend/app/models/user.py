"""A user of an organization, with a role, a password and optional second factors."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.password_reset_token import PasswordResetToken


class UserRole(StrEnum):
    """
    The role of a user. Each role has every permission of the roles below it:
    GUEST (0) < PARTICIPANT (1) < COORDINATOR (2) < CREATOR (3) < ORG_ADMIN (4)

    Roles:
        GUEST: Anonymous or temporary user with token-based auth (no password)
        PARTICIPANT: Takes part in runs
        COORDINATOR: Coordinates runs and sees participant data
        CREATOR: Creates and manages rallye templates
        ORG_ADMIN: Full administration within the organization
    """

    GUEST = "guest"
    PARTICIPANT = "participant"
    COORDINATOR = "coordinator"
    CREATOR = "creator"
    ORG_ADMIN = "org_admin"


class User(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    An authenticated user of one organization (tenant isolation).

    The role (``UserRole``) determines the permissions. A guest has no email and an empty
    password hash. Every other user has an Argon2id password hash. TOTP is the optional second
    factor. The column ``webauthn_credentials`` is left from a second factor with a security key,
    which the App no longer offers, and no feature writes it. The erasure (``anonymize_user``) clears
    it together with the TOTP secret and the recovery codes.

    Security fields: ``failed_login_attempts`` and ``locked_until`` lock the account after a
    configurable number of failed logins. ``last_password_hash`` prevents the reuse of the last
    password. ``session_version`` is incremented on every password change, and tokens carry the
    version they were minted at, so older tokens stop authorizing. ``is_active`` lets an admin
    disable the account. Soft deletion (``SoftDeleteMixin``) supports GDPR compliance.

    Structure of ``webauthn_credentials``, as earlier releases wrote it::

        {
            "credentials": [
                {
                    "credential_id": "base64url-encoded-id",
                    "public_key": "base64url-encoded-public-key",
                    "sign_count": 0,
                    "transports": ["usb", "nfc"],
                    "created_at": "2026-01-15T10:30:00Z"
                }
            ]
        }
    """

    __tablename__ = "users"

    # Mirrors migration 006: every non-NULL email is unique, guests share NULL.
    # Declared here so the test schema (create_all) enforces what production does.
    __table_args__ = (
        Index("ix_users_email_not_null", "email", unique=True, postgresql_where=text("email IS NOT NULL")),
    )

    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=False,  # the partial unique index is declared in __table_args__
        comment="Email address for login (NULL for guest users)",
    )

    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-readable display name",
    )

    password_hash: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Argon2id password hash (empty for guest users)",
    )

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        comment="User role determining permissions",
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization",
    )

    # TOTP 2FA fields
    totp_secret: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Base32-encoded TOTP secret (null if 2FA not set up)",
    )

    totp_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Whether TOTP 2FA is active",
    )

    totp_recovery_codes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="JSONB array of hashed recovery codes for 2FA backup",
    )

    # Left from the removed second factor with a security key. No feature writes it.
    webauthn_credentials: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="JSONB array of registered FIDO2 credentials",
    )

    # Security fields
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Counter for failed login attempts",
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp until which account is locked",
    )

    last_password_hash: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="Previous password hash for reuse prevention",
    )

    session_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
        comment="Incremented on every password change; tokens carry the version "
        "they were minted at (claim sv) and stop authorizing on a mismatch",
    )

    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of last successful login",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether user account is active",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="users",
        lazy="selectin",
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        "PasswordResetToken",
        back_populates="user",
        lazy="raise",
        passive_deletes=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, role={self.role.value})>"
