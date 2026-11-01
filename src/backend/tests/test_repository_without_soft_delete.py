"""A repository without soft delete refuses the soft-delete methods.

``FeedEventRepository`` has ``has_soft_delete`` False and its table has no ``is_deleted``
column. ``RunRepository`` has soft delete and stands for the repositories that keep it.
"""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feed_event import FeedEvent, FeedEventType
from app.models.organization import Organization
from app.models.run import Run, RunState
from app.repositories.feed_event import FeedEventRepository
from app.repositories.run import RunRepository


async def _run(db: AsyncSession, org: Organization, name: str) -> Run:
    run = Run(organization_id=org.id, name=name, state=RunState.CONFIGURED, join_code=uuid4().hex[:6].upper())
    db.add(run)
    await db.flush()
    return run


async def _event(db: AsyncSession, org: Organization, run: Run) -> FeedEvent:
    event = FeedEvent(organization_id=org.id, run_id=run.id, event_type=FeedEventType.SYSTEM, content={"de": "x"})
    db.add(event)
    await db.flush()
    return event


@pytest.mark.asyncio
async def test_count_without_soft_delete_counts_the_rows_of_the_organization(
    db_session: AsyncSession, org_a: Organization, org_b: Organization
) -> None:
    run_a = await _run(db_session, org_a, "Lauf A")
    run_b = await _run(db_session, org_b, "Lauf B")
    await _event(db_session, org_a, run_a)
    await _event(db_session, org_a, run_a)
    await _event(db_session, org_b, run_b)

    repo = FeedEventRepository(db_session, org_a.id)

    assert await repo.count() == 2
    assert await repo.count(include_deleted=True) == 2


@pytest.mark.asyncio
async def test_restore_without_soft_delete_names_the_repository(db_session: AsyncSession, org_a: Organization) -> None:
    run = await _run(db_session, org_a, "Lauf A")
    event = await _event(db_session, org_a, run)

    repo = FeedEventRepository(db_session, org_a.id)

    with pytest.raises(TypeError, match=r"FeedEventRepository.*no soft delete"):
        await repo.restore(event.id)


@pytest.mark.asyncio
async def test_soft_delete_without_soft_delete_names_the_repository_and_changes_no_row(
    db_session: AsyncSession, org_a: Organization
) -> None:
    run = await _run(db_session, org_a, "Lauf A")
    event = await _event(db_session, org_a, run)

    repo = FeedEventRepository(db_session, org_a.id)

    with pytest.raises(TypeError, match=r"FeedEventRepository.*no soft delete"):
        await repo.soft_delete(event.id)

    assert not hasattr(event, "deleted_at")
    assert not hasattr(event, "is_deleted")
    assert await repo.get_by_id(event.id) is not None
    assert await repo.count() == 1


@pytest.mark.asyncio
async def test_repository_with_soft_delete_counts_soft_deletes_and_restores(
    db_session: AsyncSession, org_a: Organization
) -> None:
    run = await _run(db_session, org_a, "Lauf A")
    await _run(db_session, org_a, "Lauf B")

    repo = RunRepository(db_session, org_a.id)
    assert await repo.count() == 2

    assert await repo.soft_delete(run.id) is True
    assert await repo.count() == 1
    assert await repo.count(include_deleted=True) == 2
    assert await repo.get_by_id(run.id) is None

    assert await repo.restore(run.id) is True
    assert await repo.count() == 2
    assert await repo.get_by_id(run.id) is not None
