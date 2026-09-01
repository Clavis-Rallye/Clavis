"""Tests for the org-admin media purge.

Covers the two admin endpoints end to end: the summary counts what a purge
would delete, and the purge deletes every object of the requested type
(a photo's WebP variants included) while keeping the submission rows and
scores, nulls the content key, sets ``media_removed``, and audits
``org.media_purged`` with type and count. Other roles are refused with 403,
a non-matching confirmation slug with 422 CONFIRMATION_MISMATCH, and another
organization's media is never touched.

Media tests touch the real object store (the Garage container that `make test`
runs on 127.0.0.1:59000; conftest uses a per-xdist-worker bucket). Object keys
are made unique per test so parallel workers don't collide, the same approach as
``test_retention_sweep.py``, whose builders these tests mirror.
"""

import io
import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog
from httpx import AsyncClient
from minio.error import S3Error
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging import configure_logging
from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.submission import Submission
from app.models.team import Team
from app.models.user import UserRole
from app.services.audit import reset_integrity_chain
from app.services.media_purge import _classify
from app.storage import ensure_bucket, get_minio_client
from tests.factories import create_test_organization, create_test_user, get_auth_headers

PNG = b"\x89PNG\r\n\x1a\n" + b"clavis-purge-test"


@pytest.fixture(autouse=True)
def _ensure_bucket() -> None:
    """CI starts a fresh Garage per run, so the bucket may not exist yet."""
    ensure_bucket()


@pytest.fixture
def configured_logging():
    """Audit logging into a temp dir (same shape as test_retention_sweep.py)."""
    import contextlib
    import logging as _logging

    import app.logging as _log_module

    with tempfile.TemporaryDirectory() as tmpdir:
        structlog.reset_defaults()
        _log_module._loggingConfigured = False
        configure_logging(environment="development", log_dir=tmpdir)
        reset_integrity_chain()

        yield tmpdir

        for name in ("audit", "app", "error", "access", ""):
            lg = _logging.getLogger(name)
            for h in list(lg.handlers):
                with contextlib.suppress(Exception):
                    h.close()
            lg.handlers.clear()
        _log_module._loggingConfigured = False
        structlog.reset_defaults()


# ---------------------------------------------------------------------------
# Builders — minimal object graphs, mirroring test_retention_sweep.py.
# ---------------------------------------------------------------------------


def _code(n: int = 6) -> str:
    return uuid.uuid4().hex[:n].upper()


async def _org(db: AsyncSession, slug: str):
    return await create_test_organization(db, name=f"Org {slug}", slug=slug)


async def _admin(db: AsyncSession, org, slug: str):
    return await create_test_user(
        db,
        email=f"admin-{slug}@purge.example.com",
        display_name=f"Admin {slug}",
        role=UserRole.ORG_ADMIN,
        organization=org,
    )


async def _run(db: AsyncSession, org) -> Run:
    run = Run(
        organization_id=org.id,
        name="Purge Run",
        state=RunState.ENDED,
        join_code=_code(),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    return run


async def _team(db: AsyncSession, org, run, *, name: str) -> Team:
    team = Team(organization_id=org.id, run_id=run.id, share_code=_code(8), name=name)
    db.add(team)
    await db.flush()
    return team


async def _run_challenge(db: AsyncSession, org, run, *, ctype: ChallengeType, sort_order: int = 0) -> RunChallenge:
    rc = RunChallenge(
        organization_id=org.id,
        run_id=run.id,
        run_station_id=(await _run_station(db, org, run)).id,
        challenge_type=ctype,
        title={"de": "Aufgabe"},
        instructions={"de": "Anleitung"},
        scoring_rules={},
        sort_order=sort_order,
    )
    db.add(rc)
    await db.flush()
    return rc


async def _run_station(db: AsyncSession, org, run) -> RunStation:
    rs = RunStation(organization_id=org.id, run_id=run.id, sort_order=0)
    db.add(rs)
    await db.flush()
    return rs


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


async def _score(db: AsyncSession, org, submission, *, points: int = 8, max_points: int = 10) -> Score:
    score = Score(
        organization_id=org.id,
        submission_id=submission.id,
        points=points,
        max_points=max_points,
    )
    db.add(score)
    await db.flush()
    return score


def _upload_key(org, run, team, filename: str) -> str:
    """A participant-upload key in the shape ``files.py`` writes."""
    return f"{org.id}/{run.id}/{team.id}/{uuid.uuid4().hex[:8]}_{filename}"


def _upload_object(key: str, data: bytes, content_type: str) -> str:
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


async def _purge(client: AsyncClient, org, admin, media_type: str, *, confirm_slug: str | None = None):
    return await client.post(
        f"/api/orgs/{org.slug}/admin/media/purge",
        headers=get_auth_headers(admin),
        json={"type": media_type, "confirm_slug": confirm_slug if confirm_slug is not None else org.slug},
    )


# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_counts_each_type(client: AsyncClient, db_session: AsyncSession):
    """The summary answers the stored photo/video/audio counts, the same set
    a purge of each type would delete. Text answers and other organizations'
    media do not count."""
    org = await _org(db_session, "purge-sum")
    admin = await _admin(db_session, org, "purge-sum")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": _upload_key(org, run, team, "a.png")},
    )
    await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO, sort_order=1),
        content={"photo_url": _upload_key(org, run, team, "b.png")},
    )
    await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.VIDEO, sort_order=2),
        content={"video_url": _upload_key(org, run, team, "c.mp4")},
    )
    await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.AUDIO, sort_order=3),
        content={"audio_url": _upload_key(org, run, team, "d.mp3")},
    )
    await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.TEXT, sort_order=4),
        content={"text": "nur Text"},
    )

    other_org = await _org(db_session, "purge-sum-other")
    other_run = await _run(db_session, other_org)
    other_team = await _team(db_session, other_org, other_run, name="Andere")
    await _submission(
        db_session,
        other_org,
        other_run,
        other_team,
        await _run_challenge(db_session, other_org, other_run, ctype=ChallengeType.PHOTO),
        content={"photo_url": _upload_key(other_org, other_run, other_team, "foreign.png")},
    )

    response = await client.get(
        f"/api/orgs/{org.slug}/admin/media/summary",
        headers=get_auth_headers(admin),
    )

    assert response.status_code == 200
    assert response.json() == {"photo": 2, "video": 1, "audio": 1}


# ---------------------------------------------------------------------------
# Purge of each type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_photo_deletes_objects_variants_and_marks_content(client: AsyncClient, db_session: AsyncSession):
    """A photo purge deletes the referenced object and both WebP variants,
    nulls the key, sets ``media_removed`` and leaves the rest of the answer."""
    org = await _org(db_session, "purge-photo")
    admin = await _admin(db_session, org, "purge-photo")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "foto.png"), PNG, "image/png")
    thumb_key = _upload_object(f"{key}.thumb.webp", PNG, "image/webp")
    display_key = _upload_object(f"{key}.display.webp", PNG, "image/webp")
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key, "text": "vor dem Rathaus"},
    )

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    assert response.json() == {"type": "photo", "purged": 1}
    assert not _object_exists(key)
    assert not _object_exists(thumb_key)
    assert not _object_exists(display_key)
    await db_session.refresh(sub)
    assert sub.content["photo_url"] is None
    assert sub.content["media_removed"] is True
    assert sub.content["text"] == "vor dem Rathaus"


@pytest.mark.asyncio
async def test_purge_classifies_by_stored_content_type(client: AsyncClient, db_session: AsyncSession):
    """The object's stored content type decides, not its file name ending.

    A PNG stored as photo.txt or photo.mp3 (type image/png) is a photo for the
    summary and the purge, and its variants go with it.
    """
    org = await _org(db_session, "purge-mime")
    admin = await _admin(db_session, org, "purge-mime")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    txt_key = _upload_object(_upload_key(org, run, team, "photo.txt"), PNG, "image/png")
    mp3_key = _upload_object(_upload_key(org, run, team, "photo.mp3"), PNG, "image/png")
    thumb_key = _upload_object(f"{txt_key}.thumb.webp", PNG, "image/webp")
    sub_txt = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": txt_key},
    )
    sub_mp3 = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO, sort_order=1),
        content={"photo_url": mp3_key},
    )

    summary = await client.get(
        f"/api/orgs/{org.slug}/admin/media/summary",
        headers=get_auth_headers(admin),
    )
    assert summary.status_code == 200
    assert summary.json() == {"photo": 2, "video": 0, "audio": 0}

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    assert response.json() == {"type": "photo", "purged": 2}
    assert not _object_exists(txt_key)
    assert not _object_exists(mp3_key)
    assert not _object_exists(thumb_key)
    for sub in (sub_txt, sub_mp3):
        await db_session.refresh(sub)
        assert sub.content["photo_url"] is None
        assert sub.content["media_removed"] is True


class _FailingStatClient:
    """A store client whose metadata lookup (HEAD) always fails."""

    def stat_object(self, *_args, **_kwargs):
        raise S3Error("InternalError", "store hiccup", "/", "req", "host", None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_failed_metadata_lookup_never_falls_back_to_file_name(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """A failed metadata lookup is not an object without a stored type.

    The object is skipped, reported as not classified, and never classified by its
    file name. photo.mp3 stored as image/png survives an audio purge whose
    classification lookup failed, while the storage calls themselves work.
    """
    from app.services import media_purge

    org = await _org(db_session, "purge-lookup-fail")
    admin = await _admin(db_session, org, "purge-lookup-fail")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "photo.mp3"), PNG, "image/png")
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key},
    )

    # Only the classification lookup fails; delete_file uses the storage module's own client.
    monkeypatch.setattr(media_purge, "get_minio_client", lambda: _FailingStatClient())

    summary = await client.get(
        f"/api/orgs/{org.slug}/admin/media/summary",
        headers=get_auth_headers(admin),
    )
    assert summary.json() == {"photo": 0, "video": 0, "audio": 0}

    response = await _purge(client, org, admin, "audio")
    assert response.status_code == 200
    assert response.json()["purged"] == 0
    assert _object_exists(key)
    await db_session.refresh(sub)
    assert sub.content["photo_url"] == key
    assert "media_removed" not in sub.content

    result = await media_purge.purge_org_media_report(db_session, org.id, "audio")
    assert result.purged == 0
    assert result.unclassified == [key]
    assert _object_exists(key)


@pytest.mark.asyncio
async def test_purge_video_marks_content(client: AsyncClient, db_session: AsyncSession):
    org = await _org(db_session, "purge-video")
    admin = await _admin(db_session, org, "purge-video")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "clip.mp4"), b"clavis-purge-video", "video/mp4")
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.VIDEO),
        content={"video_url": key},
    )

    response = await _purge(client, org, admin, "video")

    assert response.status_code == 200
    assert response.json() == {"type": "video", "purged": 1}
    assert not _object_exists(key)
    await db_session.refresh(sub)
    assert sub.content["video_url"] is None
    assert sub.content["media_removed"] is True


@pytest.mark.asyncio
async def test_purge_audio_marks_content(client: AsyncClient, db_session: AsyncSession):
    org = await _org(db_session, "purge-audio")
    admin = await _admin(db_session, org, "purge-audio")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "ton.mp3"), b"clavis-purge-audio", "audio/mpeg")
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.AUDIO),
        content={"audio_url": key},
    )

    response = await _purge(client, org, admin, "audio")

    assert response.status_code == 200
    assert response.json() == {"type": "audio", "purged": 1}
    assert not _object_exists(key)
    await db_session.refresh(sub)
    assert sub.content["audio_url"] is None
    assert sub.content["media_removed"] is True


@pytest.mark.asyncio
async def test_purge_photo_leaves_video_of_same_submission(client: AsyncClient, db_session: AsyncSession):
    """A purge of one type nulls only that type's key — a CREATIVE answer
    that also carries a video keeps it (and its object)."""
    org = await _org(db_session, "purge-mixed")
    admin = await _admin(db_session, org, "purge-mixed")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    photo_key = _upload_object(_upload_key(org, run, team, "foto.png"), PNG, "image/png")
    video_key = _upload_object(_upload_key(org, run, team, "clip.mp4"), b"clavis-purge-mixed", "video/mp4")
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.CREATIVE),
        content={"photo_url": photo_key, "video_url": video_key, "text": "beides"},
    )

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    assert response.json() == {"purged": 1, "type": "photo"}
    assert not _object_exists(photo_key)
    assert _object_exists(video_key)
    await db_session.refresh(sub)
    assert sub.content["photo_url"] is None
    assert sub.content["video_url"] == video_key
    assert sub.content["media_removed"] is True
    get_minio_client().remove_object(settings.storage.bucket, video_key)


# ---------------------------------------------------------------------------
# Rows and scores stay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_keeps_submission_rows_and_scores(client: AsyncClient, db_session: AsyncSession):
    org = await _org(db_session, "purge-score")
    admin = await _admin(db_session, org, "purge-score")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "foto.png"), PNG, "image/png")
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key},
    )
    score = await _score(db_session, org, sub, points=8, max_points=10)

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    # The submission row stays. It is counted, not id-compared: the models' uuid7
    # ids are uuid_utils.UUID, which does not compare equal to uuid.UUID.
    row_count = await db_session.scalar(select(func.count()).select_from(Submission).where(Submission.id == sub.id))
    assert row_count == 1
    await db_session.refresh(score)
    assert score.points == 8
    assert score.max_points == 10


# ---------------------------------------------------------------------------
# Other organizations untouched (tenant isolation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_leaves_other_organization_untouched(client: AsyncClient, db_session: AsyncSession):
    org_a = await _org(db_session, "purge-isol-a")
    admin_a = await _admin(db_session, org_a, "purge-isol-a")
    run_a = await _run(db_session, org_a)
    team_a = await _team(db_session, org_a, run_a, name="Team A")
    key_a = _upload_object(_upload_key(org_a, run_a, team_a, "foto.png"), PNG, "image/png")
    sub_a = await _submission(
        db_session,
        org_a,
        run_a,
        team_a,
        await _run_challenge(db_session, org_a, run_a, ctype=ChallengeType.PHOTO),
        content={"photo_url": key_a},
    )

    org_b = await _org(db_session, "purge-isol-b")
    run_b = await _run(db_session, org_b)
    team_b = await _team(db_session, org_b, run_b, name="Team B")
    key_b = _upload_object(_upload_key(org_b, run_b, team_b, "foto.png"), PNG, "image/png")
    sub_b = await _submission(
        db_session,
        org_b,
        run_b,
        team_b,
        await _run_challenge(db_session, org_b, run_b, ctype=ChallengeType.PHOTO),
        content={"photo_url": key_b},
    )

    response = await _purge(client, org_a, admin_a, "photo")

    assert response.status_code == 200
    assert response.json() == {"type": "photo", "purged": 1}
    assert not _object_exists(key_a)
    await db_session.refresh(sub_a)
    assert sub_a.content["media_removed"] is True
    assert _object_exists(key_b)
    await db_session.refresh(sub_b)
    assert sub_b.content["photo_url"] == key_b
    assert "media_removed" not in sub_b.content
    get_minio_client().remove_object(settings.storage.bucket, key_b)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_refused_for_other_roles(client: AsyncClient, db_session: AsyncSession):
    """A coordinator may neither count nor purge — 403, nothing deleted."""
    org = await _org(db_session, "purge-role")
    coordinator = await create_test_user(
        db_session,
        email="coord@purge.example.com",
        display_name="Coord",
        role=UserRole.COORDINATOR,
        organization=org,
    )
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "foto.png"), PNG, "image/png")
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key},
    )

    summary = await client.get(
        f"/api/orgs/{org.slug}/admin/media/summary",
        headers=get_auth_headers(coordinator),
    )
    purge = await _purge(client, org, coordinator, "photo")

    assert summary.status_code == 403
    assert purge.status_code == 403
    assert _object_exists(key)
    await db_session.refresh(sub)
    assert sub.content["photo_url"] == key
    assert "media_removed" not in sub.content
    get_minio_client().remove_object(settings.storage.bucket, key)


@pytest.mark.asyncio
async def test_purge_refused_on_confirmation_mismatch(client: AsyncClient, db_session: AsyncSession):
    """A confirmation slug that does not match the organization's slug is a
    coded 422, and nothing is deleted."""
    org = await _org(db_session, "purge-confirm")
    admin = await _admin(db_session, org, "purge-confirm")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "foto.png"), PNG, "image/png")
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key},
    )

    response = await _purge(client, org, admin, "photo", confirm_slug="not-the-slug")

    assert response.status_code == 422
    assert response.json()["error_code"] == "CONFIRMATION_MISMATCH"
    assert _object_exists(key)
    await db_session.refresh(sub)
    assert sub.content["photo_url"] == key
    assert "media_removed" not in sub.content
    get_minio_client().remove_object(settings.storage.bucket, key)


# ---------------------------------------------------------------------------
# The object's type decides, not the field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_clears_every_reference_regardless_of_field(client: AsyncClient, db_session: AsyncSession):
    """A photo key referenced by one submission's ``photo_url`` and another's
    ``audio_url`` is still one photo object by its extension. The purge
    deletes it once and clears both references, whichever field holds it."""
    org = await _org(db_session, "purge-xfield")
    admin = await _admin(db_session, org, "purge-xfield")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "geteilt.png"), PNG, "image/png")
    sub_a = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key},
    )
    sub_b = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.AUDIO, sort_order=1),
        content={"audio_url": key, "text": "falsch abgelegt, gleicher Key"},
    )

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    assert response.json() == {"type": "photo", "purged": 1}
    assert not _object_exists(key)
    await db_session.refresh(sub_a)
    await db_session.refresh(sub_b)
    assert sub_a.content["photo_url"] is None
    assert sub_a.content["media_removed"] is True
    assert sub_b.content["audio_url"] is None
    assert sub_b.content["media_removed"] is True
    assert sub_b.content["text"] == "falsch abgelegt, gleicher Key"


@pytest.mark.asyncio
async def test_purge_counts_and_purges_media_urls_entries(client: AsyncClient, db_session: AsyncSession):
    """A photo sitting in a CREATIVE submission's ``media_urls`` list is
    counted by the summary and purged like any other photo. The entry is
    removed from the list, the rest of the answer stays."""
    org = await _org(db_session, "purge-mediaurls")
    admin = await _admin(db_session, org, "purge-mediaurls")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "kreativ.png"), PNG, "image/png")
    other_key = "https://example.com/other.mp4"
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.CREATIVE),
        content={"text": "Kreative Antwort", "media_urls": [key, other_key]},
    )

    summary = await client.get(
        f"/api/orgs/{org.slug}/admin/media/summary",
        headers=get_auth_headers(admin),
    )
    assert summary.status_code == 200
    assert summary.json()["photo"] == 1

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    assert response.json() == {"type": "photo", "purged": 1}
    assert not _object_exists(key)
    await db_session.refresh(sub)
    assert sub.content["media_urls"] == [other_key]
    assert sub.content["media_removed"] is True
    assert sub.content["text"] == "Kreative Antwort"


@pytest.mark.asyncio
async def test_summary_and_purge_count_shared_key_once(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """Two submissions sharing one photo key count and purge as one object,
    in the summary, the response and the audit."""
    org = await _org(db_session, "purge-shared")
    admin = await _admin(db_session, org, "purge-shared")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "gemeinsam.png"), PNG, "image/png")
    sub_a = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key},
    )
    sub_b = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO, sort_order=1),
        content={"photo_url": key},
    )

    summary = await client.get(
        f"/api/orgs/{org.slug}/admin/media/summary",
        headers=get_auth_headers(admin),
    )
    assert summary.status_code == 200
    assert summary.json()["photo"] == 1

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    assert response.json() == {"type": "photo", "purged": 1}
    assert not _object_exists(key)
    await db_session.refresh(sub_a)
    await db_session.refresh(sub_b)
    assert sub_a.content["photo_url"] is None
    assert sub_a.content["media_removed"] is True
    assert sub_b.content["photo_url"] is None
    assert sub_b.content["media_removed"] is True

    audit_log = Path(configured_logging) / "audit.log"
    entries = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]
    purged_events = [e for e in entries if e.get("event") == "org.media_purged"]
    assert purged_events, "expected an org.media_purged audit event"
    assert purged_events[-1]["media_type"] == "photo"
    assert purged_events[-1]["purged"] == 1


@pytest.mark.asyncio
async def test_purge_counts_referenced_variant_as_its_own_key(client: AsyncClient, db_session: AsyncSession):
    """A photo whose thumb variant is itself referenced directly is two distinct photo objects.

    Submission validation allows a variant key. Both objects are purged and both count,
    whatever order they are collected in. The display variant nothing references still
    rides along with the original, uncounted.
    """
    org = await _org(db_session, "purge-variant")
    admin = await _admin(db_session, org, "purge-variant")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "original.png"), PNG, "image/png")
    thumb_key = _upload_object(f"{key}.thumb.webp", PNG, "image/webp")
    display_key = _upload_object(f"{key}.display.webp", PNG, "image/webp")
    sub_original = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key},
    )
    sub_variant = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.CREATIVE, sort_order=1),
        content={"text": "Direkt referenzierte Vorschau", "media_urls": [thumb_key]},
    )

    summary = await client.get(
        f"/api/orgs/{org.slug}/admin/media/summary",
        headers=get_auth_headers(admin),
    )
    assert summary.status_code == 200
    assert summary.json()["photo"] == 2  # the original and the directly-referenced thumb, distinctly

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    assert response.json() == {"type": "photo", "purged": 2}
    assert not _object_exists(key)
    assert not _object_exists(thumb_key)
    assert not _object_exists(display_key)
    await db_session.refresh(sub_original)
    await db_session.refresh(sub_variant)
    assert sub_original.content["photo_url"] is None
    assert sub_original.content["media_removed"] is True
    assert sub_variant.content["media_urls"] == []
    assert sub_variant.content["media_removed"] is True


@pytest.mark.asyncio
async def test_media_urls_entry_of_unknown_type_stays(client: AsyncClient, db_session: AsyncSession):
    """A ``media_urls`` entry whose extension resolves to no known media type
    has no field of its own to fall back on. Every purge type leaves it
    alone, untouched and uncounted."""
    org = await _org(db_session, "purge-unknown")
    admin = await _admin(db_session, org, "purge-unknown")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    unknown_url = "https://example.com/asset-without-extension"
    sub = await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.CREATIVE),
        content={"text": "Kreative Antwort", "media_urls": [unknown_url]},
    )

    summary = await client.get(
        f"/api/orgs/{org.slug}/admin/media/summary",
        headers=get_auth_headers(admin),
    )
    assert summary.status_code == 200
    assert summary.json() == {"photo": 0, "video": 0, "audio": 0}

    for media_type in ("photo", "video", "audio"):
        response = await _purge(client, org, admin, media_type)
        assert response.status_code == 200
        assert response.json()["purged"] == 0

    await db_session.refresh(sub)
    assert sub.content["media_urls"] == [unknown_url]
    assert "media_removed" not in sub.content


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_audits_type_and_count(client: AsyncClient, db_session: AsyncSession, configured_logging: str):
    org = await _org(db_session, "purge-audit")
    admin = await _admin(db_session, org, "purge-audit")
    run = await _run(db_session, org)
    team = await _team(db_session, org, run, name="Füchse")
    key = _upload_object(_upload_key(org, run, team, "a.png"), PNG, "image/png")
    await _submission(
        db_session,
        org,
        run,
        team,
        await _run_challenge(db_session, org, run, ctype=ChallengeType.PHOTO),
        content={"photo_url": key},
    )

    response = await _purge(client, org, admin, "photo")

    assert response.status_code == 200
    audit_log = Path(configured_logging) / "audit.log"
    entries = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]
    purged_events = [e for e in entries if e.get("event") == "org.media_purged"]
    assert purged_events, "expected an org.media_purged audit event"
    assert purged_events[-1]["media_type"] == "photo"
    assert purged_events[-1]["purged"] == 1
    assert purged_events[-1]["organization_id"] == str(org.id)


@pytest.mark.parametrize(
    ("stored_type", "key", "expected"),
    [
        ("", "photo.png", "photo"),  # an empty type is a missing one: the file name decides
        ("   ", "photo.png", "photo"),
        ("Image/PNG", "photo.png", "photo"),
        (None, "photo.mp3", "audio"),  # a missing type falls back to the file name
        ("image/png; charset=binary", "photo.mp3", "photo"),
        ("Image/PNG; charset=binary", "photo.mp3", "photo"),
        ("  VIDEO/MP4 ", "clip.bin", "video"),
        ("", "notes.bin", None),  # nothing stored, nothing in the name, no reference
        ("Application/PDF", "photo.png", None),  # a stored type that is no medium still decides
    ],
)
def test_classify_reads_the_stored_type_without_regard_to_case_and_space(
    stored_type: str | None, key: str, expected: str | None
):
    assert _classify(key, [], stored_type) == expected


def test_classify_with_an_empty_stored_type_falls_back_to_the_referencing_field():
    """An empty type and a name that says nothing: the scalar field decides, as for a missing type."""
    assert _classify("upload", [(object(), "photo_url")], "") == "photo"  # type: ignore[list-item]
