"""A member of a team: a registered participant or a guest."""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.team import Team
    from app.models.user import User


class TeamMember(UUIDMixin, TimestampMixin, Base):
    """
    An individual participant within a team.

    ``is_guest`` marks a member whose account has the guest role. Deleting the account sets
    ``user_id`` to null. The model has no ``SoftDeleteMixin``, because a member is not
    soft-deleted: leaving the team removes the row.
    """

    __tablename__ = "team_members"

    # Mirrors migration 004: a user sits in a team at most once. Members without a user (NULL) are exempt.
    __table_args__ = (
        Index(
            "ix_team_members_user_team_unique",
            "team_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

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
        comment="Foreign key to the team this member belongs to",
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Foreign key to user account (null for guest participants)",
    )

    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name for this team member",
    )

    is_guest: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Boolean flag indicating if this is a guest participant",
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Timestamp when the member joined the team",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    team: Mapped["Team"] = relationship(
        "Team",
        back_populates="members",
        lazy="selectin",
    )

    user: Mapped["User | None"] = relationship(
        "User",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<TeamMember(id={self.id}, team_id={self.team_id}, display_name={self.display_name!r}, is_guest={self.is_guest})>"
