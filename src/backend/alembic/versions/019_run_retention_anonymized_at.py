"""Add run LK_C retention-anonymization marker

``runs.retention_anonymized_at`` marks that the daily GDPR retention sweep has
anonymized the residual identifiers participants wrote, the team names. The sweep
does so after the LK_C window, one year after the run ended. The stored flag makes
the LK_C task idempotent without guessing from the data. Existing rows keep NULL,
not yet anonymized.

Revision ID: 019
Revises: 018
Create Date: 2026-06-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "retention_anonymized_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the LK_C retention sweep anonymized this run's residual PII (nullable = not yet anonymized)",
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "retention_anonymized_at")
