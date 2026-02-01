"""Create password_reset_tokens table for persistent password reset storage

Password reset tokens move from memory into the database. The table stores only a hash
of each token.

Revision ID: 007
Revises: 006
Create Date: 2026-02-18 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
            comment="UUID primary key",
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to user requesting reset"
        ),
        sa.Column("token_hash", sa.String(length=128), nullable=False, comment="SHA-256 hash of the reset token"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="When this token expires"),
        sa.Column(
            "used_at", sa.DateTime(timezone=True), nullable=True, comment="When this token was used (null if unused)"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When this token was created",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Password reset tokens for secure, persistent password reset flow",
    )

    # Verification looks a token up by its hash.
    op.create_index("ix_password_reset_tokens_hash", "password_reset_tokens", ["token_hash"], unique=False)

    # Finds and cleans up the tokens of one user.
    op.create_index("ix_password_reset_tokens_user", "password_reset_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_user", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_hash", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
