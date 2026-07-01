"""Date-range (organization timeseries) analytics.

Covers: per-bucket and total aggregation across runs on different dates,
a single run in range reproducing its participation top-line, an empty
range, the 422 guardrails, coordinator-only access and the CSV export.
"""

import uuid
from datetime import UTC, datetime, timedelta

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

# Midday UTC so the Europe/Berlin bucketing date equals the calendar date.
D10 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
D11 = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)


async def _run(db: AsyncSession, org, *, ended_at: datetime) -> Run:
    # An ended run has a start. The period view counts only runs that started.
    run = Run(
        organization_id=org.id,
        name="R",
        state=RunState.ENDED,
        join_code=uuid.uuid4().hex[:6].upper(),
        started_at=ended_at - timedelta(hours=1),
        ended_at=ended_at,
    )
    db.add(run)
    await db.flush()
    return run


async def _team(db: AsyncSession, org, run, *, guests: int, registered: int) -> Team:
    team = Team(
        organization_id=org.id,
        run_id=run.id,
        share_code=uuid.uuid4().hex[:8].upper(),
        name=f"T-{uuid.uuid4().hex[:6]}",
    )
    db.add(team)
    await db.flush()
    # Flush per row: a batched multi-row INSERT of these client-side-UUID +
    # server-default-timestamp (joined_at) rows trips asyncpg's insertmanyvalues
    # sentinel matching (same as the _chat helper in test_retention_sweep).
    for _ in range(guests):
        db.add(TeamMember(organization_id=org.id, team_id=team.id, display_name="G", is_guest=True))
        await db.flush()
    for _ in range(registered):
        db.add(TeamMember(organization_id=org.id, team_id=team.id, display_name="R", is_guest=False))
        await db.flush()
    return team


async def _submission(db: AsyncSession, org, run, team, *, scored: bool) -> Submission:
    rs = RunStation(organization_id=org.id, run_id=run.id, sort_order=0)
    db.add(rs)
    await db.flush()
    rc = RunChallenge(
        organization_id=org.id,
        run_id=run.id,
        run_station_id=rs.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "F"},
        instructions={"de": "x"},
        scoring_rules={},
        sort_order=0,
    )
    db.add(rc)
    await db.flush()
    sub = Submission(
        organization_id=org.id, run_id=run.id, team_id=team.id, run_challenge_id=rc.id, content={"text": "a"}
    )
    db.add(sub)
    await db.flush()
    if scored:
        db.add(Score(organization_id=org.id, submission_id=sub.id, points=5, max_points=10))
        await db.flush()
    return sub


@pytest.mark.asyncio
async def test_timeseries_aggregates_per_bucket_and_totals(client_org_a: AsyncClient, db_session: AsyncSession, org_a):
    # Day 10: 1 run, 1 team (2 guests + 1 registered), 2 submissions (1 scored).
    r1 = await _run(db_session, org_a, ended_at=D10)
    t1 = await _team(db_session, org_a, r1, guests=2, registered=1)
    await _submission(db_session, org_a, r1, t1, scored=True)
    await _submission(db_session, org_a, r1, t1, scored=False)
    # Day 11: 1 run, 2 teams (1 registered each), 1 submission (unscored).
    r2 = await _run(db_session, org_a, ended_at=D11)
    t2 = await _team(db_session, org_a, r2, guests=0, registered=1)
    await _team(db_session, org_a, r2, guests=0, registered=1)
    await _submission(db_session, org_a, r2, t2, scored=False)

    resp = await client_org_a.get(
        "/api/orgs/org-a/analytics/timeseries",
        params={"date_from": "2026-03-01", "date_to": "2026-03-31", "bucket": "day"},
    )
    assert resp.status_code == 200
    body = resp.json()
    by_bucket = {b["bucket_start"][:10]: b for b in body["buckets"]}

    d10 = by_bucket["2026-03-10"]
    assert (
        d10["run_count"],
        d10["team_count"],
        d10["participant_count"],
        d10["guest_count"],
        d10["registered_count"],
    ) == (
        1,
        1,
        3,
        2,
        1,
    )
    assert (d10["submission_count"], d10["scored_count"], d10["unscored_count"]) == (2, 1, 1)

    d11 = by_bucket["2026-03-11"]
    assert (d11["run_count"], d11["team_count"], d11["participant_count"], d11["registered_count"]) == (1, 2, 2, 2)
    assert (d11["submission_count"], d11["scored_count"], d11["unscored_count"]) == (1, 0, 1)

    t = body["totals"]
    assert (t["run_count"], t["team_count"], t["participant_count"], t["guest_count"], t["registered_count"]) == (
        2,
        3,
        5,
        2,
        3,
    )
    assert (t["submission_count"], t["scored_count"], t["unscored_count"]) == (3, 1, 2)


@pytest.mark.asyncio
async def test_single_run_in_range(client_org_a: AsyncClient, db_session: AsyncSession, org_a):
    r = await _run(db_session, org_a, ended_at=D10)
    await _team(db_session, org_a, r, guests=3, registered=2)
    resp = await client_org_a.get(
        "/api/orgs/org-a/analytics/timeseries", params={"date_from": "2026-03-10", "date_to": "2026-03-10"}
    )
    assert resp.status_code == 200
    t = resp.json()["totals"]
    # Matches the participation top-line of that one run.
    assert (t["run_count"], t["participant_count"], t["guest_count"], t["registered_count"]) == (1, 5, 3, 2)


@pytest.mark.asyncio
async def test_empty_range_is_ok(client_org_a: AsyncClient):
    resp = await client_org_a.get(
        "/api/orgs/org-a/analytics/timeseries", params={"date_from": "2030-01-01", "date_to": "2030-01-31"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["buckets"] == []
    assert body["totals"]["run_count"] == 0


@pytest.mark.asyncio
async def test_reversed_range_422(client_org_a: AsyncClient):
    resp = await client_org_a.get(
        "/api/orgs/org-a/analytics/timeseries", params={"date_from": "2026-03-31", "date_to": "2026-03-01"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_day_bucket_overflow_422(client_org_a: AsyncClient):
    resp = await client_org_a.get(
        "/api/orgs/org-a/analytics/timeseries",
        params={"date_from": "2020-01-01", "date_to": "2026-01-01", "bucket": "day"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_requires_coordinator(client: AsyncClient, db_session: AsyncSession, org_a):
    participant = await create_test_user(
        db_session, email="p-ts@example.com", display_name="P", role=UserRole.PARTICIPANT, organization=org_a
    )
    client.headers.update(get_auth_headers(participant))
    resp = await client.get(
        "/api/orgs/org-a/analytics/timeseries", params={"date_from": "2026-03-01", "date_to": "2026-03-31"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_csv_export(client_org_a: AsyncClient, db_session: AsyncSession, org_a):
    r = await _run(db_session, org_a, ended_at=D10)
    await _team(db_session, org_a, r, guests=1, registered=0)
    resp = await client_org_a.get(
        "/api/orgs/org-a/analytics/timeseries/export",
        params={"date_from": "2026-03-01", "date_to": "2026-03-31", "bucket": "day"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    # The period is in the file name, like JSON and PNG.
    assert resp.headers["content-disposition"].endswith("-2026-03-01-2026-03-31.csv")
    lines = resp.text.strip().splitlines()
    # BOM, ";" and the screen's names for a German reader.
    assert lines[0].startswith("\ufeffZeitraum ab;Läufe;Teilnehmende")
    # no PII columns
    assert "email" not in lines[0] and "display_name" not in lines[0]
    # A German file writes the day as a German reads it.
    assert any(line.startswith("10.03.2026;1;1;1;0") for line in lines[1:])
    # Every day of the range is a row, the empty ones with zeros.
    assert len(lines) == 1 + 31
    assert any(line.startswith("01.03.2026;0;0;0;0") for line in lines[1:])


@pytest.mark.asyncio
async def test_gaps_are_filled_per_week_and_month(client_org_a: AsyncClient, db_session: AsyncSession, org_a):
    """Weeks and months without runs appear as zero buckets between the ones with runs."""
    await _run(db_session, org_a, ended_at=D10)
    weekly = await client_org_a.get(
        "/api/orgs/org-a/analytics/timeseries",
        params={"date_from": "2026-02-01", "date_to": "2026-03-31", "bucket": "week"},
    )
    starts = [b["bucket_start"][:10] for b in weekly.json()["buckets"]]
    # Mondays from the week of 2026-02-01 (a Sunday) to the last week of March.
    assert starts[0] == "2026-01-26"
    assert starts[-1] == "2026-03-30"
    assert len(starts) == 10
    assert sum(b["run_count"] for b in weekly.json()["buckets"]) == 1

    monthly = await client_org_a.get(
        "/api/orgs/org-a/analytics/timeseries",
        params={"date_from": "2026-01-15", "date_to": "2026-04-02", "bucket": "month"},
    )
    assert [b["bucket_start"][:10] for b in monthly.json()["buckets"]] == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
        "2026-04-01",
    ]
