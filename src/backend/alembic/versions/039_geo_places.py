"""Add geo_places, the regional place index of the address search

The table is empty after the migration. The operator fills it with
``python -m app.cli geo import``. The two trigram indexes need ``pg_trgm``,
which ships with PostgreSQL and is a trusted extension: the owner of the
database may create it.

Revision ID: 039
Revises: 038
Create Date: 2026-11-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "geo_places",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("weight", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("street", sa.String(length=255), nullable=True),
        sa.Column("housenumber", sa.String(length=30), nullable=True),
        sa.Column("postcode", sa.String(length=10), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("district", sa.String(length=120), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("name_norm", sa.String(length=300), nullable=False),
        sa.Column("search", sa.String(length=700), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_geo_places_search_trgm",
        "geo_places",
        ["search"],
        postgresql_using="gin",
        postgresql_ops={"search": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_geo_places_name_norm_trgm",
        "geo_places",
        ["name_norm"],
        postgresql_using="gin",
        postgresql_ops={"name_norm": "gin_trgm_ops"},
    )
    op.create_index("ix_geo_places_lat_lng", "geo_places", ["lat", "lng"])


def downgrade() -> None:
    # The extension stays: another table may use it by then.
    op.drop_table("geo_places")
