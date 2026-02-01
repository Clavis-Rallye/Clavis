"""Add rich notification types to feed_event_type enum

Revision ID: 005
Revises: 004
Create Date: 2026-02-14 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE feed_event_type ADD VALUE IF NOT EXISTS 'countdown'")
    op.execute("ALTER TYPE feed_event_type ADD VALUE IF NOT EXISTS 'reminder'")
    op.execute("ALTER TYPE feed_event_type ADD VALUE IF NOT EXISTS 'alert'")


def downgrade() -> None:
    """PostgreSQL cannot drop an enum value, so the three values stay."""
    pass
