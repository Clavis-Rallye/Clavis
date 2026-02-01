"""An organization, the tenant of the system, with its own users, rallyes, runs and content."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, is_unreadable_json
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class Organization(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    A tenant, isolated from every other organization.

    ``slug`` is the URL-safe unique identifier. ``languages`` lists the enabled locale codes,
    for example ``["de", "en"]``. ``settings`` is extensible JSONB configuration, for example
    ``{"default_language": "de", "max_teams_per_run": 20, "allow_guest_registration": true}``.
    Read it through ``readable_settings``.
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-readable organization name",
    )

    slug: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="URL-safe unique identifier",
    )

    languages: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=["de"],
        server_default='["de"]',
        comment="List of enabled locale codes (e.g., ['de', 'en'])",
    )

    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default={},
        server_default="{}",
        comment="Extensible JSONB configuration for org-specific settings",
    )

    # Per-org legal page overrides (Markdown). NULL → frontend uses the
    # bundled fallback copy in src/frontend/src/i18n/locales/*/legal.json.
    legal_imprint: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Imprint markdown override (NULL → frontend bundled copy)",
    )
    legal_privacy: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Privacy markdown override (NULL → frontend bundled copy)",
    )
    legal_accessibility: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Accessibility markdown override (NULL → frontend bundled copy)",
    )

    # Non-null while the operator has archived the organization. Its members cannot
    # sign in, its runs leave the public endpoints, and its data stays. This is not
    # soft deletion.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the operator archived the organization (null = active)",
    )

    # Non-null while the operator has scheduled a hard deletion. The daily sweep
    # (app/services/org_deletion.py) hard-deletes the organization once
    # DELETION_GRACE_DAYS have passed. A restore clears it back to null.
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the operator requested deletion (null = none scheduled)",
    )

    # Relationships
    # Collections are lazy="raise" across the models. With lazy="selectin", loading an organization
    # would load every user, guests included, and loading a run would load its teams, members and
    # submissions. A query that needs a collection says so with selectinload(), and the database
    # cascades the deletes (passive_deletes=True).
    users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="organization",
        lazy="raise",
        passive_deletes=True,
    )

    # A deletion can be scheduled only for an archived organization. The request endpoint enforces
    # it, and this constraint is the backstop in the database. The naming convention expands the
    # name to "ck_organizations_deletion_requires_archived", the name that migration 033 creates.
    __table_args__ = (
        CheckConstraint(
            "deletion_requested_at IS NULL OR archived_at IS NOT NULL",
            name="deletion_requires_archived",
        ),
    )

    # The two accessors below are the only readers of ``settings``, so no code outside this class
    # sees the marker of ``app.database.tolerant_json_loads`` for an undecodable value. A reader
    # asks ``readable_settings`` and falls back to its own default. A caller that must not take a
    # default for an answer asks ``settings_unreadable`` first.

    @property
    def settings_unreadable(self) -> bool:
        """Whether the stored settings are a value the JSON decoder refused."""
        return is_unreadable_json(self.settings)

    @property
    def readable_settings(self) -> dict[str, Any]:
        """The settings as an object. Empty when the stored value cannot be read or is no object."""
        stored = self.settings
        if not isinstance(stored, dict) or is_unreadable_json(stored):
            return {}
        return stored

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, slug={self.slug}, name={self.name})>"
