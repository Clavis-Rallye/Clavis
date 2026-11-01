"""
The start route refuses a paused run.

A paused run goes back to `active` through resume, never through start. Start
on a paused run answers 422 and changes nothing: the state, `paused_at`, the
run stations and the run challenges stay as they were.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from tests.factories import create_test_user_with_org, get_auth_headers


async def _paused_run(client: AsyncClient, db_session: AsyncSession) -> tuple[str, str, dict[str, str]]:
    """A rallye with two stations and three challenges, a run of it started and paused.

    Returns the org slug, the run id and the auth headers.
    """
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye = await RallyeRepository(db_session, org.id).create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )
    station_repo = StationRepository(db_session, org.id)
    challenge_repo = ChallengeRepository(db_session, org.id)
    for station_index, challenge_count in enumerate((2, 1)):
        station = await station_repo.create(
            title={"de": f"Station {station_index + 1}"},
            description={"de": "Station"},
        )
        link = RallyeStation(
            organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=station_index
        )
        db_session.add(link)
        await db_session.flush()
        for challenge_index in range(challenge_count):
            challenge = await challenge_repo.create(
                challenge_type=ChallengeType.TEXT,
                title={"de": f"Challenge {station_index + 1}.{challenge_index + 1}"},
                instructions={"de": "Do something"},
                scoring_rules={"max_points": 10},
            )
            db_session.add(
                RallyeStationChallenge(
                    organization_id=org.id,
                    rallye_station_id=link.id,
                    challenge_id=challenge.id,
                    sort_order=challenge_index,
                )
            )
            await db_session.flush()
    await db_session.commit()

    created = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]

    started = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert started.status_code == 200
    paused = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/pause", headers=headers)
    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"
    return org.slug, run_id, headers


async def _snapshot_counts(db_session: AsyncSession, run_id: str) -> tuple[int, int]:
    """The number of run stations and of run challenges of the run."""
    stations = await db_session.scalar(select(func.count()).select_from(RunStation).where(RunStation.run_id == run_id))
    challenges = await db_session.scalar(
        select(func.count()).select_from(RunChallenge).where(RunChallenge.run_id == run_id)
    )
    return int(stations or 0), int(challenges or 0)


async def _run(client: AsyncClient, slug: str, run_id: str, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.get(f"/api/orgs/{slug}/runs/{run_id}", headers=headers)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.asyncio
async def test_start_refuses_a_paused_run(client: AsyncClient, db_session: AsyncSession):
    """Start on a paused run answers 422 and changes nothing."""
    slug, run_id, headers = await _paused_run(client, db_session)
    before = await _run(client, slug, run_id, headers)
    assert before["paused_at"] is not None
    counts_after_first_start = await _snapshot_counts(db_session, run_id)
    assert counts_after_first_start == (2, 3)

    response = await client.post(f"/api/orgs/{slug}/runs/{run_id}/start", headers=headers)

    assert response.status_code == 422
    after = await _run(client, slug, run_id, headers)
    assert after["state"] == "paused"
    assert after["paused_at"] == before["paused_at"]
    assert await _snapshot_counts(db_session, run_id) == counts_after_first_start


@pytest.mark.asyncio
async def test_resume_moves_a_paused_run_to_active(client: AsyncClient, db_session: AsyncSession):
    """Resume stays the way from paused to active."""
    slug, run_id, headers = await _paused_run(client, db_session)
    counts_after_first_start = await _snapshot_counts(db_session, run_id)

    response = await client.post(f"/api/orgs/{slug}/runs/{run_id}/resume", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "active"
    assert response.json()["paused_at"] is None
    assert await _snapshot_counts(db_session, run_id) == counts_after_first_start
