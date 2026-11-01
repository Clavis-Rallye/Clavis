"""An oversized upload is refused while it is read, and a caller without a session before that.

``LargeUploadRoute`` refuses a request whose Content-Length is above its ceiling
at once and counts the bytes of a request without a true length while it reads.

The bound follows the route and the declared kind of the part. The part is counted
against the cap of its declared type while it is read, a route that takes
images only has the image cap as its ceiling, and the session is checked before
the body is parsed.

Every test here proves the stop by the bytes the server pulled from the request
body, not only by the status: the body is a generator that counts what it hands
over, and it is sent chunked, without a Content-Length and without a length of
the part.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from app.storage import MAX_FILE_SIZE, MAX_VIDEO_FILE_SIZE, MULTIPART_FRAME_ALLOWANCE, max_upload_size
from app.upload_route import ImageUploadRoute, LargeUploadRoute
from tests.factories import create_test_organization, create_test_station, create_test_user, get_auth_headers
from tests.test_file_upload import setup_run_with_team

MB = 1024 * 1024
FRAME = MULTIPART_FRAME_ALLOWANCE
CHUNK = 64 * 1024
BOUNDARY = "t235boundary"

# What the server may pull beyond a cap before it notices: the chunk that
# crosses the cap and the head of the part.
SLACK = 2 * CHUNK

# The ceiling of a route that takes every kind of file: the video cap plus the frame.
OLD_CEILING = max(MAX_FILE_SIZE, MAX_VIDEO_FILE_SIZE) + FRAME
# Larger than the cap of an image, smaller than the ceiling of a route that takes video.
OVERSIZED_IMAGE = MAX_FILE_SIZE + 8 * MB
assert MAX_FILE_SIZE + SLACK < OVERSIZED_IMAGE < OLD_CEILING


class Offered:
    """A request body of ``parts`` file parts that counts the bytes the server pulled."""

    def __init__(self, content_type: str, size: int, *, parts: int = 1) -> None:
        self.content_type = content_type
        self.size = size
        self.parts = parts
        self.pulled = 0

    async def stream(self) -> AsyncIterator[bytes]:
        for number in range(self.parts):
            head = (
                f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="x{number}.bin"\r\n'
                f"Content-Type: {self.content_type}\r\n\r\n"
            ).encode()
            self.pulled += len(head)
            yield head
            remaining = self.size
            while remaining:
                chunk = min(CHUNK, remaining)
                remaining -= chunk
                self.pulled += chunk
                yield b"x" * chunk
            yield b"\r\n"
        yield f"--{BOUNDARY}--\r\n".encode()


async def _send(
    client: AsyncClient,
    method: str,
    url: str,
    body: Offered,
    headers: dict[str, str] | None = None,
) -> Response:
    """Send ``body`` chunked: httpx sets no Content-Length for a generator."""
    request_headers = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"} | (headers or {})
    response = await client.request(method, url, headers=request_headers, content=body.stream())
    assert "content-length" not in response.request.headers or "Content-Length" in (headers or {})
    return response


def _answer_url(org_slug: str = "any-org", run_id: str | None = None) -> str:
    return f"/api/orgs/{org_slug}/runs/{run_id or uuid.uuid4()}/submissions/upload/"


def _media_url(org_slug: str = "any-org") -> str:
    return f"/api/orgs/{org_slug}/media/"


def _title_image_url(org_slug: str = "any-org", station_id: object | None = None) -> str:
    return f"/api/orgs/{org_slug}/stations/{station_id or uuid.uuid4()}/title-image"


async def _creator(db_session: AsyncSession, slug: str) -> tuple[str, dict[str, str], object]:
    """An organisation with a creator and a station: (slug, headers, station id)."""
    org = await create_test_organization(db_session, name=f"Org {slug}", slug=slug)
    creator = await create_test_user(
        db_session, email=f"creator@{slug}.test", role=UserRole.CREATOR, organization_id=org.id
    )
    station = await create_test_station(db_session, organization=org, created_by_user_id=creator.id)
    return org.slug, get_auth_headers(creator), station.id


# --- One place defines the caps, and the ceiling follows the route ---


@pytest.mark.parametrize(
    ("declared", "cap"),
    [
        ("video/mp4", MAX_VIDEO_FILE_SIZE),
        (" Video/QuickTime ", MAX_VIDEO_FILE_SIZE),
        ("image/jpeg", MAX_FILE_SIZE),
        ("audio/mpeg", MAX_FILE_SIZE),
        ("application/pdf", MAX_FILE_SIZE),
        ("", MAX_FILE_SIZE),
        (None, MAX_FILE_SIZE),
    ],
)
def test_the_cap_of_a_declared_type(declared: str | None, cap: int) -> None:
    assert max_upload_size(declared) == cap


def test_the_ceiling_of_a_route_is_its_largest_file_plus_the_frame() -> None:
    assert LargeUploadRoute.max_body_size == OLD_CEILING
    assert LargeUploadRoute.part_size_cap("video/mp4") == MAX_VIDEO_FILE_SIZE
    assert LargeUploadRoute.part_size_cap("image/jpeg") == MAX_FILE_SIZE
    assert ImageUploadRoute.max_body_size == MAX_FILE_SIZE + FRAME
    assert ImageUploadRoute.part_size_cap("video/mp4") == MAX_FILE_SIZE


# --- The cap of the declared kind is enforced while the part is read ---


@pytest.mark.asyncio
async def test_answer_route_stops_an_image_at_the_image_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)
    body = Offered("image/jpeg", OVERSIZED_IMAGE)

    response = await _send(client, "POST", _answer_url(org_slug, run_id), body, headers)

    assert response.status_code == 413, response.text
    assert "exceeds maximum allowed size" in response.json()["detail"]
    assert body.pulled <= MAX_FILE_SIZE + SLACK, f"the server read {body.pulled} bytes of an image part"


@pytest.mark.asyncio
async def test_answer_route_takes_the_declared_type_in_any_case(client: AsyncClient, db_session: AsyncSession) -> None:
    """The cap follows the type as the route normalizes it: IMAGE/JPEG is an image."""
    headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)
    body = Offered("IMAGE/JPEG", OVERSIZED_IMAGE)

    response = await _send(client, "POST", _answer_url(org_slug, run_id), body, headers)

    assert response.status_code == 413, response.text
    assert body.pulled <= MAX_FILE_SIZE + SLACK, f"the server read {body.pulled} bytes of an image part"


@pytest.mark.asyncio
async def test_answer_route_reads_a_video_between_the_two_caps_whole(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The video cap still holds for a part declared as video, in any case of the type.

    The bytes are no video, so the cleaner refuses them: 422, not 413. What
    counts here is that the part was read to its end.
    """
    headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)
    size = MAX_FILE_SIZE + MB
    assert size < MAX_VIDEO_FILE_SIZE
    body = Offered("VIDEO/MP4", size)

    response = await _send(client, "POST", _answer_url(org_slug, run_id), body, headers)

    assert response.status_code == 422, response.text
    assert response.json()["error_code"] == "MEDIA_NOT_READABLE"
    assert body.pulled >= size


@pytest.mark.asyncio
async def test_rich_text_route_stops_an_image_at_the_image_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    org_slug, headers, _ = await _creator(db_session, "guard-media")
    body = Offered("image/png", OVERSIZED_IMAGE)

    response = await _send(client, "POST", _media_url(org_slug), body, headers)

    assert response.status_code == 413, response.text
    assert body.pulled <= MAX_FILE_SIZE + SLACK, f"the server read {body.pulled} bytes of an image part"


@pytest.mark.asyncio
async def test_title_image_route_stops_an_image_at_the_image_cap(client: AsyncClient, db_session: AsyncSession) -> None:
    org_slug, headers, station_id = await _creator(db_session, "guard-title")
    body = Offered("image/png", OVERSIZED_IMAGE)

    response = await _send(client, "PUT", _title_image_url(org_slug, station_id), body, headers)

    assert response.status_code == 413, response.text
    assert body.pulled <= MAX_FILE_SIZE + SLACK, f"the server read {body.pulled} bytes of an image part"


# --- A route that takes images only has the image cap as its ceiling ---


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["rich-text", "title-image"])
async def test_image_route_does_not_grant_the_video_cap_to_a_part_declared_as_video(
    client: AsyncClient, db_session: AsyncSession, route: str
) -> None:
    org_slug, headers, station_id = await _creator(db_session, f"guard-video-{route}")
    body = Offered("video/mp4", OVERSIZED_IMAGE)

    if route == "rich-text":
        response = await _send(client, "POST", _media_url(org_slug), body, headers)
    else:
        response = await _send(client, "PUT", _title_image_url(org_slug, station_id), body, headers)

    assert response.status_code == 413, response.text
    assert body.pulled <= MAX_FILE_SIZE + SLACK, f"the server read {body.pulled} bytes on an image route"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["rich-text", "title-image"])
async def test_image_route_refuses_a_declared_length_above_the_image_ceiling_without_reading(
    client: AsyncClient, db_session: AsyncSession, route: str
) -> None:
    org_slug, headers, station_id = await _creator(db_session, f"guard-length-{route}")
    body = Offered("image/png", CHUNK)
    declared = {"Content-Length": str(MAX_FILE_SIZE + FRAME + 1)}
    assert MAX_FILE_SIZE + FRAME + 1 < OLD_CEILING

    if route == "rich-text":
        response = await _send(client, "POST", _media_url(org_slug), body, headers | declared)
    else:
        response = await _send(client, "PUT", _title_image_url(org_slug, station_id), body, headers | declared)

    assert response.status_code == 413, response.text
    assert body.pulled == 0


@pytest.mark.asyncio
async def test_image_route_cuts_a_body_of_small_parts_at_its_ceiling(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Each part is inside the image cap, the request as a whole is above the ceiling of the route."""
    org_slug, headers, _ = await _creator(db_session, "guard-parts")
    part = 6 * MB
    assert part < MAX_FILE_SIZE and MAX_FILE_SIZE + FRAME < 3 * part < OLD_CEILING
    body = Offered("image/png", part, parts=3)

    response = await _send(client, "POST", _media_url(org_slug), body, headers)

    assert response.status_code == 413, response.text
    assert body.pulled <= MAX_FILE_SIZE + FRAME + SLACK, f"the server read {body.pulled} bytes"


# --- The session is checked before the body is parsed ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url"),
    [("POST", _answer_url()), ("POST", _media_url()), ("PUT", _title_image_url())],
    ids=["answer", "rich-text", "title-image"],
)
async def test_a_request_without_a_session_is_refused_before_the_file_part_is_read(
    client: AsyncClient, method: str, url: str
) -> None:
    body = Offered("image/jpeg", 4 * CHUNK)

    response = await _send(client, method, url, body)

    assert response.status_code == 401, response.text
    assert body.pulled == 0, f"the server read {body.pulled} bytes for a caller without a session"


@pytest.mark.asyncio
async def test_a_session_below_the_role_of_the_route_is_refused_before_the_file_part_is_read(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The check before the body is the check of the route: a participant uploads no rich-text image."""
    org = await create_test_organization(db_session, name="Org guard-role", slug="guard-role")
    participant = await create_test_user(
        db_session, email="p@guard-role.test", role=UserRole.PARTICIPANT, organization_id=org.id
    )
    body = Offered("image/png", 4 * CHUNK)

    response = await _send(client, "POST", _media_url(org.slug), body, get_auth_headers(participant))

    assert response.status_code == 403, response.text
    assert body.pulled == 0


# --- The whole body is bounded, also for a caller without a session ---


@pytest.mark.asyncio
async def test_declared_length_above_the_ceiling_is_refused_without_reading(client: AsyncClient) -> None:
    body = Offered("image/jpeg", CHUNK)

    response = await _send(client, "POST", _answer_url(), body, {"Content-Length": str(OLD_CEILING + 1)})

    assert response.status_code == 413
    assert body.pulled == 0


@pytest.mark.asyncio
async def test_body_within_the_ceiling_still_reaches_the_session_check(client: AsyncClient) -> None:
    body = Offered("image/jpeg", 1024)

    response = await _send(client, "POST", _answer_url(), body)

    assert response.status_code == 401
