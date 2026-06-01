"""Tests for the station title-image endpoints.

PUT/DELETE /api/orgs/{slug}/stations/{station_id}/title-image: creator-only
authoring of a station cover image. The image key lives in the queryable
`title_image_key` column; replacing or removing deletes the previous object, so
no orphans stay. Tenant isolation and image-only validation are enforced.

Assertions go through the response (the same-origin URL /api/files/{key}
carries the key) plus a direct object-store existence check, rather than
reading the DB session after the committing request. GET /api/files/{key}
serves the image to any member of the organization.
"""

import io
import random

import pytest
from httpx import AsyncClient
from minio.error import S3Error
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from app.config import settings
from app.models.run import Run, RunState
from app.models.user import UserRole
from app.repositories.run_station import RunStationRepository
from app.storage import ensure_bucket, get_minio_client
from tests.factories import (
    create_test_organization,
    create_test_station,
    create_test_user,
)


@pytest.fixture(autouse=True)
def _ensure_bucket() -> None:
    """CI starts a fresh Garage per run, so the bucket may not exist yet."""
    ensure_bucket()


def _encode(image: Image.Image, image_format: str, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **kwargs)
    return buffer.getvalue()


# The endpoint re-encodes the upload, so the bytes must be a real image.
PNG = _encode(Image.new("RGB", (8, 8), (30, 120, 200)), "PNG")


def _jpeg_with_gps() -> bytes:
    exif = Image.Exif()
    exif[34853] = {1: "N", 2: (48.0, 22.0, 0.0), 3: "E", 4: (10.0, 54.0, 0.0)}  # GPSInfo
    exif[271] = "PhoneMaker"  # Make
    return _encode(Image.new("RGB", (32, 32), (180, 40, 90)), "JPEG", exif=exif)


def _truncated_jpeg() -> bytes:
    rng = random.Random(7)
    noise = Image.frombytes("RGB", (64, 64), bytes(rng.randrange(256) for _ in range(64 * 64 * 3)))
    data = _encode(noise, "JPEG")
    return data[: len(data) // 2]


def _img(name: str = "cover.png", ctype: str = "image/png") -> dict:
    return {"file": (name, PNG, ctype)}


def _key_from_url(url: str) -> str:
    """Extract the object key from the same-origin URL ``/api/files/{key}``.

    A presigned URL signed against S3_ENDPOINT, which the browser cannot reach
    and the CSP blocks, so no title image URL may be presigned.
    """
    prefix = "/api/files/"
    assert url.startswith(prefix), url
    return url[len(prefix) :]


def _object_exists(key: str) -> bool:
    try:
        get_minio_client().stat_object(settings.storage.bucket, key)
        return True
    except S3Error:
        return False


async def _setup(db: AsyncSession, *, slug: str):
    org = await create_test_organization(db, name=f"Org {slug}", slug=slug)
    creator = await create_test_user(
        db, email=f"creator-{slug}@example.com", role=UserRole.CREATOR, organization_id=org.id
    )
    station = await create_test_station(db, organization=org, created_by_user_id=creator.id)
    token = create_access_token(user_id=creator.id, role=creator.role.value, organization_id=org.id)
    return org, creator, station, token


def _url(org_slug: str, station_id) -> str:
    return f"/api/orgs/{org_slug}/stations/{station_id}/title-image"


@pytest.mark.asyncio
async def test_creator_uploads_title_image(client: AsyncClient, db_session: AsyncSession) -> None:
    org, _creator, station, token = await _setup(db_session, slug="sti-up")

    resp = await client.put(_url(org.slug, station.id), files=_img(), cookies={"access_token": token})

    assert resp.status_code == 200, resp.text
    url = resp.json()["title_image_url"]
    assert url is not None
    key = _key_from_url(url)
    assert key.startswith(f"{org.id}/stations/{station.id}/")  # station-scoped, enumerable key
    assert _object_exists(key)


@pytest.mark.asyncio
async def test_replace_deletes_the_old_object(client: AsyncClient, db_session: AsyncSession) -> None:
    org, _creator, station, token = await _setup(db_session, slug="sti-rep")
    url = _url(org.slug, station.id)

    r1 = await client.put(url, files=_img("a.png"), cookies={"access_token": token})
    key1 = _key_from_url(r1.json()["title_image_url"])
    r2 = await client.put(url, files=_img("b.png"), cookies={"access_token": token})
    key2 = _key_from_url(r2.json()["title_image_url"])

    assert key2 != key1
    assert _object_exists(key2)
    assert not _object_exists(key1)


@pytest.mark.asyncio
async def test_remove_clears_and_deletes_object(client: AsyncClient, db_session: AsyncSession) -> None:
    org, _creator, station, token = await _setup(db_session, slug="sti-rm")
    url = _url(org.slug, station.id)
    r = await client.put(url, files=_img(), cookies={"access_token": token})
    key = _key_from_url(r.json()["title_image_url"])

    resp = await client.delete(url, cookies={"access_token": token})

    assert resp.status_code == 200
    assert resp.json()["title_image_url"] is None
    assert not _object_exists(key)


@pytest.mark.asyncio
async def test_remove_is_a_noop_when_no_image(client: AsyncClient, db_session: AsyncSession) -> None:
    org, _creator, station, token = await _setup(db_session, slug="sti-noop")

    resp = await client.delete(_url(org.slug, station.id), cookies={"access_token": token})

    assert resp.status_code == 200
    assert resp.json()["title_image_url"] is None


@pytest.mark.asyncio
async def test_non_image_is_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    org, _creator, station, token = await _setup(db_session, slug="sti-bad")

    resp = await client.put(
        _url(org.slug, station.id),
        files={"file": ("notes.txt", b"hello", "text/plain")},
        cookies={"access_token": token},
    )

    assert resp.status_code == 415
    assert resp.json()["error_code"] == "IMAGE_NOT_DECODABLE"
    got = await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", cookies={"access_token": token})
    assert got.status_code == 200
    assert got.json()["title_image_url"] is None


@pytest.mark.asyncio
async def test_participant_cannot_upload(client: AsyncClient, db_session: AsyncSession) -> None:
    org, _creator, station, _token = await _setup(db_session, slug="sti-part")
    participant = await create_test_user(
        db_session, email="participant@example.com", role=UserRole.PARTICIPANT, organization_id=org.id
    )
    ptoken = create_access_token(user_id=participant.id, role=participant.role.value, organization_id=org.id)

    resp = await client.put(_url(org.slug, station.id), files=_img(), cookies={"access_token": ptoken})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_station_from_another_org_is_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    org_a, _creator_a, station_a, _token_a = await _setup(db_session, slug="sti-iso-a")
    org_b = await create_test_organization(db_session, name="Org B", slug="sti-iso-b")
    creator_b = await create_test_user(
        db_session, email="creator-b@example.com", role=UserRole.CREATOR, organization_id=org_b.id
    )
    token_b = create_access_token(user_id=creator_b.id, role=creator_b.role.value, organization_id=org_b.id)

    # creator_b targets their own org's path but org_a's station id → not in scope.
    resp = await client.put(_url(org_b.slug, station_a.id), files=_img(), cookies={"access_token": token_b})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_participant_run_station_exposes_image_url(client: AsyncClient, db_session: AsyncSession) -> None:
    """The participant station list and the overview show the frozen title image as a same-origin URL, or null."""
    org = await create_test_organization(db_session, name="Org PRun", slug="sti-prun")
    guest = await create_test_user(
        db_session, email="guest-prun@example.com", role=UserRole.PARTICIPANT, organization_id=org.id
    )
    run = Run(
        organization_id=org.id,
        rallye_id=None,
        name="Snapshot Run",
        state=RunState.ACTIVE,
        join_code="STIPRN",
        settings={},
        content_languages=["de", "en"],
    )
    db_session.add(run)
    await db_session.flush()
    # Create snapshot rows one at a time (mirrors the snapshot service) — a
    # batched add_all trips SQLAlchemy's insertmanyvalues UUID sentinel.
    rs_repo = RunStationRepository(db_session, organization_id=org.id)
    await rs_repo.create(
        run_id=run.id,
        source_station_id=None,
        title={"de": "Mit Bild"},
        description={"de": "x"},
        title_image_key=f"{org.id}/stations/abc/de1f2_cover.png",
        sort_order=0,
    )
    await rs_repo.create(
        run_id=run.id,
        source_station_id=None,
        title={"de": "Ohne Bild"},
        description={"de": "x"},
        title_image_key=None,
        sort_order=1,
    )
    await db_session.commit()
    token = create_access_token(user_id=guest.id, role=guest.role.value, organization_id=org.id)

    resp = await client.get(f"/api/orgs/{org.slug}/runs/{run.id}/stations/", cookies={"access_token": token})

    assert resp.status_code == 200, resp.text
    items = sorted(resp.json()["items"], key=lambda i: i["sort_order"])
    assert len(items) == 2
    assert items[0]["title_image_url"] == f"/api/files/{org.id}/stations/abc/de1f2_cover.png"
    assert items[1]["title_image_url"] is None

    overview = await client.get(
        f"/api/orgs/{org.slug}/runs/{run.id}/participant-overview", cookies={"access_token": token}
    )
    assert overview.status_code == 200, overview.text
    urls = {s["title"]["de"]: s["title_image_url"] for s in overview.json()["stations"]}
    assert urls == {"Mit Bild": f"/api/files/{org.id}/stations/abc/de1f2_cover.png", "Ohne Bild": None}


@pytest.mark.asyncio
async def test_title_image_is_served_same_origin(client: AsyncClient, db_session: AsyncSession) -> None:
    """A guest of the organization gets the image through GET /api/files/{key}, with a day of private caching.

    Another organization gets 403, and a caller without a session 401.
    """
    org, _creator, station, token = await _setup(db_session, slug="sti-serve")
    guest = await create_test_user(
        db_session, email="guest-sti-serve@example.com", role=UserRole.GUEST, organization_id=org.id
    )
    other = await create_test_organization(db_session, name="Org fremd", slug="sti-serve-other")
    stranger = await create_test_user(
        db_session, email="admin-sti-other@example.com", role=UserRole.ORG_ADMIN, organization_id=other.id
    )
    await db_session.commit()
    up = await client.put(_url(org.slug, station.id), files=_img(), cookies={"access_token": token})
    assert up.status_code == 200, up.text
    url = up.json()["title_image_url"]
    assert url.startswith(f"/api/files/{org.id}/stations/{station.id}/"), url

    guest_token = create_access_token(user_id=guest.id, role=guest.role.value, organization_id=org.id)
    served = await client.get(url, cookies={"access_token": guest_token})
    assert served.status_code == 200, served.text
    assert Image.open(io.BytesIO(served.content)).size == (8, 8)
    assert served.headers["cache-control"] == "private, max-age=86400"

    stranger_token = create_access_token(user_id=stranger.id, role=stranger.role.value, organization_id=other.id)
    assert (await client.get(url, cookies={"access_token": stranger_token})).status_code == 403
    # The sliding session set a renewed cookie in the client's jar above.
    client.cookies.clear()
    assert (await client.get(url)).status_code == 401


@pytest.mark.asyncio
async def test_a_type_parameter_is_accepted_and_the_bare_type_is_stored(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A parameter does not make the claimed type another type, and it is not stored."""
    org, _creator, station, token = await _setup(db_session, slug="sti-param")

    resp = await client.put(
        _url(org.slug, station.id),
        files={"file": ("cover.png", PNG, "IMAGE/PNG; charset=binary")},
        cookies={"access_token": token},
    )

    assert resp.status_code == 200, resp.text
    url = resp.json()["title_image_url"]
    stat = get_minio_client().stat_object(settings.storage.bucket, _key_from_url(url))
    assert stat.content_type == "image/png"
    served = await client.get(url, cookies={"access_token": token})
    assert served.headers["content-type"] == "image/png"
    assert served.headers["content-disposition"] == 'inline; filename="cover.png"'


@pytest.mark.asyncio
async def test_title_image_is_stored_without_gps_and_device_metadata(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A stored title image carries no GPS and no device metadata, like the photo answers."""
    org, _creator, station, token = await _setup(db_session, slug="sti-exif")
    original = _jpeg_with_gps()
    assert b"Exif" in original and b"PhoneMaker" in original

    resp = await client.put(
        _url(org.slug, station.id),
        files={"file": ("foto.jpg", original, "image/jpeg")},
        cookies={"access_token": token},
    )

    assert resp.status_code == 200, resp.text
    key = _key_from_url(resp.json()["title_image_url"])
    response = get_minio_client().get_object(settings.storage.bucket, key)
    try:
        stored = response.read()
    finally:
        response.close()
        response.release_conn()
    assert not dict(Image.open(io.BytesIO(stored)).getexif()), "EXIF survived"
    assert b"Exif" not in stored and b"PhoneMaker" not in stored


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ctype,name,make_bytes",
    [
        ("image/jpeg", "cut.jpg", _truncated_jpeg),
        ("image/png", "fake.png", lambda: b"\x89PNG\r\n\x1a\n" + b"not-an-image"),
    ],
)
async def test_undecodable_title_image_is_refused(
    client: AsyncClient, db_session: AsyncSession, ctype: str, name: str, make_bytes
) -> None:
    """An undecodable image is refused with 415 IMAGE_NOT_DECODABLE, and nothing is stored."""
    org, _creator, station, token = await _setup(db_session, slug=f"sti-bad-{name[:3]}")

    resp = await client.put(
        _url(org.slug, station.id), files={"file": (name, make_bytes(), ctype)}, cookies={"access_token": token}
    )

    assert resp.status_code == 415, resp.text
    assert resp.json()["error_code"] == "IMAGE_NOT_DECODABLE"
    stored = list(
        get_minio_client().list_objects(
            settings.storage.bucket, prefix=f"{org.id}/stations/{station.id}/", recursive=True
        )
    )
    assert stored == []
    got = await client.get(f"/api/orgs/{org.slug}/stations/{station.id}", cookies={"access_token": token})
    assert got.json()["title_image_url"] is None
