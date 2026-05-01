"""Public Run Lookup Endpoint Tests.

Tests for GET /api/public/runs/{code} — anonymous endpoint that returns
{run_id, rallye_name} for a given join code. Used by the Welcome screen.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from app.models.organization import Organization
from app.models.run import Run, RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from tests.factories import create_test_organization, create_test_user


async def _create_run(
    session: AsyncSession,
    *,
    org_slug: str,
    name: str,
    join_code: str,
) -> Run:
    org = await create_test_organization(session, name=f"Org {org_slug}", slug=org_slug)
    run = Run(
        organization_id=org.id,
        rallye_id=None,
        name=name,
        state=RunState.CONFIGURED,
        join_code=join_code,
        settings={},
        content_languages=["de", "en"],
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_public_run_lookup_returns_minimal_payload(client: AsyncClient, db_session: AsyncSession) -> None:
    """200 path: known code → only run_id + rallye_name, nothing else."""
    run = await _create_run(db_session, org_slug="public-runs-200", name="Augsburg Explorer", join_code="ABC123")

    response = await client.get(f"/api/public/runs/{run.join_code}")

    assert response.status_code == 200
    body = response.json()
    # organization_slug is part of the response on purpose: slugs are public (also exposed via
    # GET /api/public/organizations and in other public URLs),
    # and the onboarding flow needs the slug to register users into the right org.
    assert body == {
        "run_id": str(run.id),
        "rallye_name": "Augsburg Explorer",
        "organization_slug": "public-runs-200",
        # The first join step refuses at once, so it needs the state.
        "state": "configured",
        "scheduled_for": None,
    }
    # Multi-tenancy guard: no internal org id leaks.
    assert "organization_id" not in body


@pytest.mark.asyncio
async def test_public_run_lookup_names_the_state_and_the_planned_start(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A planned run says when it starts, an active run says that it runs."""
    from datetime import UTC, datetime

    run = await _create_run(db_session, org_slug="public-runs-state", name="Geplanter Lauf", join_code="PLAN01")
    run.state = RunState.SCHEDULED
    run.scheduled_for = datetime(2026, 12, 1, 9, 30, tzinfo=UTC)
    await db_session.commit()

    planned = (await client.get(f"/api/public/runs/{run.join_code}")).json()
    assert planned["state"] == "scheduled"
    assert planned["scheduled_for"].startswith("2026-12-01T09:30:00")

    run.state = RunState.ACTIVE
    await db_session.commit()
    assert (await client.get(f"/api/public/runs/{run.join_code}")).json()["state"] == "active"


@pytest.mark.asyncio
async def test_public_run_lookup_unknown_code_returns_404(client: AsyncClient) -> None:
    """Unknown code → 404, no body leaking organization existence."""
    response = await client.get("/api/public/runs/NOPE99")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_run_lookup_is_case_insensitive(client: AsyncClient, db_session: AsyncSession) -> None:
    """Join codes are matched case-insensitively."""
    run = await _create_run(db_session, org_slug="public-runs-case", name="Case Run", join_code="ABCDEF")
    response = await client.get(f"/api/public/runs/{run.join_code.lower()}")
    assert response.status_code == 200
    assert response.json()["rallye_name"] == "Case Run"


@pytest.mark.asyncio
async def test_public_run_lookup_cross_tenant_isolation(client: AsyncClient, db_session: AsyncSession) -> None:
    """Unknown code in a multi-org world stays a clean 404 — no org-A leak when probing."""
    # Two orgs with two distinct codes.
    run_a = await _create_run(db_session, org_slug="public-runs-a", name="Org A Rallye", join_code="AAAAAA")
    run_b = await _create_run(db_session, org_slug="public-runs-b", name="Org B Rallye", join_code="BBBBBB")

    # Each org's own code resolves correctly and reports its OWN slug,
    # never the other org's slug.
    a = await client.get(f"/api/public/runs/{run_a.join_code}")
    assert a.status_code == 200
    assert a.json()["rallye_name"] == "Org A Rallye"
    assert a.json()["organization_slug"] == "public-runs-a"

    b = await client.get(f"/api/public/runs/{run_b.join_code}")
    assert b.status_code == 200
    assert b.json()["rallye_name"] == "Org B Rallye"
    assert b.json()["organization_slug"] == "public-runs-b"

    # An unrelated code that exists in neither org is a generic 404.
    miss = await client.get("/api/public/runs/ZZZZZZ")
    assert miss.status_code == 404


@pytest.mark.asyncio
async def test_public_run_lookup_archived_organization_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A run of an archived organization is as good as unknown.

    Archiving is an operator action above the tenant — the public lookup must
    not disclose that the run or its organization still exists behind the flag.
    """
    run = await _create_run(
        db_session,
        org_slug="public-runs-archived",
        name="Archived Run",
        join_code="ARCH01",
    )

    org = await db_session.get(Organization, run.organization_id)
    assert org is not None
    org.archived_at = datetime.now(UTC)
    await db_session.commit()

    response = await client.get(f"/api/public/runs/{run.join_code}")
    assert response.status_code == 404
    # The status stays 404, but the refusal names the reason: the first
    # join page then says "no longer available" instead of letting the person
    # walk to the last step. The join itself names it already (403 ORG_ARCHIVED).
    assert response.json()["error_code"] == "ORG_ARCHIVED"


async def _archive_organization_of(session: AsyncSession, run: Run) -> None:
    org = await session.get(Organization, run.organization_id)
    assert org is not None
    org.archived_at = datetime.now(UTC)
    await session.commit()


@pytest.mark.asyncio
async def test_public_run_lookup_shared_code_active_and_archived_returns_the_active_run(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A join code is unique per organization only: an active and an
    archived organization can hold the same code, and the lookup answers with
    the active run."""
    archived = await _create_run(db_session, org_slug="public-runs-dup-arch", name="Archiv", join_code="DUP001")
    active = await _create_run(db_session, org_slug="public-runs-dup-act", name="Aktiv", join_code="DUP001")
    await _archive_organization_of(db_session, archived)

    response = await client.get("/api/public/runs/DUP001")

    assert response.status_code == 200
    assert response.json()["run_id"] == str(active.id)
    assert response.json()["organization_slug"] == "public-runs-dup-act"


@pytest.mark.asyncio
async def test_public_run_lookup_shared_code_in_two_archived_organizations_is_refused(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The same code in two archived organizations is still ORG_ARCHIVED,
    not a server error from two matching rows."""
    first = await _create_run(db_session, org_slug="public-runs-dup-a1", name="Archiv 1", join_code="DUP002")
    second = await _create_run(db_session, org_slug="public-runs-dup-a2", name="Archiv 2", join_code="DUP002")
    await _archive_organization_of(db_session, first)
    await _archive_organization_of(db_session, second)

    response = await client.get("/api/public/runs/DUP002")

    assert response.status_code == 404
    assert response.json()["error_code"] == "ORG_ARCHIVED"


@pytest.mark.asyncio
async def test_public_run_lookup_unknown_code_has_no_archived_error_code(client: AsyncClient) -> None:
    """An unknown code keeps its plain 404, and no ORG_ARCHIVED leaks onto it."""
    response = await client.get("/api/public/runs/NOPE99")
    assert response.status_code == 404
    assert response.json().get("error_code") != "ORG_ARCHIVED"


@pytest.mark.asyncio
async def test_run_state_of_archived_organization_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The run-state read answers 404 for a run of an archived organization,
    even to a team member of that run: the archive hides the
    run as thoroughly as an unknown id."""
    org = await create_test_organization(db_session, name="Org State", slug="public-runs-state-archived")
    org_id = org.id
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name="State Run",
        state=RunState.ACTIVE,
        join_code="ARCHST",
        settings={},
        content_languages=["de"],
    )
    db_session.add(run)
    await db_session.flush()

    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name="Archiv Team",
        color="#FF5733",
        share_code="TCARCH01",
    )
    db_session.add(team)
    await db_session.flush()
    user = await create_test_user(db_session, email="state-member@example.com", organization_id=org_id)
    db_session.add(
        TeamMember(
            organization_id=org_id,
            team_id=team.id,
            user_id=user.id,
            display_name="Mitglied",
            is_guest=False,
        )
    )

    # Re-fetch through the identity map before mutating — the user-creating
    # helper's refresh reloaded the organization relationship, and mutating
    # the stale factory instance would trip the identity-map guard.
    org = await db_session.get(Organization, org_id)
    assert org is not None
    org.archived_at = datetime.now(UTC)
    await db_session.commit()

    token = create_access_token(user_id=user.id, role=user.role.value, organization_id=user.organization_id)
    response = await client.get(f"/api/public/runs/{run.id}/state", cookies={"access_token": token})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_config_archived_only_organization_no_single_slug_bootstrap_stays_closed(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """With the only organization archived, ``single_org_slug`` disappears
    (the picker must not offer an archived organization), while
    ``bootstrap_open`` stays false: an archived organization
    still counts as an existing one, and after the first organization only the
    operator creates organizations."""
    org = await create_test_organization(db_session, name="Solo Archiv", slug="public-runs-config-archived")
    org.archived_at = datetime.now(UTC)
    await db_session.commit()

    response = await client.get("/api/public/config")
    assert response.status_code == 200
    data = response.json()
    assert data["single_org_slug"] is None
    assert data["bootstrap_open"] is False


# --- Both membership reads ask the shared session rule ----------------------


async def _member_of_active_run(db_session: AsyncSession, slug: str) -> tuple[Run, Team, User]:
    """An active run with one team and one member whose session we can age."""
    org = await create_test_organization(db_session, name=f"Org {slug}", slug=slug)
    org_id = org.id
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name="Session Run",
        state=RunState.ACTIVE,
        join_code=slug[-6:].upper(),
        settings={},
        content_languages=["de"],
    )
    db_session.add(run)
    await db_session.flush()
    team = Team(
        organization_id=org_id,
        run_id=run.id,
        name="Sitzung Team",
        color="#FF5733",
        share_code=f"SC{slug[-6:].upper()}",
    )
    db_session.add(team)
    await db_session.flush()
    user = await create_test_user(db_session, email=f"{slug}@example.com", organization_id=org_id)
    db_session.add(
        TeamMember(organization_id=org_id, team_id=team.id, user_id=user.id, display_name="Mitglied", is_guest=False)
    )
    await db_session.commit()
    await db_session.refresh(run)
    await db_session.refresh(team)
    await db_session.refresh(user)
    return run, team, user


def _session_token(user: User, *, purpose: str | None = None, session_version: int | None = None) -> str:
    return create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
        purpose=purpose,
        session_version=user.session_version if session_version is None else session_version,
    )


async def _age_session(db_session: AsyncSession, user: User, kind: str) -> str:
    """Return a token that is not a full current session, by ``kind``."""
    if kind == "old_session_version":
        token = _session_token(user)
        user.session_version += 1
    elif kind == "2fa_setup":
        token = _session_token(user, purpose="2fa_setup")
    else:
        token = _session_token(user)
        user.locked_until = datetime.now(UTC) + timedelta(minutes=5)
    await db_session.commit()
    return token


@pytest.mark.asyncio
async def test_my_team_with_a_full_current_session_names_the_team(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    run, team, user = await _member_of_active_run(db_session, "run-myteam-ok")
    response = await client.get(
        f"/api/public/runs/{run.join_code}/my-team", cookies={"access_token": _session_token(user)}
    )
    assert response.status_code == 200
    assert response.json()["team_id"] == str(team.id)
    assert response.json()["team_share_code"] == team.share_code


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["old_session_version", "2fa_setup", "locked_account"])
async def test_my_team_without_a_full_current_session_answers_like_no_team(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    run, _, user = await _member_of_active_run(db_session, f"run-myteam-{kind.replace('_', '-')[:8]}")
    token = await _age_session(db_session, user, kind)
    response = await client.get(f"/api/public/runs/{run.join_code}/my-team", cookies={"access_token": token})
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == str(run.id)
    assert body["team_id"] is None
    assert body["team_name"] is None
    assert body["team_share_code"] is None


@pytest.mark.asyncio
async def test_run_state_with_a_full_current_session_names_the_run(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    run, _, user = await _member_of_active_run(db_session, "run-state-ok")
    response = await client.get(f"/api/public/runs/{run.id}/state", cookies={"access_token": _session_token(user)})
    assert response.status_code == 200
    assert response.json()["run_id"] == str(run.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["old_session_version", "2fa_setup", "locked_account"])
async def test_run_state_without_a_full_current_session_answers_like_a_non_member(
    client: AsyncClient, db_session: AsyncSession, kind: str
) -> None:
    run, _, user = await _member_of_active_run(db_session, f"run-state-{kind.replace('_', '-')[:8]}")
    token = await _age_session(db_session, user, kind)
    response = await client.get(f"/api/public/runs/{run.id}/state", cookies={"access_token": token})
    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}
