"""Integration tests for PATCH /api/orgs/{slug}/runs/{run_id}/teams/{team_id}.

Covers who may rename a team, when the name is frozen, and the per-run uniqueness of names.
"""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.run import Run, RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import UserRole
from tests.factories import create_test_user_with_org, get_auth_headers
from tests.journey_helpers import setup_journey_environment

_MIGRATION_027_PATH = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "027_team_name_unique_per_run.py"
)


def _load_migration_027() -> ModuleType:
    """Import migration 027 by file path.

    Its filename starts with a digit, so it cannot be named by an ordinary
    `import` statement; `importlib.util` loads it directly from disk instead.
    """
    spec = importlib.util.spec_from_file_location("migration_027_team_name_unique_per_run", _MIGRATION_027_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _make_run_and_team(
    db_session: AsyncSession,
    org_id: UUID,
    state: RunState = RunState.CONFIGURED,
    team_name: str | None = None,
) -> tuple[Run, Team]:
    """Insert a Run + Team directly, bypassing the API's ACTIVE-state requirement."""
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name=f"Test Run {uuid4().hex[:6]}",
        state=state,
        join_code=uuid4().hex[:6].upper(),
        settings={"max_members_per_team": 10},
    )
    db_session.add(run)
    await db_session.flush()

    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name=team_name,
        color="#123456",
        share_code=uuid4().hex[:6].upper(),
    )
    db_session.add(team)
    await db_session.flush()

    return run, team


async def _add_team(
    db_session: AsyncSession,
    org_id: UUID,
    run: Run,
    name: str | None,
    deleted_at: datetime | None = None,
) -> Team:
    """Insert a second team into an existing run."""
    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name=name,
        color="#654321",
        share_code=uuid4().hex[:6].upper(),
        deleted_at=deleted_at,
        is_deleted=deleted_at is not None,
    )
    db_session.add(team)
    await db_session.flush()
    return team


async def _add_member(
    db_session: AsyncSession,
    org_id: UUID,
    team: Team,
    user,
    joined_at,
) -> TeamMember:
    member = TeamMember(
        organization_id=org_id,
        team_id=team.id,
        user_id=user.id,
        display_name=user.display_name,
        is_guest=user.role == UserRole.GUEST,
        joined_at=joined_at,
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest.mark.asyncio
async def test_patch_team_name_creator_happy_path(client: AsyncClient, db_session: AsyncSession) -> None:
    """The team creator (smallest joined_at) can rename the team."""
    from datetime import UTC, datetime

    env = await setup_journey_environment(db_session, client, org_slug="patch-creator")
    creator = env.users["participant1"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name=None)
    await _add_member(db_session, env.org.id, team, creator, joined_at=datetime(2026, 1, 1, tzinfo=UTC))
    await db_session.commit()

    headers = get_auth_headers(creator)
    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=headers,
        json={"name": "Speedy Foxes"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Speedy Foxes"


@pytest.mark.asyncio
async def test_patch_team_name_idempotent_recall(client: AsyncClient, db_session: AsyncSession) -> None:
    """Calling again with the same name is a no-op and still returns 200."""
    from datetime import UTC, datetime

    env = await setup_journey_environment(db_session, client, org_slug="patch-idem")
    creator = env.users["participant1"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name="Already Named")
    await _add_member(db_session, env.org.id, team, creator, joined_at=datetime(2026, 1, 1, tzinfo=UTC))
    await db_session.commit()

    headers = get_auth_headers(creator)
    r1 = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=headers,
        json={"name": "Already Named"},
    )
    assert r1.status_code == 200
    assert r1.json()["name"] == "Already Named"


@pytest.mark.asyncio
async def test_patch_team_name_coordinator_happy_path(client: AsyncClient, db_session: AsyncSession) -> None:
    """A coordinator who is not the creator can rename any team in their org."""
    from datetime import UTC, datetime

    env = await setup_journey_environment(db_session, client, org_slug="patch-coord")
    creator = env.users["participant1"]
    coordinator = env.users["coordinator"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name=None)
    await _add_member(db_session, env.org.id, team, creator, joined_at=datetime(2026, 1, 1, tzinfo=UTC))
    await db_session.commit()

    headers = get_auth_headers(coordinator)
    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=headers,
        json={"name": "Renamed By Coord"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed By Coord"


@pytest.mark.asyncio
async def test_patch_team_name_non_creator_member_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    """A team member who is not the creator cannot rename the team."""
    from datetime import UTC, datetime

    env = await setup_journey_environment(db_session, client, org_slug="patch-nm")
    creator = env.users["participant1"]
    other_member = env.users["participant2"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name=None)
    await _add_member(db_session, env.org.id, team, creator, joined_at=datetime(2026, 1, 1, tzinfo=UTC))
    await _add_member(db_session, env.org.id, team, other_member, joined_at=datetime(2026, 1, 2, tzinfo=UTC))
    await db_session.commit()

    headers = get_auth_headers(other_member)
    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=headers,
        json={"name": "Sneaky Rename"},
    )
    assert response.status_code == 403
    assert "creator" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_patch_team_name_cross_tenant_404(client: AsyncClient, db_session: AsyncSession) -> None:
    """A caller from a different org is rejected with 403 or 404."""
    from datetime import UTC, datetime

    env_a = await setup_journey_environment(db_session, client, org_slug="patch-tenant-a")
    creator_a = env_a.users["participant1"]
    run, team = await _make_run_and_team(db_session, env_a.org.id, team_name=None)
    await _add_member(db_session, env_a.org.id, team, creator_a, joined_at=datetime(2026, 1, 1, tzinfo=UTC))

    user_b, org_b = await create_test_user_with_org(
        db_session,
        email="patch-tenant-b@test.com",
        role=UserRole.COORDINATOR,
        org_slug="patch-tenant-b",
    )
    await db_session.commit()

    headers_b = get_auth_headers(user_b)
    # Org B's caller targets org A's slug. The org lookup must reject it, so the status is never 200.
    response = await client.patch(
        f"/api/orgs/{env_a.slug}/runs/{run.id}/teams/{team.id}",
        headers=headers_b,
        json={"name": "Hostile Rename"},
    )
    assert response.status_code in (403, 404), response.text


@pytest.mark.asyncio
async def test_patch_team_name_active_run_409(client: AsyncClient, db_session: AsyncSession) -> None:
    """Once the run is ACTIVE, the team name is frozen."""
    from datetime import UTC, datetime

    env = await setup_journey_environment(db_session, client, org_slug="patch-active")
    creator = env.users["participant1"]
    run, team = await _make_run_and_team(db_session, env.org.id, state=RunState.ENDED, team_name="Before Active")
    await _add_member(db_session, env.org.id, team, creator, joined_at=datetime(2026, 1, 1, tzinfo=UTC))
    await db_session.commit()

    headers = get_auth_headers(creator)
    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=headers,
        json={"name": "After Active"},
    )
    assert response.status_code == 409
    assert "frozen" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_patch_team_name_oversized_422(client: AsyncClient, db_session: AsyncSession) -> None:
    """A name longer than the maximum length is rejected with 422."""
    from datetime import UTC, datetime

    env = await setup_journey_environment(db_session, client, org_slug="patch-422")
    creator = env.users["participant1"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name=None)
    await _add_member(db_session, env.org.id, team, creator, joined_at=datetime(2026, 1, 1, tzinfo=UTC))
    await db_session.commit()

    headers = get_auth_headers(creator)
    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=headers,
        json={"name": "X" * 200},
    )
    assert response.status_code == 422


# --- Team names are unique per run -----------------------------------------


@pytest.mark.asyncio
async def test_patch_team_name_taken_in_same_run_409(client: AsyncClient, db_session: AsyncSession) -> None:
    """409 TEAM_NAME_TAKEN when another team of the run already has the name."""
    env = await setup_journey_environment(db_session, client, org_slug="patch-taken")
    coordinator = env.users["coordinator"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name="Langsame Igel")
    await _add_team(db_session, env.org.id, run, name="Schnelle Füchse")
    await db_session.commit()

    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=get_auth_headers(coordinator),
        json={"name": "Schnelle Füchse"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "TEAM_NAME_TAKEN"


@pytest.mark.asyncio
async def test_patch_team_name_taken_ignores_case_and_spaces_409(client: AsyncClient, db_session: AsyncSession) -> None:
    """The comparison is on the trimmed, lowercased name, so a differently spelled name collides too."""
    env = await setup_journey_environment(db_session, client, org_slug="patch-taken-case")
    coordinator = env.users["coordinator"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name="Langsame Igel")
    await _add_team(db_session, env.org.id, run, name="Schnelle Füchse")
    await db_session.commit()

    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=get_auth_headers(coordinator),
        json={"name": "  schnelle füchse  "},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "TEAM_NAME_TAKEN"


@pytest.mark.asyncio
async def test_patch_team_name_same_name_in_other_run_200(client: AsyncClient, db_session: AsyncSession) -> None:
    """Uniqueness is per run: the same name in another run does not block."""
    env = await setup_journey_environment(db_session, client, org_slug="patch-other-run")
    coordinator = env.users["coordinator"]
    other_run, _other_team = await _make_run_and_team(db_session, env.org.id, team_name="Schnelle Füchse")
    run, team = await _make_run_and_team(db_session, env.org.id, team_name="Langsame Igel")
    assert other_run.id != run.id
    await db_session.commit()

    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=get_auth_headers(coordinator),
        json={"name": "Schnelle Füchse"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Schnelle Füchse"


@pytest.mark.asyncio
async def test_patch_team_name_soft_deleted_team_frees_name(client: AsyncClient, db_session: AsyncSession) -> None:
    """A soft-deleted team no longer holds its name."""
    env = await setup_journey_environment(db_session, client, org_slug="patch-freed")
    coordinator = env.users["coordinator"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name="Langsame Igel")
    await _add_team(
        db_session,
        env.org.id,
        run,
        name="Schnelle Füchse",
        deleted_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    await db_session.commit()

    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=get_auth_headers(coordinator),
        json={"name": "Schnelle Füchse"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Schnelle Füchse"


@pytest.mark.asyncio
async def test_patch_team_name_respells_own_name_200(client: AsyncClient, db_session: AsyncSession) -> None:
    """A team does not block itself: "Füchse" to "füchse" is allowed."""
    env = await setup_journey_environment(db_session, client, org_slug="patch-respell")
    coordinator = env.users["coordinator"]
    run, team = await _make_run_and_team(db_session, env.org.id, team_name="Schnelle Füchse")
    await db_session.commit()

    response = await client.patch(
        f"/api/orgs/{env.slug}/runs/{run.id}/teams/{team.id}",
        headers=get_auth_headers(coordinator),
        json={"name": "schnelle füchse"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "schnelle füchse"


def test_migration_027_dedupe_only_renames_true_collisions() -> None:
    """`_dedupe()` groups by raw `lower(name)`, exactly what the index enforces (no trim).

    A row that collides with nothing is left byte for byte as it is, whatever its whitespace or length.
    Only a real collision gets a numeric suffix, and the earliest team (by created_at / row order)
    keeps its name unchanged.
    """
    migration = _load_migration_027()
    run = uuid4()

    # "  Foo  ", "foo" and " foo " are three DIFFERENT strings once merely
    # lowered (no trim), so none of them collides with another under the
    # index's exact definition — all three must survive untouched.
    id_foo_padded = uuid4()
    id_foo_bare = uuid4()
    id_foo_single_space = uuid4()
    # "Bar" and "bar" DO collide (lower("Bar") == lower("bar") == "bar").
    id_bar_first = uuid4()
    id_bar_second = uuid4()

    rows = [
        (id_foo_padded, run, "  Foo  "),
        (id_foo_bare, run, "foo"),
        (id_foo_single_space, run, " foo "),
        (id_bar_first, run, "Bar"),
        (id_bar_second, run, "bar"),
    ]

    renames = migration._dedupe(rows)

    assert id_foo_padded not in renames
    assert id_foo_bare not in renames
    assert id_foo_single_space not in renames
    assert id_bar_first not in renames
    assert renames[id_bar_second] == "bar 2"
    assert len(renames) == 1
