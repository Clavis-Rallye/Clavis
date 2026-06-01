"""Add station title image object key

Stations gain ``title_image_key``, the object-storage key of their cover image. It
is a column, not part of a JSON blob, so the GDPR retention sweep can list the
station images directly. The run snapshot copies it into ``run_stations``, so a
running run shows a frozen image. Existing rows keep NULL.

Revision ID: 018
Revises: 017
Create Date: 2026-06-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column(
            "title_image_key",
            sa.String(length=1024),
            nullable=True,
            comment="Object-storage key of the station's title image (nullable)",
        ),
    )
    op.add_column(
        "run_stations",
        sa.Column(
            "title_image_key",
            sa.String(length=1024),
            nullable=True,
            comment="Station title-image key frozen from source at run start (nullable)",
        ),
    )


def downgrade() -> None:
    op.drop_column("run_stations", "title_image_key")
    op.drop_column("stations", "title_image_key")
