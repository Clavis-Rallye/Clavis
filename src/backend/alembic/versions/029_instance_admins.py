"""Add instance_admins

Operator accounts of the instance, the layer above organizations. No HTTP route
creates them. Only ``python -m app.cli instance-admin create`` does, which hashes the
password with Argon2id and generates the pending TOTP secret.

A NULL ``totp_confirmed_at`` means the TOTP setup is pending. The first login hands
out the provisioning URI (``generate_provisioning_uri``), and
``POST /api/instance/auth/totp/confirm`` completes the enrollment. After that,
login requires the code and issues a token with ``scope: "instance"``.

Revision ID: 029
Revises: 028
Create Date: 2026-09-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_admins",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("totp_secret", sa.String(length=255), nullable=True),
        sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_instance_admins_email"),
    )


def downgrade() -> None:
    op.drop_table("instance_admins")
