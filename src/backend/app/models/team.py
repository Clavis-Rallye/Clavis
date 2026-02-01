"""
Team model.

A team participating in a run. A team belongs to a run and has members, from none at its
creation to the run's limit.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.run import Run
    from app.models.submission import Submission
    from app.models.team_member import TeamMember


class Team(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    A group of participants in a run.

    A team is created within a run and completes challenges together. It has a name and
    optionally a color (hex code, for example ``#FF5733``) for visual identification. ``name``
    is null while the team is name-pending.
    """

    __tablename__ = "teams"

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
        comment="Foreign key to the run this team is participating in",
    )

    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment=(
            "Display name for the team. NULL while the creator has not set a name. "
            "The UI then shows an 'Unnamed Team' fallback. The creator sets it with "
            "PATCH /api/orgs/{slug}/runs/{run_id}/teams/{team_id}."
        ),
    )

    color: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="Hex color code for visual identification (e.g., '#FF5733')",
    )

    share_code: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment=(
            "Short share-code teammates enter to join this team. "
            "Generated at team-creation, never user-chosen, never rotated. "
            "Unique within (run_id, share_code) for teams that are not deleted."
        ),
    )

    coordinator_chat_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Assist window for coordinator chat access (spec 037): NULL = closed, "
            "set = open since this moment. Opened by the team's assistance request, "
            "closed when a coordinator leaves the chat."
        ),
    )

    coordinator_joined_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "NULL = no [coordinator_joined] system message emitted yet for the "
            "current assist window; set = emitted at this moment. Reset to NULL "
            "whenever the window closes (coordinator_chat_opened_at goes NULL), "
            "so the next request+join cycle notifies again."
        ),
    )

    # Declared after the columns because the second index needs the `name`
    # column object, not just its string name.
    __table_args__ = (
        # Mirrors migration 013: (run_id, share_code) unique among teams that are not soft-deleted.
        Index(
            "ix_teams_run_share_code",
            "run_id",
            "share_code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Mirrors migration 027: one team name per run, compared case-insensitively.
        # Soft-deleted teams are excluded, so deleting a team frees its name again.
        # NULL names (name-pending teams) never collide, because PostgreSQL treats
        # NULL as distinct in a unique index.
        Index(
            "ix_teams_run_id_lower_name",
            "run_id",
            func.lower(name),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    run: Mapped["Run"] = relationship(
        "Run",
        back_populates="teams",
        lazy="selectin",
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    members: Mapped[list["TeamMember"]] = relationship(
        "TeamMember",
        back_populates="team",
        lazy="raise",
        passive_deletes=True,
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    submissions: Mapped[list["Submission"]] = relationship(
        "Submission",
        back_populates="team",
        lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Team(id={self.id}, organization_id={self.organization_id}, run_id={self.run_id}, name={self.name!r})>"
