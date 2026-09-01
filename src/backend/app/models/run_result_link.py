"""
Run result link model.

A published result link is a public, unauthenticated capability: whoever holds the slug can
read the run's frozen final standing. Only ``slug_hash`` (SHA-256) is stored, so a read of the
database or a log never discloses a live link. The slug is recomputed from ``id`` with an
HMAC (``app/repositories/run_result_link.py``), so the id must be assigned before the insert,
and re-publishing the same row yields the same slug.

A partial unique index allows one live row (``revoked_at IS NULL``) per run. A revoke never
deletes the row: it sets ``revoked_at``, and the public endpoint answers 410. A later publish
creates a new row with a new slug. A revoked row is never revived. The retention sweep deletes it
past ``expires_at``.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import UUIDMixin

if TYPE_CHECKING:
    from app.models.run import Run
    from app.models.user import User


class RunResultLink(Base, UUIDMixin):
    """A published, expiring public link to one run's frozen final standing.

    The columns carry their own comments. ``id`` is assigned before the insert,
    because the slug derives from it. ``snapshot`` holds the run name,
    ``ended_at``, the organization name, the run's first content language and
    the ranked team entries. Every re-publish replaces it, sets
    ``published_at`` and restarts ``expires_at``.
    """

    __tablename__ = "run_result_links"

    # Mirrors migration 028: at most one live link per run, slug hashes unique.
    __table_args__ = (
        UniqueConstraint("slug_hash", name="uq_run_result_links_slug_hash"),
        Index(
            "ix_run_result_links_run_active_unique",
            "run_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        comment="Foreign key to the run this published result belongs to",
    )

    slug_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 hex hash of the public slug — the slug itself is never stored",
    )

    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Frozen public projection: run name, ended_at, organization name, language, ranked entries",
    )

    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Coordinator who (re-)published this link (nullable, survives user deletion)",
    )

    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When this link was last (re-)published",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When this link stops resolving (restarted on every re-publish)",
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="When the coordinator revoked this link (NULL = live)",
    )

    run: Mapped["Run"] = relationship("Run", lazy="selectin")

    publisher: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[published_by],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<RunResultLink(id={self.id}, run_id={self.run_id}, revoked={self.revoked_at is not None})>"
