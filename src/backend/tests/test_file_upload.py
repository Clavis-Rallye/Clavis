"""
Integration tests for the file upload and file serve endpoints.

Tests reach the S3 store through the Garage container that `make test`
runs on 127.0.0.1:59000 (CI starts its own throwaway Garage the same way).
"""

from io import BytesIO
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from minio import Minio
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.main import app
from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from app.routers import files as files_router
from app.services import image_variants
from app.storage import MAX_FILE_SIZE, MAX_MULTIPART_PART_SIZE, MAX_VIDEO_FILE_SIZE, ensure_bucket, get_minio_client
from tests import media_samples
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers
from tests.test_image_variants import assert_no_metadata, jpeg_with_gps, plain_image


@pytest_asyncio.fixture(scope="function")
async def storage_bucket() -> Minio:
    """The S3 client with the bucket ready. Every object is removed after the test."""
    ensure_bucket()
    client = get_minio_client()
    yield client

    try:
        objects = client.list_objects(settings.storage.bucket, recursive=True)
        for obj in objects:
            client.remove_object(settings.storage.bucket, obj.object_name)
    except S3Error:
        pass  # Cleanup is best-effort


async def _org_for_slug(db_session: AsyncSession, slug: str):
    """Look up an Organization by slug for cross-team test helpers."""
    from sqlalchemy import select

    from app.models.organization import Organization

    result = await db_session.execute(select(Organization).where(Organization.slug == slug))
    org = result.scalar_one_or_none()
    assert org is not None, f"Organization with slug {slug!r} not found"
    return org


async def setup_run_with_team(
    client: AsyncClient,
    db_session: AsyncSession,
    org_slug: str = "test-org",
) -> tuple[dict, str, str, str]:
    """An active run with one team, joined by a coordinator. Returns (headers, org_slug, run_id, team_id)."""
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug=org_slug
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(
        title={"de": "Test Rallye"},
        description={"de": "Test"},
        is_published=True,
    )

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "First station"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_1 = await challenge_repo.create(
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Photo Challenge"},
        instructions={"de": "Take a photo"},
        scoring_rules={"max_points": 10},
    )
    db_session.add(
        RallyeStationChallenge(
            organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_1.id, sort_order=0
        )
    )
    await db_session.flush()

    await db_session.commit()

    run_response = await client.post(
        f"/api/orgs/{org.slug}/runs/",
        headers=headers,
        json={"rallye_id": str(rallye.id), "name": "Test Run"},
    )
    assert run_response.status_code == 201
    run_id = run_response.json()["id"]

    # Starting the run creates the snapshots and sets the state to ACTIVE.
    start_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)
    assert start_response.status_code == 200

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
        headers=headers,
        json={"name": "Team Alpha"},
    )
    assert team_response.status_code == 201
    team_id = team_response.json()["id"]

    join_response = await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)
    assert join_response.status_code == 204

    return headers, org.slug, run_id, team_id


class TestFileUploadSuccess:
    """Successful file uploads."""

    @pytest.mark.asyncio
    async def test_upload_valid_image(self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio):
        """A valid image uploads with 201 and answers object key, presigned URL, content type and stored size."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        # A real image: the route refuses image/* bytes that do not decode,
        # so every 201-expecting image fixture must be decodable.
        file_content = jpeg_with_gps((300, 200))
        files = {"file": ("test_image.jpg", BytesIO(file_content), "image/jpeg")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 201
        data = response.json()

        assert "object_key" in data
        assert "url" in data
        assert data["content_type"] == "image/jpeg"
        # Re-encoded, so the size is that of the cleaned bytes actually stored.
        stored = storage_bucket.get_object(settings.storage.bucket, data["object_key"]).read()
        assert data["size"] == len(stored)

        assert data["url"].startswith("http")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filename,content_type,content",
        [
            # Voice recordings: one m4a file, three labels the browsers disagree on
            ("voice.m4a", "audio/x-m4a", media_samples.tiny_mp4()),
            ("voice.m4a", "audio/mp4", media_samples.tiny_mp4()),
            ("voice.m4a", "audio/m4a", media_samples.tiny_mp4()),
            ("voice.aac", "audio/aac", media_samples.AAC_FRAMES),  # Signal exports these
            ("voice.opus", "audio/opus", media_samples.ogg_opus()),
            ("voice.3gp", "audio/3gpp", media_samples.tiny_mp4()),
            ("voice.amr", "audio/amr", media_samples.AMR_FILE),
            # Video: an iPhone films .mov
            ("clip.mov", "video/quicktime", media_samples.tiny_mp4()),
            ("clip.3gp", "video/3gpp", media_samples.tiny_mp4()),
            # Photos: the iPhone library hands out heic
            ("photo.heic", "image/heic", b"fake heic photo"),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    async def test_upload_real_device_media(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        filename: str,
        content_type: str,
        content: bytes,
    ):
        """What phones actually produce uploads, whatever it is called.

        The allowlist takes the media families wholesale. The client declares the
        type, so listing subtypes would only reject real devices: m4a voice memos,
        Signal's aac, an iPhone's video/quicktime, heic photos.

        Video and audio are real containers here, because the route cleans
        them and refuses bytes it cannot read.
        """
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        files = {"file": (filename, BytesIO(content), content_type)}
        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 201, response.text
        assert response.json()["content_type"] == content_type

    @pytest.mark.asyncio
    async def test_object_key_format(self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio):
        """The object key has four segments and carries the run id, the team id and the file name."""
        headers, org_slug, run_id, team_id = await setup_run_with_team(client, db_session)

        file_content = plain_image((20, 20), "PNG")
        filename = "my_photo.png"
        files = {"file": (filename, BytesIO(file_content), "image/png")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 201
        object_key = response.json()["object_key"]

        # Object key should have format: {org_id}/{run_id}/{team_id}/{uuid}_{filename}
        parts = object_key.split("/")
        assert len(parts) == 4, f"Expected 4 path segments, got {len(parts)}: {object_key}"

        assert run_id in object_key, f"Run ID {run_id} not found in object_key {object_key}"

        assert team_id in object_key, f"Team ID {team_id} not found in object_key {object_key}"

        last_segment = parts[-1]
        assert "_" in last_segment, f"Expected uuid_filename format, got {last_segment}"
        assert filename.replace(".", "") in last_segment.replace(".", ""), f"Filename not found in {last_segment}"


class TestFileUploadValidation:
    """Refusals of an upload by size and type."""

    @pytest.mark.asyncio
    async def test_upload_oversized_file(self, client: AsyncClient, db_session: AsyncSession):
        """A file above the size limit answers 413 with a message about the size."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        oversized_content = b"x" * (MAX_FILE_SIZE + 1)
        files = {"file": ("large_file.jpg", BytesIO(oversized_content), "image/jpeg")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 413
        detail = response.json()["detail"]
        assert "exceeds" in detail.lower() or "size" in detail.lower()

    @pytest.mark.asyncio
    async def test_upload_midrange_file_succeeds(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """A 3 MB upload succeeds, between the 1 MB default part cap of Starlette and our 10 MB limit.

        Without the raised multipart cap it would answer 500 during form parsing.
        """
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        midrange_content = b"x" * (3 * 1024 * 1024)  # 3 MB, over the default 1 MB part cap
        assert 1024 * 1024 < len(midrange_content) < MAX_FILE_SIZE
        # HEIC pass-through: undecodable bytes of a pass-through photo
        # container are stored as uploaded, so the size check is still reached.
        files = {"file": ("midrange.heic", BytesIO(midrange_content), "image/heic")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 201, response.text
        assert response.json()["size"] == len(midrange_content)

    @pytest.mark.asyncio
    async def test_upload_over_multipart_cap_returns_413(self, client: AsyncClient, db_session: AsyncSession):
        """A part exceeding MAX_MULTIPART_PART_SIZE returns a clean 413, never a 500.

        It comes from the global MultiPartException handler if the parser enforces
        the cap, otherwise from the size check of the endpoint. Both yield 413.
        """
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        too_large = b"x" * (MAX_MULTIPART_PART_SIZE + 1)
        files = {"file": ("huge.jpg", BytesIO(too_large), "image/jpeg")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 413, response.text

    @pytest.mark.asyncio
    async def test_upload_disallowed_mime_type(self, client: AsyncClient, db_session: AsyncSession):
        """A disallowed MIME type answers 422 with a message about the type."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        file_content = b"MZ executable content"
        files = {"file": ("malware.exe", BytesIO(file_content), "application/x-msdownload")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "mime" in detail.lower() or "not allowed" in detail.lower()


class TestVideoUploadSizeCap:
    """The video/* MIME cap is raised above the base file cap."""

    @pytest.mark.asyncio
    async def test_upload_over_base_cap_under_video_cap_accepted_for_video(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """A video/* upload larger than the base cap but within the raised video
        cap must be accepted."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        # Larger than the base (10MB) cap, well within the video (100MB) cap.
        oversized_for_base = media_samples.tiny_mp4(video_payload=b"x" * (MAX_FILE_SIZE + 1024 * 1024))
        assert MAX_FILE_SIZE < len(oversized_for_base) < MAX_VIDEO_FILE_SIZE
        files = {"file": ("clip.mp4", BytesIO(oversized_for_base), "video/mp4")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 201, response.text
        assert response.json()["size"] == len(oversized_for_base)

    @pytest.mark.asyncio
    async def test_upload_over_base_cap_rejected_for_non_video(self, client: AsyncClient, db_session: AsyncSession):
        """The same size, non-video/* upload must still be rejected by the base cap."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        oversized_for_base = b"x" * (MAX_FILE_SIZE + 1024 * 1024)
        files = {"file": ("large_photo.jpg", BytesIO(oversized_for_base), "image/jpeg")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 413
        detail = response.json()["detail"]
        assert "exceeds" in detail.lower() or "size" in detail.lower()


class TestFileUploadAccessControl:
    """Who may upload to a run."""

    @pytest.mark.asyncio
    async def test_upload_without_team_membership(self, client: AsyncClient, db_session: AsyncSession):
        """A user who is not a member of a team of the run gets 404."""
        coord_user, org = await create_test_user_with_org(
            db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
        )
        coord_headers = get_auth_headers(coord_user)

        other_user = await create_test_user(
            db_session, email="other@test.com", role=UserRole.PARTICIPANT, organization=org
        )
        other_headers = get_auth_headers(other_user)

        rallye_repo = RallyeRepository(db_session, org.id)
        rallye = await rallye_repo.create(
            title={"de": "Test Rallye"},
            description={"de": "Test"},
            is_published=True,
        )

        station_repo = StationRepository(db_session, org.id)
        station = await station_repo.create(
            title={"de": "Station 1"},
            description={"de": "First station"},
        )
        rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
        db_session.add(rs_station)
        await db_session.flush()

        challenge_repo = ChallengeRepository(db_session, org.id)
        _challenge_2 = await challenge_repo.create(
            challenge_type=ChallengeType.PHOTO,
            title={"de": "Photo Challenge"},
            instructions={"de": "Take a photo"},
            scoring_rules={"max_points": 10},
        )
        db_session.add(
            RallyeStationChallenge(
                organization_id=org.id, rallye_station_id=rs_station.id, challenge_id=_challenge_2.id, sort_order=0
            )
        )
        await db_session.flush()

        await db_session.commit()

        run_response = await client.post(
            f"/api/orgs/{org.slug}/runs/",
            headers=coord_headers,
            json={"rallye_id": str(rallye.id), "name": "Test Run"},
        )
        run_id = run_response.json()["id"]
        await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=coord_headers)

        # The team exists, but other_user does not join it.
        await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/teams/",
            headers=coord_headers,
            json={"name": "Team Alpha"},
        )

        file_content = b"unauthorized upload attempt"
        files = {"file": ("test.jpg", BytesIO(file_content), "image/jpeg")}

        response = await client.post(
            f"/api/orgs/{org.slug}/runs/{run_id}/submissions/upload/",
            headers=other_headers,
            files=files,
        )

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "not a member" in detail.lower() or "team" in detail.lower()

    @pytest.mark.asyncio
    async def test_upload_to_nonexistent_run(self, client: AsyncClient, db_session: AsyncSession):
        """An upload to a run that does not exist gets 404."""
        user, org = await create_test_user_with_org(
            db_session, email="user@test.com", role=UserRole.PARTICIPANT, org_slug="test-org"
        )
        headers = get_auth_headers(user)

        fake_run_id = str(uuid4())
        file_content = b"upload to nowhere"
        files = {"file": ("test.jpg", BytesIO(file_content), "image/jpeg")}

        response = await client.post(
            f"/api/orgs/{org.slug}/runs/{fake_run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "not found" in detail.lower() or "run" in detail.lower()


class TestFileUploadRunState:
    """Which run states accept an upload."""

    @pytest.mark.asyncio
    async def test_upload_to_paused_run(self, client: AsyncClient, db_session: AsyncSession):
        """A paused run refuses an upload with 422."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        pause_response = await client.post(f"/api/orgs/{org_slug}/runs/{run_id}/pause", headers=headers)
        assert pause_response.status_code == 200

        file_content = b"upload during pause"
        files = {"file": ("test.jpg", BytesIO(file_content), "image/jpeg")}

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "paused" in detail.lower() or "active" in detail.lower()


class TestFileServeEndpoint:
    """The file serve endpoint, ``GET /api/files/{key:path}``."""

    @pytest.mark.asyncio
    async def test_serve_valid_file_streams_content(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """The endpoint streams the stored bytes inline with the content type of the upload.

        The S3 store is internal-only, so the endpoint proxies the content instead
        of redirecting to a presigned URL.
        """
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        file_content = jpeg_with_gps((300, 200))
        files = {"file": ("serve_test.jpg", BytesIO(file_content), "image/jpeg")}

        upload_response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files=files,
        )
        assert upload_response.status_code == 201
        object_key = upload_response.json()["object_key"]
        stored = storage_bucket.get_object(settings.storage.bucket, object_key).read()

        serve_response = await client.get(
            f"/api/files/{object_key}",
            headers=headers,
        )

        assert serve_response.status_code == 200
        assert serve_response.content == stored
        assert serve_response.headers["content-type"] == "image/jpeg"
        assert serve_response.headers["content-disposition"].startswith("inline")

    @pytest.mark.asyncio
    async def test_serve_file_wrong_organization(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """A file of another organization answers 403."""
        headers_org1, org_slug_1, run_id_1, _ = await setup_run_with_team(client, db_session, org_slug="org-one")

        file_content = jpeg_with_gps((300, 200))
        files = {"file": ("test.jpg", BytesIO(file_content), "image/jpeg")}

        upload_response = await client.post(
            f"/api/orgs/{org_slug_1}/runs/{run_id_1}/submissions/upload/",
            headers=headers_org1,
            files=files,
        )
        assert upload_response.status_code == 201
        object_key = upload_response.json()["object_key"]

        user2, org2 = await create_test_user_with_org(
            db_session, email="other@test.com", role=UserRole.PARTICIPANT, org_slug="org-two"
        )
        headers_org2 = get_auth_headers(user2)

        serve_response = await client.get(
            f"/api/files/{object_key}",
            headers=headers_org2,
            follow_redirects=False,
        )

        assert serve_response.status_code == 403
        detail = serve_response.json()["detail"]
        assert "organization" in detail.lower() or "denied" in detail.lower()

    @pytest.mark.asyncio
    async def test_serve_nonexistent_file(self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio):
        """A key that does not exist answers 404.

        The actor is a coordinator: roles below fail at the team membership check
        (403) before the S3 store is consulted. Coordinator+ has run-wide
        visibility, so the request reaches the existence check of the S3 store.
        """
        user, org = await create_test_user_with_org(
            db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
        )
        headers = get_auth_headers(user)

        fake_key = f"{org.id}/{uuid4()}/{uuid4()}/abc123_fake.jpg"

        serve_response = await client.get(
            f"/api/files/{fake_key}",
            headers=headers,
            follow_redirects=False,
        )

        assert serve_response.status_code == 404
        detail = serve_response.json()["detail"]
        assert "not found" in detail.lower()

    @pytest.mark.asyncio
    async def test_serve_blocked_for_participant_without_team_membership(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """A participant of the same organization without a team in the run cannot see the media of a team."""
        headers_a, org_slug_a, run_id_a, _ = await setup_run_with_team(client, db_session, org_slug="org-one")
        files = {"file": ("teamA.jpg", BytesIO(jpeg_with_gps((40, 40))), "image/jpeg")}
        upload_response = await client.post(
            f"/api/orgs/{org_slug_a}/runs/{run_id_a}/submissions/upload/",
            headers=headers_a,
            files=files,
        )
        assert upload_response.status_code == 201
        object_key = upload_response.json()["object_key"]

        # A second participant in the same organization, with no team membership.
        intruder = await create_test_user(
            db_session,
            email="lurker@test.com",
            organization=(await _org_for_slug(db_session, org_slug_a)),
            role=UserRole.PARTICIPANT,
        )
        headers_intruder = get_auth_headers(intruder)

        serve_response = await client.get(
            f"/api/files/{object_key}",
            headers=headers_intruder,
            follow_redirects=False,
        )
        assert serve_response.status_code == 403

    @pytest.mark.asyncio
    async def test_serve_blocked_for_other_team_participant_in_same_run(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """A participant of team B in the same run cannot fetch the media of team A."""
        from app.repositories.team_member import TeamMemberRepository

        headers_a, org_slug, run_id, team_a_id = await setup_run_with_team(
            client, db_session, org_slug="cross-team-org"
        )

        upload_response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers_a,
            files={"file": ("teamA.jpg", BytesIO(jpeg_with_gps((40, 40))), "image/jpeg")},
        )
        assert upload_response.status_code == 201
        object_key = upload_response.json()["object_key"]

        # Team B with a different participant.
        org = await _org_for_slug(db_session, org_slug)
        other_user = await create_test_user(
            db_session,
            email="teamb@test.com",
            organization=org,
            role=UserRole.PARTICIPANT,
        )
        headers_b = get_auth_headers(other_user)

        team_b_response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/teams/",
            headers=headers_b,
            json={"name": "Team Bravo"},
        )
        # Teams endpoint requires guest+ (not coordinator+), so a participant can create a team.
        assert team_b_response.status_code == 201
        team_b_id = team_b_response.json()["id"]
        join_b = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/teams/{team_b_id}/join",
            headers=headers_b,
        )
        assert join_b.status_code == 204

        # Team B exists and other_user is a member of it.
        repo = TeamMemberRepository(db_session, organization_id=org.id)
        assert await repo.get_by_user_and_run(user_id=other_user.id, run_id=UUID(run_id)) is not None

        serve_response = await client.get(
            f"/api/files/{object_key}",
            headers=headers_b,
            follow_redirects=False,
        )
        assert serve_response.status_code == 403
        assert "team" in serve_response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_serve_allowed_for_coordinator_any_team(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """Coordinator+ can serve any team's file in their org."""
        # The setup helper signs in as a coordinator already.
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session, org_slug="coord-access-org")

        upload_response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("evidence.jpg", BytesIO(jpeg_with_gps((40, 40))), "image/jpeg")},
        )
        assert upload_response.status_code == 201
        object_key = upload_response.json()["object_key"]
        stored = storage_bucket.get_object(settings.storage.bucket, object_key).read()

        serve_response = await client.get(
            f"/api/files/{object_key}",
            headers=headers,
        )
        assert serve_response.status_code == 200
        assert serve_response.content == stored

    @pytest.mark.asyncio
    async def test_serve_unauthenticated(self, client: AsyncClient, storage_bucket: Minio):
        """A request without authentication answers 401."""
        fake_key = f"{uuid4()}/{uuid4()}/{uuid4()}/test.jpg"

        serve_response = await client.get(
            f"/api/files/{fake_key}",
            follow_redirects=False,
        )

        assert serve_response.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("variant", ["thumb", "display"])
    async def test_serve_variant_streams_webp(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        variant: str,
    ):
        """?variant=thumb|display serves the WebP object stored beside the photo, with its own content type."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        photo = jpeg_with_gps((2400, 1600))
        upload_response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.jpg", BytesIO(photo), "image/jpeg")},
        )
        assert upload_response.status_code == 201
        object_key = upload_response.json()["object_key"]
        variant_key = f"{object_key}.{variant}.webp"
        expected = storage_bucket.get_object(settings.storage.bucket, variant_key).read()

        serve_response = await client.get(
            f"/api/files/{object_key}",
            headers=headers,
            params={"variant": variant},
        )

        assert serve_response.status_code == 200
        assert serve_response.content == expected
        assert serve_response.headers["content-type"] == "image/webp"

    @pytest.mark.asyncio
    async def test_serve_variant_falls_back_to_original_when_missing(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """When the variant object does not exist, the original is served instead, with its own content type.

        An upload that never decoded gets no variants.
        """
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        file_content = b"undecodable heic, so no variants beside it"
        upload_response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.heic", BytesIO(file_content), "image/heic")},
        )
        assert upload_response.status_code == 201
        object_key = upload_response.json()["object_key"]

        serve_response = await client.get(
            f"/api/files/{object_key}",
            headers=headers,
            params={"variant": "thumb"},
        )

        assert serve_response.status_code == 200
        assert serve_response.content == file_content
        assert serve_response.headers["content-type"] == "image/heic"

    @pytest.mark.asyncio
    async def test_serve_invalid_key_format(self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio):
        """A key with fewer than four segments answers 404."""
        user, _ = await create_test_user_with_org(
            db_session, email="user@test.com", role=UserRole.PARTICIPANT, org_slug="test-org"
        )
        headers = get_auth_headers(user)

        invalid_key = "short/path"

        serve_response = await client.get(
            f"/api/files/{invalid_key}",
            headers=headers,
            follow_redirects=False,
        )

        assert serve_response.status_code == 404
        detail = serve_response.json()["detail"]
        assert "invalid" in detail.lower() or "format" in detail.lower()


class TestParticipantPhotoProcessing:
    """The participant upload route stores photos re-encoded without metadata, with two WebP variants beside them."""

    @pytest.mark.asyncio
    async def test_photo_upload_stores_cleaned_original_and_variants(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """A JPEG with GPS EXIF is stored cleaned under its own key, with a 400 px and a 1600 px WebP variant.

        The variants lie under `<key>.thumb.webp` and `<key>.display.webp`.
        """
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        photo = jpeg_with_gps((2400, 1600))
        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.jpg", BytesIO(photo), "image/jpeg")},
        )
        assert response.status_code == 201, response.text
        object_key = response.json()["object_key"]

        # Stored original: same key, still JPEG, no EXIF and no GPS, and the
        # response size reflects the cleaned bytes that were actually stored.
        stored = storage_bucket.get_object(settings.storage.bucket, object_key).read()
        cleaned = assert_no_metadata(stored, "JPEG")
        assert cleaned.size == (2400, 1600)
        assert response.json()["size"] == len(stored)

        # Both variants exist next to it, at their capped long sides.
        thumb = storage_bucket.get_object(settings.storage.bucket, f"{object_key}.thumb.webp").read()
        assert assert_no_metadata(thumb, "WEBP").size == (400, 267)
        display = storage_bucket.get_object(settings.storage.bucket, f"{object_key}.display.webp").read()
        assert assert_no_metadata(display, "WEBP").size == (1600, 1067)

    @pytest.mark.asyncio
    async def test_upload_named_like_a_variant_is_renamed_before_storing(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """An uploaded filename ending in `.thumb.webp` or `.display.webp` is renamed to
        `-thumb.webp` / `-display.webp` before the object key is formed.

        An uploaded original can never end in a generated-variant suffix. The sweep
        (sweep_orphaned_submission_media) and validation (verify_media_key_not_variant)
        both key off that suffix to tell the upload of a photo apart from a variant derived from it.
        """
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        photo = plain_image((300, 200), "WEBP")
        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.thumb.webp", BytesIO(photo), "image/webp")},
        )
        assert response.status_code == 201, response.text
        object_key = response.json()["object_key"]

        assert object_key.endswith("-thumb.webp")
        assert not object_key.endswith(".thumb.webp")
        assert storage_bucket.get_object(settings.storage.bucket, object_key).read()

        # Still gets its own two variants, appended after the renamed key.
        thumb = storage_bucket.get_object(settings.storage.bucket, f"{object_key}.thumb.webp").read()
        assert assert_no_metadata(thumb, "WEBP")
        display = storage_bucket.get_object(settings.storage.bucket, f"{object_key}.display.webp").read()
        assert assert_no_metadata(display, "WEBP")

    @pytest.mark.asyncio
    async def test_heic_pass_through_stays_byte_for_byte_without_variants(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """A HEIC photo container is stored as uploaded, and no variant objects appear next to it.

        The container is undecodable here and inert by nature. Every other
        undecodable image claim is refused (see test_upload_active_content.py).
        """
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        fake = b"fake heic photo"
        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.heic", BytesIO(fake), "image/heic")},
        )
        assert response.status_code == 201, response.text
        object_key = response.json()["object_key"]

        stored = storage_bucket.get_object(settings.storage.bucket, object_key).read()
        assert stored == fake

        keys = sorted(
            obj.object_name
            for obj in storage_bucket.list_objects(settings.storage.bucket, prefix=object_key, recursive=True)
        )
        assert keys == [object_key]

    @pytest.mark.asyncio
    async def test_oversized_image_is_rejected_with_413_and_stores_nothing(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio, monkeypatch: pytest.MonkeyPatch
    ):
        """A photo whose pixel dimensions exceed the decode cap is rejected with 413 IMAGE_TOO_LARGE.

        No object is written for it. The cap is patched low so a small real PNG trips it.
        """
        monkeypatch.setattr(image_variants, "MAX_DECODE_PIXELS", 1_000)
        headers, org_slug, run_id, team_id = await setup_run_with_team(client, db_session)
        org = await _org_for_slug(db_session, org_slug)

        photo = plain_image((50, 50), "PNG")
        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.png", BytesIO(photo), "image/png")},
        )

        assert response.status_code == 413, response.text
        assert response.json()["error_code"] == "IMAGE_TOO_LARGE"

        prefix = f"{org.id}/{run_id}/{team_id}/"
        stored = list(storage_bucket.list_objects(settings.storage.bucket, prefix=prefix, recursive=True))
        assert stored == []


class TestParticipantVideoAndAudioHygiene:
    """The participant upload route stores video and audio without container
    metadata, and stores nothing when it cannot read the container."""

    @pytest.mark.asyncio
    async def test_video_upload_is_stored_without_place_and_device(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        """A phone video with a place, a model, a title, XMP and a metadata
        track is stored without them. The picture bytes and their offset stay."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        video = media_samples.mp4_with_metadata()
        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("clip.mov", BytesIO(video), "video/quicktime")},
        )
        assert response.status_code == 201, response.text

        stored = storage_bucket.get_object(settings.storage.bucket, response.json()["object_key"]).read()
        for marker in media_samples.ALL_MARKERS:
            assert marker not in stored, f"{marker!r} was stored"
        assert len(stored) == len(video) == response.json()["size"]
        offset = media_samples.mp4_video_sample_offset(stored)
        assert stored[offset : offset + len(media_samples.VIDEO_SAMPLE)] == media_samples.VIDEO_SAMPLE

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filename,content_type,content,sound",
        [
            (
                "voice.ogg",
                "audio/ogg",
                media_samples.ogg_opus((b"LOCATION=" + media_samples.GPS_MARKER,)),
                media_samples.AUDIO_SAMPLE,
            ),
            ("voice.mp3", "audio/mpeg", media_samples.mp3_with_tags(), media_samples.MP3_FRAMES),
            ("voice.wav", "audio/wav", media_samples.wav_with_tags(), media_samples.AUDIO_SAMPLE),
            ("voice.flac", "audio/flac", media_samples.flac_with_tags(), media_samples.AUDIO_SAMPLE),
            ("clip.webm", "video/webm", media_samples.webm_with_metadata(), media_samples.VIDEO_SAMPLE),
        ],
        ids=["ogg", "mp3", "wav", "flac", "webm"],
    )
    async def test_other_containers_are_stored_without_tags(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        filename: str,
        content_type: str,
        content: bytes,
        sound: bytes,
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": (filename, BytesIO(content), content_type)},
        )
        assert response.status_code == 201, response.text

        stored = storage_bucket.get_object(settings.storage.bucket, response.json()["object_key"]).read()
        for marker in media_samples.ALL_MARKERS:
            assert marker not in stored, f"{marker!r} was stored"
        assert sound in stored
        assert response.json()["size"] == len(stored)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content_type", ["video/mp4", "audio/mpeg", "video/x-msvideo"])
    async def test_an_unreadable_recording_is_refused_and_nothing_is_stored(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio, content_type: str
    ):
        """Bytes that are no known container get 422 MEDIA_NOT_READABLE. A file
        the cleaner did not read must not reach the store with its metadata."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("clip.bin", BytesIO(b"no container " + media_samples.GPS_MARKER), content_type)},
        )

        assert response.status_code == 422, response.text
        assert response.json()["error_code"] == "MEDIA_NOT_READABLE"
        org = await _org_for_slug(db_session, org_slug)
        stored = storage_bucket.list_objects(settings.storage.bucket, prefix=f"{org.id}/{run_id}/", recursive=True)
        assert list(stored) == []


class TestDeclaredContentTypeIsNormalized:
    """The answer route lower-cases and trims the declared type once, before
    the allowlist, before the choice of the handling and before storage.
    A parameter of the type stays."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filename,declared,make_content,normalized",
        [
            ("pic.png", "IMAGE/PNG", lambda: plain_image((8, 8), "PNG"), "image/png"),
            ("pic.png", "Image/Png; Charset=Binary", lambda: plain_image((8, 8), "PNG"), "image/png; charset=binary"),
            ("pic.png", " \tIMAGE/PNG\t ", lambda: plain_image((8, 8), "PNG"), "image/png"),
            ("clip.mp4", "VIDEO/MP4", media_samples.tiny_mp4, "video/mp4"),
            ("voice.wav", "Audio/WAV", media_samples.wav_with_tags, "audio/wav"),
            # Outside the media families the list of allowed types holds whole types.
            ("notes.pdf", "APPLICATION/PDF", lambda: b"%PDF-1.4 notes", "application/pdf"),
            ("notes.txt", "Text/Plain", lambda: b"notes", "text/plain"),
        ],
        ids=["image", "image_with_parameter", "image_with_blanks", "video", "audio", "pdf", "text"],
    )
    async def test_the_stored_type_is_the_normalized_one(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        filename: str,
        declared: str,
        make_content,
        normalized: str,
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": (filename, BytesIO(make_content()), declared)},
        )

        assert response.status_code == 201, response.text
        assert response.json()["content_type"] == normalized
        stat = storage_bucket.stat_object(settings.storage.bucket, response.json()["object_key"])
        assert stat.content_type == normalized

    @pytest.mark.asyncio
    async def test_an_image_declared_in_capitals_is_cleaned_and_gets_its_variants(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.jpg", BytesIO(jpeg_with_gps((600, 400))), "IMAGE/JPEG")},
        )

        assert response.status_code == 201, response.text
        object_key = response.json()["object_key"]
        stored = storage_bucket.get_object(settings.storage.bucket, object_key).read()
        assert_no_metadata(stored, "JPEG")
        thumb = storage_bucket.get_object(settings.storage.bucket, f"{object_key}.thumb.webp").read()
        assert assert_no_metadata(thumb, "WEBP").size == (400, 267)

    @pytest.mark.asyncio
    async def test_a_video_declared_in_capitals_is_stored_without_place_and_device(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("clip.mov", BytesIO(media_samples.mp4_with_metadata()), "VIDEO/QuickTime")},
        )

        assert response.status_code == 201, response.text
        stored = storage_bucket.get_object(settings.storage.bucket, response.json()["object_key"]).read()
        for marker in media_samples.ALL_MARKERS:
            assert marker not in stored, f"{marker!r} was stored"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("declared", ["VIDEO/MP4", "Audio/MPEG"])
    async def test_an_unreadable_recording_declared_in_capitals_is_refused(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio, declared: str
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("clip.bin", BytesIO(b"no container " + media_samples.GPS_MARKER), declared)},
        )

        assert response.status_code == 422, response.text
        assert response.json()["error_code"] == "MEDIA_NOT_READABLE"
        org = await _org_for_slug(db_session, org_slug)
        stored = storage_bucket.list_objects(settings.storage.bucket, prefix=f"{org.id}/{run_id}/", recursive=True)
        assert list(stored) == []

    @pytest.mark.asyncio
    async def test_a_video_declared_in_capitals_gets_the_raised_size_cap(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        oversized_for_base = media_samples.tiny_mp4(video_payload=b"x" * (MAX_FILE_SIZE + 1024 * 1024))
        assert MAX_FILE_SIZE < len(oversized_for_base) < MAX_VIDEO_FILE_SIZE

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("clip.mp4", BytesIO(oversized_for_base), "VIDEO/MP4")},
        )

        assert response.status_code == 201, response.text
        assert response.json()["size"] == len(oversized_for_base)

    @pytest.mark.asyncio
    async def test_a_type_outside_the_list_is_still_refused_in_capitals(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("malware.exe", BytesIO(b"MZ executable content"), "APPLICATION/X-MSDOWNLOAD")},
        )

        assert response.status_code == 422, response.text
        assert "application/x-msdownload" in response.json()["detail"]


@pytest_asyncio.fixture(scope="function")
async def answering_client(client: AsyncClient) -> AsyncClient:
    """The app as a browser gets its answers.

    The ``client`` fixture hands an exception that no handler of the app takes
    to the test. This one answers it as the server does, with 500. It needs
    ``client`` for the overrides of the test database.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore
    async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as answering:
        yield answering


class TestAFaultOfTheServiceIsAServerError:
    """A ``ValueError`` of the service's own code is no fault of the upload.

    The route must not turn a ``ValueError`` below its size check into 422, or into
    413 when the message holds the word "size". The checks of ``storage.upload_file``,
    which the route does not call, are the only ones that raise it for a bad upload.
    A mistake of the service would otherwise look like a bad upload.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message",
        ["Pillow did not report a source format", "the size of the canvas was not computed"],
        ids=["a_message_about_the_source_format", "a_message_with_the_word_size"],
    )
    async def test_a_value_error_out_of_the_image_cleaning_answers_500(
        self,
        client: AsyncClient,
        answering_client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        monkeypatch: pytest.MonkeyPatch,
        message: str,
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)
        org = await _org_for_slug(db_session, org_slug)

        def broken(*args, **kwargs):
            raise ValueError(message)

        monkeypatch.setattr(image_variants, "_encode_original", broken)

        response = await answering_client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("photo.png", BytesIO(plain_image((8, 8), "PNG")), "image/png")},
        )

        assert response.status_code == 500, response.text
        stored = storage_bucket.list_objects(settings.storage.bucket, prefix=f"{org.id}/{run_id}/", recursive=True)
        assert list(stored) == []

    @pytest.mark.asyncio
    async def test_the_exception_out_of_the_image_cleaning_is_the_one_the_service_raised(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio, monkeypatch: pytest.MonkeyPatch
    ):
        """It passes through the route as it is, so the log holds the fault and its place."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        def broken(*args, **kwargs):
            raise ValueError("Pillow did not report a source format")

        monkeypatch.setattr(image_variants, "_encode_original", broken)

        with pytest.raises(ValueError, match="Pillow did not report a source format"):
            await client.post(
                f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
                headers=headers,
                files={"file": ("photo.png", BytesIO(plain_image((8, 8), "PNG")), "image/png")},
            )

    @pytest.mark.asyncio
    async def test_a_value_error_out_of_the_video_cleaning_answers_500(
        self,
        client: AsyncClient,
        answering_client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The cleaner names an unreadable file with its own class. A ``ValueError`` is its own mistake."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)
        org = await _org_for_slug(db_session, org_slug)

        def broken(data: bytes) -> bytearray:
            raise ValueError("the size table was not computed")

        monkeypatch.setattr(files_router, "strip_media_metadata", broken)

        response = await answering_client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("clip.mp4", BytesIO(media_samples.tiny_mp4()), "video/mp4")},
        )

        assert response.status_code == 500, response.text
        stored = storage_bucket.list_objects(settings.storage.bucket, prefix=f"{org.id}/{run_id}/", recursive=True)
        assert list(stored) == []

    @pytest.mark.asyncio
    async def test_a_value_error_of_the_storage_answers_500(
        self,
        client: AsyncClient,
        answering_client: AsyncClient,
        db_session: AsyncSession,
        storage_bucket: Minio,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The storage client raises ``ValueError`` for an argument the route built wrongly."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        def broken(*args, **kwargs):
            raise ValueError("the part size is not valid")

        monkeypatch.setattr(files_router, "get_presigned_url", broken)

        response = await answering_client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("notes.txt", BytesIO(b"notes"), "text/plain")},
        )

        assert response.status_code == 500, response.text

    @pytest.mark.asyncio
    async def test_a_file_above_the_size_limit_still_answers_413(
        self, answering_client: AsyncClient, client: AsyncClient, db_session: AsyncSession
    ):
        """The 413 comes from the size check of the route, by its place, not by a word in a message."""
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        response = await answering_client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": ("big.txt", BytesIO(b"x" * (MAX_FILE_SIZE + 1)), "text/plain")},
        )

        assert response.status_code == 413, response.text
        assert "exceeds maximum allowed size" in response.json()["detail"]


class TestFileNameInAnotherScript:
    """A photo named in Cyrillic, Arabic or CJK letters is stored and served."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name,encoded",
        [
            pytest.param("фото.jpg", "%D1%84%D0%BE%D1%82%D0%BE.jpg", id="cyrillic"),
            pytest.param("صورة.jpg", "%D8%B5%D9%88%D8%B1%D8%A9.jpg", id="arabic"),
            pytest.param("写真.jpg", "%E5%86%99%E7%9C%9F.jpg", id="cjk"),
        ],
    )
    async def test_upload_then_serve(
        self, client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio, name: str, encoded: str
    ):
        headers, org_slug, run_id, _ = await setup_run_with_team(client, db_session)

        upload_response = await client.post(
            f"/api/orgs/{org_slug}/runs/{run_id}/submissions/upload/",
            headers=headers,
            files={"file": (name, BytesIO(jpeg_with_gps((300, 200))), "image/jpeg")},
        )

        assert upload_response.status_code == 201, upload_response.text
        object_key = upload_response.json()["object_key"]
        # The letters of the name are kept in the key, so the name reaches the header.
        assert object_key.endswith(f"_{name}")
        stored = storage_bucket.get_object(settings.storage.bucket, object_key).read()

        for variant, served_type in ((None, "image/jpeg"), ("thumb", "image/webp")):
            serve_response = await client.get(
                f"/api/files/{object_key}", headers=headers, params={"variant": variant} if variant else None
            )

            assert serve_response.status_code == 200, serve_response.text
            assert serve_response.headers["content-type"] == served_type
            disposition = serve_response.headers["content-disposition"]
            assert disposition.startswith('inline; filename="')
            assert disposition.isascii()
            assert f"filename*=UTF-8''{encoded}" in disposition
            if variant is None:
                assert serve_response.content == stored
