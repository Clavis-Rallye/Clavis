"""Images of rich texts that no text shows any more are cleaned up.

The sweep deletes an organization image only when it is older than the grace
period and no text, copy, soft-deleted row or run copy names its key. Most
tests here prove the second half: an image that some place still shows
survives. "Old" is a future ``reference_date``, the seam the other retention
tests use.
"""

import io
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from minio.error import S3Error
from PIL import Image
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import Base
from app.models.organization import Organization
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.user import UserRole
from app.routers.media import MEDIA_SEGMENT
from app.services import retention
from app.services.content_media_retention import (
    CONTENT_MEDIA_ORPHAN_MIN_AGE_DAYS,
    NOT_CONTENT_TABLES,
    ORG_MEDIA_SEGMENT,
    REFERENCE_TABLES,
    sweep_orphaned_content_media,
    text_columns,
)
from app.services.richtext import media_prefix_for
from app.storage import ensure_bucket, get_minio_client
from tests.factories import (
    create_test_challenge,
    create_test_organization,
    create_test_rallye,
    create_test_station,
    create_test_user,
    get_auth_headers,
)


def _real_png() -> bytes:
    """A small decodable PNG: the upload routes re-encode images and refuse bytes that only look like one."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _real_png()
_ORG_MEDIA_KEY = re.compile(r"^[0-9a-f-]{36}/media/")


@pytest.fixture(autouse=True)
def _clean_org_media() -> None:
    """Start every test with no organization media in the worker's bucket, so counts are exact."""
    ensure_bucket()
    client = get_minio_client()
    for obj in client.list_objects(settings.storage.bucket, recursive=True):
        if _ORG_MEDIA_KEY.match(obj.object_name):
            client.remove_object(settings.storage.bucket, obj.object_name)


def _later() -> datetime:
    """A sweep date at which every object of this test is past the grace period."""
    return datetime.now(UTC) + timedelta(days=CONTENT_MEDIA_ORPHAN_MIN_AGE_DAYS + 1)


def _upload(org, name: str = "rathaus.png") -> str:
    """Store an image under the key shape of ``POST /api/orgs/{slug}/media/``."""
    key = f"{org.id}/{MEDIA_SEGMENT}/{uuid.uuid4()}_{name}"
    get_minio_client().put_object(settings.storage.bucket, key, io.BytesIO(PNG), length=len(PNG))
    return key


def _img(org, key: str) -> str:
    return f'<p>Vor dem Rathaus</p><img src="{media_prefix_for(org.slug)}{key}" alt="Rathaus">'


def _exists(key: str) -> bool:
    try:
        get_minio_client().stat_object(settings.storage.bucket, key)
        return True
    except S3Error:
        return False


async def _org(db: AsyncSession, label: str):
    return await create_test_organization(db, name=f"Org {label}", slug=f"img-{label}-{uuid.uuid4().hex[:6]}")


async def _run(db: AsyncSession, org) -> Run:
    run = Run(
        organization_id=org.id,
        name="Bildlauf",
        state=RunState.ENDED,
        join_code=uuid.uuid4().hex[:6].upper(),
        ended_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(run)
    await db.flush()
    return run


# ---------------------------------------------------------------------------
# What goes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_upload_never_saved_into_a_text_goes_after_the_grace_period(db_session: AsyncSession):
    org = await _org(db_session, "unsaved")
    key = _upload(org)

    assert await sweep_orphaned_content_media(db_session) == 0  # still inside the grace period
    assert _exists(key)

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 1
    assert not _exists(key)
    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0  # idempotent


@pytest.mark.asyncio
async def test_removing_the_image_from_the_text_cleans_up_the_file(client: AsyncClient, db_session: AsyncSession):
    """An image removed from a text through the real routes is cleaned up by the sweep."""
    org = await _org(db_session, "api")
    creator = await create_test_user(
        db_session, email=f"creator-{uuid.uuid4().hex[:6]}@img.test", role=UserRole.CREATOR, organization=org
    )
    headers = get_auth_headers(creator)

    upload = await client.post(
        f"/api/orgs/{org.slug}/media/",
        headers=headers,
        files={"file": ("Rathaus Platz.png", io.BytesIO(PNG), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    key, url = upload.json()["key"], upload.json()["url"]
    with_image = f'<p>Treffpunkt</p><img src="{url}" alt="Rathausplatz">'

    created = await client.post(
        f"/api/orgs/{org.slug}/stations/",
        headers=headers,
        json={"title": {"de": "Rathaus"}, "description": {"de": with_image}},
    )
    assert created.status_code == 201, created.text
    station_id = created.json()["id"]

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0
    assert _exists(key)

    removed = await client.put(
        f"/api/orgs/{org.slug}/stations/{station_id}",
        headers=headers,
        json={"description": {"de": "<p>Treffpunkt</p>"}},
    )
    assert removed.status_code == 200, removed.text

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 1
    assert not _exists(key)


# ---------------------------------------------------------------------------
# What stays
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_copy_keeps_the_image_after_the_original_drops_it(client: AsyncClient, db_session: AsyncSession):
    """A duplicated station carries the same ``src``. The file goes with the last text, not the first."""
    org = await _org(db_session, "copy")
    creator = await create_test_user(
        db_session, email=f"creator-{uuid.uuid4().hex[:6]}@img.test", role=UserRole.CREATOR, organization=org
    )
    headers = get_auth_headers(creator)
    key = _upload(org)
    original = await create_test_station(db_session, org, description={"de": _img(org, key)})

    copy = await client.post(f"/api/orgs/{org.slug}/stations/{original.id}/duplicate", headers=headers)
    assert copy.status_code == 201, copy.text
    assert key in copy.json()["description"]["de"]

    dropped = await client.put(
        f"/api/orgs/{org.slug}/stations/{original.id}", headers=headers, json={"description": {"de": "<p>Ohne</p>"}}
    )
    assert dropped.status_code == 200, dropped.text
    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0
    assert _exists(key)

    dropped = await client.put(
        f"/api/orgs/{org.slug}/stations/{copy.json()['id']}",
        headers=headers,
        json={"description": {"de": "<p>Ohne</p>"}},
    )
    assert dropped.status_code == 200, dropped.text
    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 1
    assert not _exists(key)


@pytest.mark.asyncio
async def test_an_image_another_language_still_shows_stays(db_session: AsyncSession):
    org = await _org(db_session, "lang")
    key = _upload(org)
    await create_test_station(db_session, org, description={"de": "<p>Ohne Bild</p>", "ar": _img(org, key)})

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0
    assert _exists(key)


@pytest.mark.asyncio
async def test_a_run_copy_keeps_the_image_after_the_template_lost_it(db_session: AsyncSession):
    """A run shows its frozen copy. The template station may change or be deleted for good."""
    org = await _org(db_session, "snapshot")
    station_key, task_key = _upload(org, "station.png"), _upload(org, "aufgabe.png")
    run = await _run(db_session, org)
    run_station = RunStation(
        organization_id=org.id,
        run_id=run.id,
        sort_order=0,
        title={"de": "Rathaus"},
        description={"de": _img(org, station_key)},
    )
    db_session.add(run_station)
    await db_session.flush()
    db_session.add(
        RunChallenge(
            organization_id=org.id,
            run_id=run.id,
            run_station_id=run_station.id,
            challenge_type="text",
            title={"de": "Frage"},
            instructions={"de": _img(org, task_key)},
            scoring_rules={},
            sort_order=0,
        )
    )
    await db_session.flush()

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0
    assert _exists(station_key)
    assert _exists(task_key)


@pytest.mark.asyncio
async def test_a_soft_deleted_station_keeps_its_image_because_it_can_be_restored(db_session: AsyncSession):
    org = await _org(db_session, "softdel")
    key = _upload(org)
    station = await create_test_station(db_session, org, description={"de": _img(org, key)})
    station.is_deleted = True
    station.deleted_at = datetime.now(UTC)
    await db_session.flush()

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0
    assert _exists(key)


@pytest.mark.asyncio
async def test_every_rich_text_field_counts_as_a_reference(db_session: AsyncSession):
    """Rallye description, station tips header, task instructions and an organization's legal text."""
    org = await _org(db_session, "fields")
    rallye_key, tips_key, task_key, legal_key = (_upload(org, f"{n}.png") for n in ("r", "t", "c", "l"))
    await create_test_rallye(db_session, org, description={"de": _img(org, rallye_key)})
    station = await create_test_station(db_session, org)
    station.tips = {"header": {"de": _img(org, tips_key)}, "links": []}
    await create_test_challenge(db_session, org, instructions={"de": _img(org, task_key)})
    await db_session.execute(
        update(Organization).where(Organization.id == org.id).values(legal_privacy=_img(org, legal_key))
    )
    await db_session.flush()

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0
    for key in (rallye_key, tips_key, task_key, legal_key):
        assert _exists(key), key


@pytest.mark.asyncio
async def test_a_variant_of_a_shown_image_stays(db_session: AsyncSession):
    """Should an image ever get a derived variant, it lives as long as its original."""
    org = await _org(db_session, "variant")
    key = _upload(org)
    variant = f"{key}.thumb.webp"
    get_minio_client().put_object(settings.storage.bucket, variant, io.BytesIO(PNG), length=len(PNG))
    await create_test_station(db_session, org, description={"de": _img(org, key)})

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0
    assert _exists(variant)
    get_minio_client().remove_object(settings.storage.bucket, variant)


@pytest.mark.asyncio
async def test_station_title_images_are_not_this_sweeps_business(db_session: AsyncSession):
    org = await _org(db_session, "title")
    title_key = f"{org.id}/stations/{uuid.uuid4()}/abc_titel.png"
    get_minio_client().put_object(settings.storage.bucket, title_key, io.BytesIO(PNG), length=len(PNG))

    assert await sweep_orphaned_content_media(db_session, reference_date=_later()) == 0
    assert _exists(title_key)
    get_minio_client().remove_object(settings.storage.bucket, title_key)


# ---------------------------------------------------------------------------
# What holds the sweep together
# ---------------------------------------------------------------------------


def test_every_table_with_text_chooses_a_side():
    """A new table with a text or JSON column must say whether it can show an organization's image."""
    # Other test modules put their own tables on the shared metadata (test_repository_base.py).
    test_only = {
        mapper.local_table.name for mapper in Base.registry.mappers if not mapper.class_.__module__.startswith("app.")
    }
    with_text = {name for name, table in Base.metadata.tables.items() if text_columns(table)} - test_only
    assert set(REFERENCE_TABLES).isdisjoint(NOT_CONTENT_TABLES)
    assert set(REFERENCE_TABLES) <= with_text, "a reference table without text columns is a typo"
    unclassified = sorted(with_text - set(REFERENCE_TABLES) - set(NOT_CONTENT_TABLES))
    assert unclassified == [], f"classify in content_media_retention.py: {unclassified}"


def test_the_segment_matches_the_upload_route():
    assert ORG_MEDIA_SEGMENT == MEDIA_SEGMENT


def test_the_daily_sweep_runs_it():
    assert ("orphaned_content_media", sweep_orphaned_content_media) in retention._RETENTION_TASKS
