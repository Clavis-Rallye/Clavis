"""Add teams.coordinator_chat_opened_at for on-request coordinator chat

A coordinator reads a team chat only after the team asks for it, and the
coordinator can close that access at any time. The timestamp is the assist window
of the team: NULL means closed, a value means open since then. Existing rows stay
NULL, so standing coordinator access ends with this migration.

Revision ID: 021
Revises: 020
Create Date: 2026-07-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column(
            "coordinator_chat_opened_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Assist window for coordinator chat access (spec 037): NULL = closed, "
                "set = open since this moment. Opened by the team's assistance request, "
                "closed when a coordinator leaves the chat."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("teams", "coordinator_chat_opened_at")
