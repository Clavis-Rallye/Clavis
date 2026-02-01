"""
Feed event model: an entry in the feed of a run (system event, coordinator announcement, milestone).

Feed events are shown in chronological order, to every participant of the run or, with
``target_team_id``, to one team and the staff.
"""

from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.run import Run
    from app.models.user import User


class FeedEventType(StrEnum):
    """
    The kind of a feed event.

    A coordinator creates `announcement`, `countdown`, `reminder`, `alert` and
    `team_update` through the feed route. The app creates `system`,
    `results_available` and `feedback_prompt` itself, without an author. No
    code creates `milestone`, and the feed route refuses it.
    """

    SYSTEM = "system"
    ANNOUNCEMENT = "announcement"
    MILESTONE = "milestone"
    COUNTDOWN = "countdown"
    REMINDER = "reminder"
    ALERT = "alert"
    # A coordinator creates TEAM_UPDATE. The end and the publish of a run create the other two.
    TEAM_UPDATE = "team_update"
    RESULTS_AVAILABLE = "results_available"
    FEEDBACK_PROMPT = "feedback_prompt"


class FeedEvent(UUIDMixin, Base):
    """
    An entry in the activity feed of a run.

    The backend generates a system event, and it has no author. A coordinator creates an
    announcement. ``content`` is a JSONB object, multilingual or structured, for example
    ``{"de": "Pause bis 15:00 Uhr", "en": "Break until 3:00 PM"}``.
    Deleting the author sets ``author_id`` to null.

    An event is immutable, so the model has an explicit ``created_at`` and no ``updated_at``
    (no ``TimestampMixin``). It has no ``SoftDeleteMixin`` either: an event is removed only
    when its run or organization is hard-deleted.
    """

    __tablename__ = "feed_events"

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
        comment="Foreign key to the run this event belongs to",
    )

    event_type: Mapped[FeedEventType] = mapped_column(
        Enum(
            FeedEventType,
            name="feed_event_type",
            native_enum=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        comment="Type of event (system, announcement, milestone)",
    )

    content: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
        comment="JSONB content (multilingual or structured data)",
    )

    author_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Foreign key to the user who created the event (nullable for system events)",
    )

    target_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="If set, this event is only visible to this team (NULL = broadcast)",
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Timestamp when the event was created (server-set, immutable)",
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

    author: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[author_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<FeedEvent(id={self.id}, run_id={self.run_id}, event_type={self.event_type})>"
