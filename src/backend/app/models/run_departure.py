"""
RunDeparture model.

Records that a guest left a team in a run. A guest who left a team must not join the same
run again. Team members are hard-deleted on leave, so this table is the only evidence that
a departed guest was ever in the run.

Rows are written for guest users only. Registered participants may leave and re-join
freely. The user FK cascades on delete, so when the GDPR retention sweep removes a guest
user, the departure goes with it.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.run import Run
    from app.models.user import User


class RunDeparture(UUIDMixin, TimestampMixin, Base):
    """
    A guest's departure from a run: the guest left their team.

    There is no ``SoftDeleteMixin``. A departure is immutable evidence and is removed only by
    an FK cascade (run or user deleted).
    """

    __tablename__ = "run_departures"
    __table_args__ = (UniqueConstraint("run_id", "user_id", name="uq_run_departures_run_user"),)

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
        comment="Foreign key to the run the guest departed from",
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the guest user who left their team",
    )

    departed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when the guest left the team",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization")
    run: Mapped["Run"] = relationship("Run")
    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"<RunDeparture(id={self.id}, run_id={self.run_id}, user_id={self.user_id})>"
