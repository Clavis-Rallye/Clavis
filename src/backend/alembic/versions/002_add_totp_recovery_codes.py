"""Add totp_recovery_codes field

Revision ID: 002_add_totp_recovery_codes
Revises: 001_initial_schema
Create Date: 2026-02-12 10:17:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "totp_recovery_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="JSONB array of hashed recovery codes for 2FA backup",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "totp_recovery_codes")
