"""Add instance_admins.disabled_at and last_password_hash

An operator can disable another operator's account (``instance-admin
disable`` / ``enable``). Non-null means disabled: login and ``totp/confirm``
answer 403 ``INSTANCE_ADMIN_DISABLED`` and ``require_instance_admin`` refuses
an already-open session of it.

``last_password_hash`` mirrors the users table's own field: a password
change (``POST /api/instance/auth/password`` or ``instance-admin
set-password``) stores the previous hash here so it can refuse a new
password that matches the current or the last one.

Revision ID: 032
Revises: 031
Create Date: 2026-09-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instance_admins",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "instance_admins",
        sa.Column("last_password_hash", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("instance_admins", "last_password_hash")
    op.drop_column("instance_admins", "disabled_at")
