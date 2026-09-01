"""
The operator account of the instance, the layer above organizations.

Operators sign in with password and TOTP and get tokens with ``scope: "instance"`` that carry no
organization. No HTTP route creates an operator: only ``python -m app.cli instance-admin create``
does, so the table starts empty on every deployment and only staff with server access add an account.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UUIDMixin


class InstanceAdmin(UUIDMixin, Base):
    """Operator account for the instance (not a member of any organization).

    Attributes:
        email: Unique email address for login
        password_hash: Argon2id hash of the password
        last_password_hash: Previous Argon2id hash, kept so that a password change
            can refuse the current and the last password. Mirrors the users table.
        session_version: Incremented on every password change (HTTP route or
            ``set-password``). Tokens minted before the change stop authorizing.
            Mirrors the users table.
        totp_secret: Base32-encoded TOTP secret, generated at creation so the
            first login can hand out the provisioning URI (null never expected)
        totp_confirmed_at: When the operator completed the TOTP setup. Null means
            the setup is pending, and login returns the provisioning URI instead
            of a session.
        last_login_at: Timestamp of the last successful password proof. It is also
            the marker that the provisioning URI has been handed out once.
        disabled_at: When an operator disabled this account (null = active). A
            disabled operator cannot sign in, and an open session ends at the
            next request.
    """

    __tablename__ = "instance_admins"

    # Mirrors migration 029: operator addresses are unique, one account per human.
    __table_args__ = (UniqueConstraint("email", name="uq_instance_admins_email"),)

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Operator email address for login",
    )

    password_hash: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Argon2id password hash",
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

    totp_secret: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Base32-encoded TOTP secret (set at creation, confirmed on first login)",
    )

    totp_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the operator confirmed the TOTP setup (null = setup pending)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Account creation timestamp",
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last successful password proof / provisioning-URI handout",
    )

    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When an operator disabled this account (null = active)",
    )

    def __repr__(self) -> str:
        return f"<InstanceAdmin(id={self.id}, email={self.email})>"
