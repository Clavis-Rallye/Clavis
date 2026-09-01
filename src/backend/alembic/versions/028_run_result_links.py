"""Add run_result_links

A row is a published, expiring public link to the frozen final standing of a run.
The table stores only ``slug_hash``, the SHA-256 of the slug, never the slug. So a
read of the database or a log never discloses a live link that needs no login. Each
request recomputes the slug from the row's ``id`` by HMAC
(``app/repositories/run_result_link.py``).

The partial unique index on ``run_id`` where ``revoked_at IS NULL`` allows one live
link per run. Revoked rows stay beside the new one until the retention sweep deletes
them after ``expires_at``.

Revision ID: 028
Revises: 027
Create Date: 2026-09-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_result_links",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "slug_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 hex hash of the public slug — the slug itself is never stored",
        ),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Frozen public projection: run name, ended_at, organization name, language, ranked entries",
        ),
        sa.Column("published_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug_hash", name="uq_run_result_links_slug_hash"),
    )
    op.create_index(
        "ix_run_result_links_run_active_unique",
        "run_result_links",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_run_result_links_run_active_unique", table_name="run_result_links")
    op.drop_table("run_result_links")
