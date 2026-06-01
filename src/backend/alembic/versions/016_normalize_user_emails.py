"""Normalize stored user emails to lowercase

A user with a mixed-case email (``Erika.Beispiel@…``) could not log in with the
lowercase variant. The request schemas now lowercase every email, and the login
lookup ignores case. This migration lowercases the stored emails, so each equals
its normalized form.

Two rows whose emails differ only by case would break the partial unique index of
migration 006. The migration then fails and names them, and an operator resolves
them by hand.

The downgrade changes nothing. The original casing is lost, and lowercase emails are
valid under the old schema.

Revision ID: 016
Revises: 015
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    collisions = conn.execute(
        sa.text(
            "SELECT lower(email) AS email_lower, count(*) AS n "
            "FROM users WHERE email IS NOT NULL "
            "GROUP BY lower(email) HAVING count(*) > 1"
        )
    ).fetchall()
    if collisions:
        details = ", ".join(f"{row.email_lower} (x{row.n})" for row in collisions)
        raise RuntimeError(
            "Cannot normalize user emails: case-insensitive duplicates exist "
            f"and must be resolved manually first: {details}"
        )

    op.execute("UPDATE users SET email = lower(email) WHERE email IS NOT NULL AND email != lower(email)")


def downgrade() -> None:
    """No-op: the original casing is lost and not needed."""
