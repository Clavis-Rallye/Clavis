"""Add share_code column to teams

Three steps add a NOT NULL ``share_code`` with a partial unique index:
  1. Add the column nullable.
  2. Back-fill a code unique within its run for every team with ``deleted_at IS NULL``.
  3. Set NOT NULL, add a CHECK constraint and the partial unique index.

The back-fill runs on ``op.get_bind()`` and ``text()``, not the async ORM session,
because revision files are synchronous.

Revision ID: 013
Revises: 012
Create Date: 2026-05-07 13:00:00.000000

"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# A copy of the alphabet and length of app/services/team_share_code.py, so the
# migration survives changes to that module.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LENGTH = 5


def _gen() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))


def upgrade() -> None:
    # Step 1
    op.add_column(
        "teams",
        sa.Column(
            "share_code",
            sa.String(length=8),
            nullable=True,
            comment="Short share-code teammates enter to join this team (spec 023)",
        ),
    )

    # Step 2
    bind = op.get_bind()
    teams = bind.execute(sa.text("SELECT id, run_id FROM teams WHERE deleted_at IS NULL ORDER BY run_id")).fetchall()
    used_per_run: dict[str, set[str]] = {}
    for team_id, run_id in teams:
        used = used_per_run.setdefault(str(run_id), set())
        for _ in range(5):
            code = _gen()
            if code not in used:
                used.add(code)
                break
        else:
            raise RuntimeError(f"Could not generate unique share_code for team {team_id} after 5 tries")
        bind.execute(
            sa.text("UPDATE teams SET share_code = :c WHERE id = :id"),
            {"c": code, "id": team_id},
        )

    # Step 3
    op.alter_column("teams", "share_code", nullable=False)

    op.create_check_constraint(
        "ck_teams_share_code_length",
        "teams",
        sa.text("char_length(share_code) BETWEEN 4 AND 6"),
    )

    op.create_index(
        "ix_teams_run_share_code",
        "teams",
        ["run_id", "share_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_teams_run_share_code", table_name="teams")
    op.drop_constraint("ck_teams_share_code_length", "teams", type_="check")
    op.drop_column("teams", "share_code")
