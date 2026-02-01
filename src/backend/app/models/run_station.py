"""
RunStation model.

The frozen snapshot of a station within a run.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.run import Run
    from app.models.run_challenge import RunChallenge


class RunStation(UUIDMixin, TimestampMixin, Base):
    """
    The frozen snapshot of a station for one run.

    When the run starts, the stations of the template rallye are deep-copied into this table
    with their content frozen at that moment. The run then uses immutable content even if the
    original is modified or deleted later, and every later operation references the
    RunStation, not the Station. ``source_station_id`` is set to null when the source is
    deleted. ``title`` and ``description`` map a locale to its text, as in the source station.

    There is no ``SoftDeleteMixin``. A snapshot is immutable and removed only when its run is
    hard-deleted.
    """

    __tablename__ = "run_stations"

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

    source_station_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("stations.id", ondelete="SET NULL"),
        nullable=True,
        comment="Foreign key to original template station (nullable if template deleted)",
    )

    title: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Multilingual title frozen from source: {'de': '...', 'en': '...'}",
    )

    description: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Multilingual description frozen from source: {'de': '...', 'en': '...'}",
    )

    location_lat: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Latitude coordinate frozen from source",
    )

    location_lng: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Longitude coordinate frozen from source",
    )

    address: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Human-readable address frozen from source (nullable)",
    )

    title_image_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="Station title-image object key frozen from source at run start (nullable)",
    )

    tips: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Tips & links popup frozen from source at run start (#262, nullable)",
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Order of this station within the run (frozen from source)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    run: Mapped["Run"] = relationship(
        "Run",
        back_populates="run_stations",
        lazy="selectin",
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    run_challenges: Mapped[list["RunChallenge"]] = relationship(
        "RunChallenge",
        back_populates="run_station",
        lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<RunStation(id={self.id}, run_id={self.run_id}, title={self.title.get('de', 'N/A')})>"
