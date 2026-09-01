"""Add organizations.archived_at

The operator-side archive flag: non-null while an organization is archived.
Members of an archived organization cannot sign in and its runs disappear from
the public endpoints; the data itself stays so a restore reverses everything.

Revision ID: 030
Revises: 029
Create Date: 2026-09-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "archived_at")
