"""Add domain models - Create rallyes, stations, and challenges tables

Revision ID: 003
Revises: 002
Create Date: 2026-02-12 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create rallyes, stations, and challenges tables with all columns, indexes, and constraints."""

    # Create rallyes table
    op.create_table(
        "rallyes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column(
            "title",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual title: {'de': '...', 'en': '...'}",
        ),
        sa.Column(
            "description",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual description: {'de': '...', 'en': '...'}",
        ),
        sa.Column(
            "is_published",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Whether this rallye is published and available for runs",
        ),
        sa.Column(
            "station_order",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
            comment="Ordered list of station UUIDs defining the fixed sequence",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when last updated",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when soft-deleted"),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Boolean flag indicating soft deletion",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Rallye entity representing a rallye template (stable content)",
    )

    # Create indexes for rallyes
    op.create_index(op.f("ix_rallyes_organization_id"), "rallyes", ["organization_id"], unique=False)

    # Create stations table
    op.create_table(
        "stations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("rallye_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent rallye"),
        sa.Column(
            "title",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual title: {'de': '...', 'en': '...'}",
        ),
        sa.Column(
            "description",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual description: {'de': '...', 'en': '...'}",
        ),
        sa.Column("location_lat", sa.Float(), nullable=True, comment="Latitude coordinate of the station location"),
        sa.Column("location_lng", sa.Float(), nullable=True, comment="Longitude coordinate of the station location"),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Order of this station within the rallye",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when last updated",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when soft-deleted"),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Boolean flag indicating soft deletion",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rallye_id"], ["rallyes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Station entity representing a location with challenges in a rallye",
    )

    # Create indexes for stations
    op.create_index(op.f("ix_stations_organization_id"), "stations", ["organization_id"], unique=False)
    op.create_index(op.f("ix_stations_rallye_id"), "stations", ["rallye_id"], unique=False)

    # Create challenge_type enum
    op.execute("CREATE TYPE challenge_type AS ENUM ('photo', 'text', 'qr_scan', 'quiz', 'creative')")

    # Create challenges table
    op.create_table(
        "challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent station"),
        sa.Column(
            "challenge_type",
            postgresql.ENUM("photo", "text", "qr_scan", "quiz", "creative", name="challenge_type", create_type=False),
            nullable=False,
            comment="Type of challenge (photo, text, qr_scan, quiz, creative)",
        ),
        sa.Column(
            "title",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual title: {'de': '...', 'en': '...'}",
        ),
        sa.Column(
            "instructions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual instructions: {'de': '...', 'en': '...'}",
        ),
        sa.Column(
            "scoring_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Flexible scoring configuration (type-specific)",
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Order of this challenge within the station",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when last updated",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True, comment="Timestamp when soft-deleted"),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Boolean flag indicating soft deletion",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Challenge entity representing a task teams must complete at a station",
    )

    # Create indexes for challenges
    op.create_index(op.f("ix_challenges_organization_id"), "challenges", ["organization_id"], unique=False)
    op.create_index(op.f("ix_challenges_station_id"), "challenges", ["station_id"], unique=False)


def downgrade() -> None:
    """Drop challenges, stations, and rallyes tables in reverse order."""

    # Drop challenges table and indexes
    op.drop_index(op.f("ix_challenges_station_id"), table_name="challenges")
    op.drop_index(op.f("ix_challenges_organization_id"), table_name="challenges")
    op.drop_table("challenges")

    # Drop challenge_type enum
    op.execute("DROP TYPE challenge_type")

    # Drop stations table and indexes
    op.drop_index(op.f("ix_stations_rallye_id"), table_name="stations")
    op.drop_index(op.f("ix_stations_organization_id"), table_name="stations")
    op.drop_table("stations")

    # Drop rallyes table and indexes
    op.drop_index(op.f("ix_rallyes_organization_id"), table_name="rallyes")
    op.drop_table("rallyes")
