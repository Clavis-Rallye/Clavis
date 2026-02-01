"""Add runtime models - Create runs, teams, submissions, scores, feed, chat, and feedback tables

Revision ID: 004
Revises: 003
Create Date: 2026-02-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all runtime tables with columns, indexes, and constraints."""

    # Create run_state enum
    op.execute("CREATE TYPE run_state AS ENUM ('configured', 'scheduled', 'active', 'paused', 'ended')")

    # Create runs table
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("rallye_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to template rallye"),
        sa.Column("name", sa.String(), nullable=False, comment="Human-readable name for this run"),
        sa.Column(
            "state",
            postgresql.ENUM(
                "configured", "scheduled", "active", "paused", "ended", name="run_state", create_type=False
            ),
            nullable=False,
            server_default="configured",
            comment="Current state in the run lifecycle",
        ),
        sa.Column(
            "join_code",
            sa.String(length=6),
            nullable=False,
            comment="6-character alphanumeric join code, unique within org",
        ),
        sa.Column(
            "scheduled_for", sa.DateTime(timezone=True), nullable=True, comment="When the run is scheduled to start"
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the run actually started (first transition to active)",
        ),
        sa.Column(
            "paused_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the run was paused (null if not paused or resumed)",
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True, comment="When the run ended (terminal state)"),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Run-specific settings (JSONB)",
        ),
        sa.Column(
            "max_teams", sa.Integer(), nullable=True, comment="Maximum number of teams allowed (null = unlimited)"
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
        comment="Run entity representing a live instance of a rallye",
    )

    # Create indexes for runs
    op.create_index(op.f("ix_runs_organization_id"), "runs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_runs_rallye_id"), "runs", ["rallye_id"], unique=False)
    # Unique constraint on join_code within organization (scoped to non-deleted)
    op.create_index(
        "ix_runs_org_join_code_unique",
        "runs",
        ["organization_id", "join_code"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )

    # Create run_stations table (frozen snapshots, no soft delete)
    op.create_table(
        "run_stations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent run"),
        sa.Column(
            "source_station_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Foreign key to template station (nullable - template may be deleted)",
        ),
        sa.Column(
            "title",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual title snapshot: {'de': '...', 'en': '...'}",
        ),
        sa.Column(
            "description",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual description snapshot: {'de': '...', 'en': '...'}",
        ),
        sa.Column("location_lat", sa.Float(), nullable=True, comment="Latitude coordinate of the station location"),
        sa.Column("location_lng", sa.Float(), nullable=True, comment="Longitude coordinate of the station location"),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Order of this station within the run",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when snapshot created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when last updated",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_station_id"], ["stations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        comment="RunStation entity - immutable snapshot of a Station for a specific run",
    )

    # Create indexes for run_stations
    op.create_index(op.f("ix_run_stations_organization_id"), "run_stations", ["organization_id"], unique=False)
    op.create_index(op.f("ix_run_stations_run_id"), "run_stations", ["run_id"], unique=False)

    # Create run_challenges table (frozen snapshots, no soft delete)
    op.create_table(
        "run_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent run"),
        sa.Column(
            "run_station_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent run station (snapshot)",
        ),
        sa.Column(
            "source_challenge_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Foreign key to template challenge (nullable - template may be deleted)",
        ),
        sa.Column(
            "challenge_type",
            postgresql.ENUM("photo", "text", "qr_scan", "quiz", "creative", name="challenge_type", create_type=False),
            nullable=False,
            comment="Type of challenge (reuses existing enum)",
        ),
        sa.Column(
            "title",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual title snapshot: {'de': '...', 'en': '...'}",
        ),
        sa.Column(
            "instructions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Multilingual instructions snapshot: {'de': '...', 'en': '...'}",
        ),
        sa.Column(
            "scoring_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Flexible scoring configuration snapshot (type-specific)",
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Order of this challenge within the run station",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when snapshot created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Timestamp when last updated",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_station_id"], ["run_stations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_challenge_id"], ["challenges.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        comment="RunChallenge entity - immutable snapshot of a Challenge for a specific run",
    )

    # Create indexes for run_challenges
    op.create_index(op.f("ix_run_challenges_organization_id"), "run_challenges", ["organization_id"], unique=False)
    op.create_index(op.f("ix_run_challenges_run_id"), "run_challenges", ["run_id"], unique=False)
    op.create_index(op.f("ix_run_challenges_run_station_id"), "run_challenges", ["run_station_id"], unique=False)

    # Create teams table
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent run"),
        sa.Column("name", sa.String(), nullable=False, comment="Team name"),
        sa.Column("color", sa.String(), nullable=True, comment="Team color (hex code)"),
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
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Team entity representing a group of participants in a run",
    )

    # Create indexes for teams
    op.create_index(op.f("ix_teams_organization_id"), "teams", ["organization_id"], unique=False)
    op.create_index(op.f("ix_teams_run_id"), "teams", ["run_id"], unique=False)

    # Create team_members table
    op.create_table(
        "team_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent team"),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Foreign key to user (null for guest participants)",
        ),
        sa.Column("display_name", sa.String(), nullable=False, comment="Display name for this team member"),
        sa.Column(
            "is_guest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Whether this is a guest participant",
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When this member joined the team",
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
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="TeamMember entity representing an individual participant within a team",
    )

    # Create indexes for team_members
    op.create_index(op.f("ix_team_members_organization_id"), "team_members", ["organization_id"], unique=False)
    op.create_index(op.f("ix_team_members_team_id"), "team_members", ["team_id"], unique=False)

    # Partial unique index on (team_id, user_id) for non-null user_id: a user joins a team once.
    # It does not stop a user from joining several teams of one run, because that needs run_id from the team.
    # The application enforces that rule: it refuses the join with ALREADY_IN_OTHER_TEAM in app/routers/teams.py.
    op.execute("""
        CREATE UNIQUE INDEX ix_team_members_user_team_unique
        ON team_members (team_id, user_id)
        WHERE user_id IS NOT NULL
    """)

    # Create submissions table
    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent run"),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent team"),
        sa.Column(
            "run_challenge_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to run challenge (snapshot)",
        ),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Submission content (structure varies by challenge type)",
        ),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the submission was made (server timestamp)",
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
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_challenge_id"], ["run_challenges.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Submission entity representing a team's response to a challenge",
    )

    # Create indexes for submissions
    op.create_index(op.f("ix_submissions_organization_id"), "submissions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_submissions_run_id"), "submissions", ["run_id"], unique=False)
    op.create_index(op.f("ix_submissions_team_id"), "submissions", ["team_id"], unique=False)
    op.create_index(op.f("ix_submissions_run_challenge_id"), "submissions", ["run_challenge_id"], unique=False)

    # Unique constraint: one submission per challenge per team
    op.create_index("ix_submissions_team_challenge_unique", "submissions", ["team_id", "run_challenge_id"], unique=True)

    # Create scores table
    op.create_table(
        "scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to submission (unique - one score per submission)",
        ),
        sa.Column("points", sa.Integer(), nullable=False, comment="Points awarded (0 to max_points)"),
        sa.Column("max_points", sa.Integer(), nullable=False, comment="Maximum possible points for this challenge"),
        sa.Column("comment", sa.String(), nullable=True, comment="Optional comment from coordinator"),
        sa.Column(
            "scored_by",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to user who scored (coordinator)",
        ),
        sa.Column(
            "scored_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the score was assigned",
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
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scored_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Score entity representing coordinator evaluation of a submission",
    )

    # Create indexes for scores
    op.create_index(op.f("ix_scores_organization_id"), "scores", ["organization_id"], unique=False)

    # Unique constraint: one score per submission
    op.create_index("ix_scores_submission_unique", "scores", ["submission_id"], unique=True)

    # Create feed_event_type enum
    op.execute("CREATE TYPE feed_event_type AS ENUM ('system', 'announcement', 'milestone')")

    # Create feed_events table
    op.create_table(
        "feed_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent run"),
        sa.Column(
            "event_type",
            postgresql.ENUM("system", "announcement", "milestone", name="feed_event_type", create_type=False),
            nullable=False,
            comment="Type of feed event",
        ),
        sa.Column(
            "content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Event content (multilingual or structured)",
        ),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Foreign key to user (null for system events)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the event was created",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        comment="FeedEvent entity representing system events and coordinator announcements",
    )

    # Create indexes for feed_events
    op.create_index(op.f("ix_feed_events_organization_id"), "feed_events", ["organization_id"], unique=False)
    op.create_index(op.f("ix_feed_events_run_id"), "feed_events", ["run_id"], unique=False)

    # Create chat_messages table
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent team"),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Foreign key to user (null for system messages)",
        ),
        sa.Column("content", sa.String(), nullable=False, comment="Message text content"),
        sa.Column(
            "is_system_message",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Whether this is a system-generated message",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the message was sent",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        comment="ChatMessage entity representing team-internal chat messages",
    )

    # Create indexes for chat_messages
    op.create_index(op.f("ix_chat_messages_organization_id"), "chat_messages", ["organization_id"], unique=False)
    op.create_index(op.f("ix_chat_messages_team_id"), "chat_messages", ["team_id"], unique=False)

    # Create feedback table
    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, comment="UUID primary key"),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Foreign key to parent organization",
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False, comment="Foreign key to parent run"),
        sa.Column("rating", sa.Integer(), nullable=False, comment="Rating (1-5 scale)"),
        sa.Column("comment", sa.String(), nullable=True, comment="Optional feedback comment"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the feedback was submitted",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="Feedback entity representing anonymous post-run feedback (no user_id by design)",
    )

    # Create indexes for feedback
    op.create_index(op.f("ix_feedback_organization_id"), "feedback", ["organization_id"], unique=False)
    op.create_index(op.f("ix_feedback_run_id"), "feedback", ["run_id"], unique=False)


def downgrade() -> None:
    """Drop all runtime tables in reverse order."""

    # Drop feedback table and indexes
    op.drop_index(op.f("ix_feedback_run_id"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_organization_id"), table_name="feedback")
    op.drop_table("feedback")

    # Drop chat_messages table and indexes
    op.drop_index(op.f("ix_chat_messages_team_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_organization_id"), table_name="chat_messages")
    op.drop_table("chat_messages")

    # Drop feed_events table and indexes
    op.drop_index(op.f("ix_feed_events_run_id"), table_name="feed_events")
    op.drop_index(op.f("ix_feed_events_organization_id"), table_name="feed_events")
    op.drop_table("feed_events")

    # Drop feed_event_type enum
    op.execute("DROP TYPE feed_event_type")

    # Drop scores table and indexes
    op.drop_index("ix_scores_submission_unique", table_name="scores")
    op.drop_index(op.f("ix_scores_organization_id"), table_name="scores")
    op.drop_table("scores")

    # Drop submissions table and indexes
    op.drop_index("ix_submissions_team_challenge_unique", table_name="submissions")
    op.drop_index(op.f("ix_submissions_run_challenge_id"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_team_id"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_run_id"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_organization_id"), table_name="submissions")
    op.drop_table("submissions")

    # Drop team_members table and indexes
    op.execute("DROP INDEX ix_team_members_user_team_unique")
    op.drop_index(op.f("ix_team_members_team_id"), table_name="team_members")
    op.drop_index(op.f("ix_team_members_organization_id"), table_name="team_members")
    op.drop_table("team_members")

    # Drop teams table and indexes
    op.drop_index(op.f("ix_teams_run_id"), table_name="teams")
    op.drop_index(op.f("ix_teams_organization_id"), table_name="teams")
    op.drop_table("teams")

    # Drop run_challenges table and indexes
    op.drop_index(op.f("ix_run_challenges_run_station_id"), table_name="run_challenges")
    op.drop_index(op.f("ix_run_challenges_run_id"), table_name="run_challenges")
    op.drop_index(op.f("ix_run_challenges_organization_id"), table_name="run_challenges")
    op.drop_table("run_challenges")

    # Drop run_stations table and indexes
    op.drop_index(op.f("ix_run_stations_run_id"), table_name="run_stations")
    op.drop_index(op.f("ix_run_stations_organization_id"), table_name="run_stations")
    op.drop_table("run_stations")

    # Drop runs table and indexes
    op.drop_index("ix_runs_org_join_code_unique", table_name="runs")
    op.drop_index(op.f("ix_runs_rallye_id"), table_name="runs")
    op.drop_index(op.f("ix_runs_organization_id"), table_name="runs")
    op.drop_table("runs")

    # Drop run_state enum
    op.execute("DROP TYPE run_state")
