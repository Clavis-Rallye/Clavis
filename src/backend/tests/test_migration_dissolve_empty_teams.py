"""
Migration 042 dissolves the empty teams of runs that have not ended.

The test migrates an empty database to 041, writes teams in every shape the migration has
to tell apart, runs 042 and reads the two soft-delete fields back. One team per scenario,
so a failing assertion names the rule that broke.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.database import Base

BACKEND_DIR = Path(__file__).resolve().parent.parent
_RAW_ENUM_TYPES = ("userrole", "challenge_type", "run_state", "feed_event_type")

# What a column without a default takes when a fixture does not name it.
_FILLER: dict[str, str] = {
    "jsonb": "'{}'::jsonb",
    "json": "'{}'::json",
    "integer": "0",
    "smallint": "0",
    "bigint": "0",
    "numeric": "0",
    "double precision": "0",
    "boolean": "false",
    "text": "'x'",
    "character varying": "'x'",
    "timestamp with time zone": "now()",
    "timestamp without time zone": "now()",
    "uuid": "gen_random_uuid()",
}


def _alembic(url: str, *args: str) -> None:
    # env.py takes the URL through -x dbUrl and drives its own event loop.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"dbUrl={url}", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"


def _alembic_start(url: str, *args: str) -> subprocess.Popen[str]:
    """Start alembic and return at once, for a test that has to watch it wait."""
    return subprocess.Popen(
        [sys.executable, "-m", "alembic", "-x", f"dbUrl={url}", *args],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


# A session that asked for the lock of migration 042 on team_members and has not got it.
_WAITS_FOR_MEMBER_TABLE = text(
    """
    SELECT count(*)
    FROM pg_locks l
    JOIN pg_class c ON c.oid = l.relation
    JOIN pg_database d ON d.oid = l.database
    WHERE d.datname = current_database()
      AND c.relname = 'team_members'
      AND l.mode = 'ShareRowExclusiveLock'
      AND NOT l.granted
    """
)


async def _waits_for_member_table(engine: AsyncEngine, proc: subprocess.Popen[str], timeout: float = 120.0) -> bool:
    """True once the migration waits for its lock on team_members, False when it ended
    without waiting. The answer is read from pg_locks; the pause only spaces the reads."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        while loop.time() < deadline:
            if (await conn.execute(_WAITS_FOR_MEMBER_TABLE)).scalar_one() > 0:
                return True
            if proc.poll() is not None:
                return False
            await asyncio.sleep(0.05)
    pytest.fail(f"migration 042 neither waited on team_members nor ended within {timeout:.0f} s")


async def _wipe(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        for enum_name in _RAW_ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))


def _insert(conn: Connection, table: str, **values: Any) -> uuid.UUID:
    """Insert one row, fill every other NOT NULL column without a default, return its id."""
    row_id = uuid.uuid4()
    values = {"id": row_id, **values}
    required = conn.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = :t AND is_nullable = 'NO' AND column_default IS NULL"
        ),
        {"t": table},
    ).fetchall()
    columns = list(values)
    placeholders = [f":{name}" for name in columns]
    for name, data_type in required:
        if name not in values:
            columns.append(name)
            placeholders.append(_FILLER[data_type])
    conn.execute(
        text(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"),
        values,
    )
    return row_id


class _World:
    """One organization, one run per state, and a challenge to answer in each."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.org = _insert(conn, "organizations", name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        self.runs: dict[str, uuid.UUID] = {}
        self.challenges: dict[str, uuid.UUID] = {}

    def run(self, state: str) -> uuid.UUID:
        if state not in self.runs:
            run_id = _insert(
                self.conn,
                "runs",
                organization_id=self.org,
                name=f"Run {state}",
                join_code=uuid.uuid4().hex[:6].upper(),
                state=state,
            )
            station = _insert(self.conn, "run_stations", organization_id=self.org, run_id=run_id)
            self.challenges[state] = _insert(
                self.conn,
                "run_challenges",
                organization_id=self.org,
                run_id=run_id,
                run_station_id=station,
                challenge_type="text",
            )
            self.runs[state] = run_id
        return self.runs[state]

    def team(self, state: str, *, member: str | None = None, answer: bool = False) -> uuid.UUID:
        """A team in a run of ``state``. ``member`` is ``None`` (no row), ``"account"`` or ``"guest"``
        (a row without account)."""
        run_id = self.run(state)
        team_id = _insert(
            self.conn,
            "teams",
            organization_id=self.org,
            run_id=run_id,
            share_code=uuid.uuid4().hex[:6].upper(),
            is_deleted=False,
        )
        if member == "account":
            user_id = _insert(
                self.conn,
                "users",
                email=f"{uuid.uuid4().hex}@example.org",
                role="participant",
                is_active=True,
                organization_id=self.org,
            )
            _insert(
                self.conn,
                "team_members",
                organization_id=self.org,
                team_id=team_id,
                user_id=user_id,
                display_name="Member",
            )
        elif member == "guest":
            _insert(
                self.conn,
                "team_members",
                organization_id=self.org,
                team_id=team_id,
                user_id=None,
                display_name="Guest",
                is_guest=True,
            )
        if answer:
            _insert(
                self.conn,
                "submissions",
                organization_id=self.org,
                run_id=run_id,
                team_id=team_id,
                run_challenge_id=self.challenges[state],
            )
        return team_id


def _state(conn: Connection, team_id: uuid.UUID) -> tuple[bool, datetime | None]:
    row = conn.execute(text("SELECT is_deleted, deleted_at FROM teams WHERE id = :id"), {"id": team_id}).one()
    return bool(row[0]), row[1]


def _assert_untouched(conn: Connection, team_id: uuid.UUID) -> None:
    assert _state(conn, team_id) == (False, None)


@pytest_asyncio.fixture
async def migrated_to_041(postgres_container: PostgresContainer) -> AsyncIterator[tuple[str, AsyncEngine]]:
    url = postgres_container.get_connection_url(driver="asyncpg")
    engine = create_async_engine(url, echo=False)
    try:
        await _wipe(engine)
        _alembic(url, "upgrade", "041")
        yield url, engine
    finally:
        await _wipe(engine)
        await engine.dispose()


OPEN_STATES = ("configured", "scheduled", "active", "paused")


@pytest.mark.asyncio
async def test_each_scenario_has_its_own_team(migrated_to_041: tuple[str, AsyncEngine]) -> None:
    url, engine = migrated_to_041

    async with engine.begin() as conn:

        def build(sync: Connection) -> dict[str, uuid.UUID]:
            w = _World(sync)
            # An open run, no member, no answer: the team is dissolved.
            teams = {f"empty_{s}": w.team(s) for s in OPEN_STATES}
            # The run has ended: every team stays.
            teams["ended_empty"] = w.team("ended")
            teams["ended_with_answer"] = w.team("ended", answer=True)
            teams["ended_with_member"] = w.team("ended", member="account")
            teams["ended_with_member_and_answer"] = w.team("ended", member="account", answer=True)
            # An answer keeps the team of an open run.
            teams["answer_active"] = w.team("active", answer=True)
            teams["answer_paused"] = w.team("paused", answer=True)
            teams["answer_scheduled"] = w.team("scheduled", answer=True)
            # A member keeps the team of an open run, with or without an account.
            teams["member_active"] = w.team("active", member="account")
            teams["accountless_member_active"] = w.team("active", member="guest")
            teams["accountless_member_paused"] = w.team("paused", member="guest")
            return teams

        teams = await conn.run_sync(build)

    _alembic(url, "upgrade", "042")

    async with engine.connect() as conn:

        def read(sync: Connection) -> None:
            for key, team_id in teams.items():
                if key.startswith("empty_"):
                    is_deleted, deleted_at = _state(sync, team_id)
                    assert is_deleted is True, key
                    assert deleted_at is not None, key
                else:
                    assert _state(sync, team_id) == (False, None), key

        await conn.run_sync(read)


@pytest.mark.asyncio
async def test_deleted_at_is_the_time_of_the_migration(migrated_to_041: tuple[str, AsyncEngine]) -> None:
    url, engine = migrated_to_041

    async with engine.begin() as conn:
        team_id = await conn.run_sync(lambda sync: _World(sync).team("active"))
        before = (await conn.execute(text("SELECT now()"))).scalar_one()

    _alembic(url, "upgrade", "042")

    async with engine.connect() as conn:
        deleted_at = (
            await conn.execute(text("SELECT deleted_at FROM teams WHERE id = :id"), {"id": team_id})
        ).scalar_one()
        after = (await conn.execute(text("SELECT now()"))).scalar_one()
    assert before <= deleted_at <= after


@pytest.mark.asyncio
async def test_production_shape_of_ended_runs_stays_untouched(migrated_to_041: tuple[str, AsyncEngine]) -> None:
    """Production shape: four teams without a member row, each in an
    ended run, three of them with answers. They are part of the results of those runs."""
    url, engine = migrated_to_041

    async with engine.begin() as conn:

        def build(sync: Connection) -> list[uuid.UUID]:
            w = _World(sync)
            # Four ended runs, one such team each.
            teams: list[uuid.UUID] = []
            for answered in (True, True, True, False):
                world = _World(sync)
                world.org = w.org
                teams.append(world.team("ended", answer=answered))
            return teams

        teams = await conn.run_sync(build)

    _alembic(url, "upgrade", "042")

    async with engine.connect() as conn:

        def read(sync: Connection) -> None:
            assert len(teams) == 4
            for team_id in teams:
                _assert_untouched(sync, team_id)

        await conn.run_sync(read)


@pytest.mark.asyncio
async def test_running_the_migration_twice_changes_nothing(migrated_to_041: tuple[str, AsyncEngine]) -> None:
    url, engine = migrated_to_041

    async with engine.begin() as conn:

        def build(sync: Connection) -> tuple[uuid.UUID, uuid.UUID]:
            w = _World(sync)
            return w.team("active"), w.team("ended")

        dissolved, ended = await conn.run_sync(build)

    _alembic(url, "upgrade", "042")
    async with engine.connect() as conn:
        first = await conn.run_sync(lambda sync: _state(sync, dissolved))
    assert first[0] is True

    # The downgrade is a no-op, so the second upgrade meets the dissolved team again.
    _alembic(url, "downgrade", "041")
    async with engine.connect() as conn:
        assert await conn.run_sync(lambda sync: _state(sync, dissolved)) == first
    _alembic(url, "upgrade", "042")

    async with engine.connect() as conn:
        assert await conn.run_sync(lambda sync: _state(sync, dissolved)) == first
        await conn.run_sync(lambda sync: _assert_untouched(sync, ended))


@pytest.mark.asyncio
async def test_migration_waits_for_an_open_member_insert(migrated_to_041: tuple[str, AsyncEngine]) -> None:
    """A member row written beside the migration is seen by it, and its team stays."""
    url, engine = migrated_to_041

    async with engine.begin() as conn:

        def build(sync: Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
            w = _World(sync)
            return w.org, w.team("active"), w.team("active")

        org, joined, empty = await conn.run_sync(build)

    def join(sync: Connection) -> None:
        _insert(
            sync,
            "team_members",
            organization_id=org,
            team_id=joined,
            user_id=None,
            display_name="Guest",
            is_guest=True,
        )

    proc: subprocess.Popen[str] | None = None
    writer = await engine.connect()
    try:
        # The join: its member row is written and not committed.
        join_transaction = await writer.begin()
        await writer.run_sync(join)

        proc = _alembic_start(url, "upgrade", "042")
        waited = await _waits_for_member_table(engine, proc)
        assert waited, (
            "migration 042 ended while a member insert was open: it did not wait on team_members\n"
            f"team with the open join after the migration (is_deleted, deleted_at): "
            f"{await _read_state(engine, joined)}"
        )
        assert proc.poll() is None, "migration 042 ended although its lock was not granted"

        await join_transaction.commit()
        stdout, stderr = await asyncio.to_thread(proc.communicate, timeout=120)
        assert proc.returncode == 0, f"alembic upgrade 042 failed:\n{stdout}\n{stderr}"
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        await writer.close()

    assert await _read_state(engine, joined) == (False, None)
    is_deleted, deleted_at = await _read_state(engine, empty)
    assert is_deleted is True
    assert deleted_at is not None


async def _read_state(engine: AsyncEngine, team_id: uuid.UUID) -> tuple[bool, datetime | None]:
    async with engine.connect() as conn:
        return await conn.run_sync(lambda sync: _state(sync, team_id))
