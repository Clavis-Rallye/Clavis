"""
Active-content upload hygiene.

An upload that claims to be an image and cannot be decoded by the image
library is refused. An SVG with an ``onload`` handler must never be stored
under an ``image/*`` type and later served inline in the application origin.
On the serving side, every file answer carries nosniff and a
content-security policy that allows no script. Anything whose stored type
is not a raster image, audio, video or PDF is served as an attachment, also
for objects that were stored without the upload-side refusal.
"""

import random
from io import BytesIO
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from minio import Minio
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.storage import ensure_bucket, get_minio_client
from tests.test_file_upload import _org_for_slug, setup_run_with_team

# An SVG that runs script when rendered in a browser document context.
SVG_WITH_ONLOAD = (
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="fetch(\'/exfil\')" '
    b'width="100" height="100"><rect width="100" height="100"/></svg>'
)


def noisy_image(size: tuple[int, int], image_format: str) -> bytes:
    """A real Pillow-made image of random pixels, so its pixel data is long enough to damage."""
    rng = random.Random(7)
    image = Image.frombytes("RGB", size, bytes(rng.randrange(256) for _ in range(size[0] * size[1] * 3)))
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


def truncated_jpeg() -> bytes:
    """A JPEG cut off in the middle: header intact, pixel data incomplete."""
    data = noisy_image((64, 64), "JPEG")
    return data[: len(data) // 2]


def png_with_corrupt_pixels() -> bytes:
    """A PNG whose header is valid but whose compressed pixel stream is damaged."""
    data = bytearray(noisy_image((64, 64), "PNG"))
    start = data.index(b"IDAT") + 4 + 100
    for position in range(start, start + 40):
        data[position] ^= 0xFF
    return bytes(data)


@pytest_asyncio.fixture(scope="function")
async def storage_bucket() -> Minio:
    ensure_bucket()
    client = get_minio_client()
    yield client
    try:
        for obj in client.list_objects(settings.storage.bucket, recursive=True):
            client.remove_object(settings.storage.bucket, obj.object_name)
    except Exception:
        pass


class TestUndecodableImageRefusal:
    """An image upload the library cannot decode is refused, nothing stored."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content_type", ["image/svg+xml", "image/jpeg"])
    async def test_undecodable_image_claiming_image_type_is_refused(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        content_type: str,
    ):
        headers, org_slug, run_id, team_id = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("pic.svg", BytesIO(SVG_WITH_ONLOAD), content_type)},
        )

        assert response.status_code in (415, 422), response.text
        assert response.json()["error_code"]
        stored = list(storage_bucket.list_objects(settings.storage.bucket, prefix=team_id, recursive=True))
        assert stored == [], "nothing may be stored for a refused upload"


class TestUndecodableImageIsRefusedNotAServerError:
    """Bytes Pillow identifies but cannot decode are a 415, not a 500."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content_type,filename,make_bytes",
        [
            ("image/jpeg", "cut.jpg", truncated_jpeg),
            ("image/png", "corrupt.png", png_with_corrupt_pixels),
        ],
    )
    async def test_cut_off_or_corrupt_image_is_refused_with_415(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        content_type: str,
        filename: str,
        make_bytes,
    ):
        headers, org_slug, run_id, team_id = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": (filename, BytesIO(make_bytes()), content_type)},
        )

        assert response.status_code == 415, response.text
        assert response.json()["error_code"] == "IMAGE_NOT_DECODABLE"
        org = await _org_for_slug(db_session, org_slug)
        prefix = f"{org.id}/{run_id}/{team_id}/"
        stored = list(storage_bucket.list_objects(settings.storage.bucket, prefix=prefix, recursive=True))
        assert stored == [], "nothing may be stored for a refused upload"

    @pytest.mark.asyncio
    async def test_content_type_parameter_is_ignored_in_the_pass_through_check(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.heic", BytesIO(b"fake heic photo"), "image/heic; charset=binary")},
        )

        assert response.status_code == 201, response.text


class TestServeHardening:
    """Nosniff, a no-script CSP, and attachment for non-inline types."""

    @pytest.mark.asyncio
    async def test_svg_stored_before_the_change_is_served_as_attachment(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)
        org = await _org_for_slug(db_session, org_slug)
        object_key = f"{org.id}/{run_id}/{uuid4()}/ab12_old.svg"
        storage_bucket.put_object(
            bucket_name=settings.storage.bucket,
            object_name=object_key,
            data=BytesIO(SVG_WITH_ONLOAD),
            length=len(SVG_WITH_ONLOAD),
            content_type="image/svg+xml",
        )

        response = await client.get(f"/api/files/{object_key}", headers=headers)

        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["content-disposition"].startswith("attachment")


class TestInlineAnswersStillWork:
    """A photo answer renders inline under the same no-script policy.

    An HEIC pass-through photo downloads instead.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content_type,filename", [("image/jpeg", "pic.jpg"), ("image/png", "pic.png")])
    async def test_re_encoded_photo_is_served_inline_with_the_policy(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        content_type: str,
        filename: str,
    ):
        from tests.test_image_variants import plain_image

        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)
        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={
                "file": (
                    filename,
                    BytesIO(plain_image((60, 40), "JPEG" if content_type == "image/jpeg" else "PNG")),
                    content_type,
                )
            },
        )
        assert response.status_code == 201, response.text
        object_key = response.json()["object_key"]

        serve_response = await client.get(f"/api/files/{object_key}", headers=headers)

        assert serve_response.status_code == 200
        assert serve_response.headers["content-type"] == content_type
        assert serve_response.headers["content-disposition"].startswith("inline")
        assert serve_response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in serve_response.headers["content-security-policy"]

    @pytest.mark.asyncio
    async def test_heic_pass_through_photo_is_served_as_attachment(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
    ):
        """The HEIC pass-through never went through decoding and re-encoding,
        so it is downloaded, never rendered."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)
        heic = b"fake heic photo"
        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.heic", BytesIO(heic), "image/heic")},
        )
        assert response.status_code == 201, response.text

        serve_response = await client.get(f"/api/files/{response.json()['object_key']}", headers=headers)

        assert serve_response.status_code == 200
        assert serve_response.headers["content-disposition"].startswith("attachment")
