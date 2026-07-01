"""Add user invitations

An invitation is an offer of an account. The user row is created only on
acceptance, so a pending invitation never appears in the users table — no
password-less user rows in the login path, none in the retention sweep.

Only the SHA-256 hash of the token is stored (``token_hash``, unique); the raw
token exists solely in the mail. ``invited_by_user_id`` is ``ON DELETE SET
NULL`` with the inviter's name denormalized alongside, so deleting an admin
account cannot take a colleague's pending invitation with it.

Revision ID: 024
Revises: 023
Create Date: 2026-07-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_invitations",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, comment="Invited email address"),
        # Same native ``userrole`` type as ``users.role`` (created in 001) — one
        # representation of the role vocabulary per database, not two.
        sa.Column(
            "role",
            # postgresql.ENUM, not sa.Enum: `create_type` is a dialect-specific
            # argument, and the type already exists from migration 001.
            postgresql.ENUM(
                "guest",
                "participant",
                "coordinator",
                "creator",
                "org_admin",
                name="userrole",
                create_type=False,
            ),
            nullable=False,
            comment="Role granted on acceptance",
        ),
        sa.Column(
            "display_name_suggestion",
            sa.String(length=255),
            nullable=True,
            comment="Optional name pre-filled on the accept form",
        ),
        sa.Column(
            "language",
            sa.String(length=8),
            nullable=False,
            server_default="de",
            comment="Language of the invitation mail (de|en)",
        ),
        sa.Column(
            "token_hash",
            sa.String(length=128),
            nullable=False,
            comment="SHA-256 hash of the invitation token",
        ),
        sa.Column("invited_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "invited_by_name",
            sa.String(length=255),
            nullable=False,
            server_default="",
            comment="Inviter display name, denormalized to survive their deletion",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["accepted_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_user_invitations_token_hash"),
    )
    op.create_index("ix_user_invitations_organization_id", "user_invitations", ["organization_id"])
    op.create_index("ix_user_invitations_email", "user_invitations", ["email"])
    op.create_index("ix_user_invitations_token_hash", "user_invitations", ["token_hash"])

    # One live invitation per address at a time. Without this, two admins
    # inviting the same colleague within the same second both succeed and the
    # loser's link silently stops working when the first one is accepted.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_user_invitations_pending_email
        ON user_invitations (email)
        WHERE accepted_at IS NULL AND revoked_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_invitations_pending_email")
    op.drop_index("ix_user_invitations_token_hash", table_name="user_invitations")
    op.drop_index("ix_user_invitations_email", table_name="user_invitations")
    op.drop_index("ix_user_invitations_organization_id", table_name="user_invitations")
    op.drop_table("user_invitations")
