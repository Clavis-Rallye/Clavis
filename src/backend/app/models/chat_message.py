"""An immutable message in the chat of a team during a run."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.team import Team
    from app.models.user import User


class ChatMessage(UUIDMixin, Base):
    """
    A message in the chat of a team, ordered by ``created_at``.

    Team members chat during a run, and coordinators can join a team chat when invited.
    A system message has no author. Deleting the author sets ``author_id`` to null.

    A message is immutable, so the model has an explicit ``created_at`` and no ``updated_at``
    (no ``TimestampMixin``). It has no ``SoftDeleteMixin`` either: a message is removed only
    when its team or organization is hard-deleted.
    """

    __tablename__ = "chat_messages"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization",
    )

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the team this message belongs to",
    )

    author_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Foreign key to the user who sent the message (nullable for system messages)",
    )

    content: Mapped[str] = mapped_column(
        String(2000),
        nullable=False,
        comment="Text content of the message",
    )

    is_system_message: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Boolean flag indicating if this is a system-generated message",
    )

    author_role: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="Author's role at the time the message was sent (used by the client to colour coordinator bubbles)",
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Timestamp when the message was created (server-set, immutable)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    team: Mapped["Team"] = relationship(
        "Team",
        lazy="selectin",
    )

    author: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[author_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ChatMessage(id={self.id}, team_id={self.team_id}, author_id={self.author_id}, is_system={self.is_system_message})>"
