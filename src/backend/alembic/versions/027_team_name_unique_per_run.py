"""Team names are unique per run, compared case-insensitively

Two teams of one run may no longer share a name. The index is on ``lower(name)``
without trimming, so "Schnelle Fuechse" and "schnelle fuechse" are the same name.
The ``TeamCreateRequest`` and ``TeamRenameRequest`` validators in ``schemas.py``
trim every new name. This migration does not trim old names.

The index is partial on ``deleted_at IS NULL``, like the share-code index of 013,
so a soft-deleted team frees its name. PostgreSQL treats NULLs as distinct in a
unique index, so name-pending teams (migration 014) never collide.

``CREATE UNIQUE INDEX`` would fail on duplicates that already exist. The upgrade
first renames only the rows that collide under ``(run_id, lower(name))``. In each
colliding group the earliest team by ``created_at`` keeps its name. Each later one
gets "` 2`", "` 3`" and so on appended. A row that collides with nothing stays as it
is, whatever its whitespace or length. `"  Foo  "`, `"foo"` and `" foo "` differ
under `lower(name)`, and all three survive. ``_dedupe()`` is a pure function, so a
test calls it without a database or an Alembic context. The back-fill runs on
``op.get_bind()`` and ``text()``, not the async ORM session, because revision files
are synchronous.

Revision ID: 027
Revises: 026
Create Date: 2026-09-26 10:00:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "027"
down_revision: str | Sequence[str] | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_teams_run_id_lower_name"

# teams.name is VARCHAR(255), and a suffixed name must fit it.
_COLUMN_MAX = 255


def _dedupe(rows: Sequence[tuple[Any, Any, str]]) -> dict[Any, str]:
    """Return `{id: new_name}` for the rows that must change.

    `rows` holds `(id, run_id, name)` of every active named team, ordered by
    `(run_id, created_at, id)`. So the first row of a colliding group is its earliest team.

    A unique row and the earliest row of each group are not in the result. Every later
    row gets a numeric suffix, and its name is cut only as far as the column limit
    requires. Each candidate is checked against every `lower(name)` of the run, so a
    suffix cannot hit an unrelated name either.
    """
    # Rows per (run_id, lower(name)), the key the index enforces. A count of 1 means
    # no collision.
    counts: dict[tuple[str, str], int] = {}
    for _team_id, run_id, name in rows:
        key = (str(run_id), name.lower())
        counts[key] = counts.get(key, 0) + 1

    # Every lower(name) already present in each run, before any rewrite.
    used_per_run: dict[str, set[str]] = {}
    for _team_id, run_id, name in rows:
        used_per_run.setdefault(str(run_id), set()).add(name.lower())

    renames: dict[Any, str] = {}
    seen_first: set[tuple[str, str]] = set()
    for team_id, run_id, name in rows:
        key = (str(run_id), name.lower())
        if counts[key] == 1:
            continue

        if key not in seen_first:
            # The earliest row of the group keeps its name.
            seen_first.add(key)
            continue

        used = used_per_run[str(run_id)]
        suffix = 2
        while True:
            suffix_text = f" {suffix}"
            base = name[: _COLUMN_MAX - len(suffix_text)]
            candidate = f"{base}{suffix_text}"
            if candidate.lower() not in used:
                break
            suffix += 1
        used.add(candidate.lower())
        renames[team_id] = candidate

    return renames


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, run_id, name FROM teams "
            "WHERE deleted_at IS NULL AND name IS NOT NULL "
            "ORDER BY run_id, created_at, id"
        )
    ).fetchall()

    renames = _dedupe([(row.id, row.run_id, row.name) for row in rows])
    for team_id, new_name in renames.items():
        bind.execute(
            sa.text("UPDATE teams SET name = :name WHERE id = :id"),
            {"name": new_name, "id": team_id},
        )

    op.create_index(
        _INDEX,
        "teams",
        ["run_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """The renamed duplicates keep their suffixes."""
    op.drop_index(_INDEX, table_name="teams")
