"""Make Score.scored_by nullable for auto-scoring

An auto-scored answer, such as a quiz answer, gets a score without a human scorer:
``scored_by`` is NULL.

The foreign key changes from CASCADE to SET NULL on delete, as the model declares.

Revision ID: 008
Revises: 007
Create Date: 2026-02-18 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("fk_scores_scored_by_users", "scores", type_="foreignkey")

    op.alter_column(
        "scores",
        "scored_by",
        nullable=True,
    )

    op.create_foreign_key(
        "fk_scores_scored_by_users",
        "scores",
        "users",
        ["scored_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_scores_scored_by_users", "scores", type_="foreignkey")

    # Fails while any score has a NULL scorer.
    op.alter_column(
        "scores",
        "scored_by",
        nullable=False,
    )

    op.create_foreign_key(
        "fk_scores_scored_by_users",
        "scores",
        "users",
        ["scored_by"],
        ["id"],
        ondelete="CASCADE",
    )
