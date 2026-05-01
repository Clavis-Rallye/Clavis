"""Author columns + challenge time/solution + feed event types

Three unrelated additions share one migration, so one deploy covers the matching
frontend changes:

1. ``created_by_user_id`` on rallyes, stations and challenges, so admin tables show an
   author and audit trails carry the originator. It is nullable, so legacy rows need
   no backfill. The UI shows a dash for them.
2. Three ``feed_event_type`` values for the run lifecycle messages and the run-end
   sequence.
3. ``time_estimate_minutes`` and ``suggested_solution`` on challenges, set through
   ``ChallengeCreate`` and shown in the meta row of ``ChallengeView``.

Revision ID: 009
Revises: 008
Create Date: 2026-05-04 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ----------------------------------------------------------------
    # 1. Author columns on rallyes / stations / challenges
    # ----------------------------------------------------------------
    for table in ("rallyes", "stations", "challenges"):
        op.add_column(
            table,
            sa.Column(
                "created_by_user_id",
                sa.UUID(),
                nullable=True,
                comment="User who created this row (nullable for legacy rows)",
            ),
        )
        op.create_foreign_key(
            f"fk_{table}_created_by_user_id_users",
            table,
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_{table}_created_by_user_id",
            table,
            ["created_by_user_id"],
        )

    # ----------------------------------------------------------------
    # 2. Extend feed_event_type enum
    # ----------------------------------------------------------------
    op.execute("ALTER TYPE feed_event_type ADD VALUE IF NOT EXISTS 'team_update'")
    op.execute("ALTER TYPE feed_event_type ADD VALUE IF NOT EXISTS 'results_available'")
    op.execute("ALTER TYPE feed_event_type ADD VALUE IF NOT EXISTS 'feedback_prompt'")

    # ----------------------------------------------------------------
    # 3. New optional Challenge columns
    # ----------------------------------------------------------------
    op.add_column(
        "challenges",
        sa.Column(
            "time_estimate_minutes",
            sa.Integer(),
            nullable=True,
            comment="Estimated time to complete this challenge, in minutes (nullable)",
        ),
    )
    op.add_column(
        "challenges",
        sa.Column(
            "suggested_solution",
            sa.Text(),
            nullable=True,
            comment="Reviewer-facing reference answer (not shown to participants)",
        ),
    )


def downgrade() -> None:
    # 3. Drop the new Challenge columns
    op.drop_column("challenges", "suggested_solution")
    op.drop_column("challenges", "time_estimate_minutes")

    # 2. PostgreSQL cannot drop enum values. They stay in the type, and no row
    #    uses them once the producer code is rolled back.

    # 1. Drop author columns
    for table in ("rallyes", "stations", "challenges"):
        op.drop_index(f"ix_{table}_created_by_user_id", table_name=table)
        op.drop_constraint(f"fk_{table}_created_by_user_id_users", table, type_="foreignkey")
        op.drop_column(table, "created_by_user_id")
