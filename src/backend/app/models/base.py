"""Reusable column mixins for the models: UUID key, timestamps and soft deletion."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from uuid_utils import uuid7


class UUIDMixin:
    """UUID primary key. uuid7 is time-ordered, so it indexes better than a random uuid4."""

    @declared_attr
    def id(cls) -> Mapped[UUID]:
        return mapped_column(
            PGUUID(as_uuid=True),
            primary_key=True,
            default=uuid7,
            nullable=False,
        )


class TimestampMixin:
    """
    The columns ``created_at`` and ``updated_at``, both timezone-aware.

    ``created_at`` is set by the server on insert. ``updated_at`` is set on insert
    and on every update.
    """

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )


class SoftDeleteMixin:
    """
    Soft deletion: a record is marked as deleted and stays in the table.

    This allows recovery and keeps references valid. ``deleted_at`` is null for a
    live record. A query must exclude deleted records itself, with ``is_deleted == False``.
    """

    @declared_attr
    def deleted_at(cls) -> Mapped[datetime | None]:
        return mapped_column(
            DateTime(timezone=True),
            nullable=True,
            default=None,
        )

    @declared_attr
    def is_deleted(cls) -> Mapped[bool]:
        return mapped_column(
            Boolean,
            default=False,
            nullable=False,
        )
