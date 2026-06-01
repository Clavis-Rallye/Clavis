"""Carry the station address into run snapshots

Migration 015 gave stations an ``address``, but the run snapshot did not copy it,
so participants never saw it. ``run_stations`` gains the column, and the snapshot
service freezes it when the run starts. Existing runs keep NULL, because snapshots are
immutable.

Revision ID: 017
Revises: 016
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_stations",
        sa.Column(
            "address",
            sa.String(length=500),
            nullable=True,
            comment="Human-readable address frozen from source (nullable)",
        ),
    )


def downgrade() -> None:
    op.drop_column("run_stations", "address")
