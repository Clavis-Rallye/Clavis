"""Add users.session_version and instance_admins.session_version

A password change ends the account's other sessions: every token carries the
account's session version as the ``sv`` claim, and every password change
increments the version, so tokens minted before the change stop authorizing at
their next request. Both columns start at 0 (server default), which is also
what a token without the claim (issued before the claim existed) reads as, so
existing sessions survive until the account's first password change.

Revision ID: 034
Revises: 033
Create Date: 2026-10-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "instance_admins",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("instance_admins", "session_version")
    op.drop_column("users", "session_version")
