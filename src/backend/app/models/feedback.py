"""
Feedback model: anonymous feedback from participants after a run ends.

Feedback has no ``user_id`` by design.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.run import Run


class Feedback(UUIDMixin, Base):
    """
    Anonymous feedback on a run, collected after the run ends.

    The model has no reference to a user, so no rating can be traced to a participant.
    ``rating`` runs from 1 to 5 (``feedback_rating_check``), and ``comment`` is optional.

    Feedback is immutable once submitted, so the model has an explicit ``created_at`` and no
    ``updated_at`` (no ``TimestampMixin``). It has no ``SoftDeleteMixin`` either: feedback is
    removed only when its run or organization is hard-deleted.
    """

    __tablename__ = "feedback"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization",
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the run this feedback is about",
    )

    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Rating from 1 to 5",
    )

    comment: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
        comment="Optional text comment with additional feedback",
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Timestamp when the feedback was submitted (server-set, immutable)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    run: Mapped["Run"] = relationship(
        "Run",
        lazy="selectin",
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "rating >= 1 AND rating <= 5",
            name="feedback_rating_check",
        ),
    )

    def __repr__(self) -> str:
        return f"<Feedback(id={self.id}, run_id={self.run_id}, rating={self.rating})>"
