"""The score that a coordinator gives the submission of a team."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.submission import Submission
    from app.models.user import User


class Score(UUIDMixin, TimestampMixin, Base):
    """
    A coordinator's evaluation of one submission.

    A coordinator or a higher role creates a score. A submission has at most one score,
    enforced by a unique index on ``submission_id``. ``points`` run from 0 to ``max_points``.
    Deleting the scorer sets ``scored_by`` to null.

    There is no ``SoftDeleteMixin``. A coordinator re-opens a submission by deleting its score,
    and a score is also removed when its submission or organization is hard-deleted.
    """

    __tablename__ = "scores"

    # Mirrors migration 004.
    __table_args__ = (Index("ix_scores_submission_unique", "submission_id", unique=True),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization",
    )

    submission_id: Mapped[UUID] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        comment="Foreign key to the submission being scored (unique — one score per submission)",
    )

    points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Points awarded (0 to max_points)",
    )

    max_points: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Maximum points possible for this challenge",
    )

    comment: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        comment="Optional feedback comment from coordinator",
    )

    scored_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Foreign key to the user who created the score (nullable)",
    )

    scored_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Timestamp when the score was created (server-set)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    submission: Mapped["Submission"] = relationship(
        "Submission",
        back_populates="score",
        lazy="selectin",
    )

    scorer: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[scored_by],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Score(id={self.id}, submission_id={self.submission_id}, points={self.points}/{self.max_points})>"
