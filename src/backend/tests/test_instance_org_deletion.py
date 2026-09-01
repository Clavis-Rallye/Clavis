"""
Scheduled deletion of an archived organization, in the order of the sections below.

- ``POST /{slug}/deletion`` schedules 30 days ahead, audits ``instance.org_deletion_requested``,
  and the list shows the due date. It refuses an active organization and a confirmation mismatch
  with a code and changes nothing. It also refuses an organization restored between the lookup
  and the row lock.
- Restore clears a pending schedule and audits ``instance.org_deletion_cancelled``. A hard delete
  between the lookup and the row lock answers 404, not 500.
- The sweep deletes nothing before the due date. After it, every row and every referenced object
  is gone, a legacy ``uploads/...`` key included, and a second organization keeps everything.
  ``instance.org_deleted`` carries the slug and the counts.
- The audited row count is exact. It includes the organization row and the two tables reached
  only transitively (``run_result_links``, ``password_reset_tokens``).
- One organization's failure never undoes another one already committed and audited in the same
  pass. The failure can be a row delete, a commit, or a restore after the due set was read.
  A row-delete failure leaves every object in place.
- Legacy keys never cross organizations. A key under another organization's prefix, or one that
  organization still references, survives. A referenced legacy key missing from storage is not
  counted as deleted.
- The delete order matches the cascade graph of ``Base.metadata``, and the
  ``deletion_requires_archived`` CHECK constraint is the database-level backstop.

Media tests touch the real object store, set up as in ``test_retention_sweep.py``.
"""

import inspect
import io
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import structlog
from httpx import AsyncClient
from minio.error import S3Error
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import settings
from app.database import Base
from app.logging import configure_logging
from app.models.challenge import Challenge, ChallengeType
from app.models.chat_message import ChatMessage
from app.models.feed_event import FeedEvent, FeedEventType
from app.models.feedback import Feedback
from app.models.organization import Organization
from app.models.rallye import Rallye
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_departure import RunDeparture
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.station import Station
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.models.user_invitation import UserInvitation
from app.repositories.invitation import InvitationRepository
from app.repositories.organization import OrganizationRepository
from app.services.audit import reset_integrity_chain
from app.services.org_deletion import DELETION_GRACE_DAYS, sweep_due_organizations
from app.storage import ensure_bucket, get_minio_client
from tests.factories import create_test_organization, create_test_user

OPERATOR_EMAIL = "deletion-operator@example.com"
OPERATOR_PASSWORD = "OperatorPass123!"

PNG = b"\x89PNG\r\n\x1a\n" + b"clavis-deletion-test"


@pytest.fixture(autouse=True)
def _ensure_bucket() -> None:
    ensure_bucket()


@pytest.fixture
def configured_logging():
    """Audit logging into a temp dir (same shape as test_retention_sweep.py)."""
    import logging as _logging

    import app.logging as _log_module

    with tempfile.TemporaryDirectory() as tmpdir:
        structlog.reset_defaults()
        _log_module._logging_configured = False
        configure_logging(environment="development", log_dir=tmpdir)
        reset_integrity_chain()

        yield tmpdir

        import contextlib

        for name in ("audit", "app", "error", "access", ""):
            lg = _logging.getLogger(name)
            for h in list(lg.handlers):
                with contextlib.suppress(Exception):
                    h.close()
            lg.handlers.clear()
        _log_module._logging_configured = False
        structlog.reset_defaults()


def _code(n: int = 6) -> str:
    return uuid.uuid4().hex[:n].upper()


def _put_object(key: str, data: bytes = PNG, content_type: str = "image/png") -> str:
    """Upload under an explicit key, a shape ``upload_file`` cannot produce: it always writes ``uploads/{date}/…``."""
    get_minio_client().put_object(
        settings.storage.bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return key


def _object_exists(key: str) -> bool:
    try:
        get_minio_client().stat_object(settings.storage.bucket, key)
        return True
    except S3Error:
        return False


async def _operator(db_session: AsyncSession) -> dict[str, str]:
    from app.auth.instance import create_instance_admin
    from app.auth.tokens import create_instance_admin_token

    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    return {"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"}


class _AuditRecorder:
    """Spy that stands in for record_audit_event in the router/service modules."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **kwargs: object) -> None:
        self.events.append({"event": event, **kwargs})

    def named(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]


async def _archive(db_session: AsyncSession, org: Organization) -> None:
    await OrganizationRepository(db_session).update(org.id, archived_at=datetime.now(UTC))
    await db_session.commit()


async def _build_full_org_tree(db_session: AsyncSession, slug: str) -> dict[str, object]:
    """One organization with a row in every table the sweep must clear, and five storage objects.

    Four sit under its own prefix: a station image, a frozen run-station snapshot, a participant
    medium and an organization medium. The fifth is a legacy ``uploads/…`` key that only a
    submission's content references.
    """
    org = await create_test_organization(db_session, name=f"Voll {slug}", slug=slug)
    creator = await create_test_user(
        db_session, email=f"creator-{slug}@example.com", role=UserRole.CREATOR, organization=org
    )
    guest = await create_test_user(db_session, email=f"guest-{slug}@example.com", role=UserRole.GUEST, organization=org)

    station_image_key = _put_object(f"{org.id}/stations/{uuid.uuid4()}/bild.png")
    station = Station(
        organization_id=org.id,
        title={"de": "Station"},
        description={},
        created_by_user_id=creator.id,
        title_image_key=station_image_key,
    )
    db_session.add(station)
    await db_session.flush()

    challenge = Challenge(
        organization_id=org.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge"},
        instructions={"de": "Mach es"},
        scoring_rules={"max_points": 10},
        created_by_user_id=creator.id,
    )
    db_session.add(challenge)
    await db_session.flush()

    rallye = Rallye(organization_id=org.id, title={"de": "Rallye"}, description={}, created_by_user_id=creator.id)
    db_session.add(rallye)
    await db_session.flush()

    rallye_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rallye_station)
    await db_session.flush()

    rallye_station_challenge = RallyeStationChallenge(
        organization_id=org.id, rallye_station_id=rallye_station.id, challenge_id=challenge.id, sort_order=0
    )
    db_session.add(rallye_station_challenge)
    await db_session.flush()

    run = Run(
        organization_id=org.id,
        rallye_id=rallye.id,
        name="Run",
        state=RunState.ENDED,
        join_code=_code(),
        started_at=datetime.now(UTC) - timedelta(days=1),
        ended_at=datetime.now(UTC),
        settings={},
        content_languages=["de"],
    )
    db_session.add(run)
    await db_session.flush()

    run_station_image_key = _put_object(f"{org.id}/stations/{station.id}/snapshot.png")
    run_station = RunStation(
        organization_id=org.id,
        run_id=run.id,
        source_station_id=station.id,
        title={"de": "Station"},
        description={},
        sort_order=0,
        title_image_key=run_station_image_key,
    )
    db_session.add(run_station)
    await db_session.flush()

    run_challenge = RunChallenge(
        organization_id=org.id,
        run_id=run.id,
        run_station_id=run_station.id,
        source_challenge_id=challenge.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge"},
        instructions={"de": "Mach es"},
        scoring_rules={"max_points": 10},
        sort_order=0,
    )
    db_session.add(run_challenge)
    await db_session.flush()

    team = Team(organization_id=org.id, run_id=run.id, name="Team", share_code=_code(8))
    db_session.add(team)
    await db_session.flush()

    team_member = TeamMember(
        organization_id=org.id, team_id=team.id, user_id=guest.id, display_name="Guest", is_guest=True
    )
    db_session.add(team_member)
    await db_session.flush()

    chat = ChatMessage(organization_id=org.id, team_id=team.id, content="hallo", is_system_message=False)
    db_session.add(chat)
    await db_session.flush()

    legacy_key = _put_object(f"uploads/2024-01-01/{uuid.uuid4().hex}_alt.png")
    participant_media_key = _put_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")

    submission = Submission(
        organization_id=org.id,
        run_id=run.id,
        team_id=team.id,
        run_challenge_id=run_challenge.id,
        content={"photo_url": legacy_key, "text": "Antwort"},
    )
    db_session.add(submission)
    await db_session.flush()

    score = Score(organization_id=org.id, submission_id=submission.id, points=8, max_points=10, scored_by=creator.id)
    db_session.add(score)
    await db_session.flush()

    feed_event = FeedEvent(
        organization_id=org.id, run_id=run.id, event_type=FeedEventType.ANNOUNCEMENT, content={"de": "Pause"}
    )
    db_session.add(feed_event)
    await db_session.flush()

    feedback = Feedback(organization_id=org.id, run_id=run.id, rating=5)
    db_session.add(feedback)
    await db_session.flush()

    run_departure = RunDeparture(organization_id=org.id, run_id=run.id, user_id=guest.id)
    db_session.add(run_departure)
    await db_session.flush()

    org_media_key = _put_object(f"{org.id}/media/{uuid.uuid4().hex}_logo.png")

    _invitation, _raw_token = await InvitationRepository(db_session).create(
        organization_id=org.id,
        email=f"invited-{slug}@example.com",
        role=UserRole.PARTICIPANT,
        invited_by_user_id=None,
        invited_by_name="Instance",
        expiry_days=7,
    )

    await db_session.commit()

    return {
        "org": org,
        "creator_id": creator.id,
        "guest_id": guest.id,
        "station_id": station.id,
        "challenge_id": challenge.id,
        "rallye_id": rallye.id,
        "run_id": run.id,
        "team_id": team.id,
        "submission_id": submission.id,
        "score_id": score.id,
        "legacy_key": legacy_key,
        "station_image_key": station_image_key,
        "run_station_image_key": run_station_image_key,
        "participant_media_key": participant_media_key,
        "org_media_key": org_media_key,
    }


async def _row_count(db_session: AsyncSession, model, organization_id) -> int:  # noqa: ANN001
    result = await db_session.execute(
        select(func.count()).select_from(model).where(model.organization_id == organization_id)
    )
    return result.scalar_one()


async def _minimal_submission(db_session: AsyncSession, org: Organization, *, content: dict) -> Submission:
    """The smallest row chain a submission needs, to attach a legacy or cross-org media key without the full tree."""
    run = Run(
        organization_id=org.id,
        name="Run",
        state=RunState.ENDED,
        join_code=_code(),
        settings={},
        content_languages=["de"],
    )
    db_session.add(run)
    await db_session.flush()

    team = Team(organization_id=org.id, run_id=run.id, name="Team", share_code=_code(8))
    db_session.add(team)
    await db_session.flush()

    run_station = RunStation(organization_id=org.id, run_id=run.id, title={"de": "S"}, description={}, sort_order=0)
    db_session.add(run_station)
    await db_session.flush()

    run_challenge = RunChallenge(
        organization_id=org.id,
        run_id=run.id,
        run_station_id=run_station.id,
        challenge_type=ChallengeType.TEXT,
        title={"de": "C"},
        instructions={"de": "..."},
        scoring_rules={"max_points": 10},
        sort_order=0,
    )
    db_session.add(run_challenge)
    await db_session.flush()

    submission = Submission(
        organization_id=org.id, run_id=run.id, team_id=team.id, run_challenge_id=run_challenge.id, content=content
    )
    db_session.add(submission)
    await db_session.flush()
    return submission


async def _second_session(test_engine: AsyncEngine) -> AsyncSession:
    """An independent session on the test database, acting as a concurrent request or sweep pass."""
    return async_sessionmaker(bind=test_engine, expire_on_commit=False)()


# ============================================================================
# Requesting deletion
# ============================================================================


@pytest.mark.asyncio
async def test_deletion_request_schedules_and_audits(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """An archived organization, confirmed by slug, is scheduled 30 days out."""
    from app.routers import instance as instance_module

    headers = await _operator(db_session)
    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)

    org = await create_test_organization(db_session, name="Loeschbar", slug="del-schedule")
    await _archive(db_session, org)

    before = datetime.now(UTC)
    response = await client.post(
        f"/api/instance/organizations/{org.slug}/deletion",
        headers=headers,
        json={"confirm_slug": "del-schedule"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "del-schedule"
    due_at = datetime.fromisoformat(body["deletion_due_at"].replace("Z", "+00:00"))
    assert (
        before + timedelta(days=DELETION_GRACE_DAYS)
        <= due_at
        <= datetime.now(UTC) + timedelta(days=DELETION_GRACE_DAYS)
    )

    requested = recorder.named("instance.org_deletion_requested")
    assert len(requested) == 1
    assert requested[0]["organization_id"] is None
    assert requested[0]["target_slug"] == "del-schedule"

    # The list shows the same due date.
    listing = await client.get("/api/instance/organizations", headers=headers)
    item = next(i for i in listing.json()["items"] if i["slug"] == "del-schedule")
    assert item["deletion_due_at"] == body["deletion_due_at"]


@pytest.mark.asyncio
async def test_deletion_request_on_active_org_answers_409(client: AsyncClient, db_session: AsyncSession):
    """An active organization cannot be scheduled for deletion."""
    headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Aktiv", slug="del-active")

    response = await client.post(
        f"/api/instance/organizations/{org.slug}/deletion",
        headers=headers,
        json={"confirm_slug": "del-active"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "ORG_NOT_ARCHIVED"

    await db_session.refresh(org)
    assert org.deletion_requested_at is None


@pytest.mark.asyncio
async def test_deletion_request_confirmation_mismatch_answers_422(client: AsyncClient, db_session: AsyncSession):
    """A confirm_slug that doesn't match the organization's own slug refuses."""
    headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Falsch", slug="del-mismatch")
    await _archive(db_session, org)

    response = await client.post(
        f"/api/instance/organizations/{org.slug}/deletion",
        headers=headers,
        json={"confirm_slug": "not-the-slug"},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "CONFIRMATION_MISMATCH"

    await db_session.refresh(org)
    assert org.deletion_requested_at is None


@pytest.mark.asyncio
async def test_deletion_request_refuses_organization_restored_in_between(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch
):
    """A restore that a second session commits between the lookup and the locked re-check is refused."""
    from app.routers import instance as instance_module

    headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Zwischenzeitlich", slug="del-concurrent-restore")
    await _archive(db_session, org)

    real_get_org_by_slug = instance_module._get_org_by_slug

    async def _get_org_by_slug_then_concurrent_restore(repo, slug):  # noqa: ANN001, ANN202
        found = await real_get_org_by_slug(repo, slug)
        second_session = await _second_session(test_engine)
        async with second_session:
            await OrganizationRepository(second_session).update(found.id, archived_at=None, deletion_requested_at=None)
            await second_session.commit()
        return found

    monkeypatch.setattr(instance_module, "_get_org_by_slug", _get_org_by_slug_then_concurrent_restore)

    response = await client.post(
        f"/api/instance/organizations/{org.slug}/deletion",
        headers=headers,
        json={"confirm_slug": "del-concurrent-restore"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "ORG_NOT_ARCHIVED"

    remaining = await db_session.execute(select(Organization.deletion_requested_at).where(Organization.id == org.id))
    assert remaining.scalar_one() is None


# ============================================================================
# Restore cancels a scheduled deletion
# ============================================================================


@pytest.mark.asyncio
async def test_restore_cancels_scheduled_deletion(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """Restoring before the due date cancels the schedule and audits it."""
    from app.routers import instance as instance_module

    headers = await _operator(db_session)
    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)

    org = await create_test_organization(db_session, name="Widerruf", slug="del-cancel")
    await _archive(db_session, org)
    await client.post(
        f"/api/instance/organizations/{org.slug}/deletion",
        headers=headers,
        json={"confirm_slug": "del-cancel"},
    )
    recorder.events.clear()

    response = await client.post(f"/api/instance/organizations/{org.slug}/restore", headers=headers)
    assert response.status_code == 200
    assert response.json()["is_archived"] is False

    await db_session.refresh(org)
    assert org.archived_at is None
    assert org.deletion_requested_at is None

    cancelled = recorder.named("instance.org_deletion_cancelled")
    assert len(cancelled) == 1
    assert cancelled[0]["organization_id"] is None
    assert cancelled[0]["target_slug"] == "del-cancel"
    assert recorder.named("instance.org_restored")


@pytest.mark.asyncio
async def test_restore_without_scheduled_deletion_does_not_audit_cancellation(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A plain archive/restore (no deletion ever requested) audits no cancellation."""
    from app.routers import instance as instance_module

    headers = await _operator(db_session)
    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)

    org = await create_test_organization(db_session, name="Ohne", slug="del-none-scheduled")
    await _archive(db_session, org)

    response = await client.post(f"/api/instance/organizations/{org.slug}/restore", headers=headers)
    assert response.status_code == 200
    assert recorder.named("instance.org_deletion_cancelled") == []
    assert recorder.named("instance.org_restored")


@pytest.mark.asyncio
async def test_restore_answers_404_when_organization_deleted_concurrently(
    client: AsyncClient, db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch
):
    """A hard delete that a second session commits between the lookup and the locked re-check answers 404.

    The row is gone when the lock is taken, and the endpoint does not crash on that.
    """
    from app.routers import instance as instance_module

    headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Verschwunden", slug="del-restore-gone")
    await _archive(db_session, org)

    real_get_org_by_slug = instance_module._get_org_by_slug

    async def _get_org_by_slug_then_concurrent_delete(repo, slug):  # noqa: ANN001, ANN202
        found = await real_get_org_by_slug(repo, slug)
        second_session = await _second_session(test_engine)
        async with second_session:
            await second_session.execute(delete(Organization).where(Organization.id == found.id))
            await second_session.commit()
        return found

    monkeypatch.setattr(instance_module, "_get_org_by_slug", _get_org_by_slug_then_concurrent_delete)

    response = await client.post(f"/api/instance/organizations/{org.slug}/restore", headers=headers)
    assert response.status_code == 404


# ============================================================================
# The sweep
# ============================================================================


@pytest.mark.asyncio
async def test_sweep_keeps_organization_before_due_date(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await create_test_organization(db_session, name="Zu frueh", slug="del-too-early")
    await OrganizationRepository(db_session).update(
        org.id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS - 1)
    )
    await db_session.commit()

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 0
    still_there = await db_session.get(Organization, org.id)
    assert still_there is not None


@pytest.mark.asyncio
async def test_sweep_deletes_everything_after_due_date_and_leaves_other_org_intact(
    db_session: AsyncSession, configured_logging: str, monkeypatch
):
    """The due organization loses every row and every object, the legacy key included. A second one keeps everything."""
    import json
    from pathlib import Path

    now = datetime.now(UTC)
    due = await _build_full_org_tree(db_session, "del-due")
    kept = await _build_full_org_tree(db_session, "del-kept")

    due_org: Organization = due["org"]  # type: ignore[assignment]
    kept_org: Organization = kept["org"]  # type: ignore[assignment]

    await OrganizationRepository(db_session).update(
        due_org.id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1)
    )
    await db_session.commit()

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 1

    # The organization row itself is gone.
    assert await db_session.get(Organization, due_org.id) is None

    # Every row scoped to it is gone.
    for model in (
        Score,
        Submission,
        ChatMessage,
        TeamMember,
        Team,
        RunChallenge,
        RunStation,
        RunDeparture,
        FeedEvent,
        Feedback,
        Run,
        RallyeStationChallenge,
        RallyeStation,
        Station,
        Challenge,
        Rallye,
        UserInvitation,
    ):
        assert await _row_count(db_session, model, due_org.id) == 0

    remaining_users = await db_session.execute(
        select(func.count()).select_from(User).where(User.organization_id == due_org.id)
    )
    assert remaining_users.scalar_one() == 0

    # Every object under its prefix, plus the legacy key, is gone.
    assert not _object_exists(due["station_image_key"])
    assert not _object_exists(due["run_station_image_key"])
    assert not _object_exists(due["participant_media_key"])
    assert not _object_exists(due["org_media_key"])
    assert not _object_exists(due["legacy_key"])

    # The second organization keeps every row and every object.
    assert await db_session.get(Organization, kept_org.id) is not None
    for model in (
        Score,
        Submission,
        ChatMessage,
        TeamMember,
        Team,
        RunChallenge,
        RunStation,
        RunDeparture,
        FeedEvent,
        Feedback,
        Run,
        RallyeStationChallenge,
        RallyeStation,
        Station,
        Challenge,
        Rallye,
        UserInvitation,
    ):
        assert await _row_count(db_session, model, kept_org.id) == 1
    assert _object_exists(kept["station_image_key"])
    assert _object_exists(kept["run_station_image_key"])
    assert _object_exists(kept["participant_media_key"])
    assert _object_exists(kept["org_media_key"])
    assert _object_exists(kept["legacy_key"])

    # instance.org_deleted is audited with the slug and the counts.
    audit_log = Path(configured_logging) / "audit.log"
    entries = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]
    deleted_events = [e for e in entries if e.get("event") == "instance.org_deleted"]
    assert len(deleted_events) == 1
    assert deleted_events[0]["target_slug"] == "del-due"
    assert deleted_events[0]["rows_deleted"] > 0
    assert deleted_events[0]["objects_deleted"] == 5

    client = get_minio_client()
    for key in (
        kept["station_image_key"],
        kept["run_station_image_key"],
        kept["participant_media_key"],
        kept["org_media_key"],
        kept["legacy_key"],
    ):
        client.remove_object(settings.storage.bucket, key)


@pytest.mark.asyncio
async def test_sweep_idempotent(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await create_test_organization(db_session, name="Doppelt", slug="del-idempotent")
    await OrganizationRepository(db_session).update(
        org.id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1)
    )
    await db_session.commit()

    assert await sweep_due_organizations(db_session, reference_date=now) == 1
    assert await sweep_due_organizations(db_session, reference_date=now) == 0


# ============================================================================
# rows_deleted is exact: every table _build_full_org_tree touches, the organization row, and the
# two tables reached only transitively (run_result_links, password_reset_tokens), 0 rows here.
# ============================================================================

# _build_full_org_tree creates exactly these rows:
# 1 organization + 2 users (creator, guest) + 1 each of station, challenge,
# rallye, rallye_station, rallye_station_challenge, run, run_station,
# run_challenge, team, team_member, chat_message, submission, score,
# feed_event, feedback, run_departure, user_invitation = 20 rows total.
_FULL_TREE_ROW_COUNT = 20


@pytest.mark.asyncio
async def test_sweep_audits_exact_row_count_including_org_row(db_session: AsyncSession, monkeypatch):
    """rows_deleted counts every row the fixture created for the organization, the organization row included."""
    from app.services import org_deletion as org_deletion_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(org_deletion_module, "record_audit_event", recorder)

    now = datetime.now(UTC)
    built = await _build_full_org_tree(db_session, "del-exact-count")
    org: Organization = built["org"]  # type: ignore[assignment]
    await OrganizationRepository(db_session).update(
        org.id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1)
    )
    await db_session.commit()

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 1
    events = recorder.named("instance.org_deleted")
    assert len(events) == 1
    assert events[0]["target_slug"] == "del-exact-count"
    assert events[0]["rows_deleted"] == _FULL_TREE_ROW_COUNT


def _due_days(offset: int) -> int:
    """Days since the request, past the grace window and distinct per offset.

    ``due_for_deletion`` then sorts the organizations in a fixed order, oldest request first.
    """
    return DELETION_GRACE_DAYS + 3 - offset


async def _archive_and_schedule(
    db_session: AsyncSession, org: Organization, now: datetime, *, days_overdue: int
) -> None:
    await OrganizationRepository(db_session).update(
        org.id, archived_at=now, deletion_requested_at=now - timedelta(days=days_overdue)
    )


@pytest.mark.asyncio
async def test_sweep_middle_organizations_delete_failure_does_not_undo_the_others(
    db_session: AsyncSession, monkeypatch
):
    """Each due organization is its own transaction. When the middle of three fails its delete,
    the first and the third are still deleted and audited. The middle one stays, still due and unaudited."""
    from app.services import org_deletion as org_deletion_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(org_deletion_module, "record_audit_event", recorder)

    now = datetime.now(UTC)
    org_a = await create_test_organization(db_session, name="Geht gut 1", slug="del-fail-a")
    org_b = await create_test_organization(db_session, name="Geht schief", slug="del-fail-b")
    org_c = await create_test_organization(db_session, name="Geht gut 2", slug="del-fail-c")
    # due_for_deletion orders by deletion_requested_at ascending, so the sweep takes a, then b, then c.
    await _archive_and_schedule(db_session, org_a, now, days_overdue=_due_days(0))
    await _archive_and_schedule(db_session, org_b, now, days_overdue=_due_days(1))
    await _archive_and_schedule(db_session, org_c, now, days_overdue=_due_days(2))
    await db_session.commit()
    org_a_id, org_b_id, org_c_id = org_a.id, org_b.id, org_c.id

    real_scope_predicate = org_deletion_module._scope_predicate

    def _patched_scope_predicate(model, organization_id):  # noqa: ANN001, ANN202
        if organization_id == org_b_id:
            raise RuntimeError("simulated database failure")
        return real_scope_predicate(model, organization_id)

    monkeypatch.setattr(org_deletion_module, "_scope_predicate", _patched_scope_predicate)

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 2
    remaining_a = await db_session.execute(select(Organization.id).where(Organization.id == org_a_id))
    assert remaining_a.scalar_one_or_none() is None
    remaining_c = await db_session.execute(select(Organization.id).where(Organization.id == org_c_id))
    assert remaining_c.scalar_one_or_none() is None

    remaining_b = await db_session.execute(
        select(Organization.deletion_requested_at).where(Organization.id == org_b_id)
    )
    still_due = remaining_b.scalar_one_or_none()
    assert still_due is not None  # untouched: still due for the next sweep

    deleted_events = recorder.named("instance.org_deleted")
    assert {e["target_slug"] for e in deleted_events} == {"del-fail-a", "del-fail-c"}


@pytest.mark.asyncio
async def test_sweep_commit_failure_for_one_organization_does_not_undo_others(db_session: AsyncSession, monkeypatch):
    """A failing commit is handled like a failing delete. When the middle of three fails its commit,
    the first and the third are still deleted and audited. The middle one stays, still due and unaudited."""
    from app.services import org_deletion as org_deletion_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(org_deletion_module, "record_audit_event", recorder)

    now = datetime.now(UTC)
    org_a = await create_test_organization(db_session, name="Commit gut 1", slug="del-commit-a")
    org_b = await create_test_organization(db_session, name="Commit schief", slug="del-commit-b")
    org_c = await create_test_organization(db_session, name="Commit gut 2", slug="del-commit-c")
    await _archive_and_schedule(db_session, org_a, now, days_overdue=_due_days(0))
    await _archive_and_schedule(db_session, org_b, now, days_overdue=_due_days(1))
    await _archive_and_schedule(db_session, org_c, now, days_overdue=_due_days(2))
    await db_session.commit()
    org_a_id, org_b_id, org_c_id = org_a.id, org_b.id, org_c.id

    real_commit = db_session.commit
    commit_calls = {"count": 0}

    async def _commit_that_fails_on_second_call() -> None:
        commit_calls["count"] += 1
        if commit_calls["count"] == 2:
            raise RuntimeError("simulated commit failure")
        await real_commit()

    monkeypatch.setattr(db_session, "commit", _commit_that_fails_on_second_call)

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 2
    remaining_a = await db_session.execute(select(Organization.id).where(Organization.id == org_a_id))
    assert remaining_a.scalar_one_or_none() is None
    remaining_c = await db_session.execute(select(Organization.id).where(Organization.id == org_c_id))
    assert remaining_c.scalar_one_or_none() is None

    remaining_b = await db_session.execute(
        select(Organization.deletion_requested_at).where(Organization.id == org_b_id)
    )
    still_due = remaining_b.scalar_one_or_none()
    assert still_due is not None  # untouched: still due for the next sweep

    deleted_events = recorder.named("instance.org_deleted")
    assert {e["target_slug"] for e in deleted_events} == {"del-commit-a", "del-commit-c"}


@pytest.mark.asyncio
async def test_sweep_skips_organization_restored_concurrently_after_due_set_read(
    db_session: AsyncSession, test_engine: AsyncEngine, monkeypatch
):
    """A restore that a second session commits after the due set is read leaves the organization untouched.

    Its rows stay and nothing is audited. The per-organization row lock re-checks ``archived_at`` and
    ``deletion_requested_at`` fresh, not the values read with the due set.
    """
    from app.repositories.organization import OrganizationRepository as OrgRepoClass
    from app.services import org_deletion as org_deletion_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(org_deletion_module, "record_audit_event", recorder)

    now = datetime.now(UTC)
    org = await create_test_organization(db_session, name="Zwischenzeitlich", slug="del-sweep-concurrent")
    member = await create_test_user(db_session, email="member@del-sweep-concurrent.example.com", organization=org)
    await OrganizationRepository(db_session).update(
        org.id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1)
    )
    await db_session.commit()
    org_id, member_id = org.id, member.id

    real_due_for_deletion = OrgRepoClass.due_for_deletion

    async def _due_for_deletion_then_concurrent_restore(self, cutoff):  # noqa: ANN001, ANN202
        due = await real_due_for_deletion(self, cutoff)
        second_session = await _second_session(test_engine)
        async with second_session:
            await OrganizationRepository(second_session).update(org_id, archived_at=None, deletion_requested_at=None)
            await second_session.commit()
        return due

    monkeypatch.setattr(OrgRepoClass, "due_for_deletion", _due_for_deletion_then_concurrent_restore)

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 0
    assert recorder.named("instance.org_deleted") == []

    remaining_org = await db_session.execute(select(Organization.archived_at).where(Organization.id == org_id))
    assert remaining_org.scalar_one() is None  # restored, and the sweep did not re-archive or touch it further
    remaining_member = await db_session.execute(select(User.id).where(User.id == member_id))
    assert remaining_member.scalar_one_or_none() is not None  # its rows are untouched


@pytest.mark.asyncio
async def test_legacy_keys_never_cross_organizations(db_session: AsyncSession):
    """A key under another organization's own prefix, and an ``uploads/…``
    key that organization still references, both survive the sweep."""
    now = datetime.now(UTC)
    org_due = await create_test_organization(db_session, name="Wird geloescht", slug="del-cross-due")
    org_other = await create_test_organization(db_session, name="Bleibt", slug="del-cross-other")

    other_prefixed_key = _put_object(f"{org_other.id}/media/{uuid.uuid4().hex}_shared.png")
    shared_legacy_key = _put_object(f"uploads/2024-02-02/{uuid.uuid4().hex}_shared.png")

    await _minimal_submission(
        db_session, org_due, content={"photo_url": other_prefixed_key, "audio_url": shared_legacy_key}
    )
    # org_other's own row still references the shared legacy key.
    await _minimal_submission(db_session, org_other, content={"audio_url": shared_legacy_key})

    await OrganizationRepository(db_session).update(
        org_due.id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1)
    )
    await db_session.commit()

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 1
    assert _object_exists(other_prefixed_key)  # never touched: another org's own prefix
    assert _object_exists(shared_legacy_key)  # still referenced by org_other

    client = get_minio_client()
    client.remove_object(settings.storage.bucket, other_prefixed_key)
    client.remove_object(settings.storage.bucket, shared_legacy_key)


@pytest.mark.asyncio
async def test_objects_deleted_excludes_a_missing_legacy_key(db_session: AsyncSession, monkeypatch):
    """A referenced legacy key missing from storage, never uploaded or already removed, is not in objects_deleted."""
    from app.services import org_deletion as org_deletion_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(org_deletion_module, "record_audit_event", recorder)

    now = datetime.now(UTC)
    org = await create_test_organization(db_session, name="Fehlt", slug="del-missing-legacy")
    missing_key = f"uploads/2024-03-03/{uuid.uuid4().hex}_never-uploaded.png"
    await _minimal_submission(db_session, org, content={"photo_url": missing_key})

    await OrganizationRepository(db_session).update(
        org.id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1)
    )
    await db_session.commit()

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 1
    events = recorder.named("instance.org_deleted")
    assert len(events) == 1
    assert events[0]["objects_deleted"] == 0


@pytest.mark.asyncio
async def test_row_delete_failure_leaves_every_object_in_place(db_session: AsyncSession, monkeypatch):
    """The sweep deletes and flushes the rows before it touches an object.

    So a row-delete failure leaves every object in place, and the rolled-back rows stay too.
    """
    from app.services import org_deletion as org_deletion_module

    now = datetime.now(UTC)
    built = await _build_full_org_tree(db_session, "del-row-fail")
    org: Organization = built["org"]  # type: ignore[assignment]
    org_id = org.id  # captured before the sweep: rollback() expires org below
    await OrganizationRepository(db_session).update(
        org_id, archived_at=now, deletion_requested_at=now - timedelta(days=DELETION_GRACE_DAYS + 1)
    )
    await db_session.commit()

    def _failing_scope_predicate(model, organization_id):  # noqa: ANN001, ANN202
        raise RuntimeError("simulated row-delete failure")

    monkeypatch.setattr(org_deletion_module, "_scope_predicate", _failing_scope_predicate)

    deleted = await sweep_due_organizations(db_session, reference_date=now)

    assert deleted == 0
    for key in (
        built["station_image_key"],
        built["run_station_image_key"],
        built["participant_media_key"],
        built["org_media_key"],
        built["legacy_key"],
    ):
        assert _object_exists(key)

    remaining = await db_session.execute(select(Organization.id).where(Organization.id == org_id))
    assert remaining.scalar_one_or_none() is not None
    assert await _row_count(db_session, Score, org_id) == 1

    client = get_minio_client()
    for key in (
        built["station_image_key"],
        built["run_station_image_key"],
        built["participant_media_key"],
        built["org_media_key"],
        built["legacy_key"],
    ):
        client.remove_object(settings.storage.bucket, key)


# ============================================================================
# Drift guard: the delete-order constant must match Base.metadata's own
# CASCADE graph, or a new organization-scoped table could silently escape it.
# ============================================================================


def _test_only_tables() -> set[str]:
    """Tables only the suite declares, ``test_entities`` and ``system_test_entities`` of ``test_repository_base.py``.

    Under xdist every worker imports every test module, so these tables sit on ``Base.metadata``.
    This copies ``_test_only_tables()`` of ``test_schema_migrations.py`` so this test stands alone.
    """
    tests_dir = Path(__file__).resolve().parent
    names: set[str] = set()
    for mapper in Base.registry.mappers:
        try:
            source = Path(inspect.getfile(mapper.class_)).resolve()
        except (TypeError, OSError):
            continue
        if tests_dir in source.parents:
            names.add(getattr(mapper.local_table, "name", ""))
    return names


def test_org_scoped_delete_order_matches_metadata_cascade_graph():
    import app.models  # noqa: F401  (registers every model on Base.metadata)
    from app.services import org_deletion as org_deletion_module

    test_only = _test_only_tables()

    edges: list[tuple[str, str]] = []
    for table in Base.metadata.tables.values():
        if table.name in test_only:
            continue
        for fk in table.foreign_keys:
            if fk.column.table.name in test_only:
                continue
            if (fk.ondelete or "").upper() == "CASCADE":
                edges.append((table.name, fk.column.table.name))

    children_of: dict[str, list[str]] = {}
    for child, parent in edges:
        children_of.setdefault(parent, []).append(child)

    reachable: set[str] = set()
    frontier = ["organizations"]
    while frontier:
        next_frontier: list[str] = []
        for parent in frontier:
            for child in children_of.get(parent, []):
                if child not in reachable and child != "organizations":
                    reachable.add(child)
                    next_frontier.append(child)
        frontier = next_frontier

    configured_names = [model.__tablename__ for model in org_deletion_module._ORG_SCOPED_TABLES_IN_DELETE_ORDER]

    assert len(configured_names) == len(set(configured_names)), "duplicate table in the delete-order constant"
    assert set(configured_names) == reachable, (
        "_ORG_SCOPED_TABLES_IN_DELETE_ORDER must contain exactly every table reachable from "
        "organizations via an ON DELETE CASCADE chain"
    )

    position = {name: i for i, name in enumerate(configured_names)}
    for child, parent in edges:
        if child in reachable and parent in reachable:
            assert position[child] < position[parent], f"{child} must be deleted before {parent} (child before parent)"


# ============================================================================
# Database-level backstop: the CHECK constraint
# ============================================================================


@pytest.mark.asyncio
async def test_check_constraint_refuses_scheduled_deletion_without_archived(db_session: AsyncSession):
    org = await create_test_organization(db_session, name="Ohne Archiv", slug="del-constraint")

    with pytest.raises(IntegrityError):
        await OrganizationRepository(db_session).update(org.id, deletion_requested_at=datetime.now(UTC))
    await db_session.rollback()
