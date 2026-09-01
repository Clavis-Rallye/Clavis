"""Add instance_settings

A single-row table for the instance's own legal texts: imprint, privacy,
accessibility. A server default and a CHECK constraint
(``ck_instance_settings_singleton``) pin the primary key to 1, so PostgreSQL itself
refuses a second row. The text columns and ``updated_at`` are nullable, because the
row can exist before anything is saved.

Revision ID: 031
Revises: 030
Create Date: 2026-09-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_settings",
        sa.Column("id", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("legal_imprint", sa.Text(), nullable=True),
        sa.Column("legal_privacy", sa.Text(), nullable=True),
        sa.Column("legal_accessibility", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # `name="singleton"`, as in the model. op.create_table builds against
        # target_metadata (env.py), whose naming convention
        # (ck_%(table_name)s_%(constraint_name)s) expands it to
        # "ck_instance_settings_singleton". The full name here would double-prefix to
        # "ck_instance_settings_ck_instance_settings_singleton".
        sa.CheckConstraint("id = 1", name="singleton"),
    )


def downgrade() -> None:
    op.drop_table("instance_settings")
