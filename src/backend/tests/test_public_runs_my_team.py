"""Tests for GET /api/public/runs/{code}/my-team.

The endpoint reports the authenticated caller's team membership in the run
identified by `code` so the frontend can short-circuit FindTeam when the
caller is already in a team. Anonymous callers get 401.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from app.models.run import Run, RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from tests.factories import create_test_organization, create_test_user


async def _create_run(
    session: AsyncSession,
    *,
    org_slug: str,
    join_code: str,
    name: str = "Test Run",
) -> Run:
    org = await create_test_organization(session, name=f"Org {org_slug}", slug=org_slug)
    run = Run(
        organization_id=org.id,
        rallye_id=None,
        name=name,
        state=RunState.ACTIVE,
        join_code=join_code,
        settings={},
        content_languages=["de", "en"],
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _add_team_with_member(
    session: AsyncSession,
    *,
    run: Run,
    user_id,
    team_name: str | None = "The Explorers",
    share_code: str = "TC0001AB",
    color: str = "#FF5733",
    joined_at: datetime | None = None,
) -> Team:
    team = Team(
        organization_id=run.organization_id,
        run_id=run.id,
        name=team_name,
        color=color,
        share_code=share_code,
    )
    session.add(team)
    await session.flush()
    membership = TeamMember(
        organization_id=run.organization_id,
        team_id=team.id,
        user_id=user_id,
        display_name="Test Member",
        is_guest=False,
        joined_at=joined_at or datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(membership)
    await session.commit()
    await session.refresh(team)
    return team


async def _add_member(
    session: AsyncSession,
    *,
    team: Team,
    user_id: UUID,
    joined_at: datetime,
    display_name: str = "Another Member",
) -> TeamMember:
    membership = TeamMember(
        organization_id=team.organization_id,
        team_id=team.id,
        user_id=user_id,
        display_name=display_name,
        is_guest=False,
        joined_at=joined_at,
    )
    session.add(membership)
    await session.commit()
    return membership


@pytest.mark.asyncio
async def test_my_team_returns_team_when_user_is_member(client: AsyncClient, db_session: AsyncSession) -> None:
    """Authenticated user who is a member → full team payload."""
    run = await _create_run(db_session, org_slug="myteam-200", join_code="JOIN01")
    user = await create_test_user(
        db_session,
        email="member@example.com",
        organization_id=run.organization_id,
    )
    team = await _add_team_with_member(db_session, run=run, user_id=user.id, team_name="Alpha", share_code="ALP-001")
    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    response = await client.get(
        f"/api/public/runs/{run.join_code}/my-team",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "run_id": str(run.id),
        "team_id": str(team.id),
        "team_name": "Alpha",
        "team_share_code": "ALP-001",
        "is_creator": True,
    }


@pytest.mark.asyncio
async def test_my_team_returns_null_team_when_user_has_no_membership(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Authenticated user without membership → run_id only, team_id=None."""
    run = await _create_run(db_session, org_slug="myteam-noteam", join_code="JOIN02")
    user = await create_test_user(
        db_session,
        email="solo@example.com",
        organization_id=run.organization_id,
    )
    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    response = await client.get(
        f"/api/public/runs/{run.join_code}/my-team",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == str(run.id)
    assert body["team_id"] is None
    assert body["team_name"] is None
    assert body["team_share_code"] is None
    assert body["is_creator"] is False


@pytest.mark.asyncio
async def test_my_team_returns_null_name_for_name_pending_team(client: AsyncClient, db_session: AsyncSession) -> None:
    """Team with NULL name (still in name-finding) → team_name=null."""
    run = await _create_run(db_session, org_slug="myteam-pending", join_code="JOIN03")
    user = await create_test_user(
        db_session,
        email="pending@example.com",
        organization_id=run.organization_id,
    )
    team = await _add_team_with_member(db_session, run=run, user_id=user.id, team_name=None, share_code="PEN-001")
    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    response = await client.get(
        f"/api/public/runs/{run.join_code}/my-team",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["team_id"] == str(team.id)
    assert body["team_name"] is None  # client uses this to choose waiting screen


@pytest.mark.asyncio
async def test_my_team_unauthenticated_returns_401(client: AsyncClient, db_session: AsyncSession) -> None:
    """Anonymous caller (no cookie) → 401, never 200/404, since the question
    'do I have a team' is undefined for an anonymous user."""
    run = await _create_run(db_session, org_slug="myteam-401", join_code="JOIN04")

    response = await client.get(f"/api/public/runs/{run.join_code}/my-team")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_my_team_unknown_code_returns_404(client: AsyncClient, db_session: AsyncSession) -> None:
    """Unknown code → 404 (after auth check passes)."""
    user = await create_test_user(db_session, email="auth@example.com")
    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)

    response = await client.get(
        "/api/public/runs/NOPE99/my-team",
        cookies={"access_token": token},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_my_team_cross_org_user_returns_no_team(client: AsyncClient, db_session: AsyncSession) -> None:
    """User from a different org querying a run → behaves like 'no team'.

    A cross-org user definitionally cannot have a membership in this run,
    and revealing the org mismatch would leak existence of organizations.
    """
    run = await _create_run(db_session, org_slug="myteam-orgA", join_code="JOIN05")
    other_user = await create_test_user(
        db_session,
        email="outsider@example.com",
        # Force a fresh org by not passing organization_id.
    )
    token = create_access_token(
        user_id=other_user.id,
        role=other_user.role.value,
        organization_id=other_user.organization_id,
    )

    response = await client.get(
        f"/api/public/runs/{run.join_code}/my-team",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["team_id"] is None
    assert body["run_id"] == str(run.id)


@pytest.mark.asyncio
async def test_my_team_is_creator_true_for_earliest_member(client: AsyncClient, db_session: AsyncSession) -> None:
    """The member with the earliest joined_at gets is_creator=true."""
    run = await _create_run(db_session, org_slug="myteam-creator", join_code="JOIN06")
    creator = await create_test_user(
        db_session,
        email="creator@example.com",
        organization_id=run.organization_id,
    )
    other = await create_test_user(
        db_session,
        email="joiner@example.com",
        organization_id=run.organization_id,
    )
    team = await _add_team_with_member(
        db_session,
        run=run,
        user_id=creator.id,
        team_name="Beta",
        share_code="BET-001",
        joined_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await _add_member(db_session, team=team, user_id=other.id, joined_at=datetime(2026, 1, 2, tzinfo=UTC))
    token = create_access_token(user_id=creator.id, role=creator.role.value, organization_id=creator.organization_id)

    response = await client.get(
        f"/api/public/runs/{run.join_code}/my-team",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_creator"] is True


@pytest.mark.asyncio
async def test_my_team_is_creator_false_for_second_member(client: AsyncClient, db_session: AsyncSession) -> None:
    """A member who joined after the creator gets is_creator=false."""
    run = await _create_run(db_session, org_slug="myteam-second", join_code="JOIN07")
    creator = await create_test_user(
        db_session,
        email="creator2@example.com",
        organization_id=run.organization_id,
    )
    other = await create_test_user(
        db_session,
        email="joiner2@example.com",
        organization_id=run.organization_id,
    )
    team = await _add_team_with_member(
        db_session,
        run=run,
        user_id=creator.id,
        team_name="Gamma",
        share_code="GAM-001",
        joined_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await _add_member(db_session, team=team, user_id=other.id, joined_at=datetime(2026, 1, 2, tzinfo=UTC))
    token = create_access_token(user_id=other.id, role=other.role.value, organization_id=other.organization_id)

    response = await client.get(
        f"/api/public/runs/{run.join_code}/my-team",
        cookies={"access_token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_creator"] is False
