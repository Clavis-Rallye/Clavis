"""Add run_departures, the evidence that blocks a guest who left a team from joining the run again

Leaving a team hard-deletes the ``TeamMember`` row, so a departed guest left no
trace and every join path let them back in. This table records the departure of a
guest user. The join endpoints check it and refuse.

Deleting the run or the guest user cascades to these rows. So the GDPR retention
sweep removes them together with the guest users.

Revision ID: 020
Revises: 019
Create Date: 2026-07-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_departures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "departed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "user_id", name="uq_run_departures_run_user"),
    )
    op.create_index("ix_run_departures_organization_id", "run_departures", ["organization_id"])
    op.create_index("ix_run_departures_run_id", "run_departures", ["run_id"])
    op.create_index("ix_run_departures_user_id", "run_departures", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_run_departures_user_id", table_name="run_departures")
    op.drop_index("ix_run_departures_run_id", table_name="run_departures")
    op.drop_index("ix_run_departures_organization_id", table_name="run_departures")
    op.drop_table("run_departures")
