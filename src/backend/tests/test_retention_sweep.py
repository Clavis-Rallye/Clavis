"""Tests for the run-scoped GDPR retention sweep (LK_A / LK_B / LK_C, content-media orphans).

Each retention task is tested directly with the bound test session and an
injected ``reference_date`` (the same seam ``test_grace_delete.py`` uses for
LK_G), so the class boundary, idempotency, the storage side effects and the
shared-key guard are all asserted deterministically without waiting real days.

Media tests touch the real object store (the Garage container that `make test`
runs on 127.0.0.1:59000; conftest uses a per-xdist-worker bucket). Object keys
are made unique per test so parallel workers don't collide.
"""

import contextlib
import io
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from minio.error import S3Error
from sqlalchemy import event, func, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import unreadable_json_marker
from app.logging import configure_logging
from app.models.challenge import ChallengeType
from app.models.chat_message import ChatMessage
from app.models.feedback import Feedback
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_departure import RunDeparture
from app.models.run_result_link import RunResultLink
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User, UserRole
from app.services import retention, retention_tasks
from app.services.audit import reset_integrity_chain
from app.services.gdpr import ANONYMIZED_MEMBER_NAME, SYSTEM_RETENTION_ACTOR, DeletionClass, anonymize_user
from app.services.guest_cleanup import delete_run_guests
from app.services.media_purge import MEDIA_REMOVED_FLAG
from app.services.retention_tasks import (
    ANONYMIZED_TEAM_NAME,
    ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS,
    clear_submission_answer_keys,
    clear_submission_media_keys,
    run_id_from_object_key,
    submission_media_keys,
    sweep_anonymized_member_names,
    sweep_expired_result_links,
    sweep_orphaned_station_media,
    sweep_orphaned_submission_media,
    sweep_run_anonymize,
    sweep_run_answer_text,
    sweep_run_chat,
    sweep_run_guests,
    sweep_run_media,
    variant_base_key,
    variant_object_keys,
)
from app.services.team_dissolution import dissolve_team
from app.storage import ensure_bucket, get_minio_client, list_all_files, upload_file
from tests.factories import (
    create_test_organization,
    create_test_station,
    create_test_user,
)

LK_A = DeletionClass.LK_A.retention_days  # 30
LK_B = DeletionClass.LK_B.retention_days  # 90
LK_C = DeletionClass.LK_C.retention_days  # 365
LK_H = DeletionClass.LK_H.retention_days  # 365

PNG = b"\x89PNG\r\n\x1a\n" + b"clavis-retention-test"


@pytest.fixture(autouse=True)
def _ensure_bucket() -> None:
    """CI starts a fresh Garage per run, so the bucket may not exist yet."""
    ensure_bucket()


@pytest.fixture
def configured_logging():
    """Audit logging into a temp dir (same shape as test_grace_delete.py)."""
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


# ---------------------------------------------------------------------------
# Builders — minimal object graphs (no full API flow needed for retention).
# ---------------------------------------------------------------------------


def _code(n: int = 6) -> str:
    return uuid.uuid4().hex[:n].upper()


def _unique_key(prefix: str, suffix: str = ".png") -> str:
    return f"{prefix}-{uuid.uuid4().hex}{suffix}"


async def _org(db: AsyncSession, slug: str):
    return await create_test_organization(db, name=f"Org {slug}", slug=slug)


async def _run(
    db: AsyncSession,
    org,
    *,
    state: RunState = RunState.ENDED,
    ended_at: datetime | None = None,
    started_at: datetime | None = None,
    created_at: datetime | None = None,
) -> Run:
    run = Run(
        organization_id=org.id,
        name="Retention Run",
        state=state,
        join_code=_code(),
        started_at=started_at,
        ended_at=ended_at,
    )
    db.add(run)
    await db.flush()
    if created_at is not None:
        run.created_at = created_at
        await db.flush()
    return run


async def _team(db: AsyncSession, org, run, *, name: str | None = "Die Stadtfüchse") -> Team:
    team = Team(organization_id=org.id, run_id=run.id, share_code=_code(8), name=name)
    db.add(team)
    await db.flush()
    return team


async def _result_link(
    db: AsyncSession,
    run: Run,
    *,
    expires_at: datetime,
    revoked_at: datetime | None = None,
) -> RunResultLink:
    from uuid_utils import uuid7

    link = RunResultLink(
        id=uuid7(),
        run_id=run.id,
        slug_hash=uuid.uuid4().hex,
        snapshot={"run_name": run.name, "ended_at": None, "organization_name": "Org", "entries": []},
        published_at=datetime.now(UTC),
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    db.add(link)
    await db.flush()
    return link


async def _guest_member(db: AsyncSession, org, team, *, display_name: str = "Guest") -> tuple[User, TeamMember]:
    guest = await create_test_user(
        db,
        email=f"guest-{uuid.uuid4().hex[:10]}@retention.example.com",
        display_name=display_name,
        role=UserRole.GUEST,
        organization=org,
    )
    member = TeamMember(
        organization_id=org.id,
        team_id=team.id,
        user_id=guest.id,
        display_name=display_name,
        is_guest=True,
    )
    db.add(member)
    await db.flush()
    return guest, member


async def _run_station(
    db: AsyncSession, org, run, *, sort_order: int = 0, title_image_key: str | None = None
) -> RunStation:
    rs = RunStation(
        organization_id=org.id,
        run_id=run.id,
        sort_order=sort_order,
        title_image_key=title_image_key,
    )
    db.add(rs)
    await db.flush()
    return rs


async def _run_challenge(
    db: AsyncSession, org, run, run_station, *, ctype: ChallengeType = ChallengeType.PHOTO
) -> RunChallenge:
    rc = RunChallenge(
        organization_id=org.id,
        run_id=run.id,
        run_station_id=run_station.id,
        challenge_type=ctype,
        title={"de": "Foto"},
        instructions={"de": "Mach ein Foto"},
        scoring_rules={},
        sort_order=0,
    )
    db.add(rc)
    await db.flush()
    return rc


async def _submission(db: AsyncSession, org, run, team, run_challenge, *, content: dict) -> Submission:
    sub = Submission(
        organization_id=org.id,
        run_id=run.id,
        team_id=team.id,
        run_challenge_id=run_challenge.id,
        content=content,
    )
    db.add(sub)
    await db.flush()
    return sub


async def _chat(db: AsyncSession, org, team, *, count: int = 1) -> list[ChatMessage]:
    # Flush per row: a batched multi-row INSERT trips asyncpg's insertmanyvalues
    # sentinel matching for these client-side-UUID + server-default-timestamp
    # rows (production creates chat one message per request, so this is a
    # test-builder artifact, not a model bug).
    msgs = []
    for i in range(count):
        m = ChatMessage(
            organization_id=org.id,
            team_id=team.id,
            content=f"message {i}",
            is_system_message=False,
        )
        db.add(m)
        await db.flush()
        msgs.append(m)
    return msgs


def _object_exists(key: str) -> bool:
    try:
        get_minio_client().stat_object(settings.storage.bucket, key)
        return True
    except S3Error:
        return False


def _list_mtime(key: str) -> datetime:
    """The object's ``last_modified`` exactly as ``list_all_files`` reports
    it, tz-aware, used to place a ``reference_date`` cutoff precisely
    between two real uploads. Read through the same call the
    sweep itself makes, not ``stat_object``: HeadObject's Last-Modified
    header and ListObjectsV2's XML field can round the same timestamp
    differently, and a cutoff built from the other API's value could land on
    the wrong side of the sweep's own comparison."""
    for obj in list_all_files():
        if obj["key"] == key:
            last_modified = obj["last_modified"]
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=UTC)
            return last_modified
    raise AssertionError(f"{key} not found in list_all_files()")


def _upload_object(key: str, data: bytes = PNG, content_type: str = "image/png") -> str:
    """Upload under an explicit key — for the run/station/media key shapes
    that ``upload_file`` (which always writes ``uploads/{date}/…``) cannot produce."""
    get_minio_client().put_object(
        settings.storage.bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return key


def _purge_run_scoped_objects() -> None:
    """Remove every run-scoped upload from the (per-xdist-worker) bucket.

    The orphan sweep lists the whole bucket, and other test files in the same
    worker leave run-shaped keys behind — under a future-injected
    ``reference_date`` those count as old orphans and would inflate the
    deletion counts. Purging the shape first leaves only this test's fixtures.
    """
    client = get_minio_client()
    for obj in client.list_objects(settings.storage.bucket, recursive=True):
        if obj.object_name is not None and run_id_from_object_key(obj.object_name) is not None:
            client.remove_object(settings.storage.bucket, obj.object_name)


async def _user_exists(db: AsyncSession, user_id) -> bool:
    """Fresh DB existence check by id — avoids touching a possibly-expunged
    in-memory instance after a bulk delete."""
    row = await db.execute(select(User.id).where(User.id == user_id))
    return row.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Extraction helper (pure, no DB)
# ---------------------------------------------------------------------------


def test_submission_media_keys_photo_only():
    assert submission_media_keys({"photo_url": "k/photo.jpg", "text": "hi"}) == ["k/photo.jpg"]


def test_submission_media_keys_photo_and_audio():
    keys = submission_media_keys({"photo_url": "p.jpg", "audio_url": "a.mp3"})
    assert set(keys) == {"p.jpg", "a.mp3"}


def test_submission_media_keys_video():
    keys = submission_media_keys({"video_url": "v.mp4", "text": "hi"})
    assert keys == ["v.mp4"]


def test_submission_media_keys_media_urls_list():
    keys = submission_media_keys({"media_urls": ["m1.jpg", "m2.jpg", "", 5]})
    assert keys == ["m1.jpg", "m2.jpg"]


def test_submission_media_keys_text_only_is_empty():
    assert submission_media_keys({"text": "answer", "selected_option": 2}) == []


def test_submission_media_keys_ignores_blank_strings():
    assert submission_media_keys({"photo_url": "   ", "audio_url": ""}) == []


def test_clear_submission_media_keys_preserves_non_media():
    cleared = clear_submission_media_keys(
        {
            "photo_url": "p.jpg",
            "audio_url": "a.mp3",
            "video_url": "v.mp4",
            "media_urls": ["x"],
            "text": "answer",
            "selected_option": 1,
        }
    )
    assert cleared == {"text": "answer", "selected_option": 1}


def test_run_id_from_object_key_run_scoped():
    run_id = uuid.uuid4()
    key = f"{uuid.uuid4()}/{run_id}/{uuid.uuid4()}/abcd_foto.png"
    assert run_id_from_object_key(key) == run_id


def test_run_id_from_object_key_rejects_non_run_shapes():
    org_id = uuid.uuid4()
    assert run_id_from_object_key(f"{org_id}/stations/{uuid.uuid4()}/abcd_bild.png") is None
    assert run_id_from_object_key(f"{org_id}/media/{uuid.uuid4().hex}_logo.png") is None
    assert run_id_from_object_key("uploads/2026-02-12/foto.png") is None
    assert run_id_from_object_key("solo-name.png") is None
    assert run_id_from_object_key(f"{org_id}/not-a-uuid/abcd.png") is None


def test_variant_object_keys_derive_both_suffixes():
    key = "o/r/t/abcd_foto.png"
    assert variant_object_keys(key) == (
        "o/r/t/abcd_foto.png.thumb.webp",
        "o/r/t/abcd_foto.png.display.webp",
    )


def test_variant_base_key_strips_suffix():
    key = "o/r/t/abcd_foto.png"
    assert variant_base_key(f"{key}.thumb.webp") == key
    assert variant_base_key(f"{key}.display.webp") == key
    assert variant_base_key(key) is None
    # A filename that merely contains "thumb.webp" is not a variant key.
    assert variant_base_key("o/r/t/abcd_mythumb.webp") is None


# ---------------------------------------------------------------------------
# LK_B — raw submission media
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_deleted_after_lkb_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-media")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B + 1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    key = upload_file(PNG, _unique_key("ret-media"), "image/png")
    sub = await _submission(db_session, org, run, team, rc, content={"photo_url": key, "text": "answer"})
    assert _object_exists(key)

    deleted = await sweep_run_media(db_session, reference_date=now)

    assert deleted == 1
    assert not _object_exists(key)
    await db_session.refresh(sub)
    assert "photo_url" not in sub.content
    assert sub.content.get("text") == "answer"  # non-media preserved


@pytest.mark.asyncio
async def test_video_media_deleted_after_lkb_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-media-video")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B + 1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs, ctype=ChallengeType.VIDEO)
    key = upload_file(b"clavis-retention-video", _unique_key("ret-media-video", ".mp4"), "video/mp4")
    sub = await _submission(db_session, org, run, team, rc, content={"video_url": key})
    assert _object_exists(key)

    deleted = await sweep_run_media(db_session, reference_date=now)

    assert deleted == 1
    assert not _object_exists(key)
    await db_session.refresh(sub)
    assert "video_url" not in sub.content


@pytest.mark.asyncio
async def test_media_kept_within_lkb_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-media-keep")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B - 1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    key = upload_file(PNG, _unique_key("ret-media-keep"), "image/png")
    sub = await _submission(db_session, org, run, team, rc, content={"photo_url": key})

    deleted = await sweep_run_media(db_session, reference_date=now)

    assert deleted == 0
    assert _object_exists(key)
    assert sub.content["photo_url"] == key
    get_minio_client().remove_object(settings.storage.bucket, key)


@pytest.mark.asyncio
async def test_media_sweep_idempotent(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-media-idem")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B + 5))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    key = upload_file(PNG, _unique_key("ret-media-idem"), "image/png")
    await _submission(db_session, org, run, team, rc, content={"photo_url": key})

    assert await sweep_run_media(db_session, reference_date=now) == 1
    assert await sweep_run_media(db_session, reference_date=now) == 0


@pytest.mark.asyncio
async def test_media_sweep_tolerates_missing_object(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-media-missing")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B + 1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    ghost_key = f"{org.id}/never/uploaded/{uuid.uuid4().hex}.jpg"
    sub = await _submission(db_session, org, run, team, rc, content={"photo_url": ghost_key})

    # Must not raise even though the object was never in storage.
    deleted = await sweep_run_media(db_session, reference_date=now)

    assert deleted == 1  # "already gone" counts as a successful delete
    await db_session.refresh(sub)
    assert "photo_url" not in sub.content


@pytest.mark.asyncio
async def test_photo_variants_deleted_with_media_after_lkb_window(db_session: AsyncSession):
    """The LK_B sweep deletes a photo's WebP
    variants together with the referenced object. Variants ride along
    uncounted: the sweep counts the submission's referenced keys."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-media-variants")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B + 1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    key = upload_file(PNG, _unique_key("ret-media-variants"), "image/png")
    thumb_key = _upload_object(f"{key}.thumb.webp", PNG, "image/webp")
    display_key = _upload_object(f"{key}.display.webp", PNG, "image/webp")
    sub = await _submission(db_session, org, run, team, rc, content={"photo_url": key})
    assert _object_exists(key)
    assert _object_exists(thumb_key)
    assert _object_exists(display_key)

    deleted = await sweep_run_media(db_session, reference_date=now)

    assert deleted == 1  # only the referenced key counts; the variants ride along
    assert not _object_exists(key)
    assert not _object_exists(thumb_key)
    assert not _object_exists(display_key)
    await db_session.refresh(sub)
    assert "photo_url" not in sub.content


# ---------------------------------------------------------------------------
# LK_B — chat messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_deleted_after_lkb_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-chat")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B + 1))
    team = await _team(db_session, org, run)
    await _chat(db_session, org, team, count=3)

    deleted = await sweep_run_chat(db_session, reference_date=now)

    assert deleted == 3
    remaining = await db_session.scalar(
        select(func.count()).select_from(ChatMessage).where(ChatMessage.team_id == team.id)
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_chat_kept_within_lkb_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-chat-keep")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B - 1))
    team = await _team(db_session, org, run)
    await _chat(db_session, org, team, count=2)

    assert await sweep_run_chat(db_session, reference_date=now) == 0


@pytest.mark.asyncio
async def test_chat_sweep_idempotent(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-chat-idem")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B + 2))
    team = await _team(db_session, org, run)
    await _chat(db_session, org, team, count=2)

    assert await sweep_run_chat(db_session, reference_date=now) == 2
    assert await sweep_run_chat(db_session, reference_date=now) == 0


# ---------------------------------------------------------------------------
# LK_A — guest accounts (ended + abandoned)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guests_deleted_after_ended_lka_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-ended")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    team = await _team(db_session, org, run)
    guest, member = await _guest_member(db_session, org, team, display_name="Roster Name")

    deleted = await sweep_run_guests(db_session, reference_date=now)

    assert deleted == 1
    assert await _user_exists(db_session, guest.id) is False
    # Run history preserved: the membership row stays, user_id SET NULL (a DB-level
    # FK action, so re-read the row to see it rather than trusting the session cache).
    await db_session.refresh(member)
    assert member.user_id is None
    # The roster's own copy of the name goes with the account.
    assert member.display_name == ANONYMIZED_MEMBER_NAME


@pytest.mark.asyncio
async def test_guests_deleted_for_abandoned_run(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-abandoned")
    run = await _run(
        db_session,
        org,
        state=RunState.ACTIVE,  # never ended
        started_at=now - timedelta(days=settings.retention.abandoned_run_days + 1),
    )
    team = await _team(db_session, org, run)
    guest, _ = await _guest_member(db_session, org, team)

    deleted = await sweep_run_guests(db_session, reference_date=now)

    assert deleted == 1
    assert await _user_exists(db_session, guest.id) is False


@pytest.mark.asyncio
async def test_guests_deleted_for_abandoned_run_never_started(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-nostart")
    run = await _run(
        db_session,
        org,
        state=RunState.CONFIGURED,
        started_at=None,
        created_at=now - timedelta(days=settings.retention.abandoned_run_days + 1),
    )
    team = await _team(db_session, org, run)
    guest, _ = await _guest_member(db_session, org, team)

    deleted = await sweep_run_guests(db_session, reference_date=now)

    assert deleted == 1  # coalesce falls back to created_at
    assert await _user_exists(db_session, guest.id) is False


@pytest.mark.asyncio
async def test_guests_kept_for_recent_and_fresh_runs(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-keep")
    recent_ended = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A - 1))
    fresh_active = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    g1, _ = await _guest_member(db_session, org, await _team(db_session, org, recent_ended))
    g2, _ = await _guest_member(db_session, org, await _team(db_session, org, fresh_active))

    deleted = await sweep_run_guests(db_session, reference_date=now)

    assert deleted == 0
    assert await _user_exists(db_session, g1.id) is True
    assert await _user_exists(db_session, g2.id) is True


async def _departure(db: AsyncSession, org, run, user: User) -> RunDeparture:
    """The evidence row a leave or a removal writes; the membership row is gone by then."""
    departure = RunDeparture(organization_id=org.id, run_id=run.id, user_id=user.id)
    db.add(departure)
    await db.flush()
    return departure


async def _departed_guest(db: AsyncSession, org, run) -> User:
    """A guest who left the team of ``run``: a departure row, no membership."""
    guest, member = await _guest_member(db, org, await _team(db, org, run))
    await db.delete(member)
    await db.flush()
    await _departure(db, org, run, guest)
    return guest


@pytest.mark.asyncio
async def test_departed_guest_deleted_after_ended_lka_window(db_session: AsyncSession):
    """A guest who left or was removed is swept like the guests who stayed."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-departed")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    departed = await _departed_guest(db_session, org, run)
    stayed, _ = await _guest_member(db_session, org, await _team(db_session, org, run, name="Die Zweiten"))

    deleted = await sweep_run_guests(db_session, reference_date=now)

    assert deleted == 2
    assert await _user_exists(db_session, departed.id) is False
    assert await _user_exists(db_session, stayed.id) is False
    # The departure evidence goes with the account (FK ON DELETE CASCADE).
    remaining = await db_session.execute(
        select(func.count()).select_from(RunDeparture).where(RunDeparture.run_id == run.id)
    )
    assert remaining.scalar_one() == 0


@pytest.mark.asyncio
async def test_departed_guest_kept_within_lka_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-departed-keep")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A - 1))
    departed = await _departed_guest(db_session, org, run)

    assert await sweep_run_guests(db_session, reference_date=now) == 0
    assert await _user_exists(db_session, departed.id) is True


@pytest.mark.asyncio
async def test_departed_guest_deleted_for_abandoned_run(db_session: AsyncSession):
    """The same for a run that never ended and is swept as abandoned."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-departed-abandoned")
    run = await _run(
        db_session,
        org,
        state=RunState.ACTIVE,
        started_at=now - timedelta(days=settings.retention.abandoned_run_days + 1),
    )
    departed = await _departed_guest(db_session, org, run)

    assert await sweep_run_guests(db_session, reference_date=now) == 1
    assert await _user_exists(db_session, departed.id) is False


@pytest.mark.asyncio
async def test_departed_guest_kept_while_member_of_a_run_not_due(db_session: AsyncSession):
    """Left a due run, still a member in another run of the org that is not due."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-departed-other-run")
    due = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    fresh = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    guest = await _departed_guest(db_session, org, due)
    db_session.add(
        TeamMember(
            organization_id=org.id,
            team_id=(await _team(db_session, org, fresh)).id,
            user_id=guest.id,
            display_name="Guest",
            is_guest=True,
        )
    )
    await db_session.flush()

    assert await sweep_run_guests(db_session, reference_date=now) == 0
    assert await _user_exists(db_session, guest.id) is True


async def _join_team(db: AsyncSession, org, run, guest: User, *, name: str) -> TeamMember:
    """A membership row for an existing guest account in a team of ``run``."""
    member = TeamMember(
        organization_id=org.id,
        team_id=(await _team(db, org, run, name=name)).id,
        user_id=guest.id,
        display_name="Guest",
        is_guest=True,
    )
    db.add(member)
    await db.flush()
    return member


@pytest.mark.asyncio
async def test_departed_guest_deleted_when_every_other_run_is_due_too(db_session: AsyncSession):
    """A membership in a run that is due as well does not protect the guest.

    The guest left run A and is a member in runs B and C. One guest account can
    reach this state through the routes (a valid session may join a second run);
    old data can hold it too. All three runs are due: each would skip the
    account because of another due run, and nothing would ever delete it.
    """
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-multi-due")
    ended = now - timedelta(days=LK_A + 1)
    run_a = await _run(db_session, org, state=RunState.ENDED, ended_at=ended)
    run_b = await _run(db_session, org, state=RunState.ENDED, ended_at=ended)
    run_c = await _run(db_session, org, state=RunState.ENDED, ended_at=ended)
    guest = await _departed_guest(db_session, org, run_a)
    await _join_team(db_session, org, run_b, guest, name="B-Team")
    await _join_team(db_session, org, run_c, guest, name="C-Team")

    assert await sweep_run_guests(db_session, reference_date=now) == 1
    assert await _user_exists(db_session, guest.id) is False


@pytest.mark.asyncio
async def test_departed_guest_kept_while_one_of_several_other_runs_is_not_due(db_session: AsyncSession):
    """The guest stays while run C is not due, and goes with the sweep once C is due."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-multi-not-due")
    ended = now - timedelta(days=LK_A + 1)
    run_a = await _run(db_session, org, state=RunState.ENDED, ended_at=ended)
    run_b = await _run(db_session, org, state=RunState.ENDED, ended_at=ended)
    run_c = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    guest = await _departed_guest(db_session, org, run_a)
    await _join_team(db_session, org, run_b, guest, name="B-Team")
    await _join_team(db_session, org, run_c, guest, name="C-Team")

    assert await sweep_run_guests(db_session, reference_date=now) == 0
    assert await _user_exists(db_session, guest.id) is True

    # Run C ends and its period passes: a later sweep takes the account.
    run_c.state = RunState.ENDED
    run_c.ended_at = now + timedelta(days=1)
    await db_session.flush()
    later = now + timedelta(days=LK_A + 2)

    assert await sweep_run_guests(db_session, reference_date=later) == 1
    assert await _user_exists(db_session, guest.id) is False


@pytest.mark.asyncio
async def test_departed_registered_account_never_deleted(db_session: AsyncSession):
    """The role filter holds for a departure row of a registered account."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-registered-departed")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    registered, member = await _registered_member(
        db_session, org, await _team(db_session, org, run), display_name="Reg"
    )
    await db_session.delete(member)
    await db_session.flush()
    await _departure(db_session, org, run, registered)

    assert await sweep_run_guests(db_session, reference_date=now) == 0
    assert await _user_exists(db_session, registered.id) is True


# ---------------------------------------------------------------------------
# LK_C — residual run anonymization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_anonymized_after_lkc_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-anon")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C + 1))
    team = await _team(db_session, org, run, name="Die Stadtfüchse")

    anonymized = await sweep_run_anonymize(db_session, reference_date=now)

    assert anonymized == 1
    await db_session.refresh(run)
    await db_session.refresh(team)
    assert run.retention_anonymized_at is not None
    assert team.name == "Team 1"


@pytest.mark.asyncio
async def test_run_anonymize_kept_within_lkc_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-anon-keep")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C - 1))
    team = await _team(db_session, org, run, name="Die Stadtfüchse")

    assert await sweep_run_anonymize(db_session, reference_date=now) == 0
    await db_session.refresh(run)
    await db_session.refresh(team)
    assert run.retention_anonymized_at is None
    assert team.name == "Die Stadtfüchse"


@pytest.mark.asyncio
async def test_run_anonymize_idempotent(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-anon-idem")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C + 10))
    await _team(db_session, org, run, name="Die Stadtfüchse")

    assert await sweep_run_anonymize(db_session, reference_date=now) == 1
    assert await sweep_run_anonymize(db_session, reference_date=now) == 0


@pytest.mark.asyncio
async def test_run_anonymize_leaves_namepending_teams(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-anon-null")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C + 1))
    team = await _team(db_session, org, run, name=None)  # name-pending

    assert await sweep_run_anonymize(db_session, reference_date=now) == 1
    await db_session.refresh(team)
    assert team.name is None  # nothing to anonymize


# ---------------------------------------------------------------------------
# Content-media orphans — soft-deleted station title images (shared-key guard)
# ---------------------------------------------------------------------------


async def _creator(db: AsyncSession, org):
    return await create_test_user(
        db,
        email=f"creator-{uuid.uuid4().hex[:8]}@retention.example.com",
        role=UserRole.CREATOR,
        organization=org,
    )


@pytest.mark.asyncio
async def test_orphaned_station_image_deleted(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-orphan")
    creator = await _creator(db_session, org)
    station = await create_test_station(db_session, organization=org, created_by_user_id=creator.id)
    key = upload_file(PNG, _unique_key("ret-orphan"), "image/png")
    station.title_image_key = key
    station.is_deleted = True
    station.deleted_at = now
    await db_session.flush()
    assert _object_exists(key)

    deleted = await sweep_orphaned_station_media(db_session)

    assert deleted == 1
    assert not _object_exists(key)
    await db_session.refresh(station)
    assert station.title_image_key is None


@pytest.mark.asyncio
async def test_shared_station_image_kept_when_snapshot_references_it(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-orphan-shared")
    creator = await _creator(db_session, org)
    station = await create_test_station(db_session, organization=org, created_by_user_id=creator.id)
    key = upload_file(PNG, _unique_key("ret-orphan-shared"), "image/png")
    station.title_image_key = key
    station.is_deleted = True
    station.deleted_at = now
    await db_session.flush()

    # A live run snapshot still froze the same key — must NOT be deleted.
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    await _run_station(db_session, org, run, title_image_key=key)

    deleted = await sweep_orphaned_station_media(db_session)

    assert deleted == 0
    assert _object_exists(key)
    await db_session.refresh(station)
    assert station.title_image_key == key
    get_minio_client().remove_object(settings.storage.bucket, key)


# ---------------------------------------------------------------------------
# Orphaned participant media: run-scoped uploads no submission references.
# "Old" is injected via a future reference_date,
# the same seam the other classes use for their windows.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreferenced_old_run_media_deleted(db_session: AsyncSession):
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-media")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")
    assert _object_exists(key)

    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )

    assert deleted == 1
    assert not _object_exists(key)


@pytest.mark.asyncio
async def test_unreferenced_fresh_run_media_kept(db_session: AsyncSession):
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-fresh")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")

    deleted = await sweep_orphaned_submission_media(db_session, reference_date=now)

    assert deleted == 0
    assert _object_exists(key)
    get_minio_client().remove_object(settings.storage.bucket, key)


@pytest.mark.asyncio
async def test_referenced_old_run_media_kept(db_session: AsyncSession):
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-ref")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")
    await _submission(db_session, org, run, team, rc, content={"photo_url": key})

    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )

    assert deleted == 0
    assert _object_exists(key)
    get_minio_client().remove_object(settings.storage.bucket, key)


@pytest.mark.asyncio
async def test_variant_keys_of_referenced_photo_are_kept(db_session: AsyncSession):
    """The variant keys of a referenced photo count
    as referenced: no submission names `<key>.thumb.webp` itself, but the
    photo beside it is a live answer."""
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-variant-ref")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")
    thumb_key = _upload_object(f"{key}.thumb.webp", PNG, "image/webp")
    display_key = _upload_object(f"{key}.display.webp", PNG, "image/webp")
    await _submission(db_session, org, run, team, rc, content={"photo_url": key})

    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )

    assert deleted == 0
    assert _object_exists(key)
    assert _object_exists(thumb_key)
    assert _object_exists(display_key)
    client = get_minio_client()
    for kept in (key, thumb_key, display_key):
        client.remove_object(settings.storage.bucket, kept)


@pytest.mark.asyncio
async def test_variant_keys_of_orphaned_photo_are_deleted(db_session: AsyncSession):
    """An orphaned photo's variants are unreferenced
    run-scoped objects of their own and go in the same sweep pass."""
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-variant-del")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")
    thumb_key = _upload_object(f"{key}.thumb.webp", PNG, "image/webp")
    display_key = _upload_object(f"{key}.display.webp", PNG, "image/webp")

    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )

    assert deleted == 3  # the photo and both of its variants
    assert not _object_exists(key)
    assert not _object_exists(thumb_key)
    assert not _object_exists(display_key)


@pytest.mark.asyncio
async def test_original_named_like_a_variant_is_swept_with_its_own_variants(db_session: AsyncSession):
    """The upload route renames a filename
    ending in ``.thumb.webp``/``.display.webp`` to ``-thumb.webp``/
    ``-display.webp`` before the key is formed, so a real original's key can
    end in ``-thumb.webp``. It must still be classified as an original
    (``variant_base_key`` only recognises the ``.thumb.webp`` /
    ``.display.webp`` shape with the leading dot) and swept together with
    its own two variants, not misread as a variant of a nonexistent base."""
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-dash-variant-name")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto-thumb.webp")
    thumb_key = _upload_object(f"{key}.thumb.webp", PNG, "image/webp")
    display_key = _upload_object(f"{key}.display.webp", PNG, "image/webp")

    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )

    assert deleted == 3  # the renamed original and both of its variants
    assert not _object_exists(key)
    assert not _object_exists(thumb_key)
    assert not _object_exists(display_key)


@pytest.mark.asyncio
async def test_old_original_with_newer_variant_deletes_both(db_session: AsyncSession):
    """An eligible original must take a
    variant with it even when the variant is a few seconds newer. The
    upload writes them apart in real time, so age-checking each key on its
    own could leave the variant behind."""
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-variant-newer")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")
    original_mtime = _list_mtime(key)

    time.sleep(2.5)  # push the variants' real storage timestamp past the original's
    thumb_key = _upload_object(f"{key}.thumb.webp", PNG, "image/webp")
    display_key = _upload_object(f"{key}.display.webp", PNG, "image/webp")
    thumb_mtime = _list_mtime(thumb_key)
    assert thumb_mtime > original_mtime

    # Place the cutoff strictly between the original and the variants: the
    # original is eligible, a variant checked on its own would not be.
    cutoff = original_mtime + timedelta(milliseconds=1500)
    assert cutoff < thumb_mtime
    reference_date = cutoff + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS)

    deleted = await sweep_orphaned_submission_media(db_session, reference_date=reference_date)

    assert deleted == 3  # the original and both of its (newer) variants
    assert not _object_exists(key)
    assert not _object_exists(thumb_key)
    assert not _object_exists(display_key)


@pytest.mark.asyncio
async def test_leftover_variant_without_original_respects_own_age(db_session: AsyncSession):
    """A variant whose original no longer exists is a leftover of its
    own, and is only swept once the variant itself is old enough."""
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-leftover")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    base_key = f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png"
    thumb_key = _upload_object(f"{base_key}.thumb.webp", PNG, "image/webp")
    # No original at that base_key — already gone, or never a standalone object.

    deleted = await sweep_orphaned_submission_media(db_session, reference_date=now)
    assert deleted == 0
    assert _object_exists(thumb_key)

    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )
    assert deleted == 1
    assert not _object_exists(thumb_key)


@pytest.mark.asyncio
async def test_referenced_upload_named_like_a_variant_is_kept(db_session: AsyncSession):
    """An object whose own key is referenced is never deleted, even
    when its filename happens to end in ``.thumb.webp``. The ``key in
    referenced`` check must run before the variant/original classification,
    not only against the key's derived base."""
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-variant-named-ref")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.thumb.webp")
    await _submission(db_session, org, run, team, rc, content={"photo_url": key})

    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )

    assert deleted == 0
    assert _object_exists(key)
    get_minio_client().remove_object(settings.storage.bucket, key)


@pytest.mark.asyncio
async def test_original_deleted_but_referenced_variant_survives(db_session: AsyncSession):
    """An eligible original takes its
    variants with it, except a variant key a submission references directly.
    The original itself carries no reference, only its ``.thumb.webp``
    derivate does (submission validation only requires a key to sit under
    the team's own upload prefix)."""
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-variant-partial-ref")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    rs = await _run_station(db_session, org, run)
    rc = await _run_challenge(db_session, org, run, rs)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")
    thumb_key = _upload_object(f"{key}.thumb.webp", PNG, "image/webp")
    display_key = _upload_object(f"{key}.display.webp", PNG, "image/webp")
    # Some answer names the thumb variant directly — the original itself is unreferenced.
    await _submission(db_session, org, run, team, rc, content={"photo_url": thumb_key})

    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )

    assert deleted == 2  # the original and the unreferenced display variant
    assert not _object_exists(key)
    assert _object_exists(thumb_key)
    assert not _object_exists(display_key)
    get_minio_client().remove_object(settings.storage.bucket, thumb_key)


@pytest.mark.asyncio
async def test_station_and_org_media_keys_never_touched(db_session: AsyncSession):
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-foreign")
    creator = await _creator(db_session, org)
    station = await create_test_station(db_session, organization=org, created_by_user_id=creator.id)
    station_key = _upload_object(f"{org.id}/stations/{station.id}/{uuid.uuid4().hex[:8]}_bild.png")
    org_media_key = _upload_object(f"{org.id}/media/{uuid.uuid4().hex}_logo.png")

    # Unreferenced and old, but not run-scoped: the key shape alone protects them.
    deleted = await sweep_orphaned_submission_media(
        db_session,
        reference_date=now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1),
    )

    assert deleted == 0
    assert _object_exists(station_key)
    assert _object_exists(org_media_key)
    client = get_minio_client()
    client.remove_object(settings.storage.bucket, station_key)
    client.remove_object(settings.storage.bucket, org_media_key)


@pytest.mark.asyncio
async def test_orphan_sweep_counted_in_summary(db_session: AsyncSession, monkeypatch):
    """The deletion lands in the sweep summary under its own key."""
    now = datetime.now(UTC)
    _purge_run_scoped_objects()
    org = await _org(db_session, "ret-orph-summary")
    run = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    key = _upload_object(f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_foto.png")

    # The runner calls each task without a reference_date, so "old" comes from
    # retention_tasks' own clock — shift it past the orphan grace window. The
    # fixture data keeps every other class at zero (active run, no guests,
    # chat, invitations or links).
    future = now + timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS + 1)

    class _ShiftedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(retention_tasks, "datetime", _ShiftedDatetime)

    @contextlib.asynccontextmanager
    async def _test_session_maker():
        yield db_session

    monkeypatch.setattr(retention, "async_session_maker", lambda: _test_session_maker())

    summary = await retention.retention_sweep_once()

    assert summary["orphaned_submission_media"] == 1
    assert not _object_exists(key)


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_emits_audit_event(db_session: AsyncSession, configured_logging: str):
    import json
    from pathlib import Path

    now = datetime.now(UTC)
    org = await _org(db_session, "ret-audit")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_B + 1))
    team = await _team(db_session, org, run)
    await _chat(db_session, org, team, count=2)

    await sweep_run_chat(db_session, reference_date=now)

    audit_log = Path(configured_logging) / "audit.log"
    entries = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]
    swept = [e for e in entries if e.get("event") == "retention_swept"]
    assert swept, "expected a retention_swept audit event"
    assert swept[-1]["deletion_class"] == DeletionClass.LK_B.value
    assert swept[-1]["kind"] == "chat_messages"
    assert swept[-1]["swept"] == 2


# ---------------------------------------------------------------------------
# Expired result links: not a Löschklasse, but still swept daily
# so a published link can't outlive its own expiry forever.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_result_link_deleted(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-expired")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    await _result_link(db_session, run, expires_at=now - timedelta(days=1))

    deleted = await sweep_expired_result_links(db_session, reference_date=now)

    assert deleted == 1
    remaining = (await db_session.execute(select(RunResultLink).where(RunResultLink.run_id == run.id))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_result_link_kept_before_expiry(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-keep")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    await _result_link(db_session, run, expires_at=now + timedelta(days=1))

    assert await sweep_expired_result_links(db_session, reference_date=now) == 0


@pytest.mark.asyncio
async def test_expired_revoked_result_link_also_deleted(db_session: AsyncSession):
    """A revoked link is swept on the same expiry schedule a live one would be."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-revoked")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    await _result_link(
        db_session,
        run,
        expires_at=now - timedelta(days=1),
        revoked_at=now - timedelta(hours=1),
    )

    assert await sweep_expired_result_links(db_session, reference_date=now) == 1


@pytest.mark.asyncio
async def test_result_link_sweep_idempotent(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-idem")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    await _result_link(db_session, run, expires_at=now - timedelta(days=1))

    assert await sweep_expired_result_links(db_session, reference_date=now) == 1
    assert await sweep_expired_result_links(db_session, reference_date=now) == 0


@pytest.mark.asyncio
async def test_result_link_sweep_leaves_run_untouched(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-run-safe")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    await _result_link(db_session, run, expires_at=now - timedelta(days=1))

    await sweep_expired_result_links(db_session, reference_date=now)

    await db_session.refresh(run)
    assert run.is_deleted is False
    assert run.state == RunState.ENDED


async def _result_notice(db: AsyncSession, org, run: Run, **extra: object) -> None:
    """A RESULTS_AVAILABLE feed entry of the run; ``result_link=True`` makes it the notice of a published result."""
    from app.models.feed_event import FeedEvent, FeedEventType

    db.add(
        FeedEvent(
            organization_id=org.id,
            run_id=run.id,
            event_type=FeedEventType.RESULTS_AVAILABLE,
            content={"de": "Hinweis", "en": "Notice", **extra},
        )
    )
    await db.flush()


async def _feed_contents(db: AsyncSession, run: Run) -> list[dict]:
    from app.models.feed_event import FeedEvent

    return list((await db.execute(select(FeedEvent.content).where(FeedEvent.run_id == run.id))).scalars().all())


@pytest.mark.asyncio
async def test_result_link_sweep_removes_the_notice_of_the_expired_link(db_session: AsyncSession):
    """The notice leaves with the expired link, across organizations, and no interim notice returns."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-notice")
    other_org = await _org(db_session, "ret-link-notice-other")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=40))
    other_run = await _run(db_session, other_org, state=RunState.ENDED, ended_at=now - timedelta(days=40))
    untouched_run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    for an_org, a_run in ((org, run), (other_org, other_run), (org, untouched_run)):
        await _result_notice(db_session, an_org, a_run)  # the run-end notice, no result_link
        await _result_notice(db_session, an_org, a_run, result_link=True)
    await _result_link(db_session, run, expires_at=now - timedelta(days=1))
    await _result_link(db_session, other_run, expires_at=now - timedelta(days=1))
    await _result_link(db_session, untouched_run, expires_at=now + timedelta(days=1))

    assert await sweep_expired_result_links(db_session, reference_date=now) == 2

    for swept in (run, other_run):
        contents = await _feed_contents(db_session, swept)
        assert contents == [{"de": "Hinweis", "en": "Notice"}]
        assert not any(c.get("interim") for c in contents)
    assert len(await _feed_contents(db_session, untouched_run)) == 2


@pytest.mark.asyncio
async def test_result_link_sweep_keeps_the_notice_of_a_run_published_again(db_session: AsyncSession):
    """The withdrawn link expires, the link published after it is valid: link and notice stay."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-republished")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=40))
    await _result_link(db_session, run, expires_at=now - timedelta(days=1), revoked_at=now - timedelta(days=10))
    live = await _result_link(db_session, run, expires_at=now + timedelta(days=20))
    await _result_notice(db_session, org, run, result_link=True)

    assert await sweep_expired_result_links(db_session, reference_date=now) == 1

    remaining = (await db_session.execute(select(RunResultLink.id).where(RunResultLink.run_id == run.id))).scalars()
    assert [str(link_id) for link_id in remaining] == [str(live.id)]
    assert [c.get("result_link") for c in await _feed_contents(db_session, run)] == [True]


@pytest.mark.asyncio
async def test_result_link_sweep_takes_the_run_rows_before_it_deletes(db_session: AsyncSession):
    """The run rows of the expired links are locked, in ascending id, before a link is deleted."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-lock-order")
    runs = [await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=40)) for _ in range(3)]
    for run in runs:
        await _result_link(db_session, run, expires_at=now - timedelta(days=1))
    kept = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    await _result_link(db_session, kept, expires_at=now + timedelta(days=1))

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ANN202
        statements.append(" ".join(statement.split()))

    sync_engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(sync_engine, "before_cursor_execute", _record)
    locked: list = []
    original = retention_tasks.lock_run_ids

    async def _spy(session, run_ids):  # noqa: ANN001, ANN202
        held = await original(session, run_ids)
        locked.append(held)
        return held

    retention_tasks.lock_run_ids = _spy  # type: ignore[assignment]
    try:
        assert await sweep_expired_result_links(db_session, reference_date=now) == 3
    finally:
        retention_tasks.lock_run_ids = original  # type: ignore[assignment]
        event.remove(sync_engine, "before_cursor_execute", _record)

    assert [[str(run_id) for run_id in held] for held in locked] == [sorted(str(run.id) for run in runs)]
    lock_at = next(i for i, s in enumerate(statements) if "FROM runs" in s and "FOR NO KEY UPDATE" in s)
    delete_at = next(i for i, s in enumerate(statements) if s.startswith("DELETE FROM run_result_links"))
    assert "ORDER BY runs.id" in statements[lock_at]
    assert lock_at < delete_at


@pytest.mark.asyncio
async def test_result_link_sweep_emits_audit_event(db_session: AsyncSession, configured_logging: str):
    import json
    from pathlib import Path

    now = datetime.now(UTC)
    org = await _org(db_session, "ret-link-audit")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    await _result_link(db_session, run, expires_at=now - timedelta(days=1))

    await sweep_expired_result_links(db_session, reference_date=now)

    audit_log = Path(configured_logging) / "audit.log"
    entries = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]
    swept = [e for e in entries if e.get("event") == "retention_swept" and e.get("kind") == "run_result_links"]
    assert swept, "expected a retention_swept audit event for result links"
    assert swept[-1]["swept"] == 1


# ---------------------------------------------------------------------------
# Roster names and free text (answers, score and feedback comments)
# ---------------------------------------------------------------------------


async def _registered_member(db: AsyncSession, org, team, *, display_name: str) -> tuple[User, TeamMember]:
    user = await create_test_user(
        db,
        email=f"member-{uuid.uuid4().hex[:10]}@retention.example.com",
        display_name=display_name,
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    member = TeamMember(
        organization_id=org.id,
        team_id=team.id,
        user_id=user.id,
        display_name=display_name,
        is_guest=False,
    )
    db.add(member)
    await db.flush()
    return user, member


async def _score(db: AsyncSession, org, submission, *, points: int, comment: str | None) -> Score:
    score = Score(
        organization_id=org.id,
        submission_id=submission.id,
        points=points,
        max_points=10,
        comment=comment,
    )
    db.add(score)
    await db.flush()
    return score


async def _feedback(db: AsyncSession, org, run, *, rating: int, comment: str | None) -> Feedback:
    feedback = Feedback(organization_id=org.id, run_id=run.id, rating=rating, comment=comment)
    db.add(feedback)
    await db.flush()
    return feedback


async def _member_name(db: AsyncSession, member_id) -> str:
    """The stored name, read past the session cache (the sweeps use bulk UPDATEs)."""
    return (
        await db.execute(
            select(TeamMember.display_name).where(TeamMember.id == member_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _statistics(db: AsyncSession, run: Run) -> dict[str, int]:
    """The numbers the analytics and the result page are built from."""
    team_ids = select(Team.id).where(Team.run_id == run.id)
    submission_ids = select(Submission.id).where(Submission.run_id == run.id)
    return {
        "teams": await db.scalar(select(func.count()).select_from(Team).where(Team.run_id == run.id)) or 0,
        "members": await db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id.in_(team_ids)))
        or 0,
        "guests": await db.scalar(
            select(func.count())
            .select_from(TeamMember)
            .where(TeamMember.team_id.in_(team_ids), TeamMember.is_guest.is_(True))
        )
        or 0,
        "submissions": await db.scalar(select(func.count()).select_from(Submission).where(Submission.run_id == run.id))
        or 0,
        "scores": await db.scalar(
            select(func.count()).select_from(Score).where(Score.submission_id.in_(submission_ids))
        )
        or 0,
        "points": await db.scalar(select(func.sum(Score.points)).where(Score.submission_id.in_(submission_ids))) or 0,
        "feedback": await db.scalar(select(func.count()).select_from(Feedback).where(Feedback.run_id == run.id)) or 0,
        "rating_sum": await db.scalar(select(func.sum(Feedback.rating)).where(Feedback.run_id == run.id)) or 0,
    }


def test_clear_submission_answer_keys_keeps_only_media():
    content = {
        "text": "Wir wohnen in der Maxstraße",
        "selected_option": "B",
        "scanned_value": "QR-1",
        "photo_url": "o/r/t/a.png",
        "media_urls": ["o/r/t/b.png"],
    }
    assert clear_submission_answer_keys(content) == {"photo_url": "o/r/t/a.png", "media_urls": ["o/r/t/b.png"]}
    assert clear_submission_answer_keys({"text": "x"}) == {}
    # The purge marker is no answer: the views need it to say "Medium entfernt".
    assert clear_submission_answer_keys({"text": "x", MEDIA_REMOVED_FLAG: True}) == {MEDIA_REMOVED_FLAG: True}
    assert retention_tasks.SUBMISSION_MARKER_FIELDS == (MEDIA_REMOVED_FLAG,)
    assert content["text"] == "Wir wohnen in der Maxstraße"  # a copy, never in place


@pytest.mark.asyncio
async def test_guest_sweep_removes_guest_roster_name_and_keeps_registered_member(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-guest-name")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    team = await _team(db_session, org, run)
    _guest, guest_member = await _guest_member(db_session, org, team, display_name="Amira K.")
    user, registered_member = await _registered_member(db_session, org, team, display_name="Jonas Weber")
    before = await _statistics(db_session, run)

    assert await sweep_run_guests(db_session, reference_date=now) == 1

    assert await _member_name(db_session, guest_member.id) == ANONYMIZED_MEMBER_NAME
    # The registered member's account still exists, so its name stays until LK_C.
    assert await _member_name(db_session, registered_member.id) == "Jonas Weber"
    assert await _user_exists(db_session, user.id) is True
    assert await _statistics(db_session, run) == before  # team size and guest count stay


@pytest.mark.asyncio
async def test_guest_sweep_catches_up_rows_an_earlier_version_left(db_session: AsyncSession):
    """Old data: the guest account is long gone, the roster still shows the name."""
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-guest-legacy")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 30))
    team = await _team(db_session, org, run)
    legacy = TeamMember(organization_id=org.id, team_id=team.id, user_id=None, display_name="Amira K.", is_guest=True)
    db_session.add(legacy)
    await db_session.flush()

    assert await sweep_run_guests(db_session, reference_date=now) == 0  # no account left to delete
    assert await _member_name(db_session, legacy.id) == ANONYMIZED_MEMBER_NAME

    # Second pass: nothing left to change, and the row is still there.
    assert await sweep_run_guests(db_session, reference_date=now) == 0
    assert await _member_name(db_session, legacy.id) == ANONYMIZED_MEMBER_NAME


@pytest.mark.asyncio
async def test_guest_roster_name_kept_within_lka_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-guest-keep")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A - 1))
    team = await _team(db_session, org, run)
    _guest, member = await _guest_member(db_session, org, team, display_name="Amira K.")

    assert await sweep_run_guests(db_session, reference_date=now) == 0
    assert await _member_name(db_session, member.id) == "Amira K."


@pytest.mark.asyncio
async def test_anonymize_user_removes_the_roster_name(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-anon-user")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    user, member = await _registered_member(db_session, org, team, display_name="Jonas Weber")
    _other, other_member = await _registered_member(db_session, org, team, display_name="Lena Braun")

    assert await anonymize_user(db_session, user.id, actor=dict(SYSTEM_RETENTION_ACTOR)) is True

    assert await _member_name(db_session, member.id) == ANONYMIZED_MEMBER_NAME
    assert await _member_name(db_session, other_member.id) == "Lena Braun"
    assert (await _statistics(db_session, run))["members"] == 2


@pytest.mark.asyncio
async def test_already_anonymized_accounts_lose_their_roster_name(db_session: AsyncSession):
    """Old data: the account is anonymized, but the roster still holds the name."""
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-anon-legacy")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    team = await _team(db_session, org, run)
    user, member = await _registered_member(db_session, org, team, display_name="Jonas Weber")
    _other, other_member = await _registered_member(db_session, org, team, display_name="Lena Braun")
    user.email = f"deleted-user-{user.id}@anonymized.local"
    user.display_name = "Deleted User"
    await db_session.flush()

    assert await sweep_anonymized_member_names(db_session, reference_date=now) == 1
    assert await _member_name(db_session, member.id) == ANONYMIZED_MEMBER_NAME
    assert await _member_name(db_session, other_member.id) == "Lena Braun"
    assert await sweep_anonymized_member_names(db_session, reference_date=now) == 0  # idempotent


@pytest.mark.asyncio
async def test_run_anonymize_removes_every_roster_name(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-lkc-names")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C + 1))
    team = await _team(db_session, org, run, name="Die Stadtfüchse")
    _user, member = await _registered_member(db_session, org, team, display_name="Jonas Weber")
    recent = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C - 1))
    _user2, recent_member = await _registered_member(
        db_session, org, await _team(db_session, org, recent), display_name="Lena Braun"
    )
    before = await _statistics(db_session, run)

    assert await sweep_run_anonymize(db_session, reference_date=now) == 1

    assert await _member_name(db_session, member.id) == ANONYMIZED_MEMBER_NAME
    assert await _member_name(db_session, recent_member.id) == "Lena Braun"
    assert await _statistics(db_session, run) == before


@pytest.mark.asyncio
async def test_run_anonymize_catches_up_a_run_an_earlier_version_marked(db_session: AsyncSession):
    """Old data: the run carries the marker already, its roster still shows names."""
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-lkc-legacy")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C + 40))
    run.retention_anonymized_at = now - timedelta(days=39)
    team = await _team(db_session, org, run, name="Team 1")
    _user, member = await _registered_member(db_session, org, team, display_name="Jonas Weber")

    assert await sweep_run_anonymize(db_session, reference_date=now) == 0  # no run newly anonymized
    assert await _member_name(db_session, member.id) == ANONYMIZED_MEMBER_NAME


async def _run_with_answers(db: AsyncSession, org, *, ended_days_ago: int, now: datetime):
    run = await _run(db, org, state=RunState.ENDED, ended_at=now - timedelta(days=ended_days_ago))
    team = await _team(db, org, run)
    station = await _run_station(db, org, run)
    text_challenge = await _run_challenge(db, org, run, station, ctype=ChallengeType.TEXT)
    quiz_challenge = await _run_challenge(db, org, run, station, ctype=ChallengeType.QUIZ)
    photo_challenge = await _run_challenge(db, org, run, station, ctype=ChallengeType.PHOTO)
    text = await _submission(db, org, run, team, text_challenge, content={"text": "Ich heiße Amira und wohne hier"})
    quiz = await _submission(db, org, run, team, quiz_challenge, content={"selected_option": "B"})
    photo = await _submission(
        db, org, run, team, photo_challenge, content={"photo_url": "org/run/team/a.png", "text": "Das sind wir"}
    )
    text_score = await _score(db, org, text, points=7, comment="Amira hat das gut erklärt")
    quiz_score = await _score(db, org, quiz, points=10, comment=None)
    feedback = await _feedback(db, org, run, rating=4, comment="Frau Weber war sehr nett")
    await _feedback(db, org, run, rating=2, comment=None)
    return run, {"text": text, "quiz": quiz, "photo": photo}, {"text": text_score, "quiz": quiz_score}, feedback


async def _content(db: AsyncSession, submission_id) -> dict:
    return (
        await db.execute(
            select(Submission.content).where(Submission.id == submission_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_answer_text_removed_after_lkh_window_and_statistics_stay(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-lkh")
    run, subs, scores, feedback = await _run_with_answers(db_session, org, ended_days_ago=LK_H + 1, now=now)
    before = await _statistics(db_session, run)
    assert before["submissions"] == 3 and before["points"] == 17 and before["rating_sum"] == 6

    # 3 submissions + 1 score comment + 1 feedback comment
    assert await sweep_run_answer_text(db_session, reference_date=now) == 5

    assert await _content(db_session, subs["text"].id) == {}
    assert await _content(db_session, subs["quiz"].id) == {}
    # The media key belongs to LK_B and stays, or the orphan sweep would take the object early.
    assert await _content(db_session, subs["photo"].id) == {"photo_url": "org/run/team/a.png"}
    score_rows = (
        await db_session.execute(
            select(Score.points, Score.comment)
            .where(Score.id.in_([scores["text"].id, scores["quiz"].id]))
            .execution_options(populate_existing=True)
        )
    ).all()
    assert sorted(points for points, _ in score_rows) == [7, 10]
    assert all(comment is None for _, comment in score_rows)
    stored_feedback = (
        await db_session.execute(
            select(Feedback.rating, Feedback.comment)
            .where(Feedback.id == feedback.id)
            .execution_options(populate_existing=True)
        )
    ).one()
    assert stored_feedback == (4, None)
    # No row is gone: every count and every sum is what it was.
    assert await _statistics(db_session, run) == before


@pytest.mark.asyncio
async def test_answer_text_kept_within_lkh_window(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-lkh-keep")
    _run_, subs, scores, feedback = await _run_with_answers(db_session, org, ended_days_ago=LK_H - 1, now=now)

    assert await sweep_run_answer_text(db_session, reference_date=now) == 0

    assert await _content(db_session, subs["text"].id) == {"text": "Ich heiße Amira und wohne hier"}
    assert await _content(db_session, subs["quiz"].id) == {"selected_option": "B"}
    await db_session.refresh(scores["text"])
    await db_session.refresh(feedback)
    assert scores["text"].comment == "Amira hat das gut erklärt"
    assert feedback.comment == "Frau Weber war sehr nett"


@pytest.mark.asyncio
async def test_answer_text_sweep_idempotent(db_session: AsyncSession):
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-lkh-idem")
    run, _subs, _scores, _feedback_ = await _run_with_answers(db_session, org, ended_days_ago=LK_H + 10, now=now)

    assert await sweep_run_answer_text(db_session, reference_date=now) == 5
    after_first = await _statistics(db_session, run)
    assert await sweep_run_answer_text(db_session, reference_date=now) == 0
    assert await _statistics(db_session, run) == after_first


@pytest.mark.asyncio
async def test_answer_text_sweep_follows_the_organization_period(db_session: AsyncSession):
    now = datetime.now(UTC)
    short = await _org(db_session, "gap-lkh-short")
    default = await _org(db_session, "gap-lkh-default")
    short.settings = {"retention": {"LK_H": 90}}
    await db_session.flush()
    _r1, short_subs, _s1, _f1 = await _run_with_answers(db_session, short, ended_days_ago=100, now=now)
    _r2, default_subs, _s2, _f2 = await _run_with_answers(db_session, default, ended_days_ago=100, now=now)

    assert await sweep_run_answer_text(db_session, reference_date=now) == 5

    assert await _content(db_session, short_subs["text"].id) == {}
    assert await _content(db_session, default_subs["text"].id) == {"text": "Ich heiße Amira und wohne hier"}


@pytest.mark.asyncio
async def test_every_sweep_together_keeps_rows_scores_and_the_result_link(db_session: AsyncSession):
    """A run past every period: names and text are gone, every number is still there."""
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-all")
    run, subs, _scores, _feedback_ = await _run_with_answers(db_session, org, ended_days_ago=LK_C + 5, now=now)
    team = (await db_session.execute(select(Team).where(Team.run_id == run.id))).scalar_one()
    await _guest_member(db_session, org, team, display_name="Amira K.")
    await _registered_member(db_session, org, team, display_name="Jonas Weber")
    # ``_statistics`` counts rows, so the object behind the key need not exist;
    # the photo answer loses its key to LK_B and its text to LK_H.
    link = await _result_link(db_session, run, expires_at=now + timedelta(days=30))
    before = await _statistics(db_session, run)

    for task in (
        sweep_run_media,
        sweep_run_chat,
        sweep_run_guests,
        sweep_run_anonymize,
        sweep_run_answer_text,
        sweep_anonymized_member_names,
        sweep_expired_result_links,
    ):
        await task(db_session, reference_date=now)

    assert await _statistics(db_session, run) == before
    names = (
        (
            await db_session.execute(
                select(TeamMember.display_name)
                .where(TeamMember.team_id == team.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert names == [ANONYMIZED_MEMBER_NAME, ANONYMIZED_MEMBER_NAME]
    for submission in subs.values():
        assert await _content(db_session, submission.id) == {}
    assert await db_session.scalar(select(func.count()).select_from(RunResultLink).where(RunResultLink.id == link.id))


@pytest.mark.asyncio
async def test_answer_text_sweep_keeps_the_purge_marker(db_session: AsyncSession):
    """A purged photo answer still says "Medium entfernt" after its text is gone."""
    now = datetime.now(UTC)
    org = await _org(db_session, "gap-lkh-marker")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_H + 1))
    team = await _team(db_session, org, run)
    challenge = await _run_challenge(db_session, org, run, await _run_station(db_session, org, run))
    purged = await _submission(
        db_session, org, run, team, challenge, content={MEDIA_REMOVED_FLAG: True, "text": "Das sind wir"}
    )

    assert await sweep_run_answer_text(db_session, reference_date=now) == 1
    assert await _content(db_session, purged.id) == {MEDIA_REMOVED_FLAG: True}
    assert await sweep_run_answer_text(db_session, reference_date=now) == 0  # the marker alone is no answer


@pytest.mark.asyncio
async def test_a_stored_value_the_json_decoder_refuses_stops_the_sweep_for_that_organization_only(
    db_session: AsyncSession,
):
    """A stored value the JSON decoder refuses stops the sweep for that organization only.

    PostgreSQL takes a number of 5,000 digits into JSONB. The driver decodes
    JSONB with ``json.loads``, which refuses an integer of that length. The
    settings of each organization are decoded inside the guarded loop,
    so this one row stops the sweep for its organization only.
    """
    now = datetime.now(UTC)
    broken = await _org(db_session, "gap-undecodable")
    short = await _org(db_session, "gap-undecodable-short")
    default = await _org(db_session, "gap-undecodable-default")
    short.settings = {"retention": {"LK_H": 90}}
    await db_session.flush()
    _r0, broken_subs, _s0, _f0 = await _run_with_answers(db_session, broken, ended_days_ago=100, now=now)
    _r1, short_subs, _s1, _f1 = await _run_with_answers(db_session, short, ended_days_ago=100, now=now)
    _r2, default_subs, _s2, _f2 = await _run_with_answers(db_session, default, ended_days_ago=100, now=now)
    await db_session.execute(
        sql_text(
            "UPDATE organizations SET settings = "
            "('{\"retention\":{\"LK_H\":' || repeat('9', 5000) || '}}')::jsonb WHERE id = :id"
        ),
        {"id": broken.id},
    )

    assert await sweep_run_answer_text(db_session, reference_date=now) == 5

    # The organization with the shorter period was swept, the others keep their answers:
    # the default one is not due yet, and the unreadable one is left out of the pass.
    assert await _content(db_session, short_subs["text"].id) == {}
    assert await _content(db_session, default_subs["text"].id) == {"text": "Ich heiße Amira und wohne hier"}
    assert await _content(db_session, broken_subs["text"].id) == {"text": "Ich heiße Amira und wohne hier"}


_UNDECODABLE_SETTINGS = (
    "UPDATE organizations SET settings = "
    "('{\"retention\":{\"LK_H\":' || repeat('9', 5000) || '}}')::jsonb WHERE id = :id"
)
_ANSWER = {"text": "Ich heiße Amira und wohne hier"}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_override", [False, True], ids=["single-cutoff", "per-organization"])
async def test_nothing_of_an_organization_with_unreadable_settings_is_deleted(
    db_session: AsyncSession, with_override: bool
):
    """Unreadable settings are no default.

    Nobody knows which period the organization wanted, so a run that ended
    longer ago than the default period stays as well. Both eligibility paths
    are covered: the single cutoff (no organization overrides the class) and
    the per-organization filter (another organization does).
    """
    now = datetime.now(UTC)
    suffix = "o" if with_override else "s"
    broken = await _org(db_session, f"gap-unreadable-old-{suffix}")
    default = await _org(db_session, f"gap-unreadable-old-default-{suffix}")
    if with_override:
        short = await _org(db_session, f"gap-unreadable-old-short-{suffix}")
        short.settings = {"retention": {"LK_H": 90}}
        await db_session.flush()
    _r0, broken_subs, _s0, _f0 = await _run_with_answers(db_session, broken, ended_days_ago=LK_H + 35, now=now)
    _r1, default_subs, _s1, _f1 = await _run_with_answers(db_session, default, ended_days_ago=LK_H + 35, now=now)
    await db_session.execute(sql_text(_UNDECODABLE_SETTINGS), {"id": broken.id})

    await sweep_run_answer_text(db_session, reference_date=now)

    assert await _content(db_session, default_subs["text"].id) == {}  # the sweep went on for the others
    assert await _content(db_session, broken_subs["text"].id) == _ANSWER


@pytest.mark.asyncio
async def test_unreadable_settings_are_warned_once_per_organization_and_pass(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """One pass reads the eligibility several times, the warning comes once."""
    from structlog.testing import CapturingLogger

    now = datetime.now(UTC)
    broken = await _org(db_session, "gap-unreadable-warned")
    await db_session.execute(sql_text(_UNDECODABLE_SETTINGS), {"id": broken.id})
    capturing = CapturingLogger()
    monkeypatch.setattr(retention_tasks, "logger", capturing)

    with retention_tasks.override_warning_scope():
        for deletion_class in (DeletionClass.LK_B, DeletionClass.LK_B, DeletionClass.LK_H):
            await retention_tasks.ended_due_run_ids(db_session, now, deletion_class)

    warnings = [
        call
        for call in capturing.calls
        if call.method_name == "warning"
        and call.args == ("retention_settings_unreadable",)
        and call.kwargs.get("org_id") == str(broken.id)
    ]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_an_abandoned_run_of_an_organization_with_unreadable_settings_does_not_stop_the_guest_sweep(
    db_session: AsyncSession,
):
    """The guest sweep leaves out an organization whose settings the decoder refuses.

    The guest sweep selects abandoned runs by itself, and a run loads its
    organization with it. Without the guard, the abandoned run of an
    organization whose settings the decoder refuses raises inside that fetch,
    before the cleanup begins, and the task rolls back the cleanup of every
    organization. With the guard, the healthy one is cleaned.
    """
    now = datetime.now(UTC)
    abandoned_since = now - timedelta(days=settings.retention.abandoned_run_days + 1)
    broken = await _org(db_session, "gap-unreadable-abandoned")
    healthy = await _org(db_session, "gap-unreadable-abandoned-healthy")
    broken_run = await _run(db_session, broken, state=RunState.ACTIVE, started_at=abandoned_since)
    healthy_run = await _run(db_session, healthy, state=RunState.ACTIVE, started_at=abandoned_since)
    broken_guest, _m0 = await _guest_member(db_session, broken, await _team(db_session, broken, broken_run))
    healthy_guest, _m1 = await _guest_member(db_session, healthy, await _team(db_session, healthy, healthy_run))
    broken_guest_id, healthy_guest_id = broken_guest.id, healthy_guest.id
    await db_session.execute(sql_text(_UNDECODABLE_SETTINGS), {"id": broken.id})
    # The sweep runs in a session of its own: nothing is loaded yet, so the run
    # loads its organization from the database, the stored settings included.
    db_session.expunge_all()

    assert await sweep_run_guests(db_session, reference_date=now) == 1

    assert await _user_exists(db_session, healthy_guest_id) is False
    # Nothing of the organization with unreadable settings is due in this pass.
    assert await _user_exists(db_session, broken_guest_id) is True


@pytest.mark.asyncio
async def test_settings_that_hold_the_unreadable_marker_leave_the_organization_out(db_session: AsyncSession):
    """The marker the engine's decoder returns for an undecodable value reads as unreadable settings.

    It is no settings object without an override: the default period is no
    answer for it either.
    """
    now = datetime.now(UTC)
    marked = await _org(db_session, "gap-unreadable-marker")
    default = await _org(db_session, "gap-unreadable-marker-default")
    marked.settings = unreadable_json_marker()
    await db_session.flush()
    _r0, marked_subs, _s0, _f0 = await _run_with_answers(db_session, marked, ended_days_ago=LK_H + 35, now=now)
    _r1, default_subs, _s1, _f1 = await _run_with_answers(db_session, default, ended_days_ago=LK_H + 35, now=now)

    await sweep_run_answer_text(db_session, reference_date=now)

    assert await _content(db_session, default_subs["text"].id) == {}
    assert await _content(db_session, marked_subs["text"].id) == _ANSWER


# ---------------------------------------------------------------------------
# LK_A — the guest cleanup keeps an account that an open run still needs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_departed_guest_kept_while_departed_from_a_run_not_due(db_session: AsyncSession):
    """Left a due run and left a run that is not due: the second departure keeps the account.

    The departure row of the open run is what refuses a new join of that run
    from the same browser. It goes with the account by the cascade, so the
    account stays until that run is due as well.
    """
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-two-departures")
    due = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    fresh = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    guest = await _departed_guest(db_session, org, due)
    await _departure(db_session, org, fresh, guest)

    assert await sweep_run_guests(db_session, reference_date=now) == 0
    assert await _user_exists(db_session, guest.id) is True
    kept = await db_session.execute(
        select(func.count()).select_from(RunDeparture).where(RunDeparture.run_id == fresh.id)
    )
    assert kept.scalar_one() == 1

    # The open run ends and its period passes: a later sweep takes the account.
    fresh.state = RunState.ENDED
    fresh.ended_at = now + timedelta(days=1)
    await db_session.flush()

    assert await sweep_run_guests(db_session, reference_date=now + timedelta(days=LK_A + 2)) == 1
    assert await _user_exists(db_session, guest.id) is False


@pytest.mark.asyncio
async def test_member_guest_kept_while_departed_from_a_run_not_due(db_session: AsyncSession):
    """The same for a guest who is still a member of the due run."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-member-and-departure")
    due = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    fresh = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    guest, member = await _guest_member(db_session, org, await _team(db_session, org, due))
    await _departure(db_session, org, fresh, guest)

    assert await delete_run_guests(db_session, due.id, org.id, {due.id}) == 0
    assert await _user_exists(db_session, guest.id) is True
    await db_session.refresh(member)
    assert member.user_id == guest.id


@pytest.mark.asyncio
async def test_guest_deleted_when_the_other_membership_is_in_a_deleted_team(db_session: AsyncSession):
    """A member row that points to a deleted team of a run that is not due protects nothing."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-deleted-team")
    due = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    fresh = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    guest = await _departed_guest(db_session, org, due)
    stale = await _join_team(db_session, org, fresh, guest, name="Aufgelöst")
    gone_team = await db_session.get(Team, stale.team_id)
    assert gone_team is not None
    gone_team.is_deleted = True
    gone_team.deleted_at = now
    await db_session.flush()

    assert await sweep_run_guests(db_session, reference_date=now) == 1
    assert await _user_exists(db_session, guest.id) is False
    await db_session.refresh(stale)
    assert stale.user_id is None


@pytest.mark.asyncio
async def test_guest_kept_while_member_of_a_live_team_beside_a_deleted_one(db_session: AsyncSession):
    """The live team of the open run still protects; the deleted one beside it changes nothing."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-live-and-deleted-team")
    due = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    fresh = await _run(db_session, org, state=RunState.ACTIVE, started_at=now - timedelta(days=1))
    guest = await _departed_guest(db_session, org, due)
    stale = await _join_team(db_session, org, fresh, guest, name="Aufgelöst")
    gone_team = await db_session.get(Team, stale.team_id)
    assert gone_team is not None
    gone_team.is_deleted = True
    gone_team.deleted_at = now
    await _join_team(db_session, org, fresh, guest, name="Lebt")
    await db_session.flush()

    assert await sweep_run_guests(db_session, reference_date=now) == 0
    assert await _user_exists(db_session, guest.id) is True


async def _delete_run_guests_with_a_change_before_the_delete(db: AsyncSession, run, org, values: dict) -> int:
    """Run the cleanup of ``run`` and write ``values`` to its guest accounts right before the DELETE.

    The select of the accounts has run by then and holds its locks, so no other
    transaction can make this change. The event makes it in the transaction of
    the cleanup itself, at the one place where the select and the DELETE could
    disagree: after the last read, before the DELETE is sent.
    """
    fired: list[bool] = []

    def change_before_delete(state) -> None:
        if state.is_delete and not fired:
            fired.append(True)
            state.session.execute(
                update(User)
                .where(User.organization_id == org.id, User.role == UserRole.GUEST)
                .values(**values)
                .execution_options(synchronize_session=False)
            )

    event.listen(db.sync_session, "do_orm_execute", change_before_delete)
    try:
        deleted = await delete_run_guests(db, run.id, org.id, {run.id})
    finally:
        event.remove(db.sync_session, "do_orm_execute", change_before_delete)
    assert fired, "the cleanup sent no DELETE"
    return deleted


@pytest.mark.asyncio
async def test_account_whose_role_changed_before_the_delete_is_not_deleted(db_session: AsyncSession):
    """The DELETE repeats the role: an account that is no guest any more stays, and is not counted."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-role-change")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    guest, member = await _guest_member(db_session, org, await _team(db_session, org, run))

    deleted = await _delete_run_guests_with_a_change_before_the_delete(
        db_session, run, org, {"role": UserRole.PARTICIPANT}
    )

    assert deleted == 0
    assert await _user_exists(db_session, guest.id) is True
    await db_session.refresh(member)
    assert member.user_id == guest.id


@pytest.mark.asyncio
async def test_account_whose_organization_changed_before_the_delete_is_not_deleted(db_session: AsyncSession):
    """The DELETE repeats the organization as well."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-org-change")
    other_org = await _org(db_session, "ret-guest-org-change-other")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    guest, _ = await _guest_member(db_session, org, await _team(db_session, org, run))

    deleted = await _delete_run_guests_with_a_change_before_the_delete(
        db_session, run, org, {"organization_id": other_org.id}
    )

    assert deleted == 0
    assert await _user_exists(db_session, guest.id) is True


@pytest.mark.asyncio
async def test_delete_run_guests_counts_the_rows_the_delete_took(db_session: AsyncSession):
    """Without a change in between the count is the number of accounts deleted."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-guest-rowcount")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_A + 1))
    team = await _team(db_session, org, run)
    first, _ = await _guest_member(db_session, org, team)
    second, _ = await _guest_member(db_session, org, team)

    assert await delete_run_guests(db_session, run.id, org.id, {run.id}) == 2
    assert await _user_exists(db_session, first.id) is False
    assert await _user_exists(db_session, second.id) is False


# ---------------------------------------------------------------------------
# A dissolved team — the sweeps of its run still reach what it left stored
# ---------------------------------------------------------------------------


async def _dissolved_team_with_stored_data(db: AsyncSession, org, run, *, photo_key: str) -> dict:
    """A team with a chat message, a photo answer, a text answer and a guest, then dissolved.

    The dissolution is the one of the application: the member row goes, the
    guest gets a departure row, the team row is marked as deleted. Chat,
    answers and the media object stay stored.
    """
    team = await _team(db, org, run, name="Die Aufgelösten")
    station = await _run_station(db, org, run)
    photo_challenge = await _run_challenge(db, org, run, station, ctype=ChallengeType.PHOTO)
    text_challenge = await _run_challenge(db, org, run, station, ctype=ChallengeType.TEXT)
    photo = await _submission(db, org, run, team, photo_challenge, content={"photo_url": photo_key})
    text = await _submission(db, org, run, team, text_challenge, content={"text": "Wir wohnen in der Maxstraße"})
    await _chat(db, org, team, count=1)
    guest, _member = await _guest_member(db, org, team, display_name="Amira K.")

    result = await dissolve_team(db, team)
    assert result.member_count == 1 and result.answer_count == 2

    return {"team": team, "photo": photo, "text": text, "guest": guest}


async def _team_state(db: AsyncSession, team_id) -> tuple[str | None, bool, datetime | None]:
    """Name and the two fields of the dissolution, read past the session cache."""
    return (
        (
            await db.execute(
                select(Team.name, Team.is_deleted, Team.deleted_at)
                .where(Team.id == team_id)
                .execution_options(populate_existing=True)
            )
        )
        .one()
        ._tuple()
    )


async def _chat_count(db: AsyncSession, team_id) -> int:
    return await db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.team_id == team_id)) or 0


async def _member_rows(db: AsyncSession, team_id) -> int:
    return await db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)) or 0


_RUN_SWEEPS = (
    sweep_run_chat,
    sweep_run_media,
    sweep_run_answer_text,
    sweep_run_guests,
    sweep_run_anonymize,
    sweep_anonymized_member_names,
)


@pytest.mark.asyncio
async def test_sweeps_reach_a_dissolved_team_at_the_periods_of_its_run(db_session: AsyncSession):
    """Chat, media, answer text, team name and guest of a dissolved team go as those of a live team.

    A live team of the same run stands beside it: the sweeps take its data in
    the same pass and leave it a live team.
    """
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-dissolved")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C + 1))
    photo_key = upload_file(PNG, _unique_key("ret-dissolved"), "image/png")
    stored = await _dissolved_team_with_stored_data(db_session, org, run, photo_key=photo_key)
    dissolved = stored["team"]
    live = await _team(db_session, org, run, name="Die Stadtfüchse")
    await _chat(db_session, org, live, count=1)
    live_guest, _live_member = await _guest_member(db_session, org, live, display_name="Jonas W.")

    # What the dissolution left: no member row, the guest known by its departure only.
    _name, is_deleted, deleted_at = await _team_state(db_session, dissolved.id)
    assert is_deleted is True and deleted_at is not None
    assert await _member_rows(db_session, dissolved.id) == 0
    assert await db_session.scalar(
        select(func.count()).select_from(RunDeparture).where(RunDeparture.user_id == stored["guest"].id)
    )
    assert _object_exists(photo_key)

    for task in _RUN_SWEEPS:
        await task(db_session, reference_date=now)

    assert await _chat_count(db_session, dissolved.id) == 0
    assert not _object_exists(photo_key)
    assert await _content(db_session, stored["photo"].id) == {}
    assert await _content(db_session, stored["text"].id) == {}
    name, is_deleted, deleted_at_after = await _team_state(db_session, dissolved.id)
    assert name in {f"{ANONYMIZED_TEAM_NAME} 1", f"{ANONYMIZED_TEAM_NAME} 2"}
    assert await _user_exists(db_session, stored["guest"].id) is False
    # The dissolved team is still the dissolved team: the sweeps changed its name only.
    assert is_deleted is True and deleted_at_after == deleted_at

    # The live team beside it: swept alike, and not dissolved by any sweep.
    live_name, live_is_deleted, live_deleted_at = await _team_state(db_session, live.id)
    assert live_name in {f"{ANONYMIZED_TEAM_NAME} 1", f"{ANONYMIZED_TEAM_NAME} 2"} and live_name != name
    assert live_is_deleted is False and live_deleted_at is None
    assert await _member_rows(db_session, live.id) == 1
    assert await _chat_count(db_session, live.id) == 0
    assert await _user_exists(db_session, live_guest.id) is False


@pytest.mark.asyncio
async def test_sweeps_keep_the_data_of_a_dissolved_team_within_the_periods(db_session: AsyncSession):
    """Dissolving a team deletes nothing early: before the periods of its run everything stays stored."""
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-dissolved-keep")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=1))
    photo_key = upload_file(PNG, _unique_key("ret-dissolved-keep"), "image/png")
    stored = await _dissolved_team_with_stored_data(db_session, org, run, photo_key=photo_key)
    dissolved = stored["team"]

    for task in _RUN_SWEEPS:
        assert await task(db_session, reference_date=now) == 0

    assert await _chat_count(db_session, dissolved.id) == 1
    assert _object_exists(photo_key)
    assert await _content(db_session, stored["photo"].id) == {"photo_url": photo_key}
    assert await _content(db_session, stored["text"].id) == {"text": "Wir wohnen in der Maxstraße"}
    name, is_deleted, _deleted_at = await _team_state(db_session, dissolved.id)
    assert name == "Die Aufgelösten" and is_deleted is True
    assert await _user_exists(db_session, stored["guest"].id) is True
    get_minio_client().remove_object(settings.storage.bucket, photo_key)


@pytest.mark.asyncio
async def test_sweeps_dissolve_no_team(db_session: AsyncSession):
    """A team that was not dissolved before the sweeps is not dissolved after them.

    Three shapes of a team in a run past every period: with a guest, with a
    registered member, and without any member row (old data).
    """
    now = datetime.now(UTC)
    org = await _org(db_session, "ret-dissolve-nothing")
    run = await _run(db_session, org, state=RunState.ENDED, ended_at=now - timedelta(days=LK_C + 1))
    with_guest = await _team(db_session, org, run, name="Gäste")
    await _guest_member(db_session, org, with_guest, display_name="Amira K.")
    with_member = await _team(db_session, org, run, name="Mitglieder")
    await _registered_member(db_session, org, with_member, display_name="Jonas Weber")
    without_rows = await _team(db_session, org, run, name="Ohne Zeilen")
    for team in (with_guest, with_member, without_rows):
        await _chat(db_session, org, team, count=1)

    for task in _RUN_SWEEPS:
        await task(db_session, reference_date=now)

    for team, member_rows in ((with_guest, 1), (with_member, 1), (without_rows, 0)):
        _name, is_deleted, deleted_at = await _team_state(db_session, team.id)
        assert is_deleted is False and deleted_at is None
        assert await _member_rows(db_session, team.id) == member_rows
