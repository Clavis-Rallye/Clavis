"""Audio/video challenge types + run content languages + announcement targeting

1. ``challenge_type`` gains ``audio`` and ``video``. ``challenges`` and
   ``run_challenges`` share this enum type, so one ALTER TYPE covers both.
2. ``runs.content_languages`` lists the ISO language codes of the run. It feeds the
   language selection before a run and filters content by language. Legacy rows get
   ``["de", "en"]``.
3. ``feed_events.target_team_id`` lets a coordinator announcement target one team.
   NULL broadcasts to the whole run.

Revision ID: 010
Revises: 009
Create Date: 2026-05-04 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ----------------------------------------------------------------
    # 1. Extend challenge_type enum with audio + video
    # ----------------------------------------------------------------
    op.execute("ALTER TYPE challenge_type ADD VALUE IF NOT EXISTS 'audio'")
    op.execute("ALTER TYPE challenge_type ADD VALUE IF NOT EXISTS 'video'")

    # ----------------------------------------------------------------
    # 2. runs.content_languages
    # ----------------------------------------------------------------
    op.add_column(
        "runs",
        sa.Column(
            "content_languages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text('\'["de", "en"]\'::jsonb'),
            comment="ISO codes of languages this run is configured to display",
        ),
    )

    # ----------------------------------------------------------------
    # 3. feed_events.target_team_id
    # ----------------------------------------------------------------
    op.add_column(
        "feed_events",
        sa.Column(
            "target_team_id",
            sa.UUID(),
            nullable=True,
            comment="If set, this event is only visible to this team (NULL = broadcast)",
        ),
    )
    op.create_foreign_key(
        "fk_feed_events_target_team_id_teams",
        "feed_events",
        "teams",
        ["target_team_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_feed_events_target_team_id",
        "feed_events",
        ["target_team_id"],
    )


def downgrade() -> None:
    # 3. Drop announcement targeting
    op.drop_index("ix_feed_events_target_team_id", table_name="feed_events")
    op.drop_constraint("fk_feed_events_target_team_id_teams", "feed_events", type_="foreignkey")
    op.drop_column("feed_events", "target_team_id")

    # 2. Drop content_languages
    op.drop_column("runs", "content_languages")

    # 1. PostgreSQL cannot drop enum values, so audio and video stay in the type.
