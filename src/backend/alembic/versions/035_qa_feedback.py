"""Add qa_notes and qa_state, the feedback module for test sessions

Two tables without any foreign key: notes survive the deletion of an
organization and stay outside the cascade graph of the organization deletion
sweep. ``qa_state`` is a singleton like ``instance_settings``: the primary key
is pinned to 1 by a server default and the CHECK constraint
``ck_qa_state_singleton``. Constraint names are given short, the naming
convention of ``target_metadata`` expands them (see migration 031).

Revision ID: 035
Revises: 034
Create Date: 2026-10-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qa_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("route", sa.String(length=500), nullable=True),
        sa.Column("selector", sa.String(length=500), nullable=True),
        sa.Column("element_text", sa.String(length=300), nullable=True),
        sa.Column(
            "context", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
        sa.UniqueConstraint("client_id"),
        sa.CheckConstraint("kind IN ('ok', 'skip', 'note')", name="kind"),
    )
    op.create_index("ix_qa_notes_created_at", "qa_notes", ["created_at"])

    op.create_table(
        "qa_state",
        sa.Column("id", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("do", sa.Text(), nullable=True),
        sa.Column("expect", sa.Text(), nullable=True),
        sa.Column("hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("session", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="singleton"),
    )


def downgrade() -> None:
    op.drop_table("qa_state")
    op.drop_index("ix_qa_notes_created_at", table_name="qa_notes")
    op.drop_table("qa_notes")
