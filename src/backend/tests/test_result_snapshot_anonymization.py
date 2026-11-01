"""The LK_C sweep anonymizes the copies of the team names, not only teams.name.

Two copies outlive the rename:

- ``run_result_links.snapshot``: the public result page would keep answering with the
  old names until the link expired.
- ``feed_events.content`` of the system entries (help request): ``team_name``
  and the German/English sentences built from it.

The sweep is called directly with the bound test session and an injected
reference date, like ``test_retention_sweep.py``.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from app.models.feed_event import FeedEvent, FeedEventType
from app.models.run import Run, RunState
from app.models.run_result_link import RunResultLink
from app.models.team import Team
from app.models.user import UserRole
from app.services.gdpr import DeletionClass
from app.services.retention_tasks import sweep_run_anonymize
from tests.factories import create_test_organization, create_test_user

LK_C = DeletionClass.LK_C.retention_days

OLD_A = "Die Stadtfüchse"
OLD_B = "Rote Raben"


async def _due_run(db: AsyncSession, slug: str, *, days: int = LK_C + 1) -> tuple[Any, Run]:
    org = await create_test_organization(db, name=f"Org {slug}", slug=slug)
    run = Run(
        organization_id=org.id,
        name="Snapshot Run",
        state=RunState.ENDED,
        join_code=uuid.uuid4().hex[:6].upper(),
        ended_at=datetime.now(UTC) - timedelta(days=days),
    )
    db.add(run)
    await db.flush()
    return org, run


async def _team(db: AsyncSession, org: Any, run: Run, name: str | None) -> Team:
    team = Team(organization_id=org.id, run_id=run.id, share_code=uuid.uuid4().hex[:8].upper(), name=name)
    db.add(team)
    await db.flush()
    return team


def _snapshot(*names_and_points: tuple[str | None, int]) -> dict[str, Any]:
    entries = [
        {"team_name": name, "points": points, "rank": rank, "is_winner": rank == 1 and points > 0}
        for rank, (name, points) in enumerate(names_and_points, start=1)
    ]
    return {"run_name": "Snapshot Run", "ended_at": None, "language": "de", "entries": entries}


async def _link(
    db: AsyncSession,
    run: Run,
    snapshot: dict[str, Any],
    *,
    revoked: bool = False,
    expired: bool = False,
) -> RunResultLink:
    now = datetime.now(UTC)
    link = RunResultLink(
        id=uuid7(),
        run_id=run.id,
        slug_hash=uuid.uuid4().hex,
        snapshot=snapshot,
        published_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1) if expired else now + timedelta(days=30),
        revoked_at=now - timedelta(hours=1) if revoked else None,
    )
    db.add(link)
    await db.flush()
    return link


def _help_request(team: Team, name: str | None) -> dict[str, Any]:
    """Shape of the entry that routers/chat.py writes for a help request."""
    return {
        "message_key": "help_request",
        "team_id": str(team.id),
        "team_name": name,
        "de": f"Team {name} benötigt Hilfe." if name else "Ein Team benötigt Hilfe.",
        "en": f"Team {name} needs help." if name else "A team needs help.",
    }


async def _feed_event(
    db: AsyncSession,
    org: Any,
    run: Run,
    content: dict[str, Any],
    *,
    event_type: FeedEventType = FeedEventType.SYSTEM,
    author_id: uuid.UUID | None = None,
    target_team_id: uuid.UUID | None = None,
) -> FeedEvent:
    event = FeedEvent(
        organization_id=org.id,
        run_id=run.id,
        event_type=event_type,
        content=content,
        author_id=author_id,
        target_team_id=target_team_id,
    )
    db.add(event)
    await db.flush()
    return event


async def _reload(db: AsyncSession, *objs: Any) -> None:
    for obj in objs:
        await db.refresh(obj)


@pytest.mark.asyncio
async def test_snapshots_of_every_link_state_get_the_names_the_teams_got(db_session: AsyncSession):
    """Live, revoked and expired links; points and ranks stay."""
    org, run = await _due_run(db_session, "snap-anon-all")
    team_a = await _team(db_session, org, run, OLD_A)
    team_b = await _team(db_session, org, run, OLD_B)
    # Ranked by points, not by creation: the ranking order differs from the team order.
    snapshot = _snapshot((OLD_B, 20), (OLD_A, 10))
    live = await _link(db_session, run, snapshot)
    revoked = await _link(db_session, run, snapshot, revoked=True)
    # At most one link per run is unrevoked; the expired one is a withdrawn link past its date.
    expired = await _link(db_session, run, snapshot, revoked=True, expired=True)

    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await _reload(db_session, team_a, team_b, live, revoked, expired)

    assert {team_a.name, team_b.name} == {"Team 1", "Team 2"}
    for link in (live, revoked, expired):
        entries = link.snapshot["entries"]
        assert [e["team_name"] for e in entries] == [team_b.name, team_a.name]
        assert [(e["points"], e["rank"], e["is_winner"]) for e in entries] == [(20, 1, True), (10, 2, False)]
        assert link.snapshot["run_name"] == "Snapshot Run"
        assert OLD_A not in str(link.snapshot)
        assert OLD_B not in str(link.snapshot)


@pytest.mark.asyncio
async def test_a_name_that_is_no_team_any_more_does_not_survive(db_session: AsyncSession):
    """A team renamed after publishing leaves a name no team carries; it is replaced too."""
    org, run = await _due_run(db_session, "snap-anon-stale")
    await _team(db_session, org, run, "Neuer Name")
    link = await _link(db_session, run, _snapshot(("Alter Name", 5), (None, 0)))

    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await _reload(db_session, link)

    names = [e["team_name"] for e in link.snapshot["entries"]]
    assert "Alter Name" not in names
    assert names[0] is not None
    assert names[1] is None  # a name-pending team has no name to remove


@pytest.mark.asyncio
async def test_snapshot_of_a_run_inside_the_window_is_kept(db_session: AsyncSession):
    org, run = await _due_run(db_session, "snap-anon-keep", days=LK_C - 1)
    await _team(db_session, org, run, OLD_A)
    link = await _link(db_session, run, _snapshot((OLD_A, 10)))

    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await _reload(db_session, link)

    assert link.snapshot["entries"][0]["team_name"] == OLD_A


@pytest.mark.asyncio
async def test_snapshot_anonymization_is_idempotent(db_session: AsyncSession):
    org, run = await _due_run(db_session, "snap-anon-idem")
    team = await _team(db_session, org, run, OLD_A)
    link = await _link(db_session, run, _snapshot((OLD_A, 10)))
    now = datetime.now(UTC)

    await sweep_run_anonymize(db_session, reference_date=now)
    await sweep_run_anonymize(db_session, reference_date=now)
    await _reload(db_session, team, link)

    assert team.name == "Team 1"
    assert link.snapshot["entries"][0]["team_name"] == "Team 1"


@pytest.mark.asyncio
async def test_help_request_entry_gets_the_new_name_and_keeps_its_shape(db_session: AsyncSession):
    """team_name and both sentences change; kind, time and team id stay."""
    org, run = await _due_run(db_session, "feed-anon-help")
    team = await _team(db_session, org, run, OLD_A)
    event = await _feed_event(db_session, org, run, _help_request(team, OLD_A), target_team_id=team.id)
    created_at = event.created_at

    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await _reload(db_session, team, event)

    assert team.name == "Team 1"
    assert event.content == {
        "message_key": "help_request",
        "team_id": str(team.id),
        "team_name": "Team 1",
        "de": "Team Team 1 benötigt Hilfe.",
        "en": "Team Team 1 needs help.",
    }
    assert OLD_A not in str(event.content)
    assert event.event_type == FeedEventType.SYSTEM
    assert event.created_at == created_at
    assert event.target_team_id == team.id
    assert event.author_id is None


@pytest.mark.asyncio
async def test_entry_of_a_name_pending_team_is_left_as_it_is(db_session: AsyncSession):
    org, run = await _due_run(db_session, "feed-anon-pending")
    team = await _team(db_session, org, run, None)
    content = _help_request(team, None)
    event = await _feed_event(db_session, org, run, content, target_team_id=team.id)

    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await _reload(db_session, event)

    assert event.content == content


@pytest.mark.asyncio
async def test_entries_a_person_wrote_are_not_changed(db_session: AsyncSession):
    """An announcement, even one that mentions a team and carries a team_name key."""
    org, run = await _due_run(db_session, "feed-anon-person")
    team = await _team(db_session, org, run, OLD_A)
    coordinator = await create_test_user(
        db_session,
        email=f"coord-{uuid.uuid4().hex[:8]}@snap.example.com",
        role=UserRole.COORDINATOR,
        organization=org,
    )
    written = {"de": f"Glückwunsch {OLD_A}!", "en": f"Congratulations {OLD_A}!", "team_name": OLD_A}
    announcement = await _feed_event(
        db_session,
        org,
        run,
        written,
        event_type=FeedEventType.ANNOUNCEMENT,
        author_id=coordinator.id,
    )
    other_system = await _feed_event(
        db_session,
        org,
        run,
        {"de": "Quiz automatisch bewertet: 3/5 Punkte", "en": "Quiz auto-scored: 3/5 points"},
        target_team_id=team.id,
    )

    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await _reload(db_session, announcement, other_system)

    assert announcement.content == written
    assert other_system.content == {"de": "Quiz automatisch bewertet: 3/5 Punkte", "en": "Quiz auto-scored: 3/5 points"}


@pytest.mark.asyncio
async def test_entries_of_a_run_inside_the_window_are_kept(db_session: AsyncSession):
    org, run = await _due_run(db_session, "feed-anon-keep", days=LK_C - 1)
    team = await _team(db_session, org, run, OLD_A)
    content = _help_request(team, OLD_A)
    event = await _feed_event(db_session, org, run, content, target_team_id=team.id)

    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await _reload(db_session, event)

    assert event.content == content


@pytest.mark.asyncio
async def test_feed_anonymization_is_idempotent(db_session: AsyncSession):
    org, run = await _due_run(db_session, "feed-anon-idem")
    team = await _team(db_session, org, run, OLD_A)
    await _feed_event(db_session, org, run, _help_request(team, OLD_A), target_team_id=team.id)
    now = datetime.now(UTC)

    await sweep_run_anonymize(db_session, reference_date=now)
    await sweep_run_anonymize(db_session, reference_date=now)

    events = (await db_session.execute(select(FeedEvent).where(FeedEvent.run_id == run.id))).scalars().all()
    assert len(events) == 1
    await db_session.refresh(events[0])
    assert events[0].content["team_name"] == "Team 1"
    assert events[0].content["de"] == "Team Team 1 benötigt Hilfe."
