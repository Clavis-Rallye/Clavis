"""
Snapshot service for creating frozen run content.

Handles deep-copying rallye templates (stations and challenges) into run-scoped
snapshot tables (run_stations, run_challenges) when a run transitions to active state.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.rallye import Rallye
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run import Run
from app.models.run_station import RunStation
from app.repositories.run_challenge import RunChallengeRepository
from app.repositories.run_station import RunStationRepository


async def create_run_snapshot(
    session: AsyncSession,
    run: Run,
    organization_id: UUID,
) -> None:
    """
    Create frozen snapshot of rallye content for a run.

    Traverses the library composition (rallye -> rallye_stations -> [station,
    rallye_station_challenges -> challenge]) and deep-copies the referenced stations
    and challenges into the run_stations and run_challenges tables. This creates an
    immutable snapshot that the run operates on, even if the original library
    content is later modified, re-linked, or deleted.

    Ordering is taken from the link rows: RunStation.sort_order comes from the
    per-rallye RallyeStation.sort_order, and RunChallenge.sort_order comes from
    the per-rallye-station RallyeStationChallenge.sort_order. Soft-deleted stations
    and challenges are skipped.

    This function must be called within a transaction. It does NOT commit —
    the caller is responsible for transaction management.

    Raises ``ValueError`` when the run has no ``rallye_id``, the rallye is not
    found or has no station links, or the run already has run stations. A run
    has one snapshot: a second build would double its stations and challenges.
    The callers hold the run's row lock, so no other build of the run runs
    beside this check.
    """
    if run.rallye_id is None:
        raise ValueError("Run has no associated rallye_id")

    existing = await session.scalar(select(RunStation.id).where(RunStation.run_id == run.id).limit(1))
    if existing is not None:
        raise ValueError(f"Run {run.id} already has its snapshot")

    # Eager-load the whole composition: rallye_stations -> [station, challenge_links -> challenge]
    stmt = (
        select(Rallye)
        .where(Rallye.id == run.rallye_id)
        .where(Rallye.organization_id == organization_id)
        .where(Rallye.is_deleted == False)  # noqa: E712
        .options(
            selectinload(Rallye.rallye_stations).selectinload(RallyeStation.station),
            selectinload(Rallye.rallye_stations)
            .selectinload(RallyeStation.challenge_links)
            .selectinload(RallyeStationChallenge.challenge),
        )
    )

    result = await session.execute(stmt)
    rallye = result.scalar_one_or_none()

    if rallye is None:
        raise ValueError(f"Rallye {run.rallye_id} not found")

    if not rallye.rallye_stations:
        raise ValueError(f"Rallye {run.rallye_id} has no stations")

    run_station_repo = RunStationRepository(session, organization_id)
    run_challenge_repo = RunChallengeRepository(session, organization_id)

    for rallye_station in sorted(rallye.rallye_stations, key=lambda link: link.sort_order):
        station = rallye_station.station

        # Skip links whose station has been soft-deleted from the library.
        if station is None or station.is_deleted:
            continue

        run_station = await run_station_repo.create(
            run_id=run.id,
            source_station_id=station.id,
            title=station.title,
            description=station.description,
            location_lat=station.location_lat,
            location_lng=station.location_lng,
            address=station.address,
            title_image_key=station.title_image_key,
            tips=station.tips,
            sort_order=rallye_station.sort_order,
        )

        for rsc in sorted(rallye_station.challenge_links, key=lambda link: link.sort_order):
            challenge = rsc.challenge

            # Skip links whose challenge has been soft-deleted from the library.
            if challenge is None or challenge.is_deleted:
                continue

            await run_challenge_repo.create(
                run_id=run.id,
                run_station_id=run_station.id,
                source_challenge_id=challenge.id,
                challenge_type=challenge.challenge_type,
                title=challenge.title,
                instructions=challenge.instructions,
                scoring_rules=challenge.scoring_rules,
                sort_order=rsc.sort_order,
                time_estimate_minutes=challenge.time_estimate_minutes,
                suggested_solution=challenge.suggested_solution,
            )
