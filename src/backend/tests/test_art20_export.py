"""Tests for the GDPR Art. 20 self-service data export.

Covers: a registered user gets their own account + participations + submissions;
strict per-user isolation (no other subject's data); guests refused (403);
unauthenticated refused (401).
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import UserRole
from tests.factories import create_test_user, get_auth_headers


async def _seed_participation(db: AsyncSession, org, user, *, answer: str) -> tuple[Run, Submission]:
    """Create a run + team the user is a member of, with one scored submission."""
    run = Run(
        organization_id=org.id,
        name="Export Run",
        state=RunState.ENDED,
        join_code=uuid.uuid4().hex[:6].upper(),
    )
    db.add(run)
    await db.flush()
    team = Team(organization_id=org.id, run_id=run.id, share_code=uuid.uuid4().hex[:8].upper(), name="Team")
    db.add(team)
    await db.flush()
    db.add(
        TeamMember(
            organization_id=org.id,
            team_id=team.id,
            user_id=user.id,
            display_name=user.display_name or "Member",
            is_guest=False,
        )
    )
    rs = RunStation(organization_id=org.id, run_id=run.id, sort_order=0)
    db.add(rs)
    await db.flush()
    rc = RunChallenge(
        organization_id=org.id,
        run_id=run.id,
        run_station_id=rs.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "Frage"},
        instructions={"de": "Antworte"},
        scoring_rules={},
        sort_order=0,
    )
    db.add(rc)
    await db.flush()
    sub = Submission(
        organization_id=org.id,
        run_id=run.id,
        team_id=team.id,
        run_challenge_id=rc.id,
        content={"text": answer},
    )
    db.add(sub)
    await db.flush()
    db.add(Score(organization_id=org.id, submission_id=sub.id, points=8, max_points=10))
    await db.flush()
    return run, sub


@pytest.mark.asyncio
async def test_export_returns_own_data(client: AsyncClient, db_session: AsyncSession, org_a):
    user = await create_test_user(
        db_session, email="p-a@example.com", display_name="Part A", role=UserRole.PARTICIPANT, organization=org_a
    )
    run, sub = await _seed_participation(db_session, org_a, user, answer="my answer")

    client.headers.update(get_auth_headers(user))
    resp = await client.get("/api/me/export")

    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment")
    body = resp.json()
    assert body["export_version"] == "1.0"
    assert body["account"]["user_id"] == str(user.id)
    assert body["account"]["email"] == "p-a@example.com"
    assert any(p["run_id"] == str(run.id) for p in body["participations"])
    subs = body["submissions"]
    assert any(s["submission_id"] == str(sub.id) and s["content"] == {"text": "my answer"} for s in subs)
    exported = next(s for s in subs if s["submission_id"] == str(sub.id))
    assert exported["challenge_type"] == "text"
    assert exported["score"] == {"points": 8, "max_points": 10}


@pytest.mark.asyncio
async def test_export_is_isolated_per_user(client: AsyncClient, db_session: AsyncSession, org_a):
    user_a = await create_test_user(
        db_session, email="iso-a@example.com", display_name="A", role=UserRole.PARTICIPANT, organization=org_a
    )
    user_b = await create_test_user(
        db_session, email="iso-b@example.com", display_name="B", role=UserRole.PARTICIPANT, organization=org_a
    )
    await _seed_participation(db_session, org_a, user_a, answer="A answer")
    _run_b, sub_b = await _seed_participation(db_session, org_a, user_b, answer="B answer")

    client.headers.update(get_auth_headers(user_a))
    body = (await client.get("/api/me/export")).json()

    sub_ids = {s["submission_id"] for s in body["submissions"]}
    assert str(sub_b.id) not in sub_ids
    assert all(s["content"].get("text") != "B answer" for s in body["submissions"])


@pytest.mark.asyncio
async def test_export_refuses_guest(client: AsyncClient, db_session: AsyncSession, org_a):
    guest = await create_test_user(
        db_session, email="guest-a@example.com", display_name="Guest", role=UserRole.GUEST, organization=org_a
    )
    client.headers.update(get_auth_headers(guest))
    assert (await client.get("/api/me/export")).status_code == 403


@pytest.mark.asyncio
async def test_export_requires_auth(client: AsyncClient):
    assert (await client.get("/api/me/export")).status_code == 401
