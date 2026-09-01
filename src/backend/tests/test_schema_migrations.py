"""
The migrations and the models must describe the same schema.

The suite builds its tables from ``Base.metadata.create_all`` for speed, production runs
``alembic upgrade head``. Where the two disagree, every test asserts something about a
database that exists nowhere. A foreign key can say ``SET NULL`` in the model and ``CASCADE``
in production, and a green test then claims that a row survives that production deletes.

This test runs the whole migration chain on an empty database and asks Alembic's
autogenerate comparison whether the models would still want to change anything.
An empty diff is the only acceptable answer. Comments, server defaults and the
enum storage form (native type versus VARCHAR) are filtered out, see ``_IGNORED_OPS``.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.database import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Native enum types the early migrations create with raw ``op.execute("CREATE
# TYPE ...")`` (the models themselves use Enum(native_enum=False) / VARCHAR,
# see _IGNORED_OPS above). They sit outside Base.metadata, so drop_all never
# touches them; a leftover type collides with the next full upgrade-head run
# sharing this module's container.
_RAW_ENUM_TYPES = ("userrole", "challenge_type", "run_state", "feed_event_type")


async def _wipe(engine: AsyncEngine) -> None:
    """Drop every model table, the raw-SQL enum types, and Alembic's bookkeeping table."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        for enum_name in _RAW_ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))


# Differences that change no behaviour and are accepted as they are:
# - comments live only in the models, the early migrations never wrote them;
# - server defaults: the models declare a few for raw SQL, the app always inserts
#   through the ORM, so production rows never depend on them;
# - enum columns: the models use Enum(native_enum=False) (VARCHAR), production uses
#   the native PostgreSQL types the migrations created. Same values, different
#   storage. A NEW enum value still needs a migration, that is a separate check.
_IGNORED_OPS = {"modify_comment", "add_table_comment", "remove_table_comment", "modify_default"}


def _flatten(diff: list[Any]) -> list[Any]:
    out: list[Any] = []
    for entry in diff:
        out.extend(entry if isinstance(entry, list) else [entry])
    return out


def _is_enum_storage_difference(op: Any) -> bool:
    if op[0] != "modify_type":
        return False
    db_type, model_type = op[5], op[6]
    db_values = list(getattr(db_type, "enums", []) or [])
    model_values = list(getattr(model_type, "enums", []) or [])
    return bool(db_values) and db_values == model_values


def _significant(diff: list[Any]) -> list[Any]:
    return [op for op in _flatten(diff) if op[0] not in _IGNORED_OPS and not _is_enum_storage_difference(op)]


def _test_only_tables() -> set[str]:
    """Tables that exist only for the suite (``tests/test_repository_base.py`` declares
    ``test_entities`` and ``system_test_entities``). Under xdist every worker imports
    every test module, so they sit on ``Base.metadata`` next to the real models."""
    tests_dir = Path(__file__).resolve().parent
    names: set[str] = set()
    for mapper in Base.registry.mappers:
        try:
            source = Path(inspect.getfile(mapper.class_)).resolve()
        except (TypeError, OSError):
            continue
        if tests_dir in source.parents:
            names.add(getattr(mapper.local_table, "name", ""))
    return names


def _include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    test_only = _test_only_tables()
    if type_ == "table":
        return name not in test_only
    table = getattr(obj, "table", None)
    return getattr(table, "name", None) not in test_only


def _compare(sync_conn: Connection) -> list[Any]:
    ctx = MigrationContext.configure(
        sync_conn,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "include_object": _include_object,
        },
    )
    return _significant(list(compare_metadata(ctx, Base.metadata)))


@pytest.mark.asyncio
async def test_migrations_produce_the_model_schema(postgres_container: PostgresContainer) -> None:
    url = postgres_container.get_connection_url(driver="asyncpg")
    engine = create_async_engine(url, echo=False)
    try:
        await _wipe(engine)

        # env.py takes the URL through -x dbUrl, and it drives its own event loop,
        # so the upgrade runs in a subprocess rather than inside this test's loop.
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-x", f"dbUrl={url}", "upgrade", "head"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"

        async with engine.connect() as conn:
            diff = await conn.run_sync(_compare)

        assert diff == [], "models and migrations disagree, autogenerate would emit:\n" + "\n".join(
            repr(entry) for entry in diff
        )
    finally:
        await _wipe(engine)
        await engine.dispose()


def _instance_settings_check_constraints(sync_conn: Connection) -> list[str]:
    rows = sync_conn.execute(
        text("SELECT conname FROM pg_constraint WHERE conrelid = 'instance_settings'::regclass AND contype = 'c'")
    ).fetchall()
    return [row[0] for row in rows]


@pytest.mark.asyncio
async def test_instance_settings_singleton_constraint_named_like_the_model(
    postgres_container: PostgresContainer,
) -> None:
    """Migration 031's CHECK constraint must land under the name the model expects.

    That name is ``ck_instance_settings_singleton``, not a naming-convention double prefix.
    A full name passed as ``name=`` would get the prefix a second time from Alembic's naming convention."""
    url = postgres_container.get_connection_url(driver="asyncpg")
    engine = create_async_engine(url, echo=False)
    try:
        await _wipe(engine)

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-x", f"dbUrl={url}", "upgrade", "head"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"

        async with engine.connect() as conn:
            names = await conn.run_sync(_instance_settings_check_constraints)

        assert names == ["ck_instance_settings_singleton"], names
    finally:
        await _wipe(engine)
        await engine.dispose()


@pytest.mark.asyncio
async def test_qa_notes_accept_back_after_the_full_chain(postgres_container: PostgresContainer) -> None:
    """Migration 036 replaces 035's kind constraint under the same name, and the
    database then takes ``back`` and still refuses an unknown kind."""
    url = postgres_container.get_connection_url(driver="asyncpg")
    engine = create_async_engine(url, echo=False)
    try:
        await _wipe(engine)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-x", f"dbUrl={url}", "upgrade", "head"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"

        insert = text("INSERT INTO qa_notes (id, client_id, kind) VALUES (gen_random_uuid(), :cid, :kind)")
        async with engine.begin() as conn:
            await conn.execute(insert, {"cid": "mig-back", "kind": "back"})
        with pytest.raises(Exception, match="ck_qa_notes_kind"):
            async with engine.begin() as conn:
                await conn.execute(insert, {"cid": "mig-bad", "kind": "rating"})
    finally:
        await _wipe(engine)
        await engine.dispose()
