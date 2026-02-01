"""The frozen snapshot of a challenge within a run."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin
from app.models.challenge import ChallengeType

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.run import Run
    from app.models.run_station import RunStation


class RunChallenge(UUIDMixin, TimestampMixin, Base):
    """
    The frozen snapshot of a challenge for one run.

    When the run starts, the challenges of its rallye are deep-copied into this table. The run
    keeps this content even if the source changes or is deleted, and every later operation
    references the RunChallenge, not the Challenge. ``source_challenge_id`` is set to null when
    the source is deleted. ``title``, ``instructions`` and ``scoring_rules`` have the shape of
    the source challenge.

    There is no ``SoftDeleteMixin``. A snapshot is immutable and removed only when its run is
    hard-deleted.
    """

    __tablename__ = "run_challenges"

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
        comment="Foreign key to parent run",
    )

    run_station_id: Mapped[UUID] = mapped_column(
        ForeignKey("run_stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent run station",
    )

    source_challenge_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("challenges.id", ondelete="SET NULL"),
        nullable=True,
        comment="Foreign key to original template challenge (nullable if template deleted)",
    )

    challenge_type: Mapped[ChallengeType] = mapped_column(
        Enum(
            ChallengeType,
            name="challenge_type",
            native_enum=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        comment="Type of challenge frozen from source (photo, text, qr_scan, quiz, creative)",
    )

    title: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Multilingual title frozen from source: {'de': '...', 'en': '...'}",
    )

    instructions: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Multilingual instructions frozen from source: {'de': '...', 'en': '...'}",
    )

    scoring_rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Flexible scoring configuration frozen from source (type-specific)",
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Order of this challenge within the run station (frozen from source)",
    )

    time_estimate_minutes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Snapshotted estimated time to complete (from source Challenge)",
    )

    suggested_solution: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Snapshotted reviewer-facing reference answer (from source Challenge)",
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

    run_station: Mapped["RunStation"] = relationship(
        "RunStation",
        back_populates="run_challenges",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<RunChallenge(id={self.id}, run_station_id={self.run_station_id}, type={self.challenge_type}, title={self.title.get('de', 'N/A')})>"
