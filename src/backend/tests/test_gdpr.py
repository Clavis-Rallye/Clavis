"""
The final erasure of an account, ``services/gdpr.py``.

The audit entry ``team.dissolved`` of an erasure is written after the commit and
never for a transaction that was rolled back. An erasure whose team locks give
up answers a conflict instead of a server error.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.run import Run, RunState
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.services import gdpr, team_dissolution
from app.services.gdpr import anonymize_user
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers


class _AuditSpy:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: str, **kwargs: Any) -> None:
        self.events.append({"event": event, **kwargs})

    def dissolved(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == "team.dissolved"]


@pytest.fixture
def audit_spy(monkeypatch: pytest.MonkeyPatch) -> _AuditSpy:
    spy = _AuditSpy()
    monkeypatch.setattr(gdpr, "record_audit_event", spy)
    return spy


@dataclass(frozen=True)
class _Ids:
    org_id: UUID
    slug: str


async def _last_member_of_a_live_team(db: AsyncSession, slug: str) -> tuple[_Ids, UUID, UUID]:
    """An organization, a participant, and a team of an active run whose only member is that participant."""
    user, org = await create_test_user_with_org(db, email=f"{slug}@test.com", role=UserRole.PARTICIPANT, org_slug=slug)
    run = Run(
        organization_id=org.id,
        rallye_id=None,
        name=f"Run {uuid4().hex[:6]}",
        state=RunState.ACTIVE,
        join_code=uuid4().hex[:6].upper(),
        settings={"max_members_per_team": 10},
    )
    db.add(run)
    await db.flush()
    team = Team(organization_id=org.id, run_id=run.id, name="Team", color="#112233", share_code=uuid4().hex[:6].upper())
    db.add(team)
    await db.flush()
    db.add(
        TeamMember(
            organization_id=org.id,
            team_id=team.id,
            user_id=user.id,
            display_name="Anna",
            is_guest=False,
            joined_at=datetime.now(UTC),
        )
    )
    await db.commit()
    # Ids and slug are read here: a rollback in a test expires the objects.
    return _Ids(org_id=org.id, slug=org.slug), user.id, team.id


async def _is_dissolved(db: AsyncSession, team_id: UUID) -> bool:
    return (
        await db.execute(select(Team.is_deleted).where(Team.id == team_id).execution_options(populate_existing=True))
    ).scalar_one()


@pytest.mark.asyncio
async def test_the_dissolution_of_an_erasure_is_audited_after_the_commit(db_session: AsyncSession, audit_spy):
    _org, user_id, team_id = await _last_member_of_a_live_team(db_session, "erase-audit-commit")

    assert await anonymize_user(db_session, user_id, {"system": "test"}) is True

    assert audit_spy.dissolved() == [], "the entry was written before the transaction committed"
    await db_session.commit()
    (event,) = audit_spy.dissolved()
    assert event["target"]["id"] == str(team_id)
    assert event["target"]["cause"] == "last_member_erased"
    assert event["actor"] == {"system": "test"}

    # One entry per dissolution: a later commit of the same session writes none.
    await db_session.commit()
    assert len(audit_spy.dissolved()) == 1


@pytest.mark.asyncio
async def test_an_erasure_that_is_rolled_back_audits_no_dissolution(db_session: AsyncSession, audit_spy):
    _org, user_id, team_id = await _last_member_of_a_live_team(db_session, "erase-audit-rollback")

    assert await anonymize_user(db_session, user_id, {"system": "test"}) is True
    await db_session.rollback()

    assert audit_spy.dissolved() == []
    assert await _is_dissolved(db_session, team_id) is False
    # The next commit of this session has nothing to write for the erasure that was rolled back.
    await db_session.commit()
    assert audit_spy.dissolved() == []


@pytest.mark.asyncio
async def test_a_savepoint_inside_the_erasure_does_not_write_or_drop_the_entry(db_session: AsyncSession, audit_spy):
    """Only the end of the whole transaction decides: a savepoint that is released or rolled back does not."""
    _org, user_id, _team_id = await _last_member_of_a_live_team(db_session, "erase-audit-savepoint")

    assert await anonymize_user(db_session, user_id, {"system": "test"}) is True
    released = await db_session.begin_nested()
    await released.commit()
    assert audit_spy.dissolved() == []
    undone = await db_session.begin_nested()
    await undone.rollback()
    assert audit_spy.dissolved() == []

    await db_session.commit()
    assert len(audit_spy.dissolved()) == 1


@pytest.mark.asyncio
async def test_final_erasure_answers_team_busy_when_the_team_locks_give_up(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, audit_spy
):
    """``lock_member_teams`` gives up after its rounds: the erasure answers 409 TEAM_BUSY and erases nothing."""
    org, user_id, team_id = await _last_member_of_a_live_team(db_session, "erase-busy")
    organization = await db_session.get(Organization, org.org_id)
    admin = await create_test_user(
        db_session, email="erase-busy-admin@test.com", role=UserRole.ORG_ADMIN, organization=organization
    )
    await db_session.commit()
    headers = get_auth_headers(admin)
    monkeypatch.setattr(team_dissolution, "_MEMBER_TEAM_LOCK_ATTEMPTS", 0)

    response = await client.delete(f"/api/orgs/{org.slug}/users/{user_id}?mode=immediate", headers=headers)

    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "TEAM_BUSY"
    await db_session.rollback()
    account = (
        await db_session.execute(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    ).scalar_one()
    assert account.is_active and not account.is_deleted
    assert account.email == "erase-busy@test.com"
    assert await _is_dissolved(db_session, team_id) is False
    assert audit_spy.dissolved() == []
