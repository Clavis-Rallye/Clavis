"""
Station model.

A reusable station of the organization-wide library, with a location (latitude and
longitude) and an optional human-readable address. Rallyes compose stations by reference
through the ``rallye_stations`` association, so one station serves many rallyes and editing
it updates every rallye that links it. Challenges are matched to a station per rallye
(``rallye_station_challenges``, on the ``rallye_stations`` link), not globally.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.rallye_station import RallyeStation
    from app.models.user import User


class Station(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    A reusable, organization-owned location with challenges.

    A station belongs to an organization, not to a single rallye. ``title`` and ``description``
    map a locale to its text, for example ``{"de": "Rathaus", "en": "City Hall"}``.
    """

    __tablename__ = "stations"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization",
    )

    title: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Multilingual title: {'de': '...', 'en': '...'}",
    )

    description: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Multilingual description: {'de': '...', 'en': '...'}",
    )

    address: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Optional human-readable address of the station location",
    )

    title_image_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        comment="Object-storage key of the station's title image (nullable). "
        "A column, not JSON, so that the retention sweep can enumerate it.",
    )

    tips: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Optional tips popup with links: "
        "{'header': {'de': ...}, 'links': [{'label': {'de': ...}, 'url': ...}]}. "
        "NULL = no popup. Shape validated by schemas.StationTips.",
    )

    location_lat: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Latitude coordinate of the station location",
    )

    location_lng: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Longitude coordinate of the station location",
    )

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who created this station (nullable for legacy rows)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    rallye_links: Mapped[list["RallyeStation"]] = relationship(
        "RallyeStation",
        back_populates="station",
        lazy="raise",
        passive_deletes=True,
        cascade="all, delete-orphan",
    )

    created_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Station(id={self.id}, title={self.title.get('de', 'N/A')})>"
