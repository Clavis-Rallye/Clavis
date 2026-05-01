"""RunChallenge snapshot fields + ChatMessage author role

1. ``run_challenges`` gains ``time_estimate_minutes`` and ``suggested_solution``, so
   a run carries the duration and the reviewer aid authored on the challenge
   (migration 009). The snapshot service copies them when a run becomes active.
2. ``chat_messages.author_role`` lets the client draw coordinator messages in their
   own bubble colour. It is stored at write time, so the colour stays right after the
   author is demoted or deleted.

Revision ID: 011
Revises: 010
Create Date: 2026-05-04 17:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ----------------------------------------------------------------
    # 1. Snapshot the new Challenge fields onto RunChallenge
    # ----------------------------------------------------------------
    op.add_column(
        "run_challenges",
        sa.Column(
            "time_estimate_minutes",
            sa.Integer(),
            nullable=True,
            comment="Snapshotted estimated time to complete (from Challenge at run start)",
        ),
    )
    op.add_column(
        "run_challenges",
        sa.Column(
            "suggested_solution",
            sa.Text(),
            nullable=True,
            comment="Snapshotted reviewer-facing reference answer (from Challenge at run start)",
        ),
    )

    # ----------------------------------------------------------------
    # 2. Stamp chat messages with the author's role at write time
    # ----------------------------------------------------------------
    op.add_column(
        "chat_messages",
        sa.Column(
            "author_role",
            sa.String(length=32),
            nullable=True,
            comment="Author's role at the time the message was sent (guest/participant/coordinator/creator/org_admin)",
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "author_role")
    op.drop_column("run_challenges", "suggested_solution")
    op.drop_column("run_challenges", "time_estimate_minutes")
