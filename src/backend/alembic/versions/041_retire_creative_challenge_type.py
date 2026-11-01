"""The library's creative challenges become photo challenges

The type "kreativ" goes. A photo, video or audio answer takes a text as a fallback,
so a photo challenge accepts what the creative form offered:
a photo, a text, or both.

Only the library changes (``challenges``, archived rows included, so a restore cannot
bring the type back). Run snapshots (``run_challenges``) and their answers stay as they
are: a snapshot is frozen at the run's start, and the app still reads and validates a
creative snapshot.

The downgrade changes nothing. After the upgrade a photo challenge that was creative
cannot be told apart from one that was always a photo challenge.

Revision ID: 041
Revises: 040
Create Date: 2026-11-19 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "041"
down_revision: str | None = "040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE challenges SET challenge_type = 'photo' WHERE challenge_type = 'creative'")


def downgrade() -> None:
    pass
