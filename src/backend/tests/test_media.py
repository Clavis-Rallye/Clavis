"""Organisation media upload and serving."""

import random
from io import BytesIO

import pytest
import pytest_asyncio
from httpx import AsyncClient
from minio import Minio
from minio.error import S3Error
from PIL import Image
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization import Organization
from app.models.user import UserRole
from app.storage import MAX_FILE_SIZE, ensure_bucket, get_minio_client
from tests.factories import create_test_user, get_auth_headers

GPS_TAGS = {1: "N", 2: (48.0, 22.0, 0.0), 3: "E", 4: (10.0, 54.0, 0.0)}


def _noisy(size: tuple[int, int]) -> Image.Image:
    rng = random.Random(7)
    return Image.frombytes("RGB", size, bytes(rng.randrange(256) for _ in range(size[0] * size[1] * 3)))


def _encode(image: Image.Image, image_format: str, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
    buffer = BytesIO()
    image.save(buffer, format=image_format, **kwargs)
    return buffer.getvalue()


# A real, decodable image: the upload route re-encodes it, so fake bytes do not pass.
PNG_BYTES = _encode(Image.new("RGB", (8, 8), (30, 120, 200)), "PNG")


def jpeg_with_gps() -> bytes:
    """A JPEG a phone could have taken: GPS and device tags in its EXIF block."""
    exif = Image.Exif()
    exif[34853] = GPS_TAGS  # GPSInfo
    exif[271] = "PhoneMaker"  # Make
    return _encode(Image.new("RGB", (32, 32), (180, 40, 90)), "JPEG", exif=exif)


def truncated_jpeg() -> bytes:
    data = _encode(_noisy((64, 64)), "JPEG")
    return data[: len(data) // 2]


def assert_without_metadata(data: bytes) -> None:
    image = Image.open(BytesIO(data))
    assert not dict(image.getexif()), "EXIF survived"
    assert b"Exif" not in data and b"PhoneMaker" not in data


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


async def _upload(client: AsyncClient, org_slug: str, headers: dict[str, str], **kwargs):  # type: ignore[no-untyped-def]
    files = {
        "file": (
            kwargs.get("name", "rathaus.png"),
            BytesIO(kwargs.get("data", PNG_BYTES)),
            kwargs.get("mime", "image/png"),
        )
    }
    return await client.post(f"/api/orgs/{org_slug}/media/", headers=headers, files=files)


@pytest.mark.asyncio
async def test_creator_uploads_and_gets_media_route(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    creator = await create_test_user(db_session, email="creator@org-a.test", role=UserRole.CREATOR, organization=org_a)
    response = await _upload(client, org_a.slug, get_auth_headers(creator), name="Das Rathaus (1).png")
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["key"].startswith(f"{org_a.id}/media/")
    assert data["key"].endswith("_Das-Rathaus-1-.png")
    assert data["url"] == f"/api/orgs/{org_a.slug}/media/{data['key']}"


@pytest.mark.asyncio
async def test_participant_cannot_upload(client: AsyncClient, db_session: AsyncSession, org_a) -> None:
    participant = await create_test_user(
        db_session, email="p@org-a.test", role=UserRole.PARTICIPANT, organization=org_a
    )
    response = await _upload(client, org_a.slug, get_auth_headers(participant))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_wrong_type_is_refused(client: AsyncClient, db_session: AsyncSession, org_a) -> None:
    creator = await create_test_user(db_session, email="creator@org-a.test", role=UserRole.CREATOR, organization=org_a)
    response = await _upload(client, org_a.slug, get_auth_headers(creator), name="x.svg", mime="image/svg+xml")
    assert response.status_code == 415
    assert response.json()["error_code"] == "IMAGE_NOT_DECODABLE"


@pytest.mark.asyncio
async def test_a_type_parameter_is_accepted_and_the_bare_type_is_stored(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    """A type parameter on the claimed MIME type is dropped, and the bare type is stored and served."""
    creator = await create_test_user(db_session, email="creator@org-a.test", role=UserRole.CREATOR, organization=org_a)
    headers = get_auth_headers(creator)

    upload = await _upload(client, org_a.slug, headers, mime="IMAGE/PNG; charset=binary")

    assert upload.status_code == 201, upload.text
    stat = storage_bucket.stat_object(settings.storage.bucket, upload.json()["key"])
    assert stat.content_type == "image/png"
    served = await client.get(upload.json()["url"], headers=headers)
    assert served.headers["content-type"] == "image/png"
    assert served.headers["content-disposition"] == 'inline; filename="rathaus.png"'


@pytest.mark.asyncio
async def test_oversized_image_is_refused(client: AsyncClient, db_session: AsyncSession, org_a) -> None:
    creator = await create_test_user(db_session, email="creator@org-a.test", role=UserRole.CREATOR, organization=org_a)
    response = await _upload(client, org_a.slug, get_auth_headers(creator), data=b"\x00" * (MAX_FILE_SIZE + 1))
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_guest_of_same_org_can_read_and_other_org_cannot(
    client: AsyncClient, db_session: AsyncSession, org_a, org_b, storage_bucket: Minio
) -> None:
    creator = await create_test_user(db_session, email="creator@org-a.test", role=UserRole.CREATOR, organization=org_a)
    upload = await _upload(client, org_a.slug, get_auth_headers(creator))
    assert upload.status_code == 201, upload.text
    url = upload.json()["url"]

    guest = await create_test_user(db_session, email="guest@org-a.test", role=UserRole.GUEST, organization=org_a)
    served = await client.get(url, headers=get_auth_headers(guest))
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].startswith("image/png")
    assert served.headers["cache-control"] == "private, max-age=3600"
    assert Image.open(BytesIO(served.content)).size == (8, 8)

    outsider = await create_test_user(db_session, email="admin@org-b.test", role=UserRole.ORG_ADMIN, organization=org_b)
    # Through the other organisation's slug the key does not belong to that org.
    foreign_slug = await client.get(
        url.replace(f"/orgs/{org_a.slug}/", f"/orgs/{org_b.slug}/"), headers=get_auth_headers(outsider)
    )
    assert foreign_slug.status_code == 403
    # Through org A's slug the caller is not a member of org A.
    foreign_member = await client.get(url, headers=get_auth_headers(outsider))
    assert foreign_member.status_code == 403


@pytest.mark.asyncio
async def test_unknown_and_malformed_keys_are_404(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    guest = await create_test_user(db_session, email="guest@org-a.test", role=UserRole.GUEST, organization=org_a)
    headers = get_auth_headers(guest)
    missing = await client.get(f"/api/orgs/{org_a.slug}/media/{org_a.id}/media/nope.png", headers=headers)
    assert missing.status_code == 404
    malformed = await client.get(f"/api/orgs/{org_a.slug}/media/not-a-key", headers=headers)
    assert malformed.status_code == 404
    submission_prefix = await client.get(f"/api/orgs/{org_a.slug}/media/{org_a.id}/runs/abc.png", headers=headers)
    assert submission_prefix.status_code == 404


# ---------------------------------------------------------------------------
# The legal pages are public, so an image in a legal text must load without a
# session. Every other image stays members-only.
# ---------------------------------------------------------------------------


async def _two_images_one_in_the_imprint(client: AsyncClient, db_session: AsyncSession, org):  # type: ignore[no-untyped-def]
    admin = await create_test_user(
        db_session, email=f"admin@{org.slug}.test", role=UserRole.ORG_ADMIN, organization=org
    )
    headers = get_auth_headers(admin)
    in_imprint = (await _upload(client, org.slug, headers, name="logo.png")).json()["url"]
    in_content = (await _upload(client, org.slug, headers, name="station.png")).json()["url"]
    saved = await client.put(
        f"/api/orgs/{org.slug}",
        headers=headers,
        json={"legal_imprint": f'<h2>Impressum</h2><p><img src="{in_imprint}" alt="Logo"></p>'},
    )
    assert saved.status_code == 200, saved.text
    return in_imprint, in_content


@pytest.mark.asyncio
async def test_an_image_in_a_legal_text_loads_without_a_session(
    client: AsyncClient, db_session: AsyncSession, org_a, org_b, storage_bucket: Minio
) -> None:
    in_imprint, in_content = await _two_images_one_in_the_imprint(client, db_session, org_a)

    anonymous = await client.get(in_imprint)
    assert anonymous.status_code == 200, anonymous.text
    assert Image.open(BytesIO(anonymous.content)).size == (8, 8)
    # Only the legal-text image: the other one still needs a member's session.
    assert (await client.get(in_content)).status_code == 401

    # Anyone means a member of another organisation too.
    outsider = await create_test_user(db_session, email="admin@org-b.test", role=UserRole.ORG_ADMIN, organization=org_b)
    assert (await client.get(in_imprint, headers=get_auth_headers(outsider))).status_code == 200
    assert (await client.get(in_content, headers=get_auth_headers(outsider))).status_code == 403


@pytest.mark.asyncio
async def test_a_legal_text_image_is_members_only_again_once_it_leaves_the_text(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    in_imprint, _ = await _two_images_one_in_the_imprint(client, db_session, org_a)
    admin = await create_test_user(db_session, email="admin2@org-a.test", role=UserRole.ORG_ADMIN, organization=org_a)

    removed = await client.put(
        f"/api/orgs/{org_a.slug}", headers=get_auth_headers(admin), json={"legal_imprint": "<p>Impressum</p>"}
    )
    assert removed.status_code == 200, removed.text

    assert (await client.get(in_imprint)).status_code == 401


@pytest.mark.asyncio
async def test_an_archived_organisations_legal_image_is_members_only(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    """An archived organisation's legal texts are not shown (routers/public.py), so neither are their images."""
    from datetime import UTC, datetime

    in_imprint, _ = await _two_images_one_in_the_imprint(client, db_session, org_a)
    org_a.archived_at = datetime.now(UTC)
    await db_session.commit()

    assert (await client.get(in_imprint)).status_code == 401


@pytest.mark.asyncio
async def test_only_a_real_image_source_of_a_legal_text_opens_an_image(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    """Text that looks like a source inside another attribute opens nothing.

    The decision reads parsed ``img`` elements. A search over the text of the tag took the first thing that
    looked like ``src=``, here the address inside ``alt``, and opened that image to anyone.
    """
    slug = org_a.slug
    admin = await create_test_user(db_session, email="admin@org-a.test", role=UserRole.ORG_ADMIN, organization=org_a)
    headers = get_auth_headers(admin)
    shown = (await _upload(client, slug, headers, name="logo.png")).json()["url"]
    hidden = (await _upload(client, slug, headers, name="intern.png")).json()["url"]

    # Through the write filter, which keeps the alt text.
    saved = await client.put(
        f"/api/orgs/{slug}",
        headers=headers,
        json={"legal_imprint": f"""<p><img alt=" src='{hidden}' " src="{shown}"></p>"""},
    )
    assert saved.status_code == 200, saved.text
    assert hidden in saved.json()["legal_imprint"]
    assert (await client.get(shown)).status_code == 200
    assert (await client.get(hidden)).status_code == 401

    # As stored text of an older release, in every place a source-like text can stand.
    decoys = [
        f"""<img alt=' src="{hidden}" ' src="{shown}">""",
        f"""<a href="https://example.org/?q=<img src='{hidden}'>">x</a><img src="{shown}">""",
        f"""<!-- <img src="{hidden}"> --><p>&lt;img src="{hidden}"&gt; src="{hidden}"</p><img src="{shown}">""",
        f"""<img src="{shown}" src="{hidden}">""",
        f"""<img src="{hidden}?x=1"><img src="{hidden}#x"><img src="{shown}">""",
    ]
    for text in decoys:
        await db_session.execute(
            update(Organization).where(Organization.slug == slug).values(legal_privacy=text, legal_imprint=None)
        )
        await db_session.commit()
        assert (await client.get(shown)).status_code == 200, text
        assert (await client.get(hidden)).status_code == 401, text


@pytest.mark.asyncio
async def test_a_legal_image_with_a_percent_encoded_name_is_open(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    """The key of the request and the key of the source are compared in one form, percent-decoded."""
    slug = org_a.slug
    admin = await create_test_user(db_session, email="admin@org-a.test", role=UserRole.ORG_ADMIN, organization=org_a)
    url = (await _upload(client, slug, get_auth_headers(admin), name="logo.png")).json()["url"]
    encoded = url.replace("logo.png", "%6cogo.png")
    assert encoded != url
    await db_session.execute(
        update(Organization)
        .where(Organization.slug == slug)
        .values(legal_imprint=f'<p><img src="{encoded}" alt="Logo"></p>')
    )
    await db_session.commit()

    assert (await client.get(url)).status_code == 200
    assert (await client.get(encoded)).status_code == 200


@pytest.mark.asyncio
async def test_a_legal_text_cannot_open_another_organisations_image(
    client: AsyncClient, db_session: AsyncSession, org_a, org_b, storage_bucket: Minio
) -> None:
    """The src passes the sanitiser (right slug), but the key belongs to org B."""
    foreign_key = f"{org_b.id}/media/00000000-0000-0000-0000-000000000000_x.png"
    org_a.legal_privacy = f'<p><img src="/api/orgs/{org_a.slug}/media/{foreign_key}" alt="x"></p>'
    await db_session.commit()

    response = await client.get(f"/api/orgs/{org_a.slug}/media/{foreign_key}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_rich_text_image_is_stored_without_gps_and_device_metadata(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio
) -> None:
    """A rich-text image is stored with the same cleaning as the photo answers."""
    original = jpeg_with_gps()
    assert b"Exif" in original and b"PhoneMaker" in original
    creator = await create_test_user(db_session, email="creator@org-a.test", role=UserRole.CREATOR, organization=org_a)
    headers = get_auth_headers(creator)

    upload = await _upload(client, org_a.slug, headers, name="foto.jpg", mime="image/jpeg", data=original)

    assert upload.status_code == 201, upload.text
    stored = storage_bucket.get_object(settings.storage.bucket, upload.json()["key"]).read()
    assert_without_metadata(stored)
    served = await client.get(upload.json()["url"], headers=headers)
    assert_without_metadata(served.content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mime,name,data",
    [
        ("image/jpeg", "cut.jpg", truncated_jpeg()),
        ("image/png", "fake.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64),
    ],
)
async def test_an_undecodable_rich_text_image_is_refused(
    client: AsyncClient, db_session: AsyncSession, org_a, storage_bucket: Minio, mime: str, name: str, data: bytes
) -> None:
    """An undecodable rich-text image is refused with 415 IMAGE_NOT_DECODABLE and nothing is stored."""
    creator = await create_test_user(db_session, email="creator@org-a.test", role=UserRole.CREATOR, organization=org_a)

    response = await _upload(client, org_a.slug, get_auth_headers(creator), name=name, mime=mime, data=data)

    assert response.status_code == 415, response.text
    assert response.json()["error_code"] == "IMAGE_NOT_DECODABLE"
    assert list(storage_bucket.list_objects(settings.storage.bucket, prefix=f"{org_a.id}/", recursive=True)) == []
