"""runs.rallye_id: ON DELETE CASCADE → SET NULL and nullable, index on team_members.user_id

A run outlives its template rallye. The model declares ``rallye_id`` nullable with
``ondelete="SET NULL"``, and the permanent delete of a rallye promises that "run
snapshots survive by FK design". Migrations 003 and 004 kept a NOT NULL column with
CASCADE. Permanently deleting an archived rallye would have deleted its runs, and
through their cascades their teams, submissions, scores and chat.

The test suite builds its schema from the models, so it could not see the gap. The
revision also adds the index on ``team_members.user_id`` that the model declares and
no migration created.

Revision ID: 026
Revises: 025
Create Date: 2026-09-18 23:45:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "026"
down_revision: str | Sequence[str] | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_runs_rallye_id_rallyes"


def upgrade() -> None:
    op.drop_constraint(_FK, "runs", type_="foreignkey")
    op.alter_column("runs", "rallye_id", nullable=True)
    op.create_foreign_key(
        _FK,
        "runs",
        "rallyes",
        ["rallye_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_team_members_user_id", table_name="team_members")
    # A run detached from its rallye cannot go back to NOT NULL. The downgrade
    # refuses while one exists, rather than delete it.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM runs WHERE rallye_id IS NULL) THEN "
        "RAISE EXCEPTION 'runs.rallye_id has NULL rows, downgrade of 026 refused'; END IF; END $$;"
    )
    op.drop_constraint(_FK, "runs", type_="foreignkey")
    op.alter_column("runs", "rallye_id", nullable=False)
    op.create_foreign_key(
        _FK,
        "runs",
        "rallyes",
        ["rallye_id"],
        ["id"],
        ondelete="CASCADE",
    )
