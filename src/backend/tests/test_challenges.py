"""Integration tests for the library Challenge endpoints and the rallye-station <-> challenge composition.

Challenges live in the organization-wide library.
`POST /rallyes/{rid}/stations/{rsid}/challenges` creates a new library challenge and
links it to the rallye's station, appended. `GET/PUT/DELETE /challenges/{cid}` manage it
in the library. The per-rallye-station order lives on the rallye_station_challenges link,
exposed as `sort_order` on the composition response, not on the challenge itself.

"""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import ChallengeType
from app.models.organization import Organization
from app.models.rallye import Rallye
from app.models.rallye_station import RallyeStation
from app.models.station import Station
from app.models.user import UserRole
from tests.factories import (
    create_test_challenge,
    create_test_rallye,
    create_test_station,
    create_test_user,
    create_test_user_with_org,
    get_auth_headers,
    link_challenge_to_rallye_station,
    link_station_to_rallye,
)


async def _rallye_station(
    db_session: AsyncSession,
    org: Organization,
    *,
    is_published: bool = False,
    station: Station | None = None,
    rallye: Rallye | None = None,
) -> tuple[Rallye, Station, RallyeStation]:
    """Create a rallye + library station and link them; return (rallye, station, link)."""
    if rallye is None:
        rallye = await create_test_rallye(db_session, org, is_published=is_published)
    if station is None:
        station = await create_test_station(db_session, org)
    link = await link_station_to_rallye(db_session, org, rallye, station, sort_order=0)
    return rallye, station, link


def _challenge_body(**overrides):
    body = {
        "challenge_type": "text",
        "title": {"de": "Aufgabe", "en": "Challenge"},
        "instructions": {"de": "Anleitung", "en": "Instructions"},
        "scoring_rules": {"max_points": 10},
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_create_challenge_with_multilingual_content(client: AsyncClient, db_session: AsyncSession):
    """Creating a challenge under a rallye-station returns the link + nested library challenge."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=headers,
        json=_challenge_body(
            challenge_type="photo",
            title={"de": "Foto", "en": "Photo"},
            scoring_rules={"max_points": 20},
        ),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["rallye_station_id"] == str(link.id)
    assert data["sort_order"] == 0
    challenge = data["challenge"]
    assert challenge["challenge_type"] == "photo"
    assert challenge["title"] == {"de": "Foto", "en": "Photo"}
    assert challenge["scoring_rules"]["max_points"] == 20
    assert challenge["organization_id"] == str(org.id)
    UUID(challenge["id"])


@pytest.mark.asyncio
async def test_create_challenge_with_all_challenge_types(client: AsyncClient, db_session: AsyncSession):
    """All challenge types can be created."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)

    for ctype in ["text", "photo", "qr_scan", "quiz", "audio", "video"]:
        response = await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
            headers=headers,
            json=_challenge_body(challenge_type=ctype),
        )
        assert response.status_code == 201, f"type {ctype}: {response.text}"
        assert response.json()["challenge"]["challenge_type"] == ctype


@pytest.mark.asyncio
async def test_the_retired_type_creative_is_refused_on_every_create_and_update(
    client: AsyncClient, db_session: AsyncSession
):
    """The retired type `creative` is refused on both create routes and on a type change, and the error names the field."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)
    challenge = await create_test_challenge(db_session, org, challenge_type=ChallengeType.PHOTO)

    responses = [
        await client.post(
            f"/api/orgs/{org.slug}/challenges/", headers=headers, json=_challenge_body(challenge_type="creative")
        ),
        await client.post(
            f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
            headers=headers,
            json=_challenge_body(challenge_type="creative"),
        ),
        await client.put(
            f"/api/orgs/{org.slug}/challenges/{challenge.id}", headers=headers, json={"challenge_type": "creative"}
        ),
    ]
    for response in responses:
        assert response.status_code == 422, response.text
        assert response.json()["detail"][0]["loc"][-1] == "challenge_type"
        assert "no longer offered" in response.json()["detail"][0]["msg"]


@pytest.mark.asyncio
async def test_create_challenge_with_invalid_type_fails(client: AsyncClient, db_session: AsyncSession):
    """An unknown challenge_type is rejected with 422."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=headers,
        json=_challenge_body(challenge_type="not_a_type"),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_challenge_requires_creator_role(client: AsyncClient, db_session: AsyncSession):
    """Participants cannot create challenges."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    rallye, _station, link = await _rallye_station(db_session, org)

    participant = await create_test_user(
        db_session, email="participant@example.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=participant_headers,
        json=_challenge_body(),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_challenges_ordered_by_sort_order(client: AsyncClient, db_session: AsyncSession):
    """Listing a rallye-station's challenges returns them ordered by sort_order."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)

    c1 = await create_test_challenge(db_session, org, title={"de": "First"})
    c2 = await create_test_challenge(db_session, org, title={"de": "Second"})
    c3 = await create_test_challenge(db_session, org, title={"de": "Third"})
    await link_challenge_to_rallye_station(db_session, org, link, c2, sort_order=0)
    await link_challenge_to_rallye_station(db_session, org, link, c3, sort_order=1)
    await link_challenge_to_rallye_station(db_session, org, link, c1, sort_order=2)

    response = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges", headers=headers
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["sort_order"] for item in items] == [0, 1, 2]
    assert [item["challenge"]["title"]["de"] for item in items] == ["Second", "Third", "First"]


@pytest.mark.asyncio
async def test_get_challenge_by_id(client: AsyncClient, db_session: AsyncSession):
    """GET /challenges/{id} returns the library challenge with usage."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    _rallye, station, link = await _rallye_station(db_session, org)
    challenge = await create_test_challenge(db_session, org, title={"de": "X", "en": "X"})
    await link_challenge_to_rallye_station(db_session, org, link, challenge, sort_order=0)

    response = await client.get(f"/api/orgs/{org.slug}/challenges/{challenge.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(challenge.id)
    assert str(station.id) in data["usage"]["station_ids"]


@pytest.mark.asyncio
async def test_get_nonexistent_challenge_returns_404(client: AsyncClient, db_session: AsyncSession):
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    response = await client.get(f"/api/orgs/{org.slug}/challenges/{uuid4()}", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_challenge_propagates(client: AsyncClient, db_session: AsyncSession):
    """Updating a library challenge changes its scoring_rules everywhere it is linked."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)
    challenge = await create_test_challenge(db_session, org, scoring_rules={"max_points": 10})
    await link_challenge_to_rallye_station(db_session, org, link, challenge, sort_order=0)

    response = await client.put(
        f"/api/orgs/{org.slug}/challenges/{challenge.id}",
        headers=headers,
        json={"scoring_rules": {"max_points": 15}},
    )
    assert response.status_code == 200
    assert response.json()["scoring_rules"]["max_points"] == 15

    listing = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges", headers=headers
    )
    assert listing.json()["items"][0]["challenge"]["scoring_rules"]["max_points"] == 15


@pytest.mark.asyncio
async def test_update_nonexistent_challenge_returns_404(client: AsyncClient, db_session: AsyncSession):
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    response = await client.put(
        f"/api/orgs/{org.slug}/challenges/{uuid4()}", headers=headers, json={"title": {"de": "X"}}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_challenge_returns_usage_and_cascade_unlinks(client: AsyncClient, db_session: AsyncSession):
    """DELETE /challenges/{id} returns usage (200) and unlinks from all rallye-stations."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, station, link = await _rallye_station(db_session, org)
    challenge = await create_test_challenge(db_session, org)
    await link_challenge_to_rallye_station(db_session, org, link, challenge, sort_order=0)

    response = await client.delete(f"/api/orgs/{org.slug}/challenges/{challenge.id}", headers=headers)
    assert response.status_code == 200
    assert str(station.id) in response.json()["station_ids"]

    listing = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges", headers=headers
    )
    assert listing.json()["items"] == []


@pytest.mark.asyncio
async def test_deleted_challenge_not_in_station_list(client: AsyncClient, db_session: AsyncSession):
    """A soft-deleted challenge no longer appears in its rallye-station's list."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)
    keep = await create_test_challenge(db_session, org, title={"de": "Keep"})
    drop = await create_test_challenge(db_session, org, title={"de": "Drop"})
    await link_challenge_to_rallye_station(db_session, org, link, keep, sort_order=0)
    await link_challenge_to_rallye_station(db_session, org, link, drop, sort_order=1)

    await client.delete(f"/api/orgs/{org.slug}/challenges/{drop.id}", headers=headers)

    listing = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges", headers=headers
    )
    ids = [item["challenge"]["id"] for item in listing.json()["items"]]
    assert str(keep.id) in ids
    assert str(drop.id) not in ids


@pytest.mark.asyncio
async def test_get_deleted_challenge_returns_404(client: AsyncClient, db_session: AsyncSession):
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    challenge = await create_test_challenge(db_session, org)

    await client.delete(f"/api/orgs/{org.slug}/challenges/{challenge.id}", headers=headers)
    response = await client.get(f"/api/orgs/{org.slug}/challenges/{challenge.id}", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cannot_create_challenge_under_other_org_rallye_station(client: AsyncClient, db_session: AsyncSession):
    """A creator cannot create a challenge under another org's rallye-station (via their slug)."""
    creator_a, org_a = await create_test_user_with_org(
        db_session, email="creator-a@example.com", role=UserRole.CREATOR, org_slug="org-a"
    )
    headers_a = get_auth_headers(creator_a)

    _creator_b, org_b = await create_test_user_with_org(
        db_session, email="creator-b@example.com", role=UserRole.CREATOR, org_slug="org-b"
    )
    rallye_b, _station_b, link_b = await _rallye_station(db_session, org_b)

    response = await client.post(
        f"/api/orgs/{org_a.slug}/rallyes/{rallye_b.id}/stations/{link_b.id}/challenges",
        headers=headers_a,
        json=_challenge_body(),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_participant_can_neither_read_nor_create_rallye_challenges(client: AsyncClient, db_session: AsyncSession):
    """A participant can neither read a rallye-station's challenge list nor create challenges.

    The list carries the full scoring rules and reference answers,
    so a participant must not read it, even for a published rallye.

    """
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    creator_headers = get_auth_headers(creator)
    participant = await create_test_user(
        db_session, email="participant@example.com", role=UserRole.PARTICIPANT, organization=org
    )
    participant_headers = get_auth_headers(participant)

    rallye, _station, link = await _rallye_station(db_session, org, is_published=True)
    challenge = await create_test_challenge(db_session, org)
    await link_challenge_to_rallye_station(db_session, org, link, challenge, sort_order=0)

    listing = await client.get(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges", headers=participant_headers
    )
    assert listing.status_code == 403

    create = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=participant_headers,
        json=_challenge_body(),
    )
    assert create.status_code == 403

    assert (
        await client.get(f"/api/orgs/{org.slug}/challenges/{challenge.id}", headers=creator_headers)
    ).status_code == 200


# ---------------------------------------------------------------------------
# QUIZ / QR auto_approve scoring-rule validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_quiz_with_auto_approve_requires_correct_option(client: AsyncClient, db_session: AsyncSession):
    """QUIZ + auto_approve without correct_option -> 422."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=headers,
        json=_challenge_body(challenge_type="quiz", scoring_rules={"max_points": 10, "auto_approve": True}),
    )
    assert response.status_code == 422
    assert "correct_option" in str(response.json()["detail"]).lower()


@pytest.mark.asyncio
async def test_create_quiz_with_auto_approve_and_correct_option_succeeds(client: AsyncClient, db_session: AsyncSession):
    """QUIZ + auto_approve + correct_option -> 201."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=headers,
        json=_challenge_body(
            challenge_type="quiz",
            scoring_rules={"max_points": 10, "auto_approve": True, "correct_option": "A", "options": ["A", "B"]},
        ),
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_create_quiz_without_auto_approve_does_not_require_correct_option(
    client: AsyncClient, db_session: AsyncSession
):
    """QUIZ without auto_approve does not require correct_option."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=headers,
        json=_challenge_body(challenge_type="quiz", scoring_rules={"max_points": 10}),
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_update_quiz_to_auto_approve_without_correct_option_fails(client: AsyncClient, db_session: AsyncSession):
    """Updating a QUIZ to auto_approve without correct_option -> 422."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    challenge = await create_test_challenge(
        db_session, org, challenge_type=ChallengeType.QUIZ, scoring_rules={"max_points": 10}
    )

    response = await client.put(
        f"/api/orgs/{org.slug}/challenges/{challenge.id}",
        headers=headers,
        json={"scoring_rules": {"max_points": 10, "auto_approve": True}},
    )
    assert response.status_code == 422
    assert "correct_option" in str(response.json()["detail"]).lower()


@pytest.mark.asyncio
async def test_update_challenge_type_to_quiz_discards_the_stored_auto_approve(
    client: AsyncClient, db_session: AsyncSession
):
    """Switching type to QUIZ drops a stored auto_approve instead of rejecting it.

    An ordinary type change must not fail over a value the creator never sent.
    Without the discard, auto_approve would survive the prune. QUIZ would then reject it for the missing
    correct_option, and the type change would answer 422.

    """
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    challenge = await create_test_challenge(
        db_session, org, challenge_type=ChallengeType.TEXT, scoring_rules={"max_points": 10, "auto_approve": True}
    )

    response = await client.put(
        f"/api/orgs/{org.slug}/challenges/{challenge.id}",
        headers=headers,
        json={"challenge_type": "quiz"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["scoring_rules"] == {"max_points": 10}


@pytest.mark.asyncio
async def test_update_quiz_to_auto_approve_with_correct_option_succeeds(client: AsyncClient, db_session: AsyncSession):
    """Updating a QUIZ to auto_approve with correct_option -> 200."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    challenge = await create_test_challenge(
        db_session, org, challenge_type=ChallengeType.QUIZ, scoring_rules={"max_points": 10}
    )

    response = await client.put(
        f"/api/orgs/{org.slug}/challenges/{challenge.id}",
        headers=headers,
        json={"scoring_rules": {"max_points": 10, "auto_approve": True, "correct_option": "A"}},
    )
    assert response.status_code == 200
    assert response.json()["scoring_rules"]["auto_approve"] is True


# --- Scoring keys must not outlive the type they belong to ----------------------
#
# A client can send scoring keys of a type that the challenge no longer has. A stored
# auto_approve is then inert, because auto_score.py handles only QUIZ and QR_SCAN.
# The challenge would show as self-scoring while it waits for a coordinator.


@pytest.mark.asyncio
async def test_create_on_station_prunes_auto_approve_from_a_text_challenge(
    client: AsyncClient, db_session: AsyncSession
):
    """The rallye-station create path drops keys the type cannot use."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    rallye, _station, link = await _rallye_station(db_session, org)

    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=headers,
        json=_challenge_body(
            challenge_type="text",
            scoring_rules={"max_points": 10, "auto_approve": True, "correct_option": "A", "options": ["A"]},
        ),
    )
    assert response.status_code == 201
    stored = response.json()["challenge"]["scoring_rules"]
    assert stored == {"max_points": 10}


@pytest.mark.asyncio
async def test_create_in_library_prunes_auto_approve_from_a_photo_challenge(
    client: AsyncClient, db_session: AsyncSession
):
    """The library create path enforces the same rule."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)

    response = await client.post(
        f"/api/orgs/{org.slug}/challenges/",
        headers=headers,
        json=_challenge_body(
            challenge_type="photo",
            scoring_rules={"max_points": 10, "options": [""], "auto_approve": True},
        ),
    )
    assert response.status_code == 201
    assert response.json()["scoring_rules"] == {"max_points": 10}


@pytest.mark.asyncio
async def test_quiz_keeps_its_scoring_keys(client: AsyncClient, db_session: AsyncSession):
    """Pruning must not touch a type that legitimately uses the keys."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)

    rules = {"max_points": 20, "auto_approve": True, "options": ["A", "B"], "correct_option": "A"}
    response = await client.post(
        f"/api/orgs/{org.slug}/challenges/",
        headers=headers,
        json=_challenge_body(challenge_type="quiz", scoring_rules=rules),
    )
    assert response.status_code == 201
    assert response.json()["scoring_rules"] == rules


@pytest.mark.asyncio
async def test_changing_type_alone_clears_the_old_types_keys(client: AsyncClient, db_session: AsyncSession):
    """
    The repair path: a challenge already stored with quiz keys, updated to text
    without sending scoring_rules at all, must come back pruned.
    """
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    challenge = await create_test_challenge(
        db_session,
        org,
        challenge_type=ChallengeType.QUIZ,
        scoring_rules={"max_points": 10, "auto_approve": True, "correct_option": "A", "options": ["A", "B"]},
    )

    response = await client.put(
        f"/api/orgs/{org.slug}/challenges/{challenge.id}",
        headers=headers,
        json={"challenge_type": "text"},
    )
    assert response.status_code == 200
    assert response.json()["scoring_rules"] == {"max_points": 10}


@pytest.mark.asyncio
async def test_switching_between_quiz_and_qr_scan_keeps_only_the_universal_keys(
    client: AsyncClient, db_session: AsyncSession
):
    """Switching quiz -> qr_scan -> quiz answers 200 on both legs.

    `auto_approve` is the one type key QUIZ and QR_SCAN share.
    Keeping it while dropping the key it scores with would make QR_SCAN demand
    an `expected_value` nobody sent, and the change would answer 422.
    A type change keeps `max_points` and `time_bonus` and nothing else,
    which is what the new type can validate.

    """
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    challenge = await create_test_challenge(
        db_session,
        org,
        challenge_type=ChallengeType.QUIZ,
        scoring_rules={
            "max_points": 15,
            "time_bonus": 5,
            "auto_approve": True,
            "options": ["A", "B"],
            "correct_option": "A",
        },
    )
    url = f"/api/orgs/{org.slug}/challenges/{challenge.id}"

    to_qr = await client.put(url, headers=headers, json={"challenge_type": "qr_scan"})
    assert to_qr.status_code == 200, to_qr.text
    assert to_qr.json()["challenge_type"] == "qr_scan"
    assert to_qr.json()["scoring_rules"] == {"max_points": 15, "time_bonus": 5}

    # The stored rules are a valid QR_SCAN configuration, not a dead end: the
    # creator can now configure automatic approval for the new type.
    configured = await client.put(
        url,
        headers=headers,
        json={"scoring_rules": {"max_points": 15, "time_bonus": 5, "auto_approve": True, "expected_value": "AUG-1"}},
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["scoring_rules"]["expected_value"] == "AUG-1"

    back_to_quiz = await client.put(url, headers=headers, json={"challenge_type": "quiz"})
    assert back_to_quiz.status_code == 200, back_to_quiz.text
    assert back_to_quiz.json()["challenge_type"] == "quiz"
    assert back_to_quiz.json()["scoring_rules"] == {"max_points": 15, "time_bonus": 5}


@pytest.mark.asyncio
async def test_challenge_instructions_are_sanitised(client: AsyncClient, db_session: AsyncSession):
    """Instructions are reduced to the editor allowlist on create and update."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    _rallye, _station, link = await _rallye_station(db_session, org)
    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{_rallye.id}/stations/{link.id}/challenges",
        headers=headers,
        json=_challenge_body(instructions={"de": '<h2>Los</h2><p style="color:red">Text</p><script>x()</script>'}),
    )
    assert response.status_code == 201, response.text
    challenge = response.json()["challenge"]
    assert challenge["instructions"]["de"] == "<h2>Los</h2><p>Text</p>"

    update = await client.put(
        f"/api/orgs/{org.slug}/challenges/{challenge['id']}",
        headers=headers,
        json={"instructions": {"de": '<p>Neu</p><a href="javascript:alert(1)">k</a>'}},
    )
    assert update.status_code == 200, update.text
    assert "javascript:" not in update.json()["instructions"]["de"]
    assert "Neu" in update.json()["instructions"]["de"]
