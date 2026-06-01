"""
RallyeStationChallenge association model.

Links a rallye's station (a ``rallye_stations`` row) to a library challenge. The same
library station can hold different challenges in different rallyes, so the challenge is
matched to the station of the rallye, not to the library station. The order within the
rallye station lives here in ``sort_order``. Editing the challenge changes it in every
rallye station, and so in every rallye, that links it.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.challenge import Challenge
    from app.models.organization import Organization
    from app.models.rallye_station import RallyeStation


class RallyeStationChallenge(UUIDMixin, TimestampMixin, Base):
    """
    Association between a rallye's station (a ``rallye_stations`` row) and a library Challenge.

    A link is either present or absent. There is no soft deletion, and unlinking removes the
    row. ``sort_order`` is the position of the challenge within this rallye station.
    ``organization_id`` is denormalised for fast tenant-scoped queries.
    """

    __tablename__ = "rallye_station_challenges"
    __table_args__ = (
        UniqueConstraint(
            "rallye_station_id", "challenge_id", name="uq_rallye_station_challenges_rallye_station_challenge"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization (tenant scope)",
    )

    rallye_station_id: Mapped[UUID] = mapped_column(
        ForeignKey("rallye_stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the rallye<->station link",
    )

    challenge_id: Mapped[UUID] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to the library challenge",
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Position of this challenge within the rallye-station",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", lazy="selectin")
    rallye_station: Mapped["RallyeStation"] = relationship(
        "RallyeStation", back_populates="challenge_links", lazy="selectin"
    )
    challenge: Mapped["Challenge"] = relationship("Challenge", back_populates="rallye_station_links", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<RallyeStationChallenge(rallye_station_id={self.rallye_station_id}, "
            f"challenge_id={self.challenge_id}, sort_order={self.sort_order})>"
        )
