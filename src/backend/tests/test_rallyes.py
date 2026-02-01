"""Integration tests for the rallye endpoints.

Covers create, list, get, update, soft-delete, duplicate, description sanitising and role enforcement.
"""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.user import UserRole
from tests.factories import create_test_user_with_org, get_auth_headers


@pytest.mark.asyncio
async def test_create_rallye_with_multilingual_content(client: AsyncClient, db_session: AsyncSession):
    """A rallye is created with a multilingual title and description, unpublished by default."""
    user, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=headers,
        json={
            "title": {"de": "Clavis Augsburg", "en": "Clavis Augsburg"},
            "description": {
                "de": "Eine spannende Tour durch die Altstadt",
                "en": "An exciting tour through the old town",
            },
        },
    )

    assert response.status_code == 201
    data = response.json()

    assert data["title"] == {"de": "Clavis Augsburg", "en": "Clavis Augsburg"}
    assert data["description"] == {
        "de": "Eine spannende Tour durch die Altstadt",
        "en": "An exciting tour through the old town",
    }
    assert data["is_published"] is False  # New rallyes start unpublished
    assert "station_order" not in data  # station ordering lives on rallye_stations links
    assert data["organization_id"] == str(org.id)

    assert "id" in data
    UUID(data["id"])


@pytest.mark.asyncio
async def test_create_rallye_requires_creator_role(client: AsyncClient, db_session: AsyncSession):
    """A participant cannot create a rallye."""
    user, org = await create_test_user_with_org(
        db_session,
        email="participant@example.com",
        role=UserRole.PARTICIPANT,
        org_slug="test-org",
    )
    headers = get_auth_headers(user)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=headers,
        json={
            "title": {"de": "Test Rallye"},
            "description": {"de": "Test"},
        },
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "Insufficient permissions" in detail or "Requires creator" in detail


@pytest.mark.asyncio
async def test_list_rallyes_coordinator_sees_only_published(client: AsyncClient, db_session: AsyncSession):
    """A coordinator lists only published rallyes, and a participant may not list rallyes at all."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    published_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Published Rallye"},
            "description": {"de": "This is published"},
        },
    )
    assert published_response.status_code == 201
    published_id = published_response.json()["id"]

    # Publish it. The request must provide all fields to avoid None values.
    await client.put(
        f"/api/orgs/{org.slug}/rallyes/{published_id}",
        headers=creator_headers,
        json={
            "title": {"de": "Published Rallye"},
            "description": {"de": "This is published"},
            "is_published": True,
            "station_order": [],
        },
    )

    unpublished_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Unpublished Rallye"},
            "description": {"de": "This is not published"},
        },
    )
    assert unpublished_response.status_code == 201

    from app.auth.passwords import hash_password
    from app.repositories.user import UserRepository

    repo = UserRepository(db_session, org.id)
    coordinator = await repo.create(
        email="coordinator@example.com",
        display_name="Coordinator",
        password_hash=hash_password("TestPassword123!"),
        role=UserRole.COORDINATOR,
    )
    participant = await repo.create(
        email="participant@example.com",
        display_name="Participant",
        password_hash=hash_password("TestPassword123!"),
        role=UserRole.PARTICIPANT,
    )
    await db_session.commit()
    await db_session.refresh(coordinator)
    await db_session.refresh(participant)

    refused = await client.get(f"/api/orgs/{org.slug}/rallyes/", headers=get_auth_headers(participant))
    assert refused.status_code == 403

    response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=get_auth_headers(coordinator),
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == published_id
    assert data["items"][0]["is_published"] is True


@pytest.mark.asyncio
async def test_list_rallyes_creator_sees_all(client: AsyncClient, db_session: AsyncSession):
    """A creator lists published and unpublished rallyes."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    published_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Published Rallye"},
            "description": {"de": "This is published"},
        },
    )
    assert published_response.status_code == 201
    published_id = published_response.json()["id"]

    # Publish it. The request must provide all fields to avoid None values.
    await client.put(
        f"/api/orgs/{org.slug}/rallyes/{published_id}",
        headers=creator_headers,
        json={
            "title": {"de": "Published Rallye"},
            "description": {"de": "This is published"},
            "is_published": True,
            "station_order": [],
        },
    )

    unpublished_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Unpublished Rallye"},
            "description": {"de": "This is not published"},
        },
    )
    assert unpublished_response.status_code == 201
    unpublished_id = unpublished_response.json()["id"]

    response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert len(data["items"]) == 2

    ids = [item["id"] for item in data["items"]]
    assert published_id in ids
    assert unpublished_id in ids


@pytest.mark.asyncio
async def test_get_rallye_by_id(client: AsyncClient, db_session: AsyncSession):
    """A rallye is returned by ID with its multilingual content."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Test Rallye", "en": "Test Rally"},
            "description": {"de": "Beschreibung", "en": "Description"},
        },
    )
    assert create_response.status_code == 201
    rallye_id = create_response.json()["id"]

    response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
        headers=creator_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == rallye_id
    assert data["title"] == {"de": "Test Rallye", "en": "Test Rally"}
    assert data["description"] == {"de": "Beschreibung", "en": "Description"}
    assert data["is_published"] is False


@pytest.mark.asyncio
async def test_get_nonexistent_rallye_returns_404(client: AsyncClient, db_session: AsyncSession):
    """A non-existent rallye ID returns 404."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{fake_uuid}",
        headers=creator_headers,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_rallye(client: AsyncClient, db_session: AsyncSession):
    """An update changes the multilingual fields and can publish the rallye."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Original Title"},
            "description": {"de": "Original Description"},
        },
    )
    assert create_response.status_code == 201
    rallye_id = create_response.json()["id"]

    response = await client.put(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
        headers=creator_headers,
        json={
            "title": {"de": "Updated Title", "en": "Updated Title EN"},
            "description": {"de": "Updated Description", "en": "Updated Description EN"},
            "is_published": True,
            "station_order": [],
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == rallye_id
    assert data["title"] == {"de": "Updated Title", "en": "Updated Title EN"}
    assert data["description"] == {"de": "Updated Description", "en": "Updated Description EN"}
    assert data["is_published"] is True
    assert "station_order" not in data


@pytest.mark.asyncio
async def test_update_nonexistent_rallye_returns_404(client: AsyncClient, db_session: AsyncSession):
    """Updating a non-existent rallye returns 404."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.put(
        f"/api/orgs/{org.slug}/rallyes/{fake_uuid}",
        headers=creator_headers,
        json={
            "title": {"de": "New Title"},
        },
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_soft_delete_rallye(client: AsyncClient, db_session: AsyncSession):
    """Soft-deleting a rallye returns 204."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "To Be Deleted"},
            "description": {"de": "This will be deleted"},
        },
    )
    assert create_response.status_code == 201
    rallye_id = create_response.json()["id"]

    response = await client.delete(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
        headers=creator_headers,
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_deleted_rallye_not_in_list(client: AsyncClient, db_session: AsyncSession):
    """A deleted rallye is not listed, and the other rallyes stay."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    create_response1 = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Rallye 1"},
            "description": {"de": "First rallye"},
        },
    )
    assert create_response1.status_code == 201
    rallye1_id = create_response1.json()["id"]

    create_response2 = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Rallye 2"},
            "description": {"de": "Second rallye"},
        },
    )
    assert create_response2.status_code == 201
    rallye2_id = create_response2.json()["id"]

    delete_response = await client.delete(
        f"/api/orgs/{org.slug}/rallyes/{rallye1_id}",
        headers=creator_headers,
    )
    assert delete_response.status_code == 204

    response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == rallye2_id


@pytest.mark.asyncio
async def test_get_deleted_rallye_returns_404(client: AsyncClient, db_session: AsyncSession):
    """A deleted rallye cannot be read by ID."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "To Be Deleted"},
            "description": {"de": "This will be deleted"},
        },
    )
    assert create_response.status_code == 201
    rallye_id = create_response.json()["id"]

    delete_response = await client.delete(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
        headers=creator_headers,
    )
    assert delete_response.status_code == 204

    response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
        headers=creator_headers,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_coordinator_cannot_see_unpublished_rallye(client: AsyncClient, db_session: AsyncSession):
    """A coordinator gets 404, not 403, for an unpublished rallye, so its existence does not leak."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    create_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Unpublished"},
            "description": {"de": "Not published"},
        },
    )
    assert create_response.status_code == 201
    rallye_id = create_response.json()["id"]

    from app.auth.passwords import hash_password
    from app.repositories.user import UserRepository

    repo = UserRepository(db_session, org.id)
    coordinator = await repo.create(
        email="coordinator@example.com",
        display_name="Coordinator",
        password_hash=hash_password("TestPassword123!"),
        role=UserRole.COORDINATOR,
    )
    await db_session.commit()
    await db_session.refresh(coordinator)

    response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
        headers=get_auth_headers(coordinator),
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_duplicate_rallye_deep_copies_stations_and_challenges(client: AsyncClient, db_session: AsyncSession):
    """A duplicate links the same library stations by reference, in order, under a new rallye.

    The title gets a locale suffix (' (Kopie)' / ' (Copy)'), the duplicate is unpublished,
    and the source rallye is unchanged.
    """
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(creator)

    rallye_resp = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=headers,
        json={
            "title": {"de": "Original", "en": "Original"},
            "description": {"de": "Beschreibung", "en": "Description"},
        },
    )
    assert rallye_resp.status_code == 201
    source_id = rallye_resp.json()["id"]

    station_ids: list[str] = []
    for idx in range(2):
        st_resp = await client.post(
            f"/api/orgs/{org.slug}/rallyes/{source_id}/stations",
            headers=headers,
            json={
                "title": {"de": f"Station {idx}"},
                "description": {"de": "Desc"},
            },
        )
        assert st_resp.status_code == 201
        station_id = st_resp.json()["station"]["id"]
        rallye_station_id = st_resp.json()["link_id"]
        station_ids.append(station_id)

        ch_resp = await client.post(
            f"/api/orgs/{org.slug}/rallyes/{source_id}/stations/{rallye_station_id}/challenges",
            headers=headers,
            json={
                "challenge_type": ChallengeType.QUIZ.value,
                "title": {"de": f"Challenge {idx}"},
                "instructions": {"de": "Antworte"},
                "scoring_rules": {"max_points": 50, "auto_approve": True, "correct_option": idx},
            },
        )
        assert ch_resp.status_code == 201

    dup_resp = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{source_id}/duplicate",
        headers=headers,
    )
    assert dup_resp.status_code == 201
    dup = dup_resp.json()

    assert dup["id"] != source_id
    UUID(dup["id"])
    assert dup["is_published"] is False
    assert dup["title"] == {"de": "Original (Kopie)", "en": "Original (Copy)"}
    assert dup["description"] == {"de": "Beschreibung", "en": "Description"}

    # Duplicate copies LINKS by reference: the duplicated rallye composes the SAME
    # library stations (same IDs), with fresh rallye_stations rows preserving order.
    new_stations_resp = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{dup['id']}/stations",
        headers=headers,
    )
    assert new_stations_resp.status_code == 200
    new_items = new_stations_resp.json()["items"]
    assert len(new_items) == 2
    new_items.sort(key=lambda s: s["sort_order"])
    for idx, item in enumerate(new_items):
        st = item["station"]
        assert item["sort_order"] == idx
        assert st["title"] == {"de": f"Station {idx}"}
        # Same library station, linked by reference (not deep-copied).
        assert st["id"] == station_ids[idx]

        # The duplicated rallye-station carries the (shared) challenge match.
        ch_list_resp = await client.get(
            f"/api/orgs/{org.slug}/rallyes/{dup['id']}/stations/{item['link_id']}/challenges",
            headers=headers,
        )
        assert ch_list_resp.status_code == 200
        challenges = ch_list_resp.json()["items"]
        assert len(challenges) == 1
        ch = challenges[0]["challenge"]
        assert ch["title"] == {"de": f"Challenge {idx}"}
        assert ch["scoring_rules"] == {"max_points": 50, "auto_approve": True, "correct_option": idx}
        assert ch["challenge_type"] == ChallengeType.QUIZ.value

    source_after = await client.get(f"/api/orgs/{org.slug}/rallyes/{source_id}", headers=headers)
    assert source_after.status_code == 200
    assert source_after.json()["title"] == {"de": "Original", "en": "Original"}
    assert source_after.json()["id"] == source_id


@pytest.mark.asyncio
async def test_duplicate_rallye_requires_creator_role(client: AsyncClient, db_session: AsyncSession):
    """Participants cannot duplicate rallyes."""
    from app.auth.passwords import hash_password
    from app.repositories.user import UserRepository

    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    creator_headers = get_auth_headers(creator)

    create_resp = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={"title": {"de": "X"}, "description": {"de": "Y"}},
    )
    rallye_id = create_resp.json()["id"]

    repo = UserRepository(db_session, org.id)
    participant = await repo.create(
        email="participant@example.com",
        display_name="P",
        password_hash=hash_password("TestPassword123!"),
        role=UserRole.PARTICIPANT,
    )
    await db_session.commit()
    await db_session.refresh(participant)
    participant_headers = get_auth_headers(participant)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/duplicate",
        headers=participant_headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_nonexistent_rallye_returns_404(client: AsyncClient, db_session: AsyncSession):
    """Duplicating a missing rallye returns 404."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@example.com",
        role=UserRole.CREATOR,
        org_slug="test-org",
    )
    headers = get_auth_headers(creator)

    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{fake_id}/duplicate",
        headers=headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rallye_description_is_sanitised(client: AsyncClient, db_session: AsyncSession):
    """A rallye description keeps its formatting and loses everything else."""
    user, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(user)
    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=headers,
        json={
            "title": {"de": "Tour"},
            "description": {"de": "<p><strong>Fett</strong><script>x()</script></p>", "en": "Plain text stays"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["description"] == {"de": "<p><strong>Fett</strong></p>", "en": "Plain text stays"}
