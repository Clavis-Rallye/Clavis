"""The operator states how long backups keep deleted data

``instance_settings.backup_retention_days`` holds the number of days a backup
of this installation can still contain data that the app already deleted. The
operator console writes it. The privacy template and the retention settings
read it. NULL means "not set": the app then uses ``BACKUP_ROTATION_DAYS``.

The migration adds one nullable column and changes no row.

Revision ID: 040
Revises: 039
Create Date: 2026-11-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instance_settings",
        sa.Column(
            "backup_retention_days",
            sa.Integer(),
            nullable=True,
            comment="Days a backup can still hold deleted data (null = BACKUP_ROTATION_DAYS)",
        ),
    )


def downgrade() -> None:
    op.drop_column("instance_settings", "backup_retention_days")
