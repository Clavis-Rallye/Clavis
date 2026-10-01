"""Allow kind 'back' in qa_notes

The feedback bar gets a back button: it reports ``back``, and the session kit
moves the step to the previous one. A database that already ran 035 would not
see an edit to it, so the CHECK constraint changes here.
Raw SQL names the constraint exactly as 035 created it through the naming
convention (``ck_qa_notes_kind``). A wrong name fails loudly.

Revision ID: 036
Revises: 035
Create Date: 2026-10-31 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE qa_notes DROP CONSTRAINT ck_qa_notes_kind")
    op.execute("ALTER TABLE qa_notes ADD CONSTRAINT ck_qa_notes_kind CHECK (kind IN ('ok', 'skip', 'note', 'back'))")


def downgrade() -> None:
    # A back report is a step move, not feedback: nothing is lost by dropping it.
    op.execute("DELETE FROM qa_notes WHERE kind = 'back'")
    op.execute("ALTER TABLE qa_notes DROP CONSTRAINT ck_qa_notes_kind")
    op.execute("ALTER TABLE qa_notes ADD CONSTRAINT ck_qa_notes_kind CHECK (kind IN ('ok', 'skip', 'note'))")
