"""
One refusal for a bad image on the three image upload routes.

The answer route (``POST .../submissions/upload/``), the rich-text route
(``POST .../media/``) and the title image route (``PUT .../title-image``) all
clean an upload with the same image library. The table below is the contract:
the same input gets the same status and the same error code on each of them,
and a refused upload stores nothing.

Every broken file is built from bytes here, so the table does not depend on
what an encoder of the day happens to write.
"""

import struct
import zlib
from collections.abc import Awaitable, Callable
from io import BytesIO

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from minio import Minio
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.main import app
from app.models.user import UserRole
from app.routers.files import PASS_THROUGH_IMAGE_TYPES
from app.services import image_variants
from app.services.file_response import FILE_SECURITY_POLICY, INLINE_MEDIA_FAMILIES, INLINE_MEDIA_TYPES
from app.services.image_variants import MAX_DECODE_PIXELS, bare_content_type
from app.storage import ensure_bucket, get_minio_client
from tests.factories import create_test_organization, create_test_station, create_test_user, get_auth_headers
from tests.test_file_upload import _org_for_slug, setup_run_with_team

# These two files are built from bytes as well. The unit tests of the cleaning use them too.
from tests.test_image_variants import (
    EXIF_ORIENTATION,
    GREY_JPEG,
    jpeg_with_exif_tag_of_wrong_type,
    xpm_image,
)

NOT_DECODABLE = (415, "IMAGE_NOT_DECODABLE")
TOO_LARGE = (413, "IMAGE_TOO_LARGE")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(identifier: bytes, data: bytes) -> bytes:
    """One PNG chunk: length, identifier, data, checksum."""
    checksum = zlib.crc32(identifier + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + identifier + data + struct.pack(">I", checksum)


def _header(width: int, height: int) -> bytes:
    """IHDR of an 8-bit RGB image without interlacing."""
    return _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))


def _pixels(width: int, height: int) -> bytes:
    """The compressed pixel stream of a ``width`` x ``height`` RGB image.

    The values vary from byte to byte, so the stream is long enough to split
    into two chunks.
    """
    rows = []
    for row in range(height):
        rows.append(b"\x00" + bytes((row * 31 + column * 17) % 251 for column in range(width * 3)))
    return zlib.compress(b"".join(rows), 0)


def valid_png() -> bytes:
    return PNG_SIGNATURE + _header(8, 8) + _chunk(b"IDAT", _pixels(8, 8)) + _chunk(b"IEND", b"")


def png_with_invalid_chunk() -> bytes:
    """Valid headers, then a chunk whose identifier is no identifier at all.

    The first IDAT chunk holds half of the pixel stream. The image library
    opens the file, starts decoding and reads the next chunk for the rest: it
    raises ``SyntaxError`` there, not ``OSError``.
    """
    stream = _pixels(32, 32)
    half = len(stream) // 2
    rest = stream[half:]
    broken_chunk = struct.pack(">I", len(rest)) + b"\x00\x01\x02\x03" + rest + b"\x00\x00\x00\x00"
    return PNG_SIGNATURE + _header(32, 32) + _chunk(b"IDAT", stream[:half]) + broken_chunk + _chunk(b"IEND", b"")


def png_with_oversized_text_chunk() -> bytes:
    """A compressed text chunk that unpacks to more than the library reads.

    The image library raises ``ValueError`` for it while it opens the file.
    """
    text = b"Comment\x00\x00" + zlib.compress(b"\x00" * (2 * 1024 * 1024))
    return PNG_SIGNATURE + _header(8, 8) + _chunk(b"zTXt", text) + _chunk(b"IDAT", _pixels(8, 8)) + _chunk(b"IEND", b"")


def png_above_the_pixel_limit() -> bytes:
    """A header that claims more pixels than the service decodes. No pixel is decoded for it."""
    side = 8000
    assert side * side > MAX_DECODE_PIXELS
    return PNG_SIGNATURE + _header(side, side) + _chunk(b"IDAT", _pixels(8, 8)) + _chunk(b"IEND", b"")


def cut_off_jpeg() -> bytes:
    """A real JPEG, cut in the middle of its pixel data."""
    from PIL import Image

    buffer = BytesIO()
    Image.frombytes("RGB", (64, 64), bytes((i * 7) % 256 for i in range(64 * 64 * 3))).save(buffer, format="JPEG")
    data = buffer.getvalue()
    return data[: len(data) // 2]


def heic_container() -> bytes:
    """The first box of a HEIC photo: a file type box with the brand ``heic``.

    The image library has no decoder for it here and cannot identify it.
    """
    brands = b"heic" + struct.pack(">I", 0) + b"mif1" + b"heic"
    return struct.pack(">I", 8 + len(brands)) + b"ftyp" + brands + b"\x00" * 64


SVG_WITH_ONLOAD = (
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="fetch(\'/exfil\')" '
    b'width="100" height="100"><rect width="100" height="100"/></svg>'
)


@pytest_asyncio.fixture(scope="function")
async def storage_bucket() -> Minio:
    ensure_bucket()
    client = get_minio_client()
    yield client
    try:
        for obj in client.list_objects(settings.storage.bucket, recursive=True):
            client.remove_object(settings.storage.bucket, obj.object_name)
    except S3Error:
        pass


Upload = tuple[str, bytes, str]
Route = Callable[[AsyncClient, AsyncSession, Upload], Awaitable[tuple[Response, str]]]


async def _answer_route(client: AsyncClient, db_session: AsyncSession, upload: Upload) -> tuple[Response, str]:
    headers, org_slug, run_id, _team_id = await setup_run_with_team(client, db_session)
    org = await _org_for_slug(db_session, org_slug)
    response = await client.post(
        f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/", headers=headers, files={"file": upload}
    )
    return response, f"{org.id}/"


async def _rich_text_route(client: AsyncClient, db_session: AsyncSession, upload: Upload) -> tuple[Response, str]:
    org = await create_test_organization(db_session, name="Org Tabelle", slug="refusal-table")
    creator = await create_test_user(
        db_session, email="creator@refusal-table.test", role=UserRole.CREATOR, organization=org
    )
    response = await client.post(
        f"/api/orgs/{org.slug}/media/", headers=get_auth_headers(creator), files={"file": upload}
    )
    return response, f"{org.id}/"


async def _title_image_route(client: AsyncClient, db_session: AsyncSession, upload: Upload) -> tuple[Response, str]:
    org = await create_test_organization(db_session, name="Org Tabelle", slug="refusal-table")
    creator = await create_test_user(
        db_session, email="creator@refusal-table.test", role=UserRole.CREATOR, organization=org
    )
    station = await create_test_station(db_session, organization=org, created_by_user_id=creator.id)
    response = await client.put(
        f"/api/orgs/{org.slug}/stations/{station.id}/title-image",
        headers=get_auth_headers(creator),
        files={"file": upload},
    )
    return response, f"{org.id}/"


ROUTES: dict[str, Route] = {
    "answer": _answer_route,
    "rich_text": _rich_text_route,
    "title_image": _title_image_route,
}

# What each route answers when it stores the upload.
STORED = {"answer": (201, None), "rich_text": (201, None), "title_image": (200, None)}

# input, file name, bytes, claimed content type, expected (status, error code) by route
TABLE: list[tuple[str, str, Callable[[], bytes], str, dict[str, tuple[int, str | None]]]] = [
    (
        "svg",
        "pic.svg",
        lambda: SVG_WITH_ONLOAD,
        "image/svg+xml",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    (
        "svg_with_type_parameter",
        "pic.svg",
        lambda: SVG_WITH_ONLOAD,
        "image/svg+xml; charset=utf-8",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    (
        "cut_off_jpeg",
        "cut.jpg",
        cut_off_jpeg,
        "image/jpeg",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    (
        "png_with_invalid_chunk",
        "chunk.png",
        png_with_invalid_chunk,
        "image/png",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    (
        "png_with_oversized_text_chunk",
        "text.png",
        png_with_oversized_text_chunk,
        "image/png",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    # Decoded, and then no writer for the format: refused at the save call.
    (
        "image_the_library_cannot_write_back",
        "pic.png",
        xpm_image,
        "image/png",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    # The same file under its own type. The answer route takes every image
    # type to the cleaning; the two content routes refuse the type itself.
    (
        "image_the_library_cannot_write_back_under_its_own_type",
        "pic.xpm",
        xpm_image,
        "image/x-xpixmap",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    # The library fails while it rotates the picture: refused, not stored
    # without the rotation.
    (
        "jpeg_with_exif_tag_of_wrong_type",
        "turned.jpg",
        jpeg_with_exif_tag_of_wrong_type,
        "image/jpeg",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    (
        "image_above_the_pixel_limit",
        "huge.png",
        png_above_the_pixel_limit,
        "image/png",
        {"answer": TOO_LARGE, "rich_text": TOO_LARGE, "title_image": TOO_LARGE},
    ),
    (
        "content_type_with_parameter",
        "pic.png",
        valid_png,
        "image/png; charset=binary",
        STORED,
    ),
    (
        "empty_file",
        "empty.png",
        lambda: b"",
        "image/png",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    # The declared type in capitals is the same type on all three routes, also for a sound image.
    (
        "png_declared_in_capitals",
        "pic.png",
        valid_png,
        "IMAGE/PNG",
        STORED,
    ),
    (
        "corrupt_png_declared_in_capitals",
        "chunk.png",
        png_with_invalid_chunk,
        "IMAGE/PNG",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    (
        "empty_file_declared_in_capitals",
        "empty.png",
        lambda: b"",
        "IMAGE/PNG",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    # The answer route keeps bytes the library cannot identify when the claim
    # is HEIC or HEIF. An empty body is no such photo: it is refused before
    # that exception applies, under every claimed type.
    (
        "empty_file_claimed_as_heic",
        "empty.heic",
        lambda: b"",
        "image/heic",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    (
        "empty_file_claimed_as_heif",
        "empty.heif",
        lambda: b"",
        "image/heif",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
    (
        "empty_file_claimed_as_heic_with_type_parameter",
        "empty.heic",
        lambda: b"",
        "image/heic; charset=binary",
        {"answer": NOT_DECODABLE, "rich_text": NOT_DECODABLE, "title_image": NOT_DECODABLE},
    ),
]

CASES = [
    pytest.param(route, name, make_bytes, content_type, *expected[route], id=f"{input_name}-{route}")
    for input_name, name, make_bytes, content_type, expected in TABLE
    for route in ROUTES
]


def test_the_broken_pngs_are_what_the_table_says_they_are() -> None:
    """The two broken PNGs raise ``SyntaxError`` when decoded and ``ValueError`` when opened, neither is an ``OSError``."""
    from PIL import Image

    with pytest.raises(SyntaxError), Image.open(BytesIO(png_with_invalid_chunk())) as image:
        image.load()
    with pytest.raises(ValueError):
        Image.open(BytesIO(png_with_oversized_text_chunk()))
    with Image.open(BytesIO(valid_png())) as image:
        image.load()
        assert image.size == (8, 8)


def test_the_xpm_and_the_jpeg_with_a_bad_exif_tag_are_what_the_table_says_they_are() -> None:
    """Both files are sound for the decoder. The library fails later, at one call each."""
    from PIL import Image, ImageOps

    with Image.open(BytesIO(xpm_image())) as image:
        image.load()
        assert (image.format, image.size) == ("XPM", (4, 4))
        with pytest.raises(KeyError):
            image.save(BytesIO(), format=image.format)

    with Image.open(BytesIO(GREY_JPEG)) as image:
        image.load()
        assert (image.format, image.size) == ("JPEG", (8, 8))
    with Image.open(BytesIO(jpeg_with_exif_tag_of_wrong_type())) as image:
        image.load()
        assert (image.format, image.size) == ("JPEG", (8, 8))
        assert image.getexif()[EXIF_ORIENTATION] == 6
        with pytest.raises(AttributeError):
            ImageOps.exif_transpose(image)


@pytest.mark.asyncio
@pytest.mark.parametrize("route,name,make_bytes,content_type,status,error_code", CASES)
async def test_refusal_table(
    client: AsyncClient,
    db_session: AsyncSession,
    storage_bucket: Minio,
    route: str,
    name: str,
    make_bytes: Callable[[], bytes],
    content_type: str,
    status: int,
    error_code: str | None,
) -> None:
    response, prefix = await ROUTES[route](client, db_session, (name, make_bytes(), content_type))

    assert response.status_code == status, response.text
    stored = [
        obj.object_name for obj in storage_bucket.list_objects(settings.storage.bucket, prefix=prefix, recursive=True)
    ]
    if error_code is None:
        assert stored, "an accepted image is stored"
    else:
        assert response.json().get("error_code") == error_code, response.text
        assert stored == [], "nothing may be stored for a refused upload"


# ---------------------------------------------------------------------------
# A fault of the service is no refusal
#
# The table above holds what the upload did wrong. A mistake in the service's
# own code is not in it: no route may answer it with a status of the table.
# A ``ValueError`` of the service must not turn into 422, or into 413 when its
# message holds the word "size".
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def answering_client(client: AsyncClient) -> AsyncClient:
    """The app as a browser gets its answers: an exception no handler takes is a 500, not an exception in the test."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore
    async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as answering:
        yield answering


@pytest.mark.asyncio
@pytest.mark.parametrize("route", list(ROUTES))
@pytest.mark.parametrize(
    "message",
    ["Pillow did not report a source format", "the size of the canvas was not computed"],
    ids=["a_message_about_the_source_format", "a_message_with_the_word_size"],
)
async def test_a_fault_of_the_service_is_a_server_error_on_every_route(
    client: AsyncClient,
    answering_client: AsyncClient,
    db_session: AsyncSession,
    storage_bucket: Minio,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    message: str,
) -> None:
    def broken(*args: object, **kwargs: object) -> bytes:
        raise ValueError(message)

    monkeypatch.setattr(image_variants, "_encode_original", broken)

    response, prefix = await ROUTES[route](answering_client, db_session, ("pic.png", valid_png(), "image/png"))

    assert response.status_code == 500, response.text
    assert list(storage_bucket.list_objects(settings.storage.bucket, prefix=prefix, recursive=True)) == []


# ---------------------------------------------------------------------------
# The exception for unidentified images
#
# The answer route keeps bytes the image library cannot identify when the
# claimed type is HEIC or HEIF, because phones hand such photos over and no
# decoder for them is installed. Below is everything that exception lets
# through unchanged: any body of at least one byte that the library cannot
# identify, under one of the two claims, on the answer route only. None of it
# is empty, and none of it is stored under a type a browser shows inline.
# ---------------------------------------------------------------------------

KEPT_UNCHANGED = [
    pytest.param("photo.heic", heic_container, "image/heic", id="heic_container-heic"),
    pytest.param("photo.heif", heic_container, "image/heif", id="heic_container-heif"),
    pytest.param("photo.heic", heic_container, "image/heic; charset=binary", id="heic_container-heic_with_parameter"),
    pytest.param("photo.HEIC", heic_container, "image/HEIC", id="heic_container-heic_upper_case"),
    # Not a photo at all, but the library cannot tell: it is kept as well, and
    # served like every other kept body, as a download.
    pytest.param("pic.heic", lambda: SVG_WITH_ONLOAD, "image/heic", id="svg_bytes-heic"),
    pytest.param("pic.heif", lambda: SVG_WITH_ONLOAD, "image/heif; charset=utf-8", id="svg_bytes-heif_with_parameter"),
    pytest.param("page.heic", lambda: b"<!doctype html><script>fetch('/exfil')</script>", "image/heic", id="html-heic"),
    # The smallest body the exception covers.
    pytest.param("one.heic", lambda: b"\x00", "image/heic", id="one_byte-heic"),
]


def test_the_exception_covers_no_type_a_browser_shows_inline() -> None:
    """The two claims of the exception are outside the inline set of the file answer."""
    assert frozenset({"image/heic", "image/heif"}) == PASS_THROUGH_IMAGE_TYPES
    for kept_type in PASS_THROUGH_IMAGE_TYPES:
        assert kept_type not in INLINE_MEDIA_TYPES
        assert not kept_type.startswith(INLINE_MEDIA_FAMILIES)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,make_bytes,content_type", KEPT_UNCHANGED)
async def test_what_the_exception_keeps_is_not_empty_and_is_served_as_a_download(
    client: AsyncClient,
    db_session: AsyncSession,
    storage_bucket: Minio,
    name: str,
    make_bytes: Callable[[], bytes],
    content_type: str,
) -> None:
    body = make_bytes()
    headers, org_slug, run_id, _team_id = await setup_run_with_team(client, db_session)

    response = await client.post(
        f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files={"file": (name, body, content_type)},
    )

    assert response.status_code == 201, response.text
    object_key = response.json()["object_key"]
    org = await _org_for_slug(db_session, org_slug)
    stored = {
        obj.object_name: obj.size
        for obj in storage_bucket.list_objects(settings.storage.bucket, prefix=f"{org.id}/", recursive=True)
    }
    assert list(stored) == [object_key], "kept as uploaded, without variants"
    assert stored[object_key] == len(body) > 0
    stat = storage_bucket.stat_object(settings.storage.bucket, object_key)
    assert bare_content_type(stat.content_type) in PASS_THROUGH_IMAGE_TYPES

    served = await client.get(f"/api/files/{object_key}", headers=headers)

    assert served.status_code == 200
    assert served.content == body
    assert served.headers["content-disposition"].startswith("attachment")
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["content-security-policy"] == FILE_SECURITY_POLICY


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["rich_text", "title_image"])
@pytest.mark.parametrize("content_type", ["image/heic", "image/heif"])
async def test_the_content_routes_have_no_such_exception(
    client: AsyncClient,
    db_session: AsyncSession,
    storage_bucket: Minio,
    route: str,
    content_type: str,
) -> None:
    """HEIC and HEIF are outside the list of the two content routes, with any body."""
    response, prefix = await ROUTES[route](client, db_session, ("photo.heic", heic_container(), content_type))

    assert (response.status_code, response.json().get("error_code")) == NOT_DECODABLE, response.text
    assert list(storage_bucket.list_objects(settings.storage.bucket, prefix=prefix, recursive=True)) == []
