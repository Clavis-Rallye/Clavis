"""A team replaces its unscored answer, and a coordinator re-opens a graded one.

A replace is refused for a scored answer, a run that is not active, another team's answer, and
content of the wrong shape. It refuses a media key the team did not upload, and a generated variant key.
A replaced photo or video answer loses its old media object. The cleanup never deletes a key outside
the team's upload prefix, such as a station image, or one another submission of the run still references.
A re-open is for coordinators. It makes the answer replaceable again, and a replaced quiz answer is
auto-scored again. The submission responses report is_scored, points and max_points.
"""

from datetime import datetime
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.challenge import ChallengeType
from app.models.user import UserRole
from app.repositories.score import ScoreRepository
from app.repositories.submission import SubmissionRepository
from app.storage import ensure_bucket, get_minio_client
from tests import media_samples
from tests.factories import create_test_user_with_org, get_auth_headers
from tests.journey_helpers import (
    JourneyEnv,
    activate_run,
    create_team_and_join,
    setup_journey_environment,
    submit_challenge,
)
from tests.test_auto_scoring import _setup_quiz_run
from tests.test_image_variants import jpeg_with_gps


@pytest.fixture(autouse=True)
def _ensure_bucket() -> None:
    """CI starts a fresh Garage per run, so the bucket may not exist yet."""
    ensure_bucket()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _start_run_with_team(
    client: AsyncClient,
    env: JourneyEnv,
    participant: str = "participant1",
    team_name: str = "Team Alpha",
) -> tuple[str, str, str]:
    """Create and start a run, join a team, and return (run_id, run_challenge_id, team_id)."""
    coordinator_headers = env.auth_headers["coordinator"]

    run_response = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json={"rallye_id": str(env.rallye_id), "name": "Editable Submissions Run"},
    )
    assert run_response.status_code == 201
    run_id: str = run_response.json()["id"]

    await activate_run(client, env.slug, run_id, coordinator_headers)

    # Get the frozen run_challenge_id from the snapshots
    stations_response = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/stations/", headers=coordinator_headers)
    run_station_id = stations_response.json()["items"][0]["id"]
    challenges_response = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=coordinator_headers
    )
    run_challenge_id: str = challenges_response.json()["items"][0]["id"]

    team = await create_team_and_join(client, env.slug, run_id, env.auth_headers[participant], team_name, "#FF0000")
    return run_id, run_challenge_id, team["id"]


async def _text_env(client: AsyncClient, db_session: AsyncSession, org_slug: str) -> JourneyEnv:
    return await setup_journey_environment(
        db_session,
        client,
        org_slug=org_slug,
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.TEXT,
    )


def _replace_url(slug: str, run_id: str, submission_id: str) -> str:
    return f"/api/orgs/{slug}/runs/{run_id}/submissions/{submission_id}"


async def _score_submission(
    client: AsyncClient,
    slug: str,
    run_id: str,
    headers: dict[str, str],
    submission_id: str,
    points: int,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/scores/",
        headers=headers,
        json={"submission_id": submission_id, "points": points},
    )
    assert response.status_code == 201
    return dict(response.json())


async def _upload_media(
    client: AsyncClient,
    slug: str,
    run_id: str,
    headers: dict[str, str],
    filename: str,
    content_type: str,
    data: bytes | None = None,
) -> str:
    """``data`` defaults to a decodable JPEG, because the upload route refuses an image claim it cannot decode."""
    if data is None:
        data = jpeg_with_gps((40, 40))
    response = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files={"file": (filename, BytesIO(data), content_type)},
    )
    assert response.status_code == 201
    return response.json()["object_key"]


def _object_exists(key: str) -> bool:
    try:
        get_minio_client().stat_object(settings.storage.bucket, key)
        return True
    except S3Error:
        return False


async def _create_submission_directly(
    db_session: AsyncSession,
    org_id: UUID,
    run_id: str,
    team_id: str,
    run_challenge_id: str,
    content: dict[str, Any],
) -> str:
    """Insert a submission through the repository, past the create-time media-ownership check.

    The create endpoint refuses a key outside the team's upload prefix. Stored data can still hold one,
    from before the check or from a coordinator-side import, and the replace path must handle it.
    """
    submission_repo = SubmissionRepository(db_session, organization_id=org_id)
    submission = await submission_repo.create(
        run_id=UUID(run_id),
        team_id=UUID(team_id),
        run_challenge_id=UUID(run_challenge_id),
        content=content,
    )
    await db_session.commit()
    return str(submission.id)


def _put_object(key: str, data: bytes, content_type: str) -> None:
    """Store an object under an exact key, such as a station-image key the upload endpoint never forms."""
    get_minio_client().put_object(
        bucket_name=settings.storage.bucket,
        object_name=key,
        data=BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


@pytest.mark.asyncio
async def test_replace_submission_success(client: AsyncClient, db_session: AsyncSession):
    """
    Replacing an unscored submission updates its content and moves submitted_at forward.

    A TEXT answer is never auto-scored, so is_scored stays False. A later GET returns the new content.
    """
    env = await _text_env(client, db_session, "replace-ok-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    original = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "First answer"}
    )
    assert original["is_scored"] is False

    response = await client.put(
        _replace_url(env.slug, run_id, original["id"]),
        headers=participant_headers,
        json={"content": {"text": "Better answer"}},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == original["id"]
    assert data["content"] == {"text": "Better answer"}
    assert data["is_scored"] is False
    assert _parse_dt(data["submitted_at"]) > _parse_dt(original["submitted_at"])

    get_response = await client.get(_replace_url(env.slug, run_id, original["id"]), headers=participant_headers)
    assert get_response.status_code == 200
    assert get_response.json()["content"] == {"text": "Better answer"}
    assert get_response.json()["is_scored"] is False


@pytest.mark.asyncio
async def test_replace_submission_rejected_when_scored(client: AsyncClient, db_session: AsyncSession):
    """
    A scored submission cannot be replaced. The 422 tells the team to ask the coordinator to re-open it.

    The submission list shows the submission as scored.
    """
    env = await _text_env(client, db_session, "replace-scored-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]
    coordinator_headers = env.auth_headers["coordinator"]

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "First answer"}
    )
    await _score_submission(client, env.slug, run_id, coordinator_headers, submission["id"], points=7)

    list_response = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/submissions/", headers=participant_headers)
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["is_scored"] is True

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"text": "Too late"}},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "already been scored" in detail
    assert "re-open" in detail


@pytest.mark.asyncio
async def test_replace_submission_run_not_active(client: AsyncClient, db_session: AsyncSession):
    """Replacing is refused with 422 and RUN_PAUSED while the run is paused."""
    env = await _text_env(client, db_session, "replace-paused-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]
    coordinator_headers = env.auth_headers["coordinator"]

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "First answer"}
    )

    pause_response = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/pause", headers=coordinator_headers)
    assert pause_response.status_code == 200

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"text": "While paused"}},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "paused" in detail.lower()
    assert "active" in detail.lower()
    assert response.json()["error_code"] == "RUN_PAUSED"


@pytest.mark.asyncio
async def test_replace_submission_other_team_forbidden(client: AsyncClient, db_session: AsyncSession):
    """A participant of another team cannot replace the submission (403)."""
    env = await _text_env(client, db_session, "replace-team-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant1_headers = env.auth_headers["participant1"]
    participant2_headers = env.auth_headers["participant2"]

    await create_team_and_join(client, env.slug, run_id, participant2_headers, "Team Beta", "#00FF00")

    submission = await submit_challenge(
        client, env.slug, run_id, participant1_headers, run_challenge_id, {"text": "Team Alpha answer"}
    )

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant2_headers,
        json={"content": {"text": "Hijacked answer"}},
    )

    assert response.status_code == 403
    assert "your own team" in response.json()["detail"]


@pytest.mark.asyncio
async def test_replace_submission_invalid_content(client: AsyncClient, db_session: AsyncSession):
    """The replacement content is validated against the challenge type, and the original content stays."""
    env = await _text_env(client, db_session, "replace-invalid-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "Valid answer"}
    )

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"selected_option": "A"}},  # Wrong shape for a TEXT challenge
    )

    assert response.status_code == 422
    assert "'text' field" in response.json()["detail"]

    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=participant_headers)
    assert get_response.json()["content"] == {"text": "Valid answer"}


@pytest.mark.asyncio
async def test_replace_photo_submission_deletes_old_object(client: AsyncClient, db_session: AsyncSession):
    """Replacing a photo answer deletes the old object once the new content is saved, and keeps the new one."""
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-photo-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    old_key = await _upload_media(client, env.slug, run_id, participant_headers, "old.jpg", "image/jpeg")
    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"photo_url": old_key}
    )
    new_key = await _upload_media(client, env.slug, run_id, participant_headers, "new.jpg", "image/jpeg")
    assert _object_exists(old_key)
    assert _object_exists(new_key)

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"photo_url": new_key}},
    )

    assert response.status_code == 200
    assert response.json()["content"] == {"photo_url": new_key}
    assert not _object_exists(old_key)
    assert _object_exists(new_key)
    get_minio_client().remove_object(settings.storage.bucket, new_key)


@pytest.mark.asyncio
async def test_replace_photo_submission_deletes_old_variants(client: AsyncClient, db_session: AsyncSession):
    """Replacing a photo answer deletes the old photo's WebP variants together with the old object."""
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-photo-variants-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    # A decodable photo, so the upload route writes its thumb and display variants.
    old_key = await _upload_media(
        client,
        env.slug,
        run_id,
        participant_headers,
        "old.jpg",
        "image/jpeg",
        data=jpeg_with_gps((2400, 1600)),
    )
    assert _object_exists(f"{old_key}.thumb.webp")
    assert _object_exists(f"{old_key}.display.webp")
    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"photo_url": old_key}
    )
    new_key = await _upload_media(client, env.slug, run_id, participant_headers, "new.jpg", "image/jpeg")

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"photo_url": new_key}},
    )

    assert response.status_code == 200
    assert response.json()["content"] == {"photo_url": new_key}
    assert not _object_exists(old_key)
    assert not _object_exists(f"{old_key}.thumb.webp")
    assert not _object_exists(f"{old_key}.display.webp")
    assert _object_exists(new_key)
    get_minio_client().remove_object(settings.storage.bucket, new_key)


@pytest.mark.asyncio
async def test_score_after_photo_replaced_uses_current_medium(client: AsyncClient, db_session: AsyncSession):
    """
    Scoring a submission whose photo was replaced scores the current answer.

    The score attaches to the same submission id, and the scoring view shows the replaced photo_url.
    """
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="score-after-replace-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]
    coordinator_headers = env.auth_headers["coordinator"]

    old_key = await _upload_media(client, env.slug, run_id, participant_headers, "old.jpg", "image/jpeg")
    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"photo_url": old_key}
    )
    new_key = await _upload_media(client, env.slug, run_id, participant_headers, "new.jpg", "image/jpeg")

    replace_response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"photo_url": new_key}},
    )
    assert replace_response.status_code == 200
    assert not _object_exists(old_key)
    assert _object_exists(new_key)

    score = await _score_submission(client, env.slug, run_id, coordinator_headers, submission["id"], points=8)
    assert score["submission_id"] == submission["id"]

    # The scoring view shows the current medium, not the original one
    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=coordinator_headers)
    assert get_response.status_code == 200
    data = get_response.json()
    assert data["content"] == {"photo_url": new_key}
    assert data["is_scored"] is True
    assert data["points"] == 8

    get_minio_client().remove_object(settings.storage.bucket, new_key)


@pytest.mark.asyncio
async def test_replace_video_submission_deletes_old_object(client: AsyncClient, db_session: AsyncSession):
    """Replacing a video answer deletes the old object once the new content is saved, and keeps the new one."""
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-video-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.VIDEO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    old_key = await _upload_media(
        client, env.slug, run_id, participant_headers, "old.mp4", "video/mp4", data=media_samples.tiny_mp4()
    )
    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"video_url": old_key}
    )
    new_key = await _upload_media(
        client, env.slug, run_id, participant_headers, "new.mp4", "video/mp4", data=media_samples.tiny_mp4()
    )
    assert _object_exists(old_key)
    assert _object_exists(new_key)

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"video_url": new_key}},
    )

    assert response.status_code == 200
    assert response.json()["content"] == {"video_url": new_key}
    assert not _object_exists(old_key)
    assert _object_exists(new_key)
    get_minio_client().remove_object(settings.storage.bucket, new_key)


@pytest.mark.asyncio
async def test_replace_submission_leaves_station_image_object_in_place(client: AsyncClient, db_session: AsyncSession):
    """
    Replacing an answer that carried a station-image key leaves the station object in place.

    The create endpoint refuses a station-image key. An answer stored before that check can carry any
    known key, so the test seeds it through the repository. The cleanup deletes only keys under the
    team's own upload prefix.
    """
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-station-key-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    # A station title-image object with the key shape app/routers/stations.py writes.
    station_key = f"{env.org.id}/stations/{env.station_ids[0]}/{uuid4().hex[:8]}_bild.png"
    _put_object(station_key, b"clavis-station-image", "image/png")

    submission_id = await _create_submission_directly(
        db_session, env.org.id, run_id, team_id, run_challenge_id, {"photo_url": station_key}
    )
    new_key = await _upload_media(client, env.slug, run_id, participant_headers, "new.jpg", "image/jpeg")
    assert _object_exists(station_key)

    response = await client.put(
        _replace_url(env.slug, run_id, submission_id),
        headers=participant_headers,
        json={"content": {"photo_url": new_key}},
    )

    assert response.status_code == 200
    assert response.json()["content"] == {"photo_url": new_key}
    # Not the team's own upload, so the replace leaves it.
    assert _object_exists(station_key)
    assert _object_exists(new_key)
    get_minio_client().remove_object(settings.storage.bucket, station_key)
    get_minio_client().remove_object(settings.storage.bucket, new_key)


@pytest.mark.asyncio
async def test_replace_submission_keeps_key_referenced_by_other_submission(
    client: AsyncClient, db_session: AsyncSession
):
    """
    A key under the team's own prefix that another submission of the run still references survives the replace.

    The create endpoint refuses an answer naming another team's key, so Team Beta's answer is seeded
    through the repository. Only data stored before that check has this shape.
    """
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-shared-key-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant1_headers = env.auth_headers["participant1"]
    participant2_headers = env.auth_headers["participant2"]
    team_beta = await create_team_and_join(client, env.slug, run_id, participant2_headers, "Team Beta", "#00FF00")

    shared_key = await _upload_media(client, env.slug, run_id, participant1_headers, "shared.jpg", "image/jpeg")
    submission = await submit_challenge(
        client, env.slug, run_id, participant1_headers, run_challenge_id, {"photo_url": shared_key}
    )
    # Team Beta's answer names Team Alpha's object.
    await _create_submission_directly(
        db_session, env.org.id, run_id, team_beta["id"], run_challenge_id, {"photo_url": shared_key}
    )
    new_key = await _upload_media(client, env.slug, run_id, participant1_headers, "new.jpg", "image/jpeg")

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant1_headers,
        json={"content": {"photo_url": new_key}},
    )

    assert response.status_code == 200
    assert response.json()["content"] == {"photo_url": new_key}
    # Team Beta's answer still references it, so it survives.
    assert _object_exists(shared_key)
    assert _object_exists(new_key)
    get_minio_client().remove_object(settings.storage.bucket, shared_key)
    get_minio_client().remove_object(settings.storage.bucket, new_key)


@pytest.mark.asyncio
async def test_replace_submission_keeps_variant_referenced_by_other_submission(
    client: AsyncClient, db_session: AsyncSession
):
    """
    A derived variant key that another submission references directly survives the replace cleanup.

    A stored answer, seeded past the create checks, can name a variant key on its own.
    The old photo and its unreferenced thumb variant are deleted.
    """
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-shared-variant-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant1_headers = env.auth_headers["participant1"]
    participant2_headers = env.auth_headers["participant2"]
    team_beta = await create_team_and_join(client, env.slug, run_id, participant2_headers, "Team Beta", "#00FF00")

    # A decodable photo, so the upload route writes its thumb and display variants.
    old_key = await _upload_media(
        client,
        env.slug,
        run_id,
        participant1_headers,
        "old.jpg",
        "image/jpeg",
        data=jpeg_with_gps((2400, 1600)),
    )
    assert _object_exists(f"{old_key}.thumb.webp")
    assert _object_exists(f"{old_key}.display.webp")
    submission = await submit_challenge(
        client, env.slug, run_id, participant1_headers, run_challenge_id, {"photo_url": old_key}
    )
    # Team Beta's answer names the display variant directly (data stored before the ownership check).
    await _create_submission_directly(
        db_session, env.org.id, run_id, team_beta["id"], run_challenge_id, {"photo_url": f"{old_key}.display.webp"}
    )
    new_key = await _upload_media(client, env.slug, run_id, participant1_headers, "new.jpg", "image/jpeg")

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant1_headers,
        json={"content": {"photo_url": new_key}},
    )

    assert response.status_code == 200
    assert response.json()["content"] == {"photo_url": new_key}
    assert not _object_exists(old_key)
    assert not _object_exists(f"{old_key}.thumb.webp")
    # Team Beta's answer still references it, so it survives.
    assert _object_exists(f"{old_key}.display.webp")
    assert _object_exists(new_key)
    client_minio = get_minio_client()
    client_minio.remove_object(settings.storage.bucket, f"{old_key}.display.webp")
    client_minio.remove_object(settings.storage.bucket, new_key)


@pytest.mark.asyncio
async def test_create_submission_with_variant_key_rejected(client: AsyncClient, db_session: AsyncSession):
    """
    A submission may not adopt a generated variant of an existing photo as its own answer.

    The create endpoint answers 422 with MEDIA_KEY_IS_VARIANT, and the photo and both variants stay.
    """
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="create-variant-key-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    key = await _upload_media(
        client,
        env.slug,
        run_id,
        participant_headers,
        "foto.jpg",
        "image/jpeg",
        data=jpeg_with_gps((2400, 1600)),
    )
    assert _object_exists(f"{key}.thumb.webp")
    assert _object_exists(f"{key}.display.webp")

    response = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/",
        headers=participant_headers,
        json={"run_challenge_id": str(run_challenge_id), "content": {"photo_url": f"{key}.thumb.webp"}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_IS_VARIANT"
    assert _object_exists(key)
    assert _object_exists(f"{key}.thumb.webp")
    assert _object_exists(f"{key}.display.webp")
    client_minio = get_minio_client()
    for kept in (key, f"{key}.thumb.webp", f"{key}.display.webp"):
        client_minio.remove_object(settings.storage.bucket, kept)


@pytest.mark.asyncio
async def test_replace_submission_with_variant_key_rejected(client: AsyncClient, db_session: AsyncSession):
    """
    Replacing an answer with a generated variant of its own old photo is refused before any cleanup runs.

    The answer is 422 with MEDIA_KEY_IS_VARIANT, the old photo and both variants stay, and the content is unchanged.
    """
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-variant-key-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    old_key = await _upload_media(
        client,
        env.slug,
        run_id,
        participant_headers,
        "old.jpg",
        "image/jpeg",
        data=jpeg_with_gps((2400, 1600)),
    )
    assert _object_exists(f"{old_key}.thumb.webp")
    assert _object_exists(f"{old_key}.display.webp")
    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"photo_url": old_key}
    )

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"photo_url": f"{old_key}.display.webp"}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_IS_VARIANT"

    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=participant_headers)
    assert get_response.json()["content"] == {"photo_url": old_key}
    assert _object_exists(old_key)
    assert _object_exists(f"{old_key}.thumb.webp")
    assert _object_exists(f"{old_key}.display.webp")
    client_minio = get_minio_client()
    for kept in (old_key, f"{old_key}.thumb.webp", f"{old_key}.display.webp"):
        client_minio.remove_object(settings.storage.bucket, kept)


@pytest.mark.asyncio
async def test_upload_named_like_a_variant_is_renamed_and_accepted(client: AsyncClient, db_session: AsyncSession):
    """
    A file named like a variant is renamed on upload, so its key is accepted as an answer.

    The upload route renames a filename ending in ``.thumb.webp`` or ``.display.webp``
    to ``-thumb.webp`` or ``-display.webp`` before the key is formed (see
    test_file_upload.py::test_upload_named_like_a_variant_is_renamed_before_storing).
    """
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="own-upload-variant-shaped-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    key = await _upload_media(client, env.slug, run_id, participant_headers, "foto.thumb.webp", "image/webp")
    assert key.endswith("-thumb.webp")
    assert not key.endswith(".thumb.webp")

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"photo_url": key}
    )

    assert submission["content"] == {"photo_url": key}
    assert _object_exists(key)
    get_minio_client().remove_object(settings.storage.bucket, key)


@pytest.mark.asyncio
async def test_replace_submission_with_key_missing_prefix_rejected(client: AsyncClient, db_session: AsyncSession):
    """A replace is refused with MEDIA_KEY_NOT_OWNED when the new photo_url has no org/run/team upload-prefix shape."""
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-missing-prefix-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    old_key = await _upload_media(client, env.slug, run_id, participant_headers, "old.jpg", "image/jpeg")
    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"photo_url": old_key}
    )

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"photo_url": "nonexistent/path/to/photo.jpg"}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"

    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=participant_headers)
    assert get_response.json()["content"] == {"photo_url": old_key}
    get_minio_client().remove_object(settings.storage.bucket, old_key)


@pytest.mark.asyncio
async def test_replace_submission_with_station_key_rejected(client: AsyncClient, db_session: AsyncSession):
    """A replace is refused with MEDIA_KEY_NOT_OWNED when the new photo_url names a station-image key."""
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-reject-station-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    old_key = await _upload_media(client, env.slug, run_id, participant_headers, "old.jpg", "image/jpeg")
    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"photo_url": old_key}
    )

    station_key = f"{env.org.id}/stations/{env.station_ids[0]}/{uuid4().hex[:8]}_bild.png"
    _put_object(station_key, b"clavis-station-image", "image/png")

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"photo_url": station_key}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"

    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=participant_headers)
    assert get_response.json()["content"] == {"photo_url": old_key}
    get_minio_client().remove_object(settings.storage.bucket, old_key)
    get_minio_client().remove_object(settings.storage.bucket, station_key)


@pytest.mark.asyncio
async def test_replace_submission_with_another_teams_key_rejected(client: AsyncClient, db_session: AsyncSession):
    """A replace is refused with MEDIA_KEY_NOT_OWNED when the new photo_url is a key another team uploaded."""
    env = await setup_journey_environment(
        db_session,
        client,
        org_slug="replace-reject-other-team-org",
        num_stations=1,
        challenges_per_station=1,
        challenge_type_override=ChallengeType.PHOTO,
    )
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant1_headers = env.auth_headers["participant1"]
    participant2_headers = env.auth_headers["participant2"]
    await create_team_and_join(client, env.slug, run_id, participant2_headers, "Team Beta", "#00FF00")

    old_key = await _upload_media(client, env.slug, run_id, participant1_headers, "old.jpg", "image/jpeg")
    submission = await submit_challenge(
        client, env.slug, run_id, participant1_headers, run_challenge_id, {"photo_url": old_key}
    )
    beta_key = await _upload_media(client, env.slug, run_id, participant2_headers, "beta.jpg", "image/jpeg")

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant1_headers,
        json={"content": {"photo_url": beta_key}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"

    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=participant1_headers)
    assert get_response.json()["content"] == {"photo_url": old_key}
    get_minio_client().remove_object(settings.storage.bucket, old_key)
    get_minio_client().remove_object(settings.storage.bucket, beta_key)


# ----------------------------------------------------------------------------
# content is an open dict, not shaped by the challenge type. These tests put a foreign media key
# in a field the challenge type never reads, such as photo_url or media_urls on a TEXT answer.
# A type-specific check misses it, because it reads only the field its own type expects.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_text_submission_with_key_missing_prefix_rejected(client: AsyncClient, db_session: AsyncSession):
    """A TEXT replace carrying a photo_url with no upload-prefix shape is rejected."""
    env = await _text_env(client, db_session, "replace-text-missing-prefix-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "First answer"}
    )

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"text": "Better answer", "photo_url": "nonexistent/path/to/photo.jpg"}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"

    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=participant_headers)
    assert get_response.json()["content"] == {"text": "First answer"}


@pytest.mark.asyncio
async def test_replace_text_submission_with_station_key_rejected(client: AsyncClient, db_session: AsyncSession):
    """A TEXT replace carrying a station-image key as photo_url is rejected."""
    env = await _text_env(client, db_session, "replace-text-station-key-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "First answer"}
    )

    station_key = f"{env.org.id}/stations/{env.station_ids[0]}/{uuid4().hex[:8]}_bild.png"
    _put_object(station_key, b"clavis-station-image", "image/png")

    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"text": "Better answer", "photo_url": station_key}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"

    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=participant_headers)
    assert get_response.json()["content"] == {"text": "First answer"}
    get_minio_client().remove_object(settings.storage.bucket, station_key)


@pytest.mark.asyncio
async def test_replace_text_submission_with_another_teams_key_rejected(client: AsyncClient, db_session: AsyncSession):
    """A TEXT replace cannot smuggle another team's uploaded key in via media_urls."""
    env = await _text_env(client, db_session, "replace-text-other-team-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant1_headers = env.auth_headers["participant1"]
    participant2_headers = env.auth_headers["participant2"]
    await create_team_and_join(client, env.slug, run_id, participant2_headers, "Team Beta", "#00FF00")

    submission = await submit_challenge(
        client, env.slug, run_id, participant1_headers, run_challenge_id, {"text": "First answer"}
    )
    beta_key = await _upload_media(client, env.slug, run_id, participant2_headers, "beta.jpg", "image/jpeg")

    # media_urls, not photo_url: only submission_media_keys() reads this field, no type-specific check.
    response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant1_headers,
        json={"content": {"text": "Better answer", "media_urls": [beta_key]}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "MEDIA_KEY_NOT_OWNED"

    get_response = await client.get(_replace_url(env.slug, run_id, submission["id"]), headers=participant1_headers)
    assert get_response.json()["content"] == {"text": "First answer"}
    get_minio_client().remove_object(settings.storage.bucket, beta_key)


@pytest.mark.asyncio
async def test_quiz_replace_reautoscores_after_reopen(client: AsyncClient, db_session: AsyncSession):
    """
    A wrong quiz answer is auto-scored with 0 points and blocks a replace.
    After the coordinator re-opens the score, the replace succeeds and auto-scoring gives full points.
    """
    user, org = await create_test_user_with_org(
        db_session, email="quizreplace@test.com", role=UserRole.COORDINATOR, org_slug="quiz-replace-org"
    )
    headers = get_auth_headers(user)

    run_id, run_challenge_id, _team_id = await _setup_quiz_run(
        client,
        db_session,
        headers,
        org,
        scoring_rules={"max_points": 10, "auto_approve": True, "correct_option": "A"},
    )

    # A wrong answer, auto-scored with 0 points.
    create_response = await client.post(
        f"/api/orgs/{org.slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": {"selected_option": "B"}},
    )
    assert create_response.status_code == 201
    submission = create_response.json()
    assert submission["is_scored"] is True

    # Replace is blocked while the auto-score exists
    blocked_response = await client.put(
        _replace_url(org.slug, run_id, submission["id"]),
        headers=headers,
        json={"content": {"selected_option": "A"}},
    )
    assert blocked_response.status_code == 422

    # Coordinator re-opens the auto-score
    score_repo = ScoreRepository(db_session, org.id)
    old_score = await score_repo.get_by_submission(UUID(submission["id"]))
    assert old_score is not None
    assert old_score.points == 0
    reopen_response = await client.delete(
        f"/api/orgs/{org.slug}/runs/{run_id}/scores/{old_score.id}",
        headers=headers,
    )
    assert reopen_response.status_code == 204

    # The correct answer replaces it, and auto-scoring runs again.
    replace_response = await client.put(
        _replace_url(org.slug, run_id, submission["id"]),
        headers=headers,
        json={"content": {"selected_option": "A"}},
    )
    assert replace_response.status_code == 200
    data = replace_response.json()
    assert data["content"] == {"selected_option": "A"}
    assert data["is_scored"] is True

    new_score = await score_repo.get_by_submission(UUID(submission["id"]))
    assert new_score is not None
    assert new_score.id != old_score.id
    assert new_score.points == 10
    assert new_score.max_points == 10


@pytest.mark.asyncio
async def test_reopen_score_success(client: AsyncClient, db_session: AsyncSession):
    """Re-opening a graded submission removes its score, lowers the scoreboard total and makes it replaceable again."""
    env = await _text_env(client, db_session, "reopen-ok-org")
    run_id, run_challenge_id, team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]
    coordinator_headers = env.auth_headers["coordinator"]

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "First answer"}
    )
    score = await _score_submission(client, env.slug, run_id, coordinator_headers, submission["id"], points=7)

    # Scoreboard shows the awarded points
    scoreboard = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard", headers=coordinator_headers)
    team_entry = next(e for e in scoreboard.json()["entries"] if e["team_id"] == team_id)
    assert team_entry["total_points"] == 7
    assert team_entry["scored_count"] == 1

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/{score['id']}",
        headers=coordinator_headers,
    )
    assert response.status_code == 204

    # Score is gone from the list
    scores_response = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/scores/", headers=coordinator_headers)
    assert scores_response.json()["total"] == 0
    assert scores_response.json()["items"] == []

    # Scoreboard drops back to zero
    scoreboard = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard", headers=coordinator_headers)
    team_entry = next(e for e in scoreboard.json()["entries"] if e["team_id"] == team_id)
    assert team_entry["total_points"] == 0
    assert team_entry["scored_count"] == 0

    # Submission is replaceable again
    replace_response = await client.put(
        _replace_url(env.slug, run_id, submission["id"]),
        headers=participant_headers,
        json={"content": {"text": "Second chance answer"}},
    )
    assert replace_response.status_code == 200
    assert replace_response.json()["content"] == {"text": "Second chance answer"}
    assert replace_response.json()["is_scored"] is False


@pytest.mark.asyncio
async def test_reopen_score_participant_forbidden(client: AsyncClient, db_session: AsyncSession):
    """A participant cannot re-open a score (403), and the score remains."""
    env = await _text_env(client, db_session, "reopen-guard-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]
    coordinator_headers = env.auth_headers["coordinator"]

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "First answer"}
    )
    score = await _score_submission(client, env.slug, run_id, coordinator_headers, submission["id"], points=5)

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/{score['id']}",
        headers=participant_headers,
    )
    assert response.status_code == 403

    scores_response = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/scores/", headers=coordinator_headers)
    assert scores_response.json()["total"] == 1


@pytest.mark.asyncio
async def test_reopen_score_not_found(client: AsyncClient, db_session: AsyncSession):
    """Re-opening an unknown score gives 404."""
    env = await _text_env(client, db_session, "reopen-404-org")
    run_id, _run_challenge_id, _team_id = await _start_run_with_team(client, env)
    coordinator_headers = env.auth_headers["coordinator"]

    response = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/{uuid4()}",
        headers=coordinator_headers,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_scored_submission_reports_points_to_the_team(client: AsyncClient, db_session: AsyncSession):
    """
    A team must be able to see what it scored, not only that it was scored.

    Participants may not read /scores/ (coordinator+ only), so the submission
    response is the only place the result reaches them. points and max_points
    are null while unscored and filled after scoring, on the list and on the single GET.
    """
    env = await _text_env(client, db_session, "points-visible-org")
    run_id, run_challenge_id, _team_id = await _start_run_with_team(client, env)
    participant_headers = env.auth_headers["participant1"]
    coordinator_headers = env.auth_headers["coordinator"]

    submission = await submit_challenge(
        client, env.slug, run_id, participant_headers, run_challenge_id, {"text": "An answer"}
    )
    assert submission["points"] is None
    assert submission["max_points"] is None

    await _score_submission(client, env.slug, run_id, coordinator_headers, submission["id"], points=7)

    listing = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/submissions/", headers=participant_headers)
    assert listing.status_code == 200
    listed = listing.json()["items"][0]
    assert listed["is_scored"] is True
    assert listed["points"] == 7
    assert listed["max_points"] is not None

    single = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/submissions/{submission['id']}", headers=participant_headers
    )
    assert single.status_code == 200
    assert single.json()["points"] == 7
    assert single.json()["max_points"] == listed["max_points"]
