"""Add organizations.deletion_requested_at

An operator schedules the deletion of an archived organization by confirming its
slug. The timestamp marks the request. ``app/services/org_deletion.py`` holds the
30-day grace window and the hard-delete sweep. NULL means no deletion is scheduled,
and a restore sets it back to NULL.

A CHECK constraint (``ck_organizations_deletion_requires_archived``) backs the rule
of the request endpoint: only an archived organization can be scheduled for
deletion. The model names it ``"deletion_requires_archived"``, and the naming
convention (ck_%(table_name)s_%(constraint_name)s) expands that.
``op.create_check_constraint`` and ``op.drop_constraint`` apply the convention to
any name they get. The expanded name would double-prefix to
``ck_organizations_ck_organizations_deletion_requires_archived``, and
``downgrade()`` would drop a name never created. ``op.f(...)`` marks the name as
final, so it matches the model.

Revision ID: 033
Revises: 032
Create Date: 2026-09-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_organizations_deletion_requires_archived"),
        "organizations",
        "deletion_requested_at IS NULL OR archived_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_organizations_deletion_requires_archived"), "organizations", type_="check")
    op.drop_column("organizations", "deletion_requested_at")
