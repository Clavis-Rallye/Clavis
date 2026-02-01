"""
Tests for the snapshot service (create_run_snapshot).

- a deep copy of stations and challenges, with their fields, source references and sort order
- the copy stays unchanged when the template is edited afterwards
- a running team keeps its challenge type when the library challenge changes type
- errors: no rallye_id, rallye not found in the organization, no stations, soft-deleted rallye
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.run import RunRepository
from app.repositories.station import StationRepository
from app.services.scoring_rules import prune_scoring_rules_for_type_change
from app.services.snapshot import create_run_snapshot
from tests.factories import create_test_organization


@pytest.mark.asyncio
async def test_create_run_snapshot_deep_copies_stations_and_challenges(db_session: AsyncSession) -> None:
    """The snapshot copies every station and challenge with its fields, source ids and sort order."""
    org = await create_test_organization(db_session, name="Test Org", slug="test-org")

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye", "en": "Test Rally"},
        description={"de": "Beschreibung", "en": "Description"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    challenge_repo = ChallengeRepository(db_session, org.id)

    station1 = await station_repo.create(
        title={"de": "Station 1", "en": "Station 1"},
        description={"de": "Erste Station", "en": "First station"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station1 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station1.id, sort_order=0)
    db_session.add(rs_station1)
    await db_session.flush()

    challenge1_1 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 1.1", "en": "Challenge 1.1"},
        instructions={"de": "Antworten Sie", "en": "Answer this"},
        scoring_rules={"max_points": 10, "criteria": "accuracy"},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station1.id, challenge_id=challenge1_1.id, sort_order=0
        )
    )
    await db_session.flush()
    challenge1_2 = await challenge_repo.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Challenge 1.2"},
        instructions={"de": "Foto machen"},
        scoring_rules={"max_points": 5},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station1.id, challenge_id=challenge1_2.id, sort_order=1
        )
    )
    await db_session.flush()
    challenge1_3 = await challenge_repo.create(
        challenge_type=ChallengeType.QUIZ,
        title={"de": "Challenge 1.3"},
        instructions={"de": "Quiz lösen"},
        scoring_rules={"max_points": 15},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station1.id, challenge_id=challenge1_3.id, sort_order=2
        )
    )
    await db_session.flush()

    station2 = await station_repo.create(
        title={"de": "Station 2", "en": "Station 2"},
        description={"de": "Zweite Station"},
        location_lat=48.3680,
        location_lng=10.8950,
    )
    rs_station2 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station2.id, sort_order=1)
    db_session.add(rs_station2)
    await db_session.flush()

    challenge2_1 = await challenge_repo.create(
        challenge_type=ChallengeType.CREATIVE,
        title={"de": "Challenge 2.1"},
        instructions={"de": "Video aufnehmen"},
        scoring_rules={"max_points": 20},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station2.id, challenge_id=challenge2_1.id, sort_order=0
        )
    )
    await db_session.flush()
    challenge2_2 = await challenge_repo.create(
        challenge_type=ChallengeType.QR_SCAN,
        title={"de": "Challenge 2.2"},
        instructions={"de": "QR-Code scannen"},
        scoring_rules={"max_points": 8},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station2.id, challenge_id=challenge2_2.id, sort_order=1
        )
    )
    await db_session.flush()
    challenge2_3 = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge 2.3"},
        instructions={"de": "Beschreiben Sie"},
        scoring_rules={"max_points": 12},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station2.id, challenge_id=challenge2_3.id, sort_order=2
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_repo = RunRepository(db_session, org.id)
    run = await run_repo.create(
        rallye_id=rallye.id,
        name="Test Run",
        state=RunState.CONFIGURED,
        join_code="TEST01",
    )
    await db_session.commit()

    await create_run_snapshot(db_session, run, org.id)
    await db_session.commit()

    stmt = select(RunStation).where(RunStation.run_id == run.id)
    result = await db_session.execute(stmt)
    run_stations = result.scalars().all()
    assert len(run_stations) == 2, "Expected 2 run_stations"

    stmt_challenges = select(RunChallenge).where(RunChallenge.run_id == run.id)
    result_challenges = await db_session.execute(stmt_challenges)
    run_challenges = result_challenges.scalars().all()
    assert len(run_challenges) == 6, "Expected 6 run_challenges"

    # Sort stations and challenges for deterministic verification
    run_stations_sorted = sorted(run_stations, key=lambda s: s.sort_order)
    run_challenges_sorted = sorted(run_challenges, key=lambda c: (c.run_station_id, c.sort_order))

    rs1 = run_stations_sorted[0]
    assert rs1.source_station_id == station1.id
    assert rs1.title == {"de": "Station 1", "en": "Station 1"}
    assert rs1.description == {"de": "Erste Station", "en": "First station"}
    assert rs1.location_lat == 48.3705
    assert rs1.location_lng == 10.8978
    assert rs1.sort_order == 0
    assert rs1.organization_id == org.id
    assert rs1.run_id == run.id

    rs2 = run_stations_sorted[1]
    assert rs2.source_station_id == station2.id
    assert rs2.title == {"de": "Station 2", "en": "Station 2"}
    assert rs2.description == {"de": "Zweite Station"}
    assert rs2.location_lat == 48.3680
    assert rs2.location_lng == 10.8950
    assert rs2.sort_order == 1

    station1_challenges = [c for c in run_challenges_sorted if c.run_station_id == rs1.id]
    assert len(station1_challenges) == 3

    rc1_1 = station1_challenges[0]
    assert rc1_1.source_challenge_id == challenge1_1.id
    assert rc1_1.challenge_type == ChallengeType.TEXT
    assert rc1_1.title == {"de": "Challenge 1.1", "en": "Challenge 1.1"}
    assert rc1_1.instructions == {"de": "Antworten Sie", "en": "Answer this"}
    assert rc1_1.scoring_rules == {"max_points": 10, "criteria": "accuracy"}
    assert rc1_1.sort_order == 0
    assert rc1_1.organization_id == org.id
    assert rc1_1.run_id == run.id

    rc1_2 = station1_challenges[1]
    assert rc1_2.source_challenge_id == challenge1_2.id
    assert rc1_2.challenge_type == ChallengeType.PHOTO
    assert rc1_2.sort_order == 1

    rc1_3 = station1_challenges[2]
    assert rc1_3.source_challenge_id == challenge1_3.id
    assert rc1_3.challenge_type == ChallengeType.QUIZ
    assert rc1_3.sort_order == 2

    station2_challenges = [c for c in run_challenges_sorted if c.run_station_id == rs2.id]
    assert len(station2_challenges) == 3

    rc2_1 = station2_challenges[0]
    assert rc2_1.source_challenge_id == challenge2_1.id
    assert rc2_1.challenge_type == ChallengeType.CREATIVE
    assert rc2_1.sort_order == 0

    rc2_2 = station2_challenges[1]
    assert rc2_2.source_challenge_id == challenge2_2.id
    assert rc2_2.challenge_type == ChallengeType.QR_SCAN
    assert rc2_2.sort_order == 1

    rc2_3 = station2_challenges[2]
    assert rc2_3.source_challenge_id == challenge2_3.id
    assert rc2_3.challenge_type == ChallengeType.TEXT
    assert rc2_3.sort_order == 2


@pytest.mark.asyncio
async def test_snapshot_immutability_editing_template_does_not_affect_run(db_session: AsyncSession) -> None:
    """Editing the template after the snapshot does not change the run's copy."""
    org = await create_test_organization(db_session, name="Test Org", slug="test-org")

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Original Rallye"},
        description={"de": "Original"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Original Station"},
        description={"de": "Original Station Description"},
        location_lat=48.0,
        location_lng=10.0,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    challenge = await challenge_repo.create(
        challenge_type=ChallengeType.TEXT,
        title={"de": "Original Challenge"},
        instructions={"de": "Original Instructions"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=challenge.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_repo = RunRepository(db_session, org.id)
    run = await run_repo.create(
        rallye_id=rallye.id,
        name="Test Run",
        state=RunState.CONFIGURED,
        join_code="TEST02",
    )
    await db_session.commit()

    await create_run_snapshot(db_session, run, org.id)
    await db_session.commit()

    stmt = select(RunStation).where(RunStation.run_id == run.id)
    result = await db_session.execute(stmt)
    run_station_before = result.scalar_one()

    stmt_challenge = select(RunChallenge).where(RunChallenge.run_id == run.id)
    result_challenge = await db_session.execute(stmt_challenge)
    run_challenge_before = result_challenge.scalar_one()

    assert run_station_before.title == {"de": "Original Station"}
    assert run_station_before.description == {"de": "Original Station Description"}
    assert run_station_before.location_lat == 48.0
    assert run_station_before.location_lng == 10.0

    assert run_challenge_before.title == {"de": "Original Challenge"}
    assert run_challenge_before.instructions == {"de": "Original Instructions"}
    assert run_challenge_before.scoring_rules == {"max_points": 10}

    await station_repo.update(
        station.id,
        title={"de": "Modified Station", "en": "Modified Station"},
        description={"de": "Modified Description"},
        location_lat=49.0,
        location_lng=11.0,
    )

    await challenge_repo.update(
        challenge.id,
        title={"de": "Modified Challenge"},
        instructions={"de": "Modified Instructions"},
        scoring_rules={"max_points": 20, "bonus": 5},
    )

    await db_session.commit()

    await db_session.refresh(run_station_before)
    await db_session.refresh(run_challenge_before)

    assert run_station_before.title == {"de": "Original Station"}
    assert run_station_before.description == {"de": "Original Station Description"}
    assert run_station_before.location_lat == 48.0
    assert run_station_before.location_lng == 10.0

    assert run_challenge_before.title == {"de": "Original Challenge"}
    assert run_challenge_before.instructions == {"de": "Original Instructions"}
    assert run_challenge_before.scoring_rules == {"max_points": 10}


@pytest.mark.asyncio
async def test_snapshot_keeps_the_challenge_type_after_the_library_challenge_changes_type(
    db_session: AsyncSession,
) -> None:
    """A running team keeps the type it started with when the library challenge changes type.

    The library challenge changes from QUIZ to QR_SCAN, the way the update
    endpoint does it: a new type plus the rules a type change leaves. The
    `run_challenges` row must still be the QUIZ the run was configured with,
    with its quiz scoring rules.
    """
    org = await create_test_organization(db_session, name="Test Org", slug="test-org")

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Rallye"},
        description={"de": "Beschreibung"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station"},
        description={"de": "Stationsbeschreibung"},
        location_lat=48.3705,
        location_lng=10.8978,
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    quiz_rules = {"max_points": 15, "auto_approve": True, "options": ["A", "B"], "correct_option": "A"}
    challenge_repo = ChallengeRepository(db_session, org.id)
    challenge = await challenge_repo.create(
        challenge_type=ChallengeType.QUIZ,
        title={"de": "Quizfrage"},
        instructions={"de": "Waehle A oder B"},
        scoring_rules=dict(quiz_rules),
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=challenge.id, sort_order=0
        )
    )
    await db_session.flush()
    await db_session.commit()

    run_repo = RunRepository(db_session, org.id)
    run = await run_repo.create(
        rallye_id=rallye.id,
        name="Test Run",
        state=RunState.CONFIGURED,
        join_code="TEST03",
    )
    await db_session.commit()

    await create_run_snapshot(db_session, run, org.id)
    await db_session.commit()

    result = await db_session.execute(select(RunChallenge).where(RunChallenge.run_id == run.id))
    run_challenge = result.scalar_one()
    assert run_challenge.challenge_type == ChallengeType.QUIZ

    # Change the library challenge's type, with the rules a type change leaves.
    await challenge_repo.update(
        challenge.id,
        challenge_type=ChallengeType.QR_SCAN,
        scoring_rules=prune_scoring_rules_for_type_change(ChallengeType.QUIZ, ChallengeType.QR_SCAN, dict(quiz_rules)),
    )
    await db_session.commit()
    await db_session.refresh(run_challenge)

    assert run_challenge.challenge_type == ChallengeType.QUIZ
    assert run_challenge.scoring_rules == quiz_rules
    assert run_challenge.instructions == {"de": "Waehle A oder B"}


@pytest.mark.asyncio
async def test_create_run_snapshot_raises_error_if_no_rallye_id(db_session: AsyncSession) -> None:
    """A run without rallye_id cannot be snapshotted."""
    org = await create_test_organization(db_session, name="Test Org", slug="test-org")

    # Built by hand, because the repository refuses a run without rallye_id.
    run = Run(
        organization_id=org.id,
        rallye_id=None,
        name="Invalid Run",
        state=RunState.CONFIGURED,
        join_code="TEST03",
    )
    db_session.add(run)
    await db_session.commit()

    with pytest.raises(ValueError, match="no associated rallye_id"):
        await create_run_snapshot(db_session, run, org.id)


@pytest.mark.asyncio
async def test_create_run_snapshot_raises_error_if_rallye_not_found(db_session: AsyncSession) -> None:
    """A snapshot in the context of another organization does not find the rallye."""
    org1 = await create_test_organization(db_session, name="Org 1", slug="org-1")
    org2 = await create_test_organization(db_session, name="Org 2", slug="org-2")

    rallye_repo1 = RallyeRepository(db_session, org1.id)
    rallye1 = await rallye_repo1.create(
        title={"de": "Rallye in Org 1"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo1 = StationRepository(db_session, org1.id)
    _station_1 = await station_repo1.create(
        title={"de": "Station 1"},
        description={"de": "Test"},
    )
    rs__station_1 = RallyeStation(organization_id=org1.id, rallye_id=rallye1.id, station_id=_station_1.id, sort_order=0)
    db_session.add(rs__station_1)
    await db_session.flush()
    await db_session.commit()

    # The run of org2 points to the rallye of org1. The foreign key accepts it, because the rallye exists.
    run_repo2 = RunRepository(db_session, org2.id)
    run2 = await run_repo2.create(
        rallye_id=rallye1.id,
        name="Run in Org 2",
        state=RunState.CONFIGURED,
        join_code="TEST04",
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="not found"):
        await create_run_snapshot(db_session, run2, org2.id)


@pytest.mark.asyncio
async def test_create_run_snapshot_raises_error_if_rallye_has_no_stations(db_session: AsyncSession) -> None:
    """A rallye without stations cannot be snapshotted."""
    org = await create_test_organization(db_session, name="Test Org", slug="test-org")

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Empty Rallye"},
        description={"de": "No stations"},
        is_published=True,
    )
    await db_session.commit()

    run_repo = RunRepository(db_session, org.id)
    run = await run_repo.create(
        rallye_id=rallye.id,
        name="Run with empty rallye",
        state=RunState.CONFIGURED,
        join_code="TEST05",
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="has no stations"):
        await create_run_snapshot(db_session, run, org.id)


@pytest.mark.asyncio
async def test_create_run_snapshot_respects_soft_delete(db_session: AsyncSession) -> None:
    """A soft-deleted rallye is not loaded by the snapshot."""
    org = await create_test_organization(db_session, name="Test Org", slug="test-org")

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "To Be Deleted"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    _station_2 = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Test"},
    )
    rs__station_2 = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=_station_2.id, sort_order=0)
    db_session.add(rs__station_2)
    await db_session.flush()
    await db_session.commit()

    run_repo = RunRepository(db_session, org.id)
    run = await run_repo.create(
        rallye_id=rallye.id,
        name="Test Run",
        state=RunState.CONFIGURED,
        join_code="TEST06",
    )
    await db_session.commit()

    await rallye_repo.soft_delete(rallye.id)
    await db_session.commit()

    with pytest.raises(ValueError, match="not found"):
        await create_run_snapshot(db_session, run, org.id)
