"""Alembic environment for the async SQLAlchemy engine."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import settings
from app.database import Base

# The first of these imports loads the package app.models, whose __init__.py imports all 26 model
# modules, so every table is on Base.metadata. The names below are not needed for that.
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin  # noqa: F401
from app.models.challenge import Challenge, ChallengeType  # noqa: F401
from app.models.organization import Organization  # noqa: F401
from app.models.rallye import Rallye  # noqa: F401
from app.models.rallye_station import RallyeStation  # noqa: F401
from app.models.rallye_station_challenge import RallyeStationChallenge  # noqa: F401
from app.models.station import Station  # noqa: F401
from app.models.user import User, UserRole  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# ``alembic -x dbUrl=<url>`` overrides the database URL of the app settings.
# Alembic's config is a ConfigParser, which interpolates ``%``, so a percent-encoded
# password (``%40`` for ``@``) must double its percent signs on the way in.
db_url = context.get_x_argument(as_dictionary=True).get("dbUrl")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
else:
    config.set_main_option("sqlalchemy.url", settings.database.database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
