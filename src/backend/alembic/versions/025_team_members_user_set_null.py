"""team_members.user_id: ON DELETE CASCADE → SET NULL

With CASCADE, deleting a guest account deleted its membership rows, so ending a run
erased who had been in which team. ``guest_cleanup`` hard-deletes guests and relies
on every foreign key to ``users.id`` being SET NULL, which keeps the history of the
run. The keys of scores, chat and feed authors were SET NULL. The key of
``team_members``, the roster itself, was CASCADE.

A membership row carries its own ``display_name`` and ``is_guest``, so the roster
stays readable once ``user_id`` is NULL.

Revision ID: 025
Revises: 024
Create Date: 2026-07-28 22:50:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "025"
down_revision: str | Sequence[str] | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_team_members_user_id_users"


def upgrade() -> None:
    op.drop_constraint(_FK, "team_members", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "team_members",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "team_members", type_="foreignkey")
    op.create_foreign_key(
        _FK,
        "team_members",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
