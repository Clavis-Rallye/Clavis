"""
Run model.

A live instance of a rallye, with a state machine. A run operates on a frozen snapshot of
the rallye content, copied at run start.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.rallye import Rallye
    from app.models.run_station import RunStation
    from app.models.team import Team


class RunState(StrEnum):
    """
    Run state enumeration defining the lifecycle of a run instance.

    States:
        CONFIGURED: Run created but not yet scheduled or started
        SCHEDULED: Run scheduled for a future start time
        ACTIVE: Run is currently in progress
        PAUSED: Run temporarily paused (can be resumed)
        ENDED: Run has finished (terminal state, no further transitions)

    Valid transitions:
        configured → scheduled, active
        scheduled → active, configured (cancel schedule)
        active → paused, ended
        paused → active, ended
        ended → (none, terminal state)
    """

    CONFIGURED = "configured"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class Run(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    A live instance of a rallye.

    A run is created from a rallye template and operates on a frozen snapshot of its content,
    copied into ``run_stations`` and ``run_challenges`` when the run starts. ``rallye_id`` is
    null when the template was deleted later. ``join_code`` is a unique code for teams, 6
    uppercase alphanumeric characters without ambiguous ones. ``started_at`` is set on the
    first transition to active and ``ended_at`` on the transition to ended. ``max_teams`` null
    means unlimited. ``settings`` is run-specific configuration, for example
    ``{"allow_late_joins": true, "enable_hints": false, "time_limit_minutes": 120}``.
    """

    __tablename__ = "runs"

    # Mirrors migration 004: (organization_id, join_code) unique among runs that are not soft-deleted.
    __table_args__ = (
        Index(
            "ix_runs_org_join_code_unique",
            "organization_id",
            "join_code",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization",
    )

    rallye_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rallyes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Foreign key to template rallye (nullable — template may be deleted)",
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Display name for this run instance",
    )

    state: Mapped[RunState] = mapped_column(
        Enum(
            RunState,
            name="run_state",
            native_enum=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=RunState.CONFIGURED,
        comment="Current state of the run",
    )

    join_code: Mapped[str] = mapped_column(
        String(6),
        nullable=False,
        comment="Unique 6-character code for teams to join",
    )

    scheduled_for: Mapped["datetime | None"] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the run is scheduled to start",
    )

    started_at: Mapped["datetime | None"] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the run actually started (first transition to active)",
    )

    paused_at: Mapped["datetime | None"] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the run was last paused",
    )

    ended_at: Mapped["datetime | None"] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the run ended (terminal)",
    )

    retention_anonymized_at: Mapped["datetime | None"] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the LK_C retention sweep anonymized this run's residual PII (null = not yet anonymized)",
    )

    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Flexible run-specific configuration",
    )

    content_languages: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: ["de", "en"],
        server_default='["de", "en"]',
        comment="ISO codes of languages this run is configured to display",
    )

    max_teams: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Maximum number of teams allowed (null = unlimited)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    rallye: Mapped["Rallye | None"] = relationship(
        "Rallye",
        lazy="selectin",
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    run_stations: Mapped[list["RunStation"]] = relationship(
        "RunStation",
        back_populates="run",
        lazy="raise",
        passive_deletes=True,
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    teams: Mapped[list["Team"]] = relationship(
        "Team",
        back_populates="run",
        lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Run(id={self.id}, organization_id={self.organization_id}, name={self.name!r}, state={self.state})>"
