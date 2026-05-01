"""Allow nullable team name for the name-pending creator flow

A creator can create a team and its share code before choosing a name. Joiners
meanwhile see a "Team Name pending" screen until the creator sets a name through
``PATCH /api/teams/{team_id}``.

``teams.name`` becomes nullable. Existing rows keep their names. Read paths in
``routers/`` pass ``None`` through instead of a server-side default. Chat, feed and
audit emitters carry structured fields, so frontends can localize.

The downgrade gives every name-pending row the sentinel ``'Unnamed Team'``, so NOT
NULL applies again without failing.

Revision ID: 014
Revises: 013
Create Date: 2026-05-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "teams",
        "name",
        existing_type=sa.String(length=255),
        nullable=True,
    )


def downgrade() -> None:
    """Backfill the sentinel name, then set NOT NULL.

    The sentinel is English because only an operator running the downgrade sees it.
    End users see a localized fallback.
    """
    op.execute("UPDATE teams SET name = 'Unnamed Team' WHERE name IS NULL")
    op.alter_column(
        "teams",
        "name",
        existing_type=sa.String(length=255),
        nullable=False,
    )
