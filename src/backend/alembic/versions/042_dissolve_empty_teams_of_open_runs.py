"""Dissolve the empty teams of runs that have not ended

The application dissolves a team when its last member goes: it sets
``is_deleted`` and ``deleted_at``. Earlier versions kept a team without a member row in its
run. This migration sets the same two fields on every such team left over, when all three
conditions hold:

- the team has no row in ``team_members``,
- the team has no row in ``submissions``,
- its run is not in state ``ended``.

Nothing else changes. A team of an ended run is never touched: its answers and its place
are part of that run's results. A team that holds an answer stays visible, the Spielleitung
removes it with "Team entfernen". A team with a member row stays, also when the row carries
no account.

The upgrade skips teams that are already deleted, so a second run changes nothing and keeps
the original ``deleted_at``.

Before it decides, the upgrade locks ``team_members`` in SHARE ROW EXCLUSIVE mode, in the
same transaction as the UPDATE. A deploy runs the migration while the old API
still serves. Without the lock a join that has written its member row and not committed is
invisible to the UPDATE, which then dissolves a team that has a member a moment later. The
lock holds every insert, update and delete of member rows until the migration commits and
lets readers through. It also waits for the writes that are open when it is asked for, so
the UPDATE sees their rows. It does not close a join of an older API that found its team
before the migration and writes its member row after it: that row lands in a dissolved
team. An installation with an open run stops the API for the migration.

The downgrade changes nothing. The rows cannot be told from teams dissolved by the
application, so there is nothing to bring back that would not also bring back those.

Revision ID: 042
Revises: 041
Create Date: 2026-11-20 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "042"
down_revision: str | None = "041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Holds member writes until this transaction commits, see the docstring.
    op.execute("LOCK TABLE team_members IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        """
        UPDATE teams
        SET is_deleted = true, deleted_at = now()
        WHERE is_deleted = false
          AND NOT EXISTS (SELECT 1 FROM team_members WHERE team_members.team_id = teams.id)
          AND NOT EXISTS (SELECT 1 FROM submissions WHERE submissions.team_id = teams.id)
          AND EXISTS (SELECT 1 FROM runs WHERE runs.id = teams.run_id AND runs.state <> 'ended')
        """
    )


def downgrade() -> None:
    # No-op: the dissolved rows cannot be told from teams dissolved by the application.
    pass
