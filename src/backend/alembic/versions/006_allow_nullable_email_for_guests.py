"""Allow nullable email for guest users

Guest users have no email address, so ``users.email`` becomes nullable. A partial
unique index keeps every non-NULL email unique.

Revision ID: 006
Revises: 005
Create Date: 2026-02-18 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")

    op.alter_column(
        "users",
        "email",
        nullable=True,
    )

    op.execute("CREATE UNIQUE INDEX ix_users_email_not_null ON users (email) WHERE email IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX ix_users_email_not_null")

    # Fails while any user row has a NULL email.
    op.alter_column(
        "users",
        "email",
        nullable=False,
    )

    op.create_index("ix_users_email", "users", ["email"], unique=True)
