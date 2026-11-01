"""An operator invitation can take over from current org admins

``user_invitations.replaces_user_ids`` holds the org admins an operator's
"Admin einladen" replaces. When the invited person accepts, those accounts are
deactivated and their sessions end. Null for every other invitation.

Revision ID: 038
Revises: 037
Create Date: 2026-11-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_invitations",
        sa.Column(
            "replaces_user_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Org admins this invitation takes over from (operator takeover, D7)",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_invitations", "replaces_user_ids")
