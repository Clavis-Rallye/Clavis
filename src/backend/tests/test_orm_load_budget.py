"""
A request loads what it answers, not the organization.

An organization-scoped request must not load whole collections, such as every user of the
organization, every team member or every challenge link. Eager loading (``lazy="selectin"``) on
Organization.users, Run.teams, Team.members, Team.submissions and the content links would do that.
Every guest is a user, so the cost would grow with every event.

These tests put 200 extra guests into the organization, run each request on
a fresh session like production, and count the instances it loads.
"""

from collections import Counter
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.database import Base, get_db, get_session_opener
from app.main import app
from app.models.user import User, UserRole
from tests.journey_helpers import activate_run, create_team_and_join, setup_journey_environment

EXTRA_GUESTS = 200


@contextmanager
def count_loads() -> Iterator[Counter[str]]:
    """Count the ORM instances loaded from rows, per class."""
    counts: Counter[str] = Counter()

    def on_load(target: object, _context: object) -> None:
        counts[type(target).__name__] += 1

    event.listen(Base, "load", on_load, propagate=True)
    try:
        yield counts
    finally:
        event.remove(Base, "load", on_load)


@pytest.mark.asyncio
async def test_a_participant_request_does_not_load_the_organization(
    db_session: AsyncSession,
    client: AsyncClient,
    test_engine: AsyncEngine,
) -> None:
    env = await setup_journey_environment(db_session, client, org_slug="orm-budget")
    coordinator = env.auth_headers["coordinator"]
    created = await client.post(
        f"/api/orgs/{env.slug}/runs/", headers=coordinator, json={"rallye_id": str(env.rallye_id), "name": "Lauf"}
    )
    run_id = created.json()["id"]
    await activate_run(client, env.slug, run_id, coordinator)
    member = env.auth_headers["participant1"]
    await create_team_and_join(client, env.slug, run_id, member, "Team Budget", "#0000FF")
    db_session.add_all(
        User(
            id=uuid4(),
            display_name=f"Gast {index}",
            password_hash="!",
            role=UserRole.GUEST,
            organization_id=env.org.id,
        )
        for index in range(EXTRA_GUESTS)
    )
    await db_session.commit()

    maker = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def real_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = real_get_db
    app.dependency_overrides[get_session_opener] = lambda: maker

    for path in ("submissions/", "stations/", "participant-overview", "teams/"):
        with count_loads() as counts:
            response = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/{path}", headers=member)
        assert response.status_code == 200, (path, response.text)
        assert counts["User"] <= 5, (path, dict(counts))
        assert sum(counts.values()) <= 60, (path, dict(counts))
