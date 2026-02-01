"""
Integration tests for submission content validation by challenge type.

Required content per type:
- TEXT: 'text' field (max 5000 chars)
- PHOTO: 'photo_url' field (must exist in the S3 store)
- QUIZ: 'selected_option' field
- CREATIVE: at least one of 'text', 'photo_url', or 'audio_url'
- VIDEO: 'video_url' field (must exist in the S3 store and be video/* MIME)
- AUDIO: 'audio_url' field (must exist in the S3 store and be audio/* MIME)

These tests verify the content validation logic in submissions.py.
Photo/video tests reach the S3 store through the Garage container that
`make test` runs on 127.0.0.1:59000.
"""

from io import BytesIO
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import AsyncClient
from minio import Minio
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.challenge import ChallengeType
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.user import UserRole
from app.repositories.challenge import ChallengeRepository
from app.repositories.rallye import RallyeRepository
from app.repositories.station import StationRepository
from app.storage import ensure_bucket, get_minio_client
from tests import media_samples
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers
from tests.test_image_variants import jpeg_with_gps


@pytest_asyncio.fixture(scope="function")
async def storage_bucket() -> Minio:
    """Ensure the S3 bucket exists for each test and clean up after."""
    ensure_bucket()
    client = get_minio_client()
    yield client

    try:
        objects = client.list_objects(settings.storage.bucket, recursive=True)
        for obj in objects:
            client.remove_object(settings.storage.bucket, obj.object_name)
    except S3Error:
        pass  # Cleanup is best-effort


async def _setup_run_with_challenge(
    client: AsyncClient,
    db_session: AsyncSession,
    challenge_type: ChallengeType,
    scoring_rules: dict | None = None,
) -> tuple[dict, str, str, str]:
    """Set up a run with a single challenge of the given type.

    Returns the headers, the org slug, the run id and the run challenge id.
    """
    user, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)

    rallye_repo = RallyeRepository(db_session, org.id)
    rallye = await rallye_repo.create(title={"de": "Test Rallye"}, description={"de": "Test"}, is_published=True)

    station_repo = StationRepository(db_session, org.id)
    station = await station_repo.create(
        title={"de": "Station 1"},
        description={"de": "Desc"},
    )
    rs_station = RallyeStation(organization_id=org.id, rallye_id=rallye.id, station_id=station.id, sort_order=0)
    db_session.add(rs_station)
    await db_session.flush()

    challenge_repo = ChallengeRepository(db_session, org.id)
    _challenge_1 = await challenge_repo.create(
        challenge_type=challenge_type,
        title={"de": f"{challenge_type.value} Challenge"},
        instructions={"de": "Instructions"},
        scoring_rules=scoring_rules or {"max_points": 10},
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
    run_id = run_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/start", headers=headers)

    stations_resp = await client.get(f"/api/orgs/{org.slug}/runs/{run_id}/stations/", headers=headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{org.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    team_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/teams/", headers=headers, json={"name": "Team Alpha"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{org.slug}/runs/{run_id}/teams/{team_id}/join", headers=headers)

    return headers, org.slug, run_id, run_challenge_id


# ============================================================================
# TEXT Challenge Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_text_submission_with_valid_text(client: AsyncClient, db_session: AsyncSession):
    """TEXT submission with 'text' field should succeed."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.TEXT)

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "My answer"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_text_submission_without_text_field(client: AsyncClient, db_session: AsyncSession):
    """TEXT submission without 'text' field should return 422."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.TEXT)

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"answer": "wrong field"}},
    )

    assert response.status_code == 422
    assert "text" in response.json()["detail"].lower()


# ============================================================================
# PHOTO Challenge Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_photo_submission_with_valid_photo_url(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """PHOTO submission with existing photo_url should succeed."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.PHOTO)

    file_content = jpeg_with_gps((40, 40))
    files = {"file": ("test.jpg", BytesIO(file_content), "image/jpeg")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    object_key = upload_response.json()["object_key"]

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"photo_url": object_key}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_photo_submission_without_photo_url_field(client: AsyncClient, db_session: AsyncSession):
    """PHOTO submission without 'photo_url' field should return 422."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.PHOTO)

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"image": "wrong-field.jpg"}},
    )

    assert response.status_code == 422
    assert "photo_url" in response.json()["detail"].lower()


# ============================================================================
# QUIZ Challenge Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_quiz_submission_with_selected_option(client: AsyncClient, db_session: AsyncSession):
    """QUIZ submission with 'selected_option' field should succeed."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(
        client, db_session, ChallengeType.QUIZ, scoring_rules={"max_points": 10, "correct_option": "A"}
    )

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"selected_option": "A"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_quiz_submission_without_selected_option(client: AsyncClient, db_session: AsyncSession):
    """QUIZ submission without 'selected_option' field should return 422."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(
        client, db_session, ChallengeType.QUIZ, scoring_rules={"max_points": 10, "correct_option": "A"}
    )

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"answer": "A"}},
    )

    assert response.status_code == 422
    assert "selected_option" in response.json()["detail"].lower()


# ============================================================================
# CREATIVE Challenge Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_creative_submission_with_text(client: AsyncClient, db_session: AsyncSession):
    """CREATIVE submission with 'text' field should succeed."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(
        client, db_session, ChallengeType.CREATIVE
    )

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "My creative story"}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_creative_submission_without_content(client: AsyncClient, db_session: AsyncSession):
    """CREATIVE submission without any valid content field should return 422."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(
        client, db_session, ChallengeType.CREATIVE
    )

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"irrelevant": "data"}},
    )

    assert response.status_code == 422
    assert "creative" in response.json()["detail"].lower()


# ============================================================================
# VIDEO Challenge Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_video_submission_with_valid_video_url(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """VIDEO submission with an existing video/* object should succeed."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.VIDEO)

    file_content = media_samples.tiny_mp4()
    files = {"file": ("test.mp4", BytesIO(file_content), "video/mp4")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    object_key = upload_response.json()["object_key"]

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"video_url": object_key}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_video_submission_without_video_url_field(client: AsyncClient, db_session: AsyncSession):
    """VIDEO submission without 'video_url' field should return 422."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.VIDEO)

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"clip": "wrong-field.mp4"}},
    )

    assert response.status_code == 422
    assert "video_url" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_video_submission_with_image_mime_object_rejected(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """VIDEO submission whose video_url points at a non-video/* object should return 422
    (the per-type MIME cross-check — a photo submitted under the video field must not pass)."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.VIDEO)

    # Upload an image, not a video
    file_content = jpeg_with_gps((40, 40))
    files = {"file": ("test.jpg", BytesIO(file_content), "image/jpeg")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    object_key = upload_response.json()["object_key"]

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"video_url": object_key}},
    )

    assert response.status_code == 422
    assert "video" in response.json()["detail"].lower()


# ============================================================================
# AUDIO Challenge Validation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_audio_submission_with_valid_audio_url(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """AUDIO submission with an existing audio/* object should succeed."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.AUDIO)

    file_content = media_samples.webm_with_metadata()
    files = {"file": ("voice.webm", BytesIO(file_content), "audio/webm")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    object_key = upload_response.json()["object_key"]

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"audio_url": object_key}},
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_audio_submission_without_audio_url_field(client: AsyncClient, db_session: AsyncSession):
    """AUDIO submission without 'audio_url' field should return 422."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.AUDIO)

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"recording": "wrong-field.webm"}},
    )

    assert response.status_code == 422
    assert "audio_url" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_audio_submission_with_image_mime_object_rejected(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """AUDIO submission whose audio_url points at a non-audio/* object should return 422."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.AUDIO)

    file_content = jpeg_with_gps((40, 40))
    files = {"file": ("test.jpg", BytesIO(file_content), "image/jpeg")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    object_key = upload_response.json()["object_key"]

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"audio_url": object_key}},
    )

    assert response.status_code == 422
    assert "audio" in response.json()["detail"].lower()


# ============================================================================
# Media Key Ownership Tests: a media key must sit under the
# submitting team's own upload prefix, {org_id}/{run_id}/{team_id}/, checked
# before the existence check.
# ============================================================================


@pytest.mark.asyncio
async def test_photo_submission_with_key_missing_prefix_rejected(client: AsyncClient, db_session: AsyncSession):
    """A photo_url with no org/run/team upload-prefix shape is rejected before the existence check."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.PHOTO)

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"photo_url": "nonexistent/path/to/photo.jpg"}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"


@pytest.mark.asyncio
async def test_photo_submission_with_station_key_rejected(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """A photo_url naming a station-image key (a known, but not team-owned, key) is rejected."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.PHOTO)

    # Upload once, only to learn this org's id from the returned object key.
    files = {"file": ("own.jpg", BytesIO(jpeg_with_gps((40, 40))), "image/jpeg")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    org_id = upload_response.json()["object_key"].split("/")[0]

    # A station title-image key shape, per app/routers/stations.py — never under a team's own prefix.
    station_key = f"{org_id}/stations/00000000-0000-0000-0000-000000000000/bild.png"

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"photo_url": station_key}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"


@pytest.mark.asyncio
async def test_photo_submission_with_another_teams_key_rejected(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """A photo_url uploaded by one team cannot be submitted as another team's answer."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.PHOTO)

    # Team Alpha (the coordinator, already joined by the setup helper) uploads a file.
    files = {"file": ("alpha.jpg", BytesIO(jpeg_with_gps((40, 40))), "image/jpeg")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    alpha_object_key = upload_response.json()["object_key"]
    org_id = alpha_object_key.split("/")[0]

    # A second user, in the same org and run, joins a different team.
    other_user = await create_test_user(
        db_session, email="beta@test.com", role=UserRole.PARTICIPANT, organization_id=UUID(org_id)
    )
    other_headers = get_auth_headers(other_user)
    team_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/", headers=other_headers, json={"name": "Team Beta"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/join", headers=other_headers)

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=other_headers,
        json={"run_challenge_id": run_challenge_id, "content": {"photo_url": alpha_object_key}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"


# ----------------------------------------------------------------------------
# content is an open dict, not shaped by the challenge type: these check a
# foreign media key riding along in a field the challenge type never looks
# at (a TEXT answer carrying a photo_url), which the type-specific checks
# above cannot catch since they only look at the field their own type
# expects.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_submission_with_key_missing_prefix_rejected(client: AsyncClient, db_session: AsyncSession):
    """A TEXT answer carrying a photo_url with no upload-prefix shape is rejected."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.TEXT)

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer", "photo_url": "nonexistent/path/to/photo.jpg"},
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"


@pytest.mark.asyncio
async def test_text_submission_with_station_key_rejected(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """A TEXT answer carrying a station-image key as photo_url is rejected, not just stored unchecked."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.TEXT)

    # Upload once, only to learn this org's id from the returned object key.
    files = {"file": ("own.jpg", BytesIO(jpeg_with_gps((40, 40))), "image/jpeg")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    org_id = upload_response.json()["object_key"].split("/")[0]
    station_key = f"{org_id}/stations/00000000-0000-0000-0000-000000000000/bild.png"

    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "My answer", "photo_url": station_key}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"


@pytest.mark.asyncio
async def test_text_submission_with_another_teams_key_rejected(
    client: AsyncClient, db_session: AsyncSession, storage_bucket: Minio
):
    """A TEXT answer cannot smuggle another team's uploaded key in via media_urls."""
    headers, slug, run_id, run_challenge_id = await _setup_run_with_challenge(client, db_session, ChallengeType.TEXT)

    # Team Alpha (the coordinator, already joined by the setup helper) uploads a file.
    files = {"file": ("alpha.jpg", BytesIO(jpeg_with_gps((40, 40))), "image/jpeg")}
    upload_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files=files,
    )
    assert upload_response.status_code == 201
    alpha_object_key = upload_response.json()["object_key"]
    org_id = alpha_object_key.split("/")[0]

    # A second user, in the same org and run, joins a different team.
    other_user = await create_test_user(
        db_session, email="beta-text@test.com", role=UserRole.PARTICIPANT, organization_id=UUID(org_id)
    )
    other_headers = get_auth_headers(other_user)
    team_response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/teams/", headers=other_headers, json={"name": "Team Beta"}
    )
    team_id = team_response.json()["id"]
    await client.post(f"/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/join", headers=other_headers)

    # media_urls, not photo_url — a field only submission_media_keys() looks at,
    # never a challenge-type-specific check.
    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=other_headers,
        json={
            "run_challenge_id": run_challenge_id,
            "content": {"text": "My answer", "media_urls": [alpha_object_key]},
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"
