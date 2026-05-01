"""Configurable legal text on Organization

Adds three nullable Markdown text columns to `organizations`:
  - legal_imprint     (Impressum / § 5 TMG)
  - legal_privacy     (Datenschutz)
  - legal_accessibility (Barrierefreiheit / EU directive 2016/2102)

NULL makes the frontend fall back to the placeholder copy in
`src/frontend/src/i18n/locales/{de,en}/legal.json`. Admins publish their own copy
without a frontend redeploy.

Revision ID: 012
Revises: 011
Create Date: 2026-05-04 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for col in ("legal_imprint", "legal_privacy", "legal_accessibility"):
        op.add_column(
            "organizations",
            sa.Column(col, sa.Text(), nullable=True, comment=f"{col} markdown override"),
        )


def downgrade() -> None:
    for col in ("legal_accessibility", "legal_privacy", "legal_imprint"):
        op.drop_column("organizations", col)
