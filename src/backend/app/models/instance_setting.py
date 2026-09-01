"""
The single row of instance-wide settings that are not per organization.

A server default and a CHECK constraint pin the primary key to 1, so the database refuses a second
row. A plain UUID key would let two concurrent first writes insert two rows. The other columns stay
nullable: the first write of any field creates the row, while the other fields have nothing saved yet.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InstanceSetting(Base):
    """The instance's own settings: exactly one row, pinned to id=1.

    Attributes:
        id: Always 1 (server default plus the CHECK constraint ``ck_instance_settings_singleton``)
        legal_imprint, legal_privacy, legal_accessibility: Instance-wide texts (HTML), null until set
        backup_retention_days: Days a backup can still hold deleted data, null until set
        updated_at: When any field was last saved (null = never)
    """

    __tablename__ = "instance_settings"
    # The metadata naming convention (ck_%(table_name)s_%(constraint_name)s) turns
    # name="singleton" into "ck_instance_settings_singleton". A literal
    # "ck_instance_settings_singleton" here would double the prefix.
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        server_default="1",
        comment="Always 1 — the singleton row",
    )

    legal_imprint: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Instance-wide imprint text (HTML)",
    )
    legal_privacy: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Instance-wide privacy text (HTML)",
    )
    legal_accessibility: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Instance-wide accessibility text (HTML)",
    )
    backup_retention_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Days a backup can still hold deleted data (null = BACKUP_ROTATION_DAYS)",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When any field was last saved (null = never)",
    )

    def __repr__(self) -> str:
        return f"<InstanceSetting(id={self.id})>"
