"""Add station tips & links popup

Stations gain ``tips``, a popup with a multilingual header and a list of external
links with multilingual labels:
``{"header": {"de": ...}, "links": [{"label": {"de": ...}, "url": ...}]}``.
NULL means no popup. The run snapshot copies it into ``run_stations``, so a running
run shows frozen tips. Existing rows keep NULL. The API validates the shape
(``schemas.StationTips``), the database does not.

Revision ID: 023
Revises: 022
Create Date: 2026-07-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column(
            "tips",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Optional tips & links popup (#262, nullable)",
        ),
    )
    op.add_column(
        "run_stations",
        sa.Column(
            "tips",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Tips & links popup frozen from source at run start (#262, nullable)",
        ),
    )


def downgrade() -> None:
    op.drop_column("run_stations", "tips")
    op.drop_column("stations", "tips")
