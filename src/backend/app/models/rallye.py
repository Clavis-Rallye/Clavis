"""
Rallye model.

A rallye template (stable content) of an organization. It contains a sequence of stations.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.rallye_station import RallyeStation
    from app.models.user import User


class Rallye(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    A rallye template (stable content), scoped to an organization.

    A rallye composes library stations by reference through ``rallye_stations``. The order of
    the stations lives on those link rows (``RallyeStation.sort_order``). Once published, a
    rallye can start runs. ``title`` and ``description`` map a locale to its text, for example
    ``{"de": "Clavis Augsburg", "en": "Clavis Augsburg"}``.
    """

    __tablename__ = "rallyes"

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

    is_published: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Whether this rallye is published and available for runs",
    )

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who created this rallye (nullable for legacy rows)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    rallye_stations: Mapped[list["RallyeStation"]] = relationship(
        "RallyeStation",
        back_populates="rallye",
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
        return f"<Rallye(id={self.id}, organization_id={self.organization_id}, is_published={self.is_published})>"
