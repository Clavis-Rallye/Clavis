"""Add teams.coordinator_joined_notified_at for the [coordinator_joined] system message

The team chat announces a coordinator who leaves (``coordinator_left``). This
column lets it announce one who arrives. It records whether the current assist
window has emitted ``[coordinator_joined]``, so several coordinator messages
trigger it once. Closing the window resets it to NULL, so the next request and
join notify again.

Revision ID: 022
Revises: 021
Create Date: 2026-07-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column(
            "coordinator_joined_notified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "NULL = no [coordinator_joined] system message emitted yet for the "
                "current assist window; set = emitted at this moment. Reset to NULL "
                "whenever the window closes (coordinator_chat_opened_at goes NULL), "
                "so the next request+join cycle notifies again."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("teams", "coordinator_joined_notified_at")
