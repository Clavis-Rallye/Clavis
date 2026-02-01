"""
Challenge model: a task of the organization-wide library.

The type of a challenge decides how a team completes it and how it is scored.
"""

from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.rallye_station_challenge import RallyeStationChallenge
    from app.models.user import User


class ChallengeType(StrEnum):
    """
    Challenge type enumeration defining the interaction method for completing a challenge.

    Types:
        PHOTO: Take/upload a photo at the location or of a specific subject
        TEXT: Provide a free-text answer to a question or prompt
        QR_SCAN: Scan a QR code present at the physical location
        QUIZ: Answer a multiple-choice question
        AUDIO: Record an audio answer
        VIDEO: Upload a video answer
        CREATIVE: Retired. A new or changed challenge cannot take it, and runs started before keep it
    """

    PHOTO = "photo"
    TEXT = "text"
    QR_SCAN = "qr_scan"
    QUIZ = "quiz"
    CREATIVE = "creative"
    # Media capture: audio recording and video upload.
    AUDIO = "audio"
    VIDEO = "video"


class Challenge(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    A task of the organization library, reusable across rallye stations.

    A rallye station links it by reference through ``rallye_station_challenges``, so editing
    a challenge changes it everywhere it is linked. ``scoring_rules`` holds type-specific rules,
    for example ``{"max_points": 100, "auto_approve": true, "required_keywords": ["rathaus"]}``.
    ``title`` and ``instructions`` map a locale to its text,
    for example ``{"de": "Fotografiere das Rathaus", "en": "Take a photo of the city hall"}``.
    """

    __tablename__ = "challenges"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to parent organization",
    )

    challenge_type: Mapped[ChallengeType] = mapped_column(
        Enum(
            ChallengeType,
            name="challenge_type",
            native_enum=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        # The column comment names the five types of the first schema. The database holds this text,
        # so it changes only with a migration. ChallengeType above lists every type.
        comment="Type of challenge (photo, text, qr_scan, quiz, creative)",
    )

    title: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Multilingual title: {'de': '...', 'en': '...'}",
    )

    instructions: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Multilingual instructions: {'de': '...', 'en': '...'}",
    )

    scoring_rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Flexible scoring configuration (type-specific)",
    )

    time_estimate_minutes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Estimated time to complete this challenge, in minutes (nullable)",
    )

    suggested_solution: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Reviewer-facing reference answer (not shown to participants)",
    )

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who created this challenge (nullable for legacy rows)",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        lazy="selectin",
    )

    # Never loaded implicitly. Query it, or use selectinload in the query that
    # needs it. See Organization.users.
    rallye_station_links: Mapped[list["RallyeStationChallenge"]] = relationship(
        "RallyeStationChallenge",
        back_populates="challenge",
        lazy="raise",
        passive_deletes=True,
    )

    created_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Challenge(id={self.id}, type={self.challenge_type}, title={self.title.get('de', 'N/A')})>"
