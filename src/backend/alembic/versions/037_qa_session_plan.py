"""The session plan in the App, the join key, the message and the trail

``qa_state`` keeps the current step and gains the plan (the cards come from it,
so ``title``, ``do`` and ``expect`` go), the SHA-256 hash of the join key, the
session id and the assistant's message. ``qa_notes`` gains the session id and the
card title of its step, what the server-side step move records (``target``,
``moved``, ``to_step``), the message a note answers, and the bar's action trail.
The kind constraint adds ``goto``.

Revision ID: 037
Revises: 036
Create Date: 2026-10-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.drop_column("qa_state", "title")
    op.drop_column("qa_state", "do")
    op.drop_column("qa_state", "expect")
    op.add_column("qa_state", sa.Column("plan", _JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("qa_state", sa.Column("key_hash", sa.String(length=64), nullable=True))
    op.add_column("qa_state", sa.Column("session_id", sa.String(length=36), nullable=True))
    op.add_column("qa_state", sa.Column("message", sa.Text(), nullable=True))
    op.add_column("qa_state", sa.Column("message_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("qa_notes", sa.Column("session_id", sa.String(length=36), nullable=True))
    op.create_index("ix_qa_notes_session_id", "qa_notes", ["session_id"])
    op.add_column("qa_notes", sa.Column("step_title", sa.String(length=300), nullable=True))
    op.add_column("qa_notes", sa.Column("target", sa.String(length=32), nullable=True))
    op.add_column("qa_notes", sa.Column("moved", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("qa_notes", sa.Column("to_step", sa.String(length=32), nullable=True))
    op.add_column("qa_notes", sa.Column("answers_message", sa.String(length=2000), nullable=True))
    op.add_column("qa_notes", sa.Column("trail", _JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.execute("ALTER TABLE qa_notes DROP CONSTRAINT ck_qa_notes_kind")
    op.execute(
        "ALTER TABLE qa_notes ADD CONSTRAINT ck_qa_notes_kind CHECK (kind IN ('ok', 'skip', 'note', 'back', 'goto'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM qa_notes WHERE kind = 'goto'")
    op.execute("ALTER TABLE qa_notes DROP CONSTRAINT ck_qa_notes_kind")
    op.execute("ALTER TABLE qa_notes ADD CONSTRAINT ck_qa_notes_kind CHECK (kind IN ('ok', 'skip', 'note', 'back'))")
    op.drop_index("ix_qa_notes_session_id", table_name="qa_notes")
    for column in ("trail", "answers_message", "to_step", "moved", "target", "step_title", "session_id"):
        op.drop_column("qa_notes", column)
    for column in ("message_at", "message", "session_id", "key_hash", "plan"):
        op.drop_column("qa_state", column)
    op.add_column("qa_state", sa.Column("expect", sa.Text(), nullable=True))
    op.add_column("qa_state", sa.Column("do", sa.Text(), nullable=True))
    op.add_column("qa_state", sa.Column("title", sa.String(length=300), nullable=True))
