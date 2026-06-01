"""
RallyeStation association model.

Links rallyes to library stations (many-to-many). A station lives in the organization-wide
library and is composed into any number of rallyes by reference. The order within a
rallye lives here in ``sort_order``. Editing the station therefore changes it in every
rallye that links it.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.rallye import Rallye
    from app.models.rallye_station_challenge import RallyeStationChallenge
    from app.models.station import Station


class RallyeStation(UUIDMixin, TimestampMixin, Base):
    """
    Association between a Rallye and a library Station.

    A link is either present or absent. There is no soft deletion, and unlinking removes the
    row. ``sort_order`` is the position of the station within this rallye, so the same station
    can have different positions in different rallyes. ``organization_id`` is denormalised for
    fast tenant-scoped queries.
    """

    __tablename__ = "rallye_stations"
    __table_args__ = (UniqueConstraint("rallye_id", "station_id", name="uq_rallye_stations_rallye_station"),)

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization (tenant scope)",
    )

    rallye_id: Mapped[UUID] = mapped_column(
        ForeignKey("rallyes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the rallye",
    )

    station_id: Mapped[UUID] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the library station",
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Position of this station within the rallye",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", lazy="selectin")
    rallye: Mapped["Rallye"] = relationship("Rallye", back_populates="rallye_stations", lazy="selectin")
    station: Mapped["Station"] = relationship("Station", back_populates="rallye_links", lazy="selectin")
    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    challenge_links: Mapped[list["RallyeStationChallenge"]] = relationship(
        "RallyeStationChallenge",
        back_populates="rallye_station",
        cascade="all, delete-orphan",
        order_by="RallyeStationChallenge.sort_order",
        lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"<RallyeStation(rallye_id={self.rallye_id}, station_id={self.station_id}, sort_order={self.sort_order})>"
        )
