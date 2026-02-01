"""Initial schema - Create organizations and users tables

Revision ID: 001
Revises:
Create Date: 2026-02-12 09:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create organizations and users tables with all columns, indexes, and constraints."""

    # Create organizations table
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column("name", sa.String(length=255), nullable=False, comment="Human-readable organization name"),
        sa.Column("slug", sa.String(length=255), nullable=False, comment="URL-safe unique identifier"),
        sa.Column(
            "languages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='["de"]',
            comment="List of enabled locale codes (e.g., ['de', 'en'])",
        ),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Extensible JSONB configuration for org-specific settings",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when last updated",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when soft-deleted"),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Boolean flag indicating soft deletion",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="Organization entity representing a tenant in the multi-tenant system",
    )

    # Create indexes for organizations
    op.create_index(op.f("ix_organizations_slug"), "organizations", ["slug"], unique=True)

    # Create user role enum
    op.execute("CREATE TYPE userrole AS ENUM ('guest', 'participant', 'coordinator', 'creator', 'org_admin')")

    # Create users table
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column("email", sa.String(length=255), nullable=False, comment="Unique email address for login"),
        sa.Column("display_name", sa.String(length=255), nullable=False, comment="Human-readable display name"),
        sa.Column(
            "password_hash",
            sa.String(length=512),
            nullable=False,
            comment="Argon2id password hash (empty for guest users)",
        ),
        sa.Column(
            "role",
            postgresql.ENUM(
                "guest", "participant", "coordinator", "creator", "org_admin", name="userrole", create_type=False
            ),
            nullable=False,
            comment="User role determining permissions",
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column(
            "totp_secret",
            sa.String(length=255),
            nullable=True,
            comment="Base32-encoded TOTP secret (null if 2FA not set up)",
        ),
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Whether TOTP 2FA is active",
        ),
        sa.Column(
            "webauthn_credentials",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="JSONB array of registered FIDO2 credentials",
        ),
        sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Counter for failed login attempts",
        ),
        sa.Column(
            "locked_until", sa.DateTime(timezone=True), nullable=True, comment="Timestamp until which account is locked"
        ),
        sa.Column(
            "last_password_hash",
            sa.String(length=512),
            nullable=True,
            comment="Previous password hash for reuse prevention",
        ),
        sa.Column(
            "last_login", sa.DateTime(timezone=True), nullable=True, comment="Timestamp of last successful login"
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Whether user account is active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when last updated",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when soft-deleted"),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Boolean flag indicating soft deletion",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="User entity representing an authenticated user within an organization",
    )

    # Create indexes for users
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"], unique=False)


def downgrade() -> None:
    """Drop users and organizations tables."""

    # Drop indexes
    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")

    # Drop users table
    op.drop_table("users")

    # Drop user role enum
    op.execute("DROP TYPE userrole")

    # Drop indexes
    op.drop_index(op.f("ix_organizations_slug"), table_name="organizations")

    # Drop organizations table
    op.drop_table("organizations")
