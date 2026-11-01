"""
One shared protection for every answer that streams a stored object.

The file endpoint (``GET /api/files/{key}``) and the rich-text media endpoint
(``GET /api/orgs/{slug}/media/{key}``) both hand stored bytes to a browser. An
object stored as ``image/svg+xml``, although the upload routes refuse it, must
not run script on direct navigation, on either of them, with or without a
session.
"""

from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from minio import Minio
from minio.error import S3Error
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import UserRole
from app.routers import files as files_router
from app.routers import media as media_router
from app.services import file_response
from app.services.file_response import file_response_headers
from app.storage import ensure_bucket, get_minio_client
from tests.factories import create_test_user, get_auth_headers
from tests.test_file_upload import _org_for_slug, setup_run_with_team

# An SVG that runs script when a browser renders it as a document.
SVG_WITH_ONLOAD = (
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="fetch(\'/exfil\')" '
    b'width="100" height="100"><rect width="100" height="100"/></svg>'
)

NO_SCRIPT_POLICY = "default-src 'none'; img-src 'self'; media-src 'self'"


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (30, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


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


def _store(bucket: Minio, key: str, data: bytes, content_type: str) -> None:
    """Write an object straight into the storage, past every upload route."""
    bucket.put_object(
        bucket_name=settings.storage.bucket,
        object_name=key,
        data=BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def _media_url(org, key: str) -> str:  # type: ignore[no-untyped-def]
    return f"/api/orgs/{org.slug}/media/{key}"


async def _show_in_imprint(db_session: AsyncSession, org, key: str) -> None:  # type: ignore[no-untyped-def]
    org.legal_imprint = f'<h2>Impressum</h2><p><img src="{_media_url(org, key)}" alt="Logo"></p>'
    await db_session.commit()


# ---------------------------------------------------------------------------
# The rich-text media endpoint serves a stored SVG as an attachment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_endpoint_serves_a_stored_svg_as_attachment_with_a_session(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    key = f"{org_a.id}/media/{uuid4()}_old.svg"
    _store(storage_bucket, key, SVG_WITH_ONLOAD, "image/svg+xml")
    guest = await create_test_user(db_session, email="guest@org-a.test", role=UserRole.GUEST, organization=org_a)

    response = await client.get(_media_url(org_a, key), headers=get_auth_headers(guest))

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == 'attachment; filename="old.svg"'
    assert response.headers["content-security-policy"] == NO_SCRIPT_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    # What the endpoint sends beside the protection stays.
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["content-length"] == str(len(SVG_WITH_ONLOAD))


@pytest.mark.asyncio
async def test_media_endpoint_serves_a_stored_svg_as_attachment_without_a_session_from_a_legal_text(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    key = f"{org_a.id}/media/{uuid4()}_old.svg"
    _store(storage_bucket, key, SVG_WITH_ONLOAD, "image/svg+xml")
    await _show_in_imprint(db_session, org_a, key)

    response = await client.get(_media_url(org_a, key))

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == 'attachment; filename="old.svg"'
    assert response.headers["content-security-policy"] == NO_SCRIPT_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# A raster image is still shown inline, with and without a session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_endpoint_serves_a_raster_image_inline_with_the_policy(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    key = f"{org_a.id}/media/{uuid4()}_logo.png"
    _store(storage_bucket, key, _png(), "image/png")
    guest = await create_test_user(db_session, email="guest@org-a.test", role=UserRole.GUEST, organization=org_a)

    response = await client.get(_media_url(org_a, key), headers=get_auth_headers(guest))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'inline; filename="logo.png"'
    assert response.headers["content-security-policy"] == NO_SCRIPT_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert Image.open(BytesIO(response.content)).size == (8, 8)
    # In no legal text: without a session there is no image.
    assert (await client.get(_media_url(org_a, key))).status_code == 401


@pytest.mark.asyncio
async def test_media_endpoint_serves_a_legal_text_raster_image_inline_without_a_session(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    key = f"{org_a.id}/media/{uuid4()}_logo.png"
    _store(storage_bucket, key, _png(), "image/png")
    await _show_in_imprint(db_session, org_a, key)

    response = await client.get(_media_url(org_a, key))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'inline; filename="logo.png"'
    assert response.headers["content-security-policy"] == NO_SCRIPT_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert Image.open(BytesIO(response.content)).size == (8, 8)


# ---------------------------------------------------------------------------
# One function builds the protection, and both endpoints call it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored_type",
    [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "IMAGE/PNG",
        "application/pdf",
        "audio/mpeg",
        "audio/mp4",
        "video/mp4",
        "video/quicktime",
    ],
)
def test_raster_images_audio_video_and_pdf_are_inline(stored_type: str) -> None:
    headers = file_response_headers(stored_type, filename="a.bin")

    assert headers == {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": NO_SCRIPT_POLICY,
        "Content-Disposition": 'inline; filename="a.bin"',
    }


@pytest.mark.parametrize(
    "stored_type",
    [
        "image/svg+xml",
        "IMAGE/SVG+XML",
        "image/svg+xml; charset=utf-8",
        "image/heic",
        "image/heif",
        "image/heic; charset=binary",
        "IMAGE/HEIF",
        "text/html",
        "application/xhtml+xml",
        "text/xml",
        "text/plain",
        "application/zip",
        "application/octet-stream",
        "",
        None,
    ],
)
def test_every_other_stored_type_is_an_attachment(stored_type: str | None) -> None:
    headers = file_response_headers(stored_type, filename="a.bin")

    assert headers == {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": NO_SCRIPT_POLICY,
        "Content-Disposition": 'attachment; filename="a.bin"',
    }


def test_the_policy_allows_no_script() -> None:
    directives = {
        part.split()[0]: part.split()[1:] for part in file_response.FILE_SECURITY_POLICY.split(";") if part.strip()
    }

    assert directives["default-src"] == ["'none'"]
    assert "script-src" not in directives
    assert all("unsafe" not in source for sources in directives.values() for source in sources)


def test_without_a_filename_the_disposition_stands_alone() -> None:
    assert file_response_headers("image/png")["Content-Disposition"] == "inline"
    assert file_response_headers("image/svg+xml")["Content-Disposition"] == "attachment"


@pytest.mark.asyncio
async def test_both_endpoints_take_the_protection_from_the_shared_function(
    client: AsyncClient,
    db_session: AsyncSession,
    storage_bucket: Minio,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither endpoint sets a protective header on its own: with the shared
    function replaced, the disposition is gone and the policy is the general
    one of app/main.py."""
    seen: list[str | None] = []

    def marker(stored_content_type: str | None, *, filename: str | None = None) -> dict[str, str]:
        seen.append(stored_content_type)
        return {"X-Shared-Protection": "yes"}

    monkeypatch.setattr(files_router, "file_response_headers", marker)
    monkeypatch.setattr(media_router, "file_response_headers", marker)

    headers, org_slug, run_id, _team_id = await setup_run_with_team(client, db_session)
    org = await _org_for_slug(db_session, org_slug)
    answer_key = f"{org.id}/{run_id}/{uuid4()}/ab12_old.svg"
    media_key = f"{org.id}/media/{uuid4()}_old.svg"
    _store(storage_bucket, answer_key, SVG_WITH_ONLOAD, "image/svg+xml")
    _store(storage_bucket, media_key, SVG_WITH_ONLOAD, "image/svg+xml")

    for url in (f"/api/files/{answer_key}", _media_url(org, media_key)):
        response = await client.get(url, headers=headers)

        assert response.status_code == 200, response.text
        assert response.headers["x-shared-protection"] == "yes"
        assert "content-disposition" not in response.headers
        assert response.headers["content-security-policy"] != NO_SCRIPT_POLICY

    assert seen == ["image/svg+xml", "image/svg+xml"]


# ---------------------------------------------------------------------------
# A file name in any script is served
#
# The stored name goes into Content-Disposition, and an HTTP header value must
# be Latin-1. A raw name with a letter outside Latin-1 fails with
# UnicodeEncodeError. The shared function carries such a name by
# RFC 5987: ``filename*`` with UTF-8 and percent encoding, beside an ASCII
# fallback in ``filename``.
# ---------------------------------------------------------------------------

# name, the ASCII fallback the header carries for it, the name in UTF-8 with percent encoding
NAMES_OUTSIDE_LATIN_1 = [
    pytest.param("фото.jpg", "____.jpg", "%D1%84%D0%BE%D1%82%D0%BE.jpg", id="cyrillic"),
    pytest.param("صورة.jpg", "____.jpg", "%D8%B5%D9%88%D8%B1%D8%A9.jpg", id="arabic"),
    pytest.param("写真.jpg", "__.jpg", "%E5%86%99%E7%9C%9F.jpg", id="cjk"),
]

# stored content type, the disposition the shared function gives it
INLINE_AND_ATTACHMENT = [
    pytest.param("image/jpeg", "inline", id="inline"),
    # What the answer route stores for a phone's HEIC photo.
    pytest.param("image/heic", "attachment", id="attachment"),
]


@pytest.mark.parametrize("stored_type,disposition", INLINE_AND_ATTACHMENT)
@pytest.mark.parametrize("name,fallback,encoded", NAMES_OUTSIDE_LATIN_1)
def test_a_name_outside_latin_1_is_carried_by_rfc_5987(
    name: str, fallback: str, encoded: str, stored_type: str, disposition: str
) -> None:
    header = file_response_headers(stored_type, filename=name)["Content-Disposition"]

    assert header == f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
    # What a server may send at all, and what a browser reads back.
    assert header.encode("latin-1").isascii()
    assert unquote(header.split("filename*=UTF-8''", 1)[1], encoding="utf-8", errors="strict") == name


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_type,disposition", INLINE_AND_ATTACHMENT)
@pytest.mark.parametrize("name,fallback,encoded", NAMES_OUTSIDE_LATIN_1)
async def test_file_endpoint_serves_a_name_outside_latin_1(
    client: AsyncClient,
    db_session: AsyncSession,
    storage_bucket: Minio,
    name: str,
    fallback: str,
    encoded: str,
    stored_type: str,
    disposition: str,
) -> None:
    headers, org_slug, run_id, team_id = await setup_run_with_team(client, db_session)
    org = await _org_for_slug(db_session, org_slug)
    key = f"{org.id}/{run_id}/{team_id}/ab12cd34_{name}"
    body = b"the stored bytes"
    _store(storage_bucket, key, body, stored_type)

    response = await client.get(f"/api/files/{key}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.content == body
    assert response.headers["content-type"] == stored_type
    assert (
        response.headers["content-disposition"] == f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
    )
    assert response.headers["content-security-policy"] == NO_SCRIPT_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored_type,disposition",
    [pytest.param("image/png", "inline", id="inline"), pytest.param("image/svg+xml", "attachment", id="attachment")],
)
@pytest.mark.parametrize("name,fallback,encoded", NAMES_OUTSIDE_LATIN_1)
async def test_media_endpoint_serves_a_name_outside_latin_1(
    client: AsyncClient,
    db_session: AsyncSession,
    org_a,
    storage_bucket: Minio,
    name: str,
    fallback: str,
    encoded: str,
    stored_type: str,
    disposition: str,
) -> None:
    key = f"{org_a.id}/media/{uuid4()}_{name}"
    body = b"the stored bytes"
    _store(storage_bucket, key, body, stored_type)
    guest = await create_test_user(db_session, email="guest@org-a.test", role=UserRole.GUEST, organization=org_a)

    response = await client.get(_media_url(org_a, key), headers=get_auth_headers(guest))

    assert response.status_code == 200, response.text
    assert response.content == body
    assert response.headers["content-type"] == stored_type
    assert (
        response.headers["content-disposition"] == f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
    )
    assert response.headers["content-security-policy"] == NO_SCRIPT_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "name,header",
    [
        # Latin-1 beyond ASCII is not sent as raw bytes, because a browser would have to guess the charset.
        pytest.param(
            "Rathaus-Müll.jpg", "inline; filename=\"Rathaus-M_ll.jpg\"; filename*=UTF-8''Rathaus-M%C3%BCll.jpg"
        ),
        # A quote or a backslash would end or escape the quoted string.
        pytest.param('a"b\\c.jpg', "inline; filename=\"a_b_c.jpg\"; filename*=UTF-8''a%22b%5Cc.jpg"),
        # Control characters, a line break above all, never reach the header.
        pytest.param("a\r\nb\x00\x7f.jpg", "inline; filename=\"a__b__.jpg\"; filename*=UTF-8''a%0D%0Ab%00%7F.jpg"),
        # Some browsers read a percent sign in the plain parameter as an escape.
        pytest.param("100%41.jpg", "inline; filename=\"100_41.jpg\"; filename*=UTF-8''100%2541.jpg"),
        # No name fails the encoding: a code point UTF-8 cannot encode becomes a question mark.
        pytest.param("a\ud800.jpg", "inline; filename=\"a_.jpg\"; filename*=UTF-8''a%3F.jpg"),
        pytest.param("mein foto.jpg", 'inline; filename="mein foto.jpg"'),
    ],
)
def test_quotes_control_characters_and_latin_1_are_made_safe(name: str, header: str) -> None:
    assert file_response_headers("image/jpeg", filename=name)["Content-Disposition"] == header


def test_a_plain_ascii_name_keeps_the_header_it_had() -> None:
    assert file_response_headers("image/png", filename="logo-2.png")["Content-Disposition"] == (
        'inline; filename="logo-2.png"'
    )
