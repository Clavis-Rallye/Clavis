"""
A text answer is always possible as a fallback when photo, video or audio do not work,
and the type "kreativ" is retired.

- A photo, video or audio answer takes a ``text``: with the medium, or instead of it.
- Migration 040 turns the library's creative challenges into photo challenges. Frozen run
  snapshots keep the type, and their answers stay valid.
"""

import importlib.util
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from minio import Minio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import Challenge, ChallengeType
from app.models.run_challenge import RunChallenge
from tests.factories import create_test_challenge, create_test_user_with_org
from tests.test_image_variants import plain_image
from tests.test_submission_validation import _setup_run_with_challenge, storage_bucket  # noqa: F401

# Found by name, so a renumbering at merge time does not break the test.
(MIGRATION,) = (Path(__file__).resolve().parent.parent / "alembic" / "versions").glob(
    "*_retire_creative_challenge_type.py"
)


async def _submit(client: AsyncClient, headers: dict, slug: str, run_id: str, run_challenge_id: str, content: dict):
    return await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=headers,
        json={"run_challenge_id": run_challenge_id, "content": content},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("challenge_type", [ChallengeType.PHOTO, ChallengeType.VIDEO, ChallengeType.AUDIO])
async def test_a_media_task_takes_a_text_instead_of_the_medium(
    client: AsyncClient, db_session: AsyncSession, challenge_type: ChallengeType
):
    headers, slug, run_id, rc_id = await _setup_run_with_challenge(client, db_session, challenge_type)
    response = await _submit(
        client, headers, slug, run_id, rc_id, {"text": "Die Kamera geht nicht. Der Saal ist golden."}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content"] == {"text": "Die Kamera geht nicht. Der Saal ist golden."}
    # A medium answer waits for the Spielleitung, and so does its text.
    assert body["is_scored"] is False


@pytest.mark.asyncio
async def test_a_photo_task_takes_the_photo_and_a_text_together(
    client: AsyncClient,
    db_session: AsyncSession,
    storage_bucket: Minio,  # noqa: F811
):
    headers, slug, run_id, rc_id = await _setup_run_with_challenge(client, db_session, ChallengeType.PHOTO)
    upload = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files={"file": ("saal.jpg", BytesIO(plain_image((8, 8), "JPEG")), "image/jpeg")},
    )
    assert upload.status_code == 201
    key = upload.json()["object_key"]
    response = await _submit(client, headers, slug, run_id, rc_id, {"photo_url": key, "text": "Links der Saal."})
    assert response.status_code == 201, response.text
    assert response.json()["content"] == {"photo_url": key, "text": "Links der Saal."}


@pytest.mark.asyncio
async def test_a_video_with_text_still_checks_the_medium(
    client: AsyncClient,
    db_session: AsyncSession,
    storage_bucket: Minio,  # noqa: F811
):
    """The text is a fallback, not a way around the check of a medium that is present."""
    headers, slug, run_id, rc_id = await _setup_run_with_challenge(client, db_session, ChallengeType.VIDEO)
    upload = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/upload/",
        headers=headers,
        files={"file": ("bild.jpg", BytesIO(plain_image((8, 8), "JPEG")), "image/jpeg")},
    )
    key = upload.json()["object_key"]
    response = await _submit(client, headers, slug, run_id, rc_id, {"video_url": key, "text": "Hallo"})
    assert response.status_code == 422
    assert "video" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "fragment"),
    [
        ({"text": "   "}, "non-empty"),
        ({"text": 42}, "non-empty"),
        ({"text": "x" * 5001}, "5000"),
        ({}, "'text' answer"),
    ],
)
async def test_a_photo_task_refuses_an_empty_or_too_long_text(
    client: AsyncClient, db_session: AsyncSession, content: dict, fragment: str
):
    headers, slug, run_id, rc_id = await _setup_run_with_challenge(client, db_session, ChallengeType.PHOTO)
    response = await _submit(client, headers, slug, run_id, rc_id, content)
    assert response.status_code == 422, response.text
    assert fragment in response.json()["detail"]


def _run_migration_040(sync_session) -> None:
    spec = importlib.util.spec_from_file_location("migration_040", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(sync_session.connection())):
        module.upgrade()


@pytest.mark.asyncio
async def test_migration_040_turns_library_creative_into_photo_and_keeps_snapshots_and_answers(
    client: AsyncClient, db_session: AsyncSession
):
    headers, slug, run_id, rc_id = await _setup_run_with_challenge(client, db_session, ChallengeType.CREATIVE)
    # An answer of a past run on the creative snapshot.
    before = await _submit(client, headers, slug, run_id, rc_id, {"text": "Ein gutes Zuhause hat Platz für alle."})
    assert before.status_code == 201, before.text
    # An archived creative challenge of a second organisation goes too.
    _user, other_org = await create_test_user_with_org(db_session, email="other@test.com", org_slug="other-org")
    archived = await create_test_challenge(db_session, other_org, challenge_type=ChallengeType.CREATIVE)
    archived.is_deleted = True
    await db_session.commit()

    await db_session.run_sync(_run_migration_040)
    await db_session.commit()
    db_session.expire_all()

    library = (await db_session.execute(select(Challenge.challenge_type))).scalars().all()
    assert ChallengeType.CREATIVE not in library
    assert library.count(ChallengeType.PHOTO) == 2

    snapshot = (await db_session.execute(select(RunChallenge).where(RunChallenge.id == UUID(rc_id)))).scalar_one()
    assert snapshot.challenge_type == ChallengeType.CREATIVE

    listing = await client.get(f"/api/orgs/{slug}/runs/{run_id}/submissions/", headers=headers)
    assert [item["content"] for item in listing.json()["items"]] == [{"text": "Ein gutes Zuhause hat Platz für alle."}]
