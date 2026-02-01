"""The async database engine, the session maker, the FastAPI session dependencies and the model base."""

import json
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import structlog
from sqlalchemy import URL, MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = structlog.get_logger(__name__)

# One naming convention for indexes and constraints, so migrations and models agree on the names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models. A model needs it to be part of the migrations."""

    metadata = metadata


# The single key of the value ``tolerant_json_loads`` returns for a stored JSON
# value that cannot be decoded.
UNREADABLE_JSON_KEY = "__unreadable__"


def unreadable_json_marker() -> dict[str, Any]:
    """The value that stands for a stored JSON value nobody could read. A new object each time."""
    return {UNREADABLE_JSON_KEY: True}


def is_unreadable_json(value: Any) -> bool:
    """Whether a loaded JSON value is the marker of ``tolerant_json_loads``.

    An object that carries the key beside others counts as well: a reader that
    merged into the marker must not make it look like readable content.
    """
    return isinstance(value, dict) and value.get(UNREADABLE_JSON_KEY) is True


def tolerant_json_loads(text: str) -> Any:
    """Decode a JSON value of the database. Never raises on a value the decoder refuses.

    PostgreSQL stores JSON that ``json.loads`` refuses: a number over the limit for integer
    strings (ValueError) or deep nesting (RecursionError). The driver decodes inside the fetch,
    so one such row would fail every query that loads it. That query would also lose the rows of
    every other organization.
    Such a value is logged and comes back as ``unreadable_json_marker()``. A reader asks
    ``is_unreadable_json`` and must neither take the marker for content nor write it back.
    """
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        # The stored value is not logged: it can hold personal data, and here it is long.
        logger.warning("json_value_unreadable", error=str(exc), error_type=type(exc).__name__, length=len(text))
        return unreadable_json_marker()


def build_engine(url: str | URL, **options: Any) -> AsyncEngine:
    """An async engine that decodes JSON through ``tolerant_json_loads``.

    Every engine that reads application rows is built here, so that no query
    raises on an undecodable JSON value.
    """
    return create_async_engine(url, json_deserializer=tolerant_json_loads, **options)


# Set by create_engine.
_engine: AsyncEngine | None = None


def create_engine() -> AsyncEngine:
    """Create the async engine from ``settings.database``, once. Later calls return the same engine."""
    global _engine

    if _engine is not None:
        return _engine

    _engine = build_engine(
        settings.database.database_url,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.pool_max_overflow,
        pool_pre_ping=True,
        # SQL logging is controlled by SQL_ECHO and routed through structlog
        # (see logging.configure_logging), not SQLAlchemy's own echo handler.
        echo=False,
    )

    return _engine


def get_engine() -> AsyncEngine:
    """Return the global engine. Raises ``RuntimeError`` before ``create_engine()`` ran."""
    if _engine is None:
        raise RuntimeError(
            "Database engine not initialized. Call create_engine() first (typically during application startup)."
        )
    return _engine


# Bound to the engine by init_db.
async_session_maker = async_sessionmaker(
    bind=None,
    class_=AsyncSession,
    expire_on_commit=False,  # Loaded attributes stay readable after the commit without a lazy load
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields one session per request.

    The session commits on success, rolls back on an exception and is always closed.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def get_session_opener() -> SessionOpener:
    """
    FastAPI dependency for long-lived handlers: opens short sessions on demand.

    The session of ``get_db`` holds its pooled connection until the handler returns. A WebSocket
    returns only when the client goes away, so about 30 open sockets would exhaust the pool and
    every HTTP request would wait for a connection. A long-lived handler opens a session with
    this, runs its queries and closes it before it waits. A larger pool only moves the limit.
    """
    return async_session_maker


async def init_db() -> None:
    """Create the engine and bind the session maker to it. Call it at startup."""
    engine = create_engine()
    async_session_maker.configure(bind=engine)


async def dispose_db() -> None:
    """Dispose of the engine and close all connections. Call it at shutdown."""
    if _engine is not None:
        await _engine.dispose()
