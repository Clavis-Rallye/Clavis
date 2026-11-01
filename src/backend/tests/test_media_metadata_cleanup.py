"""
The operator command that removes the metadata of images stored before the cleaning.

Uploads are stored without metadata by the shared cleaning of ``services/image_variants.py``.
An image stored before it keeps its EXIF block, GPS position included. The first test holds that
state: nothing in the server cleans such an image by itself. The other tests run the command's
logic, ``services/media_metadata_cleanup.py``, against the real store.
"""

import io
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from PIL.ExifTags import IFD
from PIL.PngImagePlugin import PngInfo
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from app.config import settings
from app.models.station import Station
from app.models.user import UserRole
from app.services.media_metadata_cleanup import clean_stored_image_metadata
from app.storage import ensure_bucket, get_minio_client
from tests.factories import create_test_organization, create_test_station, create_test_user

# A position as a phone embeds it (Augsburg town hall, roughly).
_GPS_TAGS = {1: "N", 2: (48.0, 22.0, 7.0), 3: "E", 4: (10.0, 53.0, 55.0)}


def jpeg_with_gps() -> bytes:
    """A small JPEG with an EXIF block that names the device and the position."""
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    exif[IFD.GPSInfo] = _GPS_TAGS
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (30, 120, 200)).save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _device_exif() -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    return exif


def _encoded(image_format: str, **save_options: object) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (30, 120, 200)).save(buffer, format=image_format, **save_options)
    return buffer.getvalue()


def gps_of(data: bytes) -> dict[int, object]:
    """The GPS block of an image, empty when it has none."""
    with Image.open(io.BytesIO(data)) as image:
        return dict(image.getexif().get_ifd(IFD.GPSInfo))


def put(key: str, data: bytes, content_type: str) -> None:
    """Write straight into the store, past every upload route, as an image stored before the cleaning lies there."""
    get_minio_client().put_object(
        settings.storage.bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
    )


def stored(key: str) -> bytes:
    response = get_minio_client().get_object(settings.storage.bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def stored_content_type(key: str) -> str | None:
    return get_minio_client().stat_object(settings.storage.bucket, key).content_type


@pytest.fixture(autouse=True)
def _own_bucket(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a bucket of its own, and remove it afterwards.

    The command walks the whole bucket. In a shared one the counts would not be
    exact, and an apply run would rewrite the images of a test that runs beside
    this one.
    """
    name = f"{settings.storage.bucket}-cleanup-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(settings.storage, "bucket", name)
    ensure_bucket()
    yield
    client = get_minio_client()
    for obj in client.list_objects(name, recursive=True):
        client.remove_object(name, obj.object_name)
    client.remove_bucket(name)


async def _station_with_old_title_image(db: AsyncSession, slug: str):
    """A station whose title image lies in the store with GPS data, and its creator's cookies."""
    org = await create_test_organization(db, name=f"Org {slug}", slug=slug, languages=["de", "en"])
    creator = await create_test_user(db, email=f"c-{slug}@example.com", role=UserRole.CREATOR, organization_id=org.id)
    station = await create_test_station(db, organization=org, created_by_user_id=creator.id)
    key = f"{org.id}/stations/{station.id}/0a1b2c3d_cover.jpg"
    put(key, jpeg_with_gps(), "image/jpeg")
    await db.execute(update(Station).where(Station.id == station.id).values(title_image_key=key))
    await db.commit()
    token = create_access_token(user_id=creator.id, role=creator.role.value, organization_id=org.id)
    return org, station, key, {"access_token": token}


async def _duplicate_image(client: AsyncClient, org, station, cookies) -> bytes:
    """Duplicate the station and return the bytes of the copy's title image."""
    response = await client.post(f"/api/orgs/{org.slug}/stations/{station.id}/duplicate", cookies=cookies)
    assert response.status_code == 201, response.text
    url = response.json()["title_image_url"]
    assert url.startswith("/api/files/"), url
    return stored(url[len("/api/files/") :])


@pytest.mark.asyncio
async def test_an_image_stored_before_the_cleaning_keeps_its_gps_data(client: AsyncClient, db_session: AsyncSession):
    """The server serves and copies an old image as it lies, and cleans nothing by itself."""
    org, station, key, cookies = await _station_with_old_title_image(db_session, "old-image")

    served = await client.get(f"/api/files/{key}", cookies=cookies)
    assert served.status_code == 200, served.text
    assert gps_of(served.content), "the served image carries the position"

    assert gps_of(await _duplicate_image(client, org, station, cookies)), "the copy carries the position"
    assert gps_of(stored(key)), "the stored image is as it was"


def _old_objects() -> dict[str, str]:
    """One old image of each kind, written past the routes. Key to stored content type."""
    org, run, team, station = (uuid.uuid4() for _ in range(4))
    photo = f"{org}/{run}/{team}/1a2b3c4d_foto.jpg"
    objects = {
        # A type as an old phone browser claimed it: the command must keep it as stored.
        photo: "image/pjpeg",
        f"{photo}.thumb.webp": "image/webp",
        f"{org}/media/{uuid.uuid4()}_karte.jpg": "image/jpeg",
        f"{org}/stations/{station}/5e6f7a8b_cover.jpg": "image/jpeg",
    }
    for key, content_type in objects.items():
        put(key, _encoded("WEBP", exif=_device_exif()) if key.endswith(".webp") else jpeg_with_gps(), content_type)
    return objects


def _exif_of(data: bytes) -> dict[int, object]:
    with Image.open(io.BytesIO(data)) as image:
        return dict(image.getexif())


def test_the_dry_run_changes_nothing_and_lists_each_object():
    """The default lists the photo answer, its variant, the rich-text image and the title image."""
    objects = _old_objects()
    before = {key: stored(key) for key in objects}
    lines: list[str] = []

    result = clean_stored_image_metadata(emit=lines.append)

    assert result.applied is False
    assert result.changed == sorted(objects)
    assert (result.undecodable, result.unchanged) == ([], 0)
    assert {key: stored(key) for key in objects} == before, "a dry run writes nothing"
    assert len(lines) == len(objects)
    for key in objects:
        line = next(line for line in lines if key in line)
        assert line.startswith("would clean: ") and "exif" in line
    kinds = ("photo answer ", "photo answer variant ", "rich-text image ", "station title image ")
    assert all(any(kind in line for line in lines) for kind in kinds)
    assert sum("gps" in line for line in lines) == 3, "the three JPEGs carry a position, the variant does not"


def test_the_apply_run_removes_the_metadata_and_keeps_key_and_content_type():
    """Every object is re-encoded in place, so every reference stays valid."""
    objects = _old_objects()
    lines: list[str] = []

    result = clean_stored_image_metadata(apply=True, emit=lines.append)

    assert result.applied is True
    assert result.changed == sorted(objects)
    assert all(line.startswith("cleaned: ") for line in lines) and len(lines) == len(objects)
    listed = [obj.object_name for obj in get_minio_client().list_objects(settings.storage.bucket, recursive=True)]
    assert sorted(listed) == sorted(objects), "no object is created and none is deleted"
    for key, content_type in objects.items():
        data = stored(key)
        assert _exif_of(data) == {} and gps_of(data) == {}, key
        assert stored_content_type(key) == content_type, key
        with Image.open(io.BytesIO(data)) as image:
            assert image.format == ("WEBP" if key.endswith(".webp") else "JPEG")
            assert image.size == (64, 48)


def test_a_second_apply_run_rewrites_nothing():
    """An object without metadata is not rewritten, whatever its format."""
    objects = _old_objects()
    org = uuid.uuid4()
    note = PngInfo()
    note.add_text("Comment", "taken at home")
    others = {
        f"{org}/media/{uuid.uuid4()}_text.png": ("image/png", _encoded("PNG", pnginfo=note)),
        f"{org}/media/{uuid.uuid4()}_exif.png": ("image/png", _encoded("PNG", exif=_device_exif())),
        f"{org}/media/{uuid.uuid4()}_comment.gif": ("image/gif", _encoded("GIF", comment=b"taken at home")),
        # A TIFF keeps its structure in the tag directory that also holds the device name.
        f"{org}/{uuid.uuid4()}/{uuid.uuid4()}/9c8d7e6f_scan.tiff": (
            "image/tiff",
            _encoded("TIFF", tiffinfo={271: "PhoneMaker"}),
        ),
        # Never carried metadata: not rewritten in the first run either.
        f"{org}/media/{uuid.uuid4()}_plain.png": ("image/png", _encoded("PNG")),
    }
    for key, (content_type, data) in others.items():
        put(key, data, content_type)
    plain_key = next(key for key in others if key.endswith("_plain.png"))
    plain_etag = get_minio_client().stat_object(settings.storage.bucket, plain_key).etag

    first = clean_stored_image_metadata(apply=True, emit=lambda line: None)
    assert first.changed == sorted([*objects, *(key for key in others if key != plain_key)])
    assert first.unchanged == 1
    after_first = {
        obj.object_name: obj.etag for obj in get_minio_client().list_objects(settings.storage.bucket, recursive=True)
    }
    assert after_first[plain_key] == plain_etag

    lines: list[str] = []
    second = clean_stored_image_metadata(apply=True, emit=lines.append)

    assert second.changed == [] and second.undecodable == [] and lines == []
    assert second.unchanged == len(objects) + len(others)
    after_second = {
        obj.object_name: obj.etag for obj in get_minio_client().list_objects(settings.storage.bucket, recursive=True)
    }
    assert after_second == after_first
    assert clean_stored_image_metadata(emit=lines.append).changed == [], "a dry run after it lists nothing"


def test_an_undecodable_object_is_left_alone_and_counted_apart():
    """What the image library cannot decode is named, counted apart and never written."""
    org, run, team = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # The last bytes of the pixel data are missing, the header is whole.
    cut_off = jpeg_with_gps()[:-20]
    undecodable = {
        # Unknown to the library: a HEIC photo is stored as uploaded.
        f"{org}/{run}/{team}/0f1e2d3c_foto.heic": ("image/heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64),
        # Known to the library and broken: a cut-off JPEG, with its position still in the header.
        f"{org}/media/{uuid.uuid4()}_halb.jpg": ("image/jpeg", cut_off),
        # No image at all under an image type.
        f"{org}/stations/{uuid.uuid4()}/4b5a6978_cover.png": ("image/png", b"<svg onload='alert(1)'/>"),
    }
    ignored = {
        # A video answer is no image, and an object outside the three kinds is not the command's business.
        f"{org}/{run}/{team}/7a6b5c4d_film.mp4": ("video/mp4", jpeg_with_gps()),
        "uploads/2026-01-01/alt.jpg": ("image/jpeg", jpeg_with_gps()),
    }
    good = f"{org}/media/{uuid.uuid4()}_karte.jpg"
    put(good, jpeg_with_gps(), "image/jpeg")
    for key, (content_type, data) in {**undecodable, **ignored}.items():
        put(key, data, content_type)
    assert gps_of(cut_off), "the cut-off fixture must still carry the position, or it proves nothing"

    for apply in (False, True):
        lines: list[str] = []
        result = clean_stored_image_metadata(apply=apply, emit=lines.append)

        assert result.undecodable == sorted(undecodable)
        assert result.changed == [good]
        assert result.unchanged == 0
        for key in undecodable:
            assert sum(key in line and line.startswith("not decodable, left as it is: ") for line in lines) == 1
        assert not any(key in line for key in ignored for line in lines)
        for key, (content_type, data) in {**undecodable, **ignored}.items():
            assert stored(key) == data, key
            assert stored_content_type(key) == content_type, key
    assert gps_of(stored(good)) == {}


@pytest.mark.asyncio
async def test_a_station_duplicated_after_the_run_has_a_title_image_without_metadata(
    client: AsyncClient, db_session: AsyncSession
):
    """The copy takes the source object as it lies, and the source has no metadata after the cleaning."""
    org, station, key, cookies = await _station_with_old_title_image(db_session, "cleaned-image")

    result = clean_stored_image_metadata(apply=True, emit=lambda line: None)
    assert result.changed == [key]

    copy = await _duplicate_image(client, org, station, cookies)
    assert _exif_of(copy) == {} and gps_of(copy) == {}
    served = await client.get(f"/api/files/{key}", cookies=cookies)
    assert served.status_code == 200, served.text
    assert served.headers["content-type"] == "image/jpeg"
    assert _exif_of(served.content) == {}, "the original key still serves, now without metadata"


def test_nothing_but_the_command_line_starts_the_cleanup():
    """Only ``app/cli.py`` names the module: not the start of the server, not a sweep, not a migration."""
    backend = Path(__file__).resolve().parents[1]
    sources = [*(backend / "app").rglob("*.py"), *(backend / "alembic").rglob("*.py")]
    assert len(sources) > 50, "the walk must see the application, or it proves nothing"
    naming = {
        path.relative_to(backend).as_posix()
        for path in sources
        if "media_metadata_cleanup" in path.read_text(encoding="utf-8")
        or "clean_stored_image_metadata" in path.read_text(encoding="utf-8")
    }
    assert naming == {"app/cli.py", "app/services/media_metadata_cleanup.py"}
