"""Station & Challenge global library (many-to-many reuse across rallyes)

Stations and challenges stop belonging to one rallye. They form a library of the
organization, which rallyes compose by reference:

- ``rallye_stations`` links a rallye to a station, with a sort order per rallye.
- ``rallye_station_challenges`` links a rallye station to a challenge, with a sort
  order per rallye station.
- ``stations`` loses ``rallye_id`` and ``sort_order`` and gains ``address``.
- ``challenges`` loses ``station_id`` and ``sort_order``.
- ``rallyes`` loses ``station_order``. The order lives on the link rows.

The upgrade fills the link tables from the old foreign keys before it drops them.
Each station keeps its rallye and each challenge its station, in the same order. The
station order follows ``rallyes.station_order`` before ``stations.sort_order``, as
the creator UI showed it. Run snapshots (``run_stations``, ``run_challenges``) stay
untouched, because they are immutable copies.

The downgrade loses data for an item linked more than once, such as a station in two
rallyes: it keeps the link with the lowest sort order. That is acceptable for an
operator escape hatch, which no user path reaches.

Revision ID: 015
Revises: 014
Create Date: 2026-06-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Association tables -------------------------------------------------
    op.create_table(
        "rallye_stations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rallye_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rallye_id"], ["rallyes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("rallye_id", "station_id", name="uq_rallye_stations_rallye_station"),
    )
    op.create_index("ix_rallye_stations_organization_id", "rallye_stations", ["organization_id"])
    op.create_index("ix_rallye_stations_rallye_id", "rallye_stations", ["rallye_id"])
    op.create_index("ix_rallye_stations_station_id", "rallye_stations", ["station_id"])

    op.create_table(
        "rallye_station_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rallye_station_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rallye_station_id"], ["rallye_stations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "rallye_station_id", "challenge_id", name="uq_rallye_station_challenges_rallye_station_challenge"
        ),
    )
    op.create_index("ix_rallye_station_challenges_organization_id", "rallye_station_challenges", ["organization_id"])
    op.create_index(
        "ix_rallye_station_challenges_rallye_station_id", "rallye_station_challenges", ["rallye_station_id"]
    )
    op.create_index("ix_rallye_station_challenges_challenge_id", "rallye_station_challenges", ["challenge_id"])

    # 2. Add station.address -----------------------------------------------
    op.add_column("stations", sa.Column("address", sa.String(length=500), nullable=True))

    # 3. Backfill links from the existing single-owner FKs ------------------
    # The creator UI ordered stations by ``rallyes.station_order``, a JSONB array of
    # station ids, and appended the missing ones by ``stations.sort_order``. The
    # INSERT reproduces that and renumbers 0..n-1 per rallye. Plain ``s.sort_order``
    # would lose every drag reorder since the rallye was created.
    op.execute(
        """
        INSERT INTO rallye_stations (id, organization_id, rallye_id, station_id, sort_order, created_at, updated_at)
        SELECT gen_random_uuid(), s.organization_id, s.rallye_id, s.id,
               (ROW_NUMBER() OVER (
                    PARTITION BY s.rallye_id
                    ORDER BY (ord.position IS NULL), ord.position, s.sort_order
                ) - 1),
               now(), now()
        FROM stations s
        JOIN rallyes r ON r.id = s.rallye_id
        LEFT JOIN LATERAL (
            SELECT elem.position
            FROM jsonb_array_elements_text(r.station_order)
                 WITH ORDINALITY AS elem(station_id, position)
            WHERE elem.station_id = s.id::text
        ) ord ON TRUE
        WHERE s.rallye_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO rallye_station_challenges (
            id, organization_id, rallye_station_id, challenge_id, sort_order, created_at, updated_at
        )
        SELECT gen_random_uuid(), c.organization_id, rs.id, c.id, c.sort_order, now(), now()
        FROM challenges c
        JOIN rallye_stations rs ON rs.station_id = c.station_id
        WHERE c.station_id IS NOT NULL
        """
    )

    # 4. Drop the now-superseded owner columns ------------------------------
    op.drop_index("ix_stations_rallye_id", table_name="stations")
    op.drop_constraint("fk_stations_rallye_id_rallyes", "stations", type_="foreignkey")
    op.drop_column("stations", "rallye_id")
    op.drop_column("stations", "sort_order")

    op.drop_index("ix_challenges_station_id", table_name="challenges")
    op.drop_constraint("fk_challenges_station_id_stations", "challenges", type_="foreignkey")
    op.drop_column("challenges", "station_id")
    op.drop_column("challenges", "sort_order")

    op.drop_column("rallyes", "station_order")


def downgrade() -> None:
    # 1. Re-add owner columns (nullable first for backfill) -----------------
    op.add_column(
        "rallyes",
        sa.Column("station_order", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("stations", sa.Column("rallye_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("stations", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("challenges", sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("challenges", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))

    # 2. Restore FK from the lowest-sort link (lossy for shared items) ------
    op.execute(
        """
        UPDATE stations s
        SET rallye_id = rs.rallye_id, sort_order = rs.sort_order
        FROM (
            SELECT DISTINCT ON (station_id) station_id, rallye_id, sort_order
            FROM rallye_stations
            ORDER BY station_id, sort_order, id
        ) rs
        WHERE rs.station_id = s.id
        """
    )
    op.execute(
        """
        UPDATE challenges c
        SET station_id = sc.station_id, sort_order = sc.sort_order
        FROM (
            SELECT DISTINCT ON (rsc.challenge_id)
                rsc.challenge_id, rs.station_id, rsc.sort_order
            FROM rallye_station_challenges rsc
            JOIN rallye_stations rs ON rs.id = rsc.rallye_station_id
            ORDER BY rsc.challenge_id, rsc.sort_order, rsc.id
        ) sc
        WHERE sc.challenge_id = c.id
        """
    )
    # Rebuild rallyes.station_order from the restored station ownership.
    op.execute(
        """
        UPDATE rallyes r
        SET station_order = COALESCE((
            SELECT jsonb_agg(to_jsonb(s.id::text) ORDER BY s.sort_order, s.id)
            FROM stations s
            WHERE s.rallye_id = r.id AND s.is_deleted = false
        ), '[]'::jsonb)
        """
    )

    # 3. Drop orphaned rows that had no owner link, then enforce NOT NULL ---
    op.execute("DELETE FROM stations WHERE rallye_id IS NULL")
    op.execute("DELETE FROM challenges WHERE station_id IS NULL")
    op.alter_column("stations", "rallye_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column("challenges", "station_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.create_foreign_key(
        "fk_stations_rallye_id_rallyes", "stations", "rallyes", ["rallye_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_stations_rallye_id", "stations", ["rallye_id"])
    op.create_foreign_key(
        "fk_challenges_station_id_stations", "challenges", "stations", ["station_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_challenges_station_id", "challenges", ["station_id"])

    # 4. Drop the address column added by the upgrade -----------------------
    op.drop_column("stations", "address")

    # 5. Drop association tables (children before parents) -------------------
    op.drop_table("rallye_station_challenges")
    op.drop_table("rallye_stations")
