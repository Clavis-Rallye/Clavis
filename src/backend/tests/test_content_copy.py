"""
Copies of stations and tasks, and the shared library made visible.

A copy is complete and independent: every language, location, tips, scoring
and reference answer, and a station image under its own key. "Als Kopie
bearbeiten" in the rallye editor gives one rallye its own copy and leaves the
original in every other rallye. The composition reports how many rallyes use
an item, which the editor's sharing chip shows.
"""

import io

import pytest
from httpx import AsyncClient
from minio.error import S3Error
from PIL import Image
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import create_access_token
from app.config import settings
from app.models.challenge import ChallengeType
from app.models.station import Station
from app.models.user import UserRole
from app.storage import ensure_bucket, get_minio_client
from tests.factories import (
    create_test_challenge,
    create_test_organization,
    create_test_rallye,
    create_test_station,
    create_test_user,
    link_challenge_to_rallye_station,
    link_station_to_rallye,
)


def _real_png() -> bytes:
    """A small decodable PNG: the upload routes re-encode images and refuse bytes that only look like one."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _real_png()


@pytest.fixture(autouse=True)
def _ensure_bucket() -> None:
    ensure_bucket()


def _key(url: str) -> str:
    """The object key of a same-origin title image URL (``/api/files/{key}``)."""
    assert url.startswith("/api/files/"), url
    return url[len("/api/files/") :]


def _exists(key: str) -> bool:
    try:
        get_minio_client().stat_object(settings.storage.bucket, key)
        return True
    except S3Error:
        return False


async def _creator(db: AsyncSession, slug: str):
    org = await create_test_organization(db, name=f"Org {slug}", slug=slug, languages=["de", "en"])
    creator = await create_test_user(db, email=f"c-{slug}@example.com", role=UserRole.CREATOR, organization_id=org.id)
    token = create_access_token(user_id=creator.id, role=creator.role.value, organization_id=org.id)
    return org, creator, {"access_token": token}


@pytest.mark.asyncio
async def test_a_station_copy_is_complete_and_has_its_own_image(client: AsyncClient, db_session: AsyncSession):
    org, creator, cookies = await _creator(db_session, "copy-station")
    station = await create_test_station(
        db_session,
        organization=org,
        title={"de": "Rathaus", "en": "City Hall"},
        description={"de": "<p>Text</p>", "en": "<p>Text</p>"},
        address="Rathausplatz 2",
        created_by_user_id=creator.id,
    )
    tips = {"header": {"de": "Tipps"}, "links": [{"label": {"de": "Web"}, "url": "https://example.com"}]}
    await db_session.execute(update(Station).where(Station.id == station.id).values(tips=tips))
    await db_session.commit()
    image = await client.put(
        f"/api/orgs/{org.slug}/stations/{station.id}/title-image",
        files={"file": ("cover.png", PNG, "image/png")},
        cookies=cookies,
    )
    assert image.status_code == 200, image.text
    original_key = _key(image.json()["title_image_url"])

    response = await client.post(f"/api/orgs/{org.slug}/stations/{station.id}/duplicate", cookies=cookies)
    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy["id"] != str(station.id)
    assert copy["title"] == {"de": "Rathaus (Kopie)", "en": "City Hall (Copy)"}
    assert copy["description"] == {"de": "<p>Text</p>", "en": "<p>Text</p>"}
    assert copy["address"] == "Rathausplatz 2"
    assert copy["tips"]["header"] == {"de": "Tipps"}
    assert copy["tips"]["links"][0]["url"] == "https://example.com"
    assert copy["usage_count"] == 0

    copy_key = _key(copy["title_image_url"])
    assert copy_key != original_key
    assert copy_key.startswith(f"{org.id}/stations/{copy['id']}/")
    assert _exists(copy_key) and _exists(original_key)

    # Removing the copy's image leaves the original's object alone.
    await client.delete(f"/api/orgs/{org.slug}/stations/{copy['id']}/title-image", cookies=cookies)
    assert _exists(original_key)


@pytest.mark.asyncio
async def test_a_task_copy_keeps_type_scoring_and_reference(client: AsyncClient, db_session: AsyncSession):
    org, creator, cookies = await _creator(db_session, "copy-task")
    challenge = await create_test_challenge(
        db_session,
        org,
        challenge_type=ChallengeType.QUIZ,
        title={"de": "Fluss", "en": "River"},
        instructions={"de": "Welcher Fluss?", "en": "Which river?"},
        scoring_rules={"max_points": 5, "options": ["Lech", "Wertach"], "correct_option": 0, "auto_approve": True},
        time_estimate_minutes=4,
        suggested_solution="Lech",
        created_by_user_id=creator.id,
    )
    response = await client.post(f"/api/orgs/{org.slug}/challenges/{challenge.id}/duplicate", cookies=cookies)
    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy["title"] == {"de": "Fluss (Kopie)", "en": "River (Copy)"}
    assert copy["challenge_type"] == "quiz"
    assert copy["instructions"] == {"de": "Welcher Fluss?", "en": "Which river?"}
    assert copy["scoring_rules"] == challenge.scoring_rules
    assert copy["time_estimate_minutes"] == 4
    assert copy["suggested_solution"] == "Lech"


@pytest.mark.asyncio
async def test_edit_as_copy_gives_one_rallye_its_own_items(client: AsyncClient, db_session: AsyncSession):
    """Editing as a copy swaps the station and the task of one rallye and leaves the original in the other."""
    org, creator, cookies = await _creator(db_session, "copy-swap")
    shared_station = await create_test_station(db_session, organization=org, title={"de": "Geteilt"})
    shared_task = await create_test_challenge(db_session, org, title={"de": "Geteilte Aufgabe"})
    first = await create_test_rallye(db_session, org, title={"de": "Erste"})
    second = await create_test_rallye(db_session, org, title={"de": "Zweite"})
    links = {}
    for rallye in (first, second):
        link = await link_station_to_rallye(db_session, org, rallye, shared_station)
        await link_challenge_to_rallye_station(db_session, org, link, shared_task)
        links[rallye.id] = link.id

    composed = (await client.get(f"/api/orgs/{org.slug}/rallyes/{first.id}/stations", cookies=cookies)).json()
    assert composed["items"][0]["station"]["usage_count"] == 2
    tasks_url = f"/api/orgs/{org.slug}/rallyes/{first.id}/stations/{links[first.id]}/challenges"
    assert (await client.get(tasks_url, cookies=cookies)).json()["items"][0]["challenge"]["usage_count"] == 2

    swapped = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{first.id}/stations/{links[first.id]}/copy", cookies=cookies
    )
    assert swapped.status_code == 200, swapped.text
    assert swapped.json()["station"]["title"] == {"de": "Geteilt (Kopie)"}
    assert swapped.json()["station"]["usage_count"] == 1
    assert swapped.json()["challenge_count"] == 1
    other = (await client.get(f"/api/orgs/{org.slug}/rallyes/{second.id}/stations", cookies=cookies)).json()
    assert other["items"][0]["station"]["id"] == str(shared_station.id)
    assert other["items"][0]["station"]["usage_count"] == 1

    task_swap = await client.post(f"{tasks_url}/{shared_task.id}/copy", cookies=cookies)
    assert task_swap.status_code == 200, task_swap.text
    assert task_swap.json()["challenge"]["title"] == {"de": "Geteilte Aufgabe (Kopie)"}
    other_tasks = f"/api/orgs/{org.slug}/rallyes/{second.id}/stations/{links[second.id]}/challenges"
    assert (await client.get(other_tasks, cookies=cookies)).json()["items"][0]["challenge"]["id"] == str(shared_task.id)
