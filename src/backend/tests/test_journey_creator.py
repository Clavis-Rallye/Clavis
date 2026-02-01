"""
Content authoring journey of a creator.

Stations and challenges live in the org-wide library and are composed into
rallyes and stations by reference. Creating a station under a rallye
(`POST /rallyes/{rid}/stations`) creates a NEW library station and links it.
Creating a challenge under a station (`POST /stations/{sid}/challenges`) creates
a NEW library challenge and links it. Composition responses carry the per-link
`sort_order` plus a nested `station` or `challenge` object.

The scenarios cover:
- the full authoring flow with all challenge types
- editing content and adding a language
- soft delete and the unlinking it causes
- the permission boundaries for participants and coordinators
- read access to published content
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import create_test_user, create_test_user_with_org, get_auth_headers

# =============================================================================
# Full Rallye Authoring Flow
# =============================================================================


@pytest.mark.asyncio
async def test_creator_authors_a_rallye_with_all_challenge_types(client: AsyncClient, db_session: AsyncSession) -> None:
    """A creator builds a rallye with stations and all five challenge types, and the server assigns the sort order."""
    creator, org = await create_test_user_with_org(
        db_session,
        email="creator@authoring.test",
        role=UserRole.CREATOR,
        org_slug="authoring-org",
        org_name="Content Authoring Org",
    )
    creator_headers = get_auth_headers(creator)

    rallye_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Clavis Augsburg", "en": "Clavis Augsburg"},
            "description": {
                "de": "Eine spannende Rallye durch Augsburg",
                "en": "An exciting rally through Augsburg",
            },
        },
    )
    assert rallye_response.status_code == 201
    rallye_data = rallye_response.json()
    rallye_id = rallye_data["id"]
    assert rallye_data["is_published"] is False

    # Add 3 stations (composition append; sort_order assigned by the server).
    stations_data = [
        {
            "title": {"de": "Rathaus", "en": "City Hall"},
            "description": {"de": "Das historische Rathaus", "en": "The historic city hall"},
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
        {
            "title": {"de": "Dom", "en": "Cathedral"},
            "description": {"de": "Der Augsburger Dom", "en": "The Augsburg Cathedral"},
            "location_lat": 48.3713,
            "location_lng": 10.8976,
        },
        {
            "title": {"de": "Fuggerei", "en": "Fuggerei"},
            "description": {"de": "Die älteste Sozialsiedlung", "en": "The oldest social housing"},
            "location_lat": 48.3685,
            "location_lng": 10.9020,
        },
    ]

    station_ids = []
    rallye_station_ids = []
    for idx, station_payload in enumerate(stations_data):
        response = await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
            headers=creator_headers,
            json=station_payload,
        )
        assert response.status_code == 201
        link = response.json()
        assert link["sort_order"] == idx
        station = link["station"]
        station_ids.append(station["id"])
        rallye_station_ids.append(link["link_id"])
        assert station["title"] == station_payload["title"]
        assert station["location_lat"] == station_payload["location_lat"]
        assert station["location_lng"] == station_payload["location_lng"]

    challenges_per_station = [
        [
            {
                "challenge_type": "text",
                "title": {"de": "Baujahr?", "en": "Year?"},
                "instructions": {"de": "Jahr", "en": "Year"},
                "scoring_rules": {"max_points": 10},
            },
            {
                "challenge_type": "photo",
                "title": {"de": "Fassade", "en": "Facade"},
                "instructions": {"de": "Foto", "en": "Photo"},
                "scoring_rules": {"max_points": 20},
            },
        ],
        [
            {
                "challenge_type": "qr_scan",
                "title": {"de": "QR", "en": "QR"},
                "instructions": {"de": "Scan", "en": "Scan"},
                "scoring_rules": {"max_points": 5, "expected_value": "abc", "auto_approve": True},
            },
            {
                "challenge_type": "quiz",
                "title": {"de": "Bischof?", "en": "Bishop?"},
                "instructions": {"de": "Antwort", "en": "Answer"},
                "scoring_rules": {"max_points": 15},
            },
        ],
        [
            {
                "challenge_type": "video",
                "title": {"de": "Video", "en": "Video"},
                "instructions": {"de": "Video", "en": "Video"},
                "scoring_rules": {"max_points": 25},
            },
            {
                "challenge_type": "text",
                "title": {"de": "Geschichte", "en": "History"},
                "instructions": {"de": "Schreiben", "en": "Write"},
                "scoring_rules": {"max_points": 10},
            },
        ],
    ]

    for station_idx, rallye_station_id in enumerate(rallye_station_ids):
        for c_idx, challenge_payload in enumerate(challenges_per_station[station_idx]):
            response = await client.post(
                f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
                headers=creator_headers,
                json=challenge_payload,
            )
            assert response.status_code == 201
            link = response.json()
            assert link["sort_order"] == c_idx
            challenge = link["challenge"]
            assert challenge["challenge_type"] == challenge_payload["challenge_type"]
            assert challenge["title"] == challenge_payload["title"]
            assert challenge["scoring_rules"]["max_points"] == challenge_payload["scoring_rules"]["max_points"]

    rallye_get = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=creator_headers)
    assert rallye_get.status_code == 200

    stations_list = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations", headers=creator_headers)
    assert stations_list.status_code == 200
    stations_items = stations_list.json()["items"]
    assert len(stations_items) == 3
    for idx, item in enumerate(stations_items):
        assert item["sort_order"] == idx

    for rallye_station_id in rallye_station_ids:
        challenges_list = await client.get(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
            headers=creator_headers,
        )
        assert challenges_list.status_code == 200
        items = challenges_list.json()["items"]
        assert len(items) == 2
        for idx, item in enumerate(items):
            assert item["sort_order"] == idx


@pytest.mark.asyncio
async def test_creator_can_publish_rallye(client: AsyncClient, db_session: AsyncSession) -> None:
    """A creator publishes a rallye with PUT is_published=True."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@publish.test", role=UserRole.CREATOR, org_slug="publish-org"
    )
    creator_headers = get_auth_headers(creator)

    rallye_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={"title": {"de": "Test", "en": "Test"}, "description": {"de": "Test", "en": "Test"}},
    )
    assert rallye_response.status_code == 201
    rallye_id = rallye_response.json()["id"]
    assert rallye_response.json()["is_published"] is False

    update_response = await client.put(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
        headers=creator_headers,
        json={"is_published": True},
    )
    assert update_response.status_code == 200
    assert update_response.json()["is_published"] is True

    get_response = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=creator_headers)
    assert get_response.status_code == 200
    assert get_response.json()["is_published"] is True


# =============================================================================
# Content Editing and Multilingual Updates
# =============================================================================


@pytest.mark.asyncio
async def test_creator_edits_content_and_adds_a_language(client: AsyncClient, db_session: AsyncSession) -> None:
    """A creator edits rallye, station and challenge content and adds a language, and GET returns the changes."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@edit.test", role=UserRole.CREATOR, org_slug="edit-org"
    )
    creator_headers = get_auth_headers(creator)

    rallye_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=creator_headers,
        json={
            "title": {"de": "Original Rallye", "en": "Original Rally"},
            "description": {"de": "Original", "en": "Original"},
        },
    )
    assert rallye_response.status_code == 201
    rallye_id = rallye_response.json()["id"]
    original_rallye_updated_at = rallye_response.json()["updated_at"]

    station_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
        headers=creator_headers,
        json={
            "title": {"de": "Station Original", "en": "Station Original"},
            "description": {"de": "Original", "en": "Original"},
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
    )
    assert station_response.status_code == 201
    rallye_station_id = station_response.json()["link_id"]
    station = station_response.json()["station"]
    station_id = station["id"]
    original_station_updated_at = station["updated_at"]

    challenge_response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
        headers=creator_headers,
        json={
            "challenge_type": "text",
            "title": {"de": "Challenge Original", "en": "Challenge Original"},
            "instructions": {"de": "Original", "en": "Original"},
            "scoring_rules": {"max_points": 10},
        },
    )
    assert challenge_response.status_code == 201
    challenge = challenge_response.json()["challenge"]
    challenge_id = challenge["id"]
    original_challenge_updated_at = challenge["updated_at"]

    # Add Turkish to the rallye
    rallye_update = await client.put(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
        headers=creator_headers,
        json={
            "title": {"de": "Aktualisierte Rallye", "en": "Updated Rally", "tr": "Güncellenmiş Rallye"},
            "description": {"de": "Aktualisiert", "en": "Updated", "tr": "Güncellendi"},
        },
    )
    assert rallye_update.status_code == 200
    rallye_data = rallye_update.json()
    assert rallye_data["title"]["tr"] == "Güncellenmiş Rallye"
    assert rallye_data["updated_at"] != original_rallye_updated_at

    # Change the coordinates and description of the library station
    station_update = await client.put(
        f"/api/orgs/{org.slug}/stations/{station_id}",
        headers=creator_headers,
        json={
            "title": {"de": "Station Aktualisiert", "en": "Station Updated"},
            "description": {"de": "Neue Beschreibung", "en": "New Description"},
            "location_lat": 48.3720,
            "location_lng": 10.9000,
        },
    )
    assert station_update.status_code == 200
    station_data = station_update.json()
    assert station_data["location_lat"] == 48.3720
    assert station_data["location_lng"] == 10.9000
    assert station_data["description"]["de"] == "Neue Beschreibung"
    assert station_data["updated_at"] != original_station_updated_at

    # Change max_points of the library challenge to 15
    challenge_update = await client.put(
        f"/api/orgs/{org.slug}/challenges/{challenge_id}",
        headers=creator_headers,
        json={"scoring_rules": {"max_points": 15}},
    )
    assert challenge_update.status_code == 200
    challenge_data = challenge_update.json()
    assert challenge_data["scoring_rules"]["max_points"] == 15
    assert challenge_data["updated_at"] != original_challenge_updated_at

    assert (await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=creator_headers)).json()["title"][
        "tr"
    ] == "Güncellenmiş Rallye"
    assert (await client.get(f"/api/orgs/{org.slug}/stations/{station_id}", headers=creator_headers)).json()[
        "location_lat"
    ] == 48.3720
    assert (await client.get(f"/api/orgs/{org.slug}/challenges/{challenge_id}", headers=creator_headers)).json()[
        "scoring_rules"
    ]["max_points"] == 15


# =============================================================================
# Content Delete + Cascade-Unlink
# =============================================================================


@pytest.mark.asyncio
async def test_challenge_delete_unlinks_from_station(client: AsyncClient, db_session: AsyncSession) -> None:
    """Deleting a library challenge unlinks it from its station."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@delete.test", role=UserRole.CREATOR, org_slug="delete-org"
    )
    creator_headers = get_auth_headers(creator)

    rallye_id = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/",
            headers=creator_headers,
            json={"title": {"de": "Test"}, "description": {"de": "Test"}},
        )
    ).json()["id"]

    station_link = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
            headers=creator_headers,
            json={
                "title": {"de": "Station"},
                "description": {"de": "Test"},
                "location_lat": 48.3705,
                "location_lng": 10.8978,
            },
        )
    ).json()
    station_id = station_link["station"]["id"]
    rallye_station_id = station_link["link_id"]

    challenge1_id = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
            headers=creator_headers,
            json={
                "challenge_type": "text",
                "title": {"de": "Challenge 1"},
                "instructions": {"de": "Test"},
                "scoring_rules": {"max_points": 10},
            },
        )
    ).json()["challenge"]["id"]
    challenge2_id = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
            headers=creator_headers,
            json={
                "challenge_type": "photo",
                "title": {"de": "Challenge 2"},
                "instructions": {"de": "Test"},
                "scoring_rules": {"max_points": 20},
            },
        )
    ).json()["challenge"]["id"]

    # Deleting the challenge answers 200 and names the stations that used it
    delete_response = await client.delete(f"/api/orgs/{org.slug}/challenges/{challenge1_id}", headers=creator_headers)
    assert delete_response.status_code == 200
    assert station_id in delete_response.json()["station_ids"]

    # The soft-deleted challenge is gone
    get_response = await client.get(f"/api/orgs/{org.slug}/challenges/{challenge1_id}", headers=creator_headers)
    assert get_response.status_code == 404

    # Only the second challenge remains in the station's list
    list_response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
        headers=creator_headers,
    )
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert len(items) == 1
    assert items[0]["challenge"]["id"] == challenge2_id


@pytest.mark.asyncio
async def test_station_delete_unlinks_from_rallye(client: AsyncClient, db_session: AsyncSession) -> None:
    """Deleting a library station removes it from the rallye composition."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@station-delete.test", role=UserRole.CREATOR, org_slug="station-delete-org"
    )
    creator_headers = get_auth_headers(creator)

    rallye_id = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/",
            headers=creator_headers,
            json={"title": {"de": "Test"}, "description": {"de": "Test"}},
        )
    ).json()["id"]

    station_link = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
            headers=creator_headers,
            json={
                "title": {"de": "Station"},
                "description": {"de": "Test"},
                "location_lat": 48.3705,
                "location_lng": 10.8978,
            },
        )
    ).json()
    station_id = station_link["station"]["id"]
    rallye_station_id = station_link["link_id"]

    for i in range(2):
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
            headers=creator_headers,
            json={
                "challenge_type": "text",
                "title": {"de": f"Challenge {i}"},
                "instructions": {"de": "Test"},
                "scoring_rules": {"max_points": 10},
            },
        )

    # Deleting the station answers 200 and names the rallyes that used it
    delete_response = await client.delete(f"/api/orgs/{org.slug}/stations/{station_id}", headers=creator_headers)
    assert delete_response.status_code == 200
    assert rallye_id in delete_response.json()["rallye_ids"]

    # The soft-deleted station is gone
    assert (await client.get(f"/api/orgs/{org.slug}/stations/{station_id}", headers=creator_headers)).status_code == 404

    # The rallye composition no longer includes the station
    listing = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations", headers=creator_headers)
    assert listing.status_code == 200
    assert listing.json()["items"] == []


@pytest.mark.asyncio
async def test_rallye_delete_keeps_its_stations_in_the_library(client: AsyncClient, db_session: AsyncSession) -> None:
    """Deleting a rallye removes it; its stations remain in the library."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@rallye-delete.test", role=UserRole.CREATOR, org_slug="rallye-delete-org"
    )
    creator_headers = get_auth_headers(creator)

    rallye_id = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/",
            headers=creator_headers,
            json={"title": {"de": "Test Rallye"}, "description": {"de": "Test"}},
        )
    ).json()["id"]

    station_link = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
            headers=creator_headers,
            json={
                "title": {"de": "Station"},
                "description": {"de": "Test"},
                "location_lat": 48.3705,
                "location_lng": 10.8978,
            },
        )
    ).json()
    station_id = station_link["station"]["id"]
    rallye_station_id = station_link["link_id"]

    await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
        headers=creator_headers,
        json={
            "challenge_type": "text",
            "title": {"de": "Challenge"},
            "instructions": {"de": "Test"},
            "scoring_rules": {"max_points": 10},
        },
    )

    delete_response = await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=creator_headers)
    assert delete_response.status_code == 204

    assert (await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=creator_headers)).status_code == 404
    # The rallye is gone, so its composition listing is 404.
    assert (
        await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations", headers=creator_headers)
    ).status_code == 404
    # The library station survives the rallye deletion.
    assert (await client.get(f"/api/orgs/{org.slug}/stations/{station_id}", headers=creator_headers)).status_code == 200


# =============================================================================
# Participant/Coordinator Cannot Author Content
# =============================================================================


@pytest.mark.asyncio
async def test_participant_cannot_author_content(client: AsyncClient, db_session: AsyncSession) -> None:
    """A participant cannot create, update or delete content."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@participant-test.test", role=UserRole.CREATOR, org_slug="participant-test-org"
    )
    creator_headers = get_auth_headers(creator)

    participant = await create_test_user(
        db_session, email="participant@participant-test.test", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)

    rallye_id = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/",
            headers=creator_headers,
            json={"title": {"de": "Creator Rallye"}, "description": {"de": "Test"}},
        )
    ).json()["id"]

    station_link = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
            headers=creator_headers,
            json={
                "title": {"de": "Station"},
                "description": {"de": "Test"},
                "location_lat": 48.3705,
                "location_lng": 10.8978,
            },
        )
    ).json()
    rallye_station_id = station_link["link_id"]

    assert (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
            headers=creator_headers,
            json={
                "challenge_type": "text",
                "title": {"de": "Challenge"},
                "instructions": {"de": "Test"},
                "scoring_rules": {"max_points": 10},
            },
        )
    ).status_code == 201

    # A participant cannot create a rallye (403)
    assert (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/",
            headers=participant_headers,
            json={"title": {"de": "Participant Rallye"}, "description": {"de": "Test"}},
        )
    ).status_code == 403

    # A participant cannot create a station (403)
    assert (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
            headers=participant_headers,
            json={
                "title": {"de": "Participant Station"},
                "description": {"de": "Test"},
                "location_lat": 48.3705,
                "location_lng": 10.8978,
            },
        )
    ).status_code == 403

    # A participant cannot create a challenge (403)
    assert (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
            headers=participant_headers,
            json={
                "challenge_type": "text",
                "title": {"de": "Participant Challenge"},
                "instructions": {"de": "Test"},
                "scoring_rules": {"max_points": 10},
            },
        )
    ).status_code == 403

    # A participant cannot update a rallye (403)
    assert (
        await client.put(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}",
            headers=participant_headers,
            json={"title": {"de": "Hijacked"}},
        )
    ).status_code == 403

    # A participant cannot delete a rallye (403)
    assert (
        await client.delete(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=participant_headers)
    ).status_code == 403


@pytest.mark.asyncio
async def test_coordinator_cannot_author_content(client: AsyncClient, db_session: AsyncSession) -> None:
    """A coordinator cannot create rallyes."""
    coordinator, org = await create_test_user_with_org(
        db_session, email="coordinator@author-test.test", role=UserRole.COORDINATOR, org_slug="coord-author-org"
    )
    coord_headers = get_auth_headers(coordinator)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/",
        headers=coord_headers,
        json={"title": {"de": "Coordinator Rallye"}, "description": {"de": "Test"}},
    )
    assert response.status_code == 403


# =============================================================================
# Participant Can Read Published Content
# =============================================================================


@pytest.mark.asyncio
async def test_coordinator_reads_a_published_rallye_and_only_the_creator_its_composition(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """
    A coordinator reads a published rallye. Only the creator and above read its composition.

    A participant reads none of it. The composition carries the answers and no
    participant screen needs the rallye routes. A team reads its tasks through the run.
    """
    creator, org = await create_test_user_with_org(
        db_session, email="creator@read-test.test", role=UserRole.CREATOR, org_slug="read-test-org"
    )
    creator_headers = get_auth_headers(creator)

    participant = await create_test_user(
        db_session, email="participant@read-test.test", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)
    coordinator = await create_test_user(
        db_session, email="coordinator@read-test.test", role=UserRole.COORDINATOR, organization=org
    )
    coordinator_headers = get_auth_headers(coordinator)

    rallye_id = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/",
            headers=creator_headers,
            json={"title": {"de": "Unpublished Rallye"}, "description": {"de": "Test"}},
        )
    ).json()["id"]

    station_link = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations",
            headers=creator_headers,
            json={
                "title": {"de": "Station"},
                "description": {"de": "Test"},
                "location_lat": 48.3705,
                "location_lng": 10.8978,
            },
        )
    ).json()
    station_id = station_link["station"]["id"]
    rallye_station_id = station_link["link_id"]

    challenge_id = (
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges",
            headers=creator_headers,
            json={
                "challenge_type": "text",
                "title": {"de": "Challenge"},
                "instructions": {"de": "Test"},
                "scoring_rules": {"max_points": 10},
            },
        )
    ).json()["challenge"]["id"]

    # Coordinator can hit the list endpoint while unpublished.
    assert (await client.get(f"/api/orgs/{org.slug}/rallyes/", headers=coordinator_headers)).status_code == 200

    publish_response = await client.put(
        f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=creator_headers, json={"is_published": True}
    )
    assert publish_response.status_code == 200

    # The published rallye shows in the list
    list_response = await client.get(f"/api/orgs/{org.slug}/rallyes/", headers=coordinator_headers)
    assert list_response.status_code == 200
    assert rallye_id in [r["id"] for r in list_response.json()["items"]]

    detail_response = await client.get(f"/api/orgs/{org.slug}/rallyes/{rallye_id}", headers=coordinator_headers)
    assert detail_response.status_code == 200

    # The composition is the editor's: creator reads it, nobody below does.
    stations_url = f"/api/orgs/{org.slug}/rallyes/{rallye_id}/stations"
    challenges_url = f"{stations_url}/{rallye_station_id}/challenges"
    stations_response = await client.get(stations_url, headers=creator_headers)
    assert stations_response.status_code == 200
    items = stations_response.json()["items"]
    assert len(items) == 1
    assert items[0]["station"]["id"] == station_id
    challenges_response = await client.get(challenges_url, headers=creator_headers)
    assert challenges_response.status_code == 200
    c_items = challenges_response.json()["items"]
    assert len(c_items) == 1
    assert c_items[0]["challenge"]["id"] == challenge_id

    for headers in (participant_headers, coordinator_headers):
        assert (await client.get(stations_url, headers=headers)).status_code == 403
        assert (await client.get(challenges_url, headers=headers)).status_code == 403
    for url in (f"/api/orgs/{org.slug}/rallyes/", f"/api/orgs/{org.slug}/rallyes/{rallye_id}"):
        assert (await client.get(url, headers=participant_headers)).status_code == 403


@pytest.mark.asyncio
async def test_coordinator_lists_only_the_published_rallyes_of_its_own_organization(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """
    A coordinator lists the published rallyes of their own organization and none of another organization.
    A participant lists no rallyes, so the check runs with a coordinator.
    """
    participant_a, org_a = await create_test_user_with_org(
        db_session, email="participant-a@list-test.test", role=UserRole.COORDINATOR, org_slug="list-org-a"
    )
    participant_a_headers = get_auth_headers(participant_a)
    creator_a = await create_test_user(
        db_session, email="creator-a@list-test.test", role=UserRole.CREATOR, organization=org_a
    )

    creator_b, org_b = await create_test_user_with_org(
        db_session, email="creator-b@list-test.test", role=UserRole.CREATOR, org_slug="list-org-b"
    )

    async def published_rallye(slug: str, creator, title: str) -> str:
        # Published, because a coordinator lists only published rallyes: an unpublished one would hide a leak.
        headers = get_auth_headers(creator)
        body = {"title": {"de": title}, "description": {"de": "Test"}}
        created = await client.post(f"/api/orgs/{slug}/rallyes/", headers=headers, json=body)
        assert created.status_code == 201
        rallye_id = created.json()["id"]
        published = await client.put(
            f"/api/orgs/{slug}/rallyes/{rallye_id}", headers=headers, json={"is_published": True}
        )
        assert published.status_code == 200
        return rallye_id

    rallye_a = await published_rallye(org_a.slug, creator_a, "Org A Rallye")
    rallye_b = await published_rallye(org_b.slug, creator_b, "Org B Rallye")

    response = await client.get(f"/api/orgs/{org_a.slug}/rallyes/", headers=participant_a_headers)
    assert response.status_code == 200
    listed = [r["id"] for r in response.json()["items"]]
    assert listed == [rallye_a]
    assert rallye_b not in listed
