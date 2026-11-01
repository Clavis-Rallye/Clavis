"""The engine's JSON decoder never raises.

PostgreSQL takes values into JSONB that ``json.loads`` refuses, a number of
5,000 digits for one. The driver decodes inside the fetch, so one such row
would raise in every query that loads it, on any path. The engine decodes
through ``tolerant_json_loads``, which returns a marker instead.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from app import database
from app.database import (
    UNREADABLE_JSON_KEY,
    build_engine,
    is_unreadable_json,
    tolerant_json_loads,
    unreadable_json_marker,
)
from app.models.organization import Organization
from app.models.run import Run, RunState
from tests.factories import create_test_organization

_UNDECODABLE_NUMBER = '{"retention":{"LK_H":' + "9" * 5000 + "}}"
_UNDECODABLE_SETTINGS = (
    "UPDATE organizations SET settings = "
    "('{\"retention\":{\"LK_H\":' || repeat('9', 5000) || '}}')::jsonb WHERE id = :id"
)


def test_a_readable_value_decodes_as_before():
    assert tolerant_json_loads('{"retention": {"LK_H": 90}, "list": [1, null, "a"]}') == {
        "retention": {"LK_H": 90},
        "list": [1, None, "a"],
    }


def test_a_value_the_decoder_refuses_becomes_the_marker_with_one_warning():
    with capture_logs() as logs:
        value = tolerant_json_loads(_UNDECODABLE_NUMBER)

    assert value == {UNREADABLE_JSON_KEY: True}
    assert is_unreadable_json(value)
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert [entry["event"] for entry in warnings] == ["json_value_unreadable"]
    # The warning names the failure and the size, never the stored value.
    assert "9999" not in repr(warnings[0])


def test_a_value_nested_too_deep_becomes_the_marker(monkeypatch: pytest.MonkeyPatch):
    def too_deep(_text: str) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(database.json, "loads", too_deep)

    assert is_unreadable_json(tolerant_json_loads("[[[[1]]]]"))


def test_each_marker_is_an_object_of_its_own():
    first = unreadable_json_marker()
    first["changed"] = 1

    assert unreadable_json_marker() == {UNREADABLE_JSON_KEY: True}


@pytest.mark.parametrize("value", [None, {}, [], "text", 7, {"retention": {}}, {UNREADABLE_JSON_KEY: False}])
def test_ordinary_values_are_not_the_marker(value: object):
    assert is_unreadable_json(value) is False


def test_the_marker_is_recognized_beside_other_keys():
    assert is_unreadable_json({UNREADABLE_JSON_KEY: True, "auto_start_runs": False})


@pytest_asyncio.fixture
async def app_engine(test_engine: AsyncEngine) -> AsyncGenerator[AsyncEngine, None]:
    """An engine built the way the application builds its own, on the test database."""
    engine = build_engine(test_engine.url)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_application_engine_is_built_with_the_tolerant_decoder(app_engine: AsyncEngine):
    assert app_engine.dialect._json_deserializer is tolerant_json_loads


@pytest.mark.asyncio
async def test_loading_an_organization_with_an_undecodable_value_does_not_raise(app_engine: AsyncEngine):
    """A plain query, and a query that loads the organization with a run."""
    session_maker = async_sessionmaker(bind=app_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        broken = await create_test_organization(session, name="Unreadable", slug="db-unreadable")
        healthy = await create_test_organization(session, name="Readable", slug="db-readable")
        healthy.settings = {"auto_start_runs": False}
        run = Run(organization_id=broken.id, name="Abandoned", state=RunState.ACTIVE, join_code="DBUNRD")
        session.add(run)
        await session.flush()
        await session.execute(sql_text(_UNDECODABLE_SETTINGS), {"id": broken.id})
        await session.commit()
        broken_id, run_id = broken.id, run.id

    async with session_maker() as session:
        with capture_logs() as logs:
            organizations = (await session.execute(select(Organization).order_by(Organization.slug))).scalars().all()
        by_slug = {organization.slug: organization for organization in organizations}
        assert by_slug["db-readable"].settings == {"auto_start_runs": False}
        assert is_unreadable_json(by_slug["db-unreadable"].settings)
        assert [entry["event"] for entry in logs if entry["log_level"] == "warning"] == ["json_value_unreadable"]

    async with session_maker() as session:
        loaded = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
        assert loaded.organization.id == broken_id
        assert is_unreadable_json(loaded.organization.settings)
