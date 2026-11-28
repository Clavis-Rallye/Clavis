"""Every rich text of a request has at most ``RICH_TEXT_MAX_LENGTH`` characters per language.

The limit holds twice: for the text that comes in, and for the cleaned text that the write stores.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from app.routers.schemas import (
    ChallengeCreateRequest,
    ChallengeUpdateRequest,
    InstanceLegalUpdateRequest,
    OrganizationUpdateRequest,
    RallyeCreateRequest,
    RallyeUpdateRequest,
    StationCreateRequest,
    StationUpdateRequest,
)
from app.services.richtext import RICH_TEXT_MAX_LENGTH
from tests.factories import create_test_user_with_org, get_auth_headers

CHALLENGE_BASE: dict[str, Any] = {
    "challenge_type": "photo",
    "title": {"de": "Der Saal"},
    "instructions": {"de": "<p>Macht ein Foto.</p>"},
    "scoring_rules": {"max_points": 10},
}

# (model, the other required fields, the field that holds a rich text, a text per language or one string)
MULTILINGUAL: list[tuple[type[BaseModel], dict[str, Any], str]] = [
    (RallyeCreateRequest, {"title": {"de": "T"}}, "description"),
    (RallyeUpdateRequest, {}, "description"),
    (StationCreateRequest, {"title": {"de": "T"}}, "description"),
    (StationUpdateRequest, {}, "description"),
    (ChallengeCreateRequest, CHALLENGE_BASE, "instructions"),
    (ChallengeUpdateRequest, {}, "instructions"),
]
SINGLE: list[tuple[type[BaseModel], dict[str, Any], str]] = [
    (OrganizationUpdateRequest, {}, "legal_imprint"),
    (OrganizationUpdateRequest, {}, "legal_privacy"),
    (OrganizationUpdateRequest, {}, "legal_accessibility"),
    (InstanceLegalUpdateRequest, {}, "legal_imprint"),
    (InstanceLegalUpdateRequest, {}, "legal_privacy"),
    (InstanceLegalUpdateRequest, {}, "legal_accessibility"),
]
REFERENCE_ANSWER: list[tuple[type[BaseModel], dict[str, Any], str]] = [
    (ChallengeCreateRequest, CHALLENGE_BASE, "suggested_solution"),
    (ChallengeUpdateRequest, {}, "suggested_solution"),
]


def _ids(cases: list[tuple[type[BaseModel], dict[str, Any], str]]) -> list[str]:
    return [f"{model.__name__}.{field}" for model, _base, field in cases]


REFERENCE_ANSWER_MAX_LENGTH = 10_000


def _refused_field(error: ValidationError) -> str:
    return str(error.errors()[0]["loc"][-1])


def _assert_too_long(error: ValidationError, field: str) -> None:
    first = error.errors()[0]
    assert _refused_field(error) == field
    assert first["type"] == "string_too_long"
    assert first["ctx"]["max_length"] == RICH_TEXT_MAX_LENGTH == 50_000


@pytest.mark.parametrize(("model", "base", "field"), MULTILINGUAL, ids=_ids(MULTILINGUAL))
def test_a_multilingual_text_of_exactly_the_limit_passes_and_one_more_is_refused(
    model: type[BaseModel], base: dict[str, Any], field: str
) -> None:
    model.model_validate({**base, field: {"de": "a" * RICH_TEXT_MAX_LENGTH, "en": "short"}})
    with pytest.raises(ValidationError) as caught:
        model.model_validate({**base, field: {"de": "short", "en": "a" * (RICH_TEXT_MAX_LENGTH + 1)}})
    _assert_too_long(caught.value, field)


@pytest.mark.parametrize(("model", "base", "field"), SINGLE, ids=_ids(SINGLE))
def test_a_legal_text_of_exactly_the_limit_passes_and_one_more_is_refused(
    model: type[BaseModel], base: dict[str, Any], field: str
) -> None:
    assert RICH_TEXT_MAX_LENGTH == 50_000
    model.model_validate({**base, field: "a" * RICH_TEXT_MAX_LENGTH})
    with pytest.raises(ValidationError) as caught:
        model.model_validate({**base, field: "a" * (RICH_TEXT_MAX_LENGTH + 1)})
    _assert_too_long(caught.value, field)


@pytest.mark.parametrize(("model", "base", "field"), REFERENCE_ANSWER, ids=_ids(REFERENCE_ANSWER))
def test_a_reference_answer_keeps_its_own_stricter_limit(
    model: type[BaseModel], base: dict[str, Any], field: str
) -> None:
    model.model_validate({**base, field: "a" * REFERENCE_ANSWER_MAX_LENGTH})
    with pytest.raises(ValidationError) as caught:
        model.model_validate({**base, field: "a" * (REFERENCE_ANSWER_MAX_LENGTH + 1)})
    assert _refused_field(caught.value) == field
    assert caught.value.errors()[0]["type"] == "string_too_long"
    assert caught.value.errors()[0]["ctx"]["max_length"] == REFERENCE_ANSWER_MAX_LENGTH


def test_an_absent_text_is_not_a_limit_problem() -> None:
    RallyeUpdateRequest.model_validate({"description": None})
    OrganizationUpdateRequest.model_validate({"legal_imprint": None})


@pytest.mark.asyncio
async def test_a_route_answers_422_for_a_text_over_the_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    creator, org = await create_test_user_with_org(
        db_session, email="limit@example.com", role=UserRole.CREATOR, org_slug="limit-org"
    )
    url = f"/api/orgs/{org.slug}/challenges/"
    headers = get_auth_headers(creator)
    over = {**CHALLENGE_BASE, "instructions": {"de": "a" * (RICH_TEXT_MAX_LENGTH + 1)}}
    refused = await client.post(url, headers=headers, json=over)
    assert refused.status_code == 422
    first = refused.json()["detail"][0]
    assert first["loc"][-1] == "instructions"
    assert first["type"] == "string_too_long"
    assert first["ctx"]["max_length"] == 50000
    exact = {**CHALLENGE_BASE, "instructions": {"de": "a" * RICH_TEXT_MAX_LENGTH}}
    accepted = await client.post(url, headers=headers, json=exact)
    assert accepted.status_code == 201, accepted.text


# ---------------------------------------------------------------------------
# The cleaned text. An ampersand comes in as one character and is stored as five (``&amp;``), so a text
# inside the limit can be stored far over it. Every write path refuses it with a 422 that names the field.
# ---------------------------------------------------------------------------


def _ampersands(limit: int) -> str:
    """A text of exactly ``limit`` characters that cleaning makes about five times as long."""
    return "<p>" + "&" * (limit - 7) + "</p>"


def _assert_refused_after_cleaning(response: Any, loc: list[str], limit: int) -> None:
    assert response.status_code == 422, response.text
    first = response.json()["detail"][0]
    assert first["loc"] == ["body", *loc]
    assert first["type"] == "string_too_long"
    assert first["ctx"]["max_length"] == limit
    assert f"at most {limit} characters after cleaning" in first["msg"]


@pytest.mark.asyncio
async def test_every_write_path_refuses_a_text_that_cleaning_makes_too_long(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin, org = await create_test_user_with_org(
        db_session, email="cleaned@example.com", role=UserRole.ORG_ADMIN, org_slug="cleaned-org"
    )
    headers = get_auth_headers(admin)
    base = f"/api/orgs/{org.slug}"
    long_text = _ampersands(RICH_TEXT_MAX_LENGTH)
    long_answer = _ampersands(REFERENCE_ANSWER_MAX_LENGTH)
    long_header = _ampersands(2000)
    tips = {"header": {"de": long_header}, "links": [{"label": {"de": "Mehr"}, "url": "https://example.org"}]}

    # Create paths.
    _assert_refused_after_cleaning(
        await client.post(
            f"{base}/rallyes/", headers=headers, json={"title": {"de": "T"}, "description": {"de": long_text}}
        ),
        ["description", "de"],
        RICH_TEXT_MAX_LENGTH,
    )
    _assert_refused_after_cleaning(
        await client.post(
            f"{base}/stations/", headers=headers, json={"title": {"de": "T"}, "description": {"en": long_text}}
        ),
        ["description", "en"],
        RICH_TEXT_MAX_LENGTH,
    )
    _assert_refused_after_cleaning(
        await client.post(
            f"{base}/stations/",
            headers=headers,
            json={"title": {"de": "T"}, "description": {"de": "<p>x</p>"}, "tips": tips},
        ),
        ["tips", "header", "de"],
        2000,
    )
    _assert_refused_after_cleaning(
        await client.post(
            f"{base}/challenges/", headers=headers, json={**CHALLENGE_BASE, "instructions": {"de": long_text}}
        ),
        ["instructions", "de"],
        RICH_TEXT_MAX_LENGTH,
    )
    _assert_refused_after_cleaning(
        await client.post(
            f"{base}/challenges/", headers=headers, json={**CHALLENGE_BASE, "suggested_solution": long_answer}
        ),
        ["suggested_solution"],
        REFERENCE_ANSWER_MAX_LENGTH,
    )
    _assert_refused_after_cleaning(
        await client.put(base, headers=headers, json={"legal_privacy": long_text}),
        ["legal_privacy"],
        RICH_TEXT_MAX_LENGTH,
    )
    # Nothing was stored by a refused request.
    for kind in ("rallyes", "stations", "challenges"):
        listed = await client.get(f"{base}/{kind}/", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 0, kind

    # Update paths.
    rallye = (
        await client.post(
            f"{base}/rallyes/", headers=headers, json={"title": {"de": "T"}, "description": {"de": "<p>x</p>"}}
        )
    ).json()
    station = (
        await client.post(
            f"{base}/stations/", headers=headers, json={"title": {"de": "T"}, "description": {"de": "<p>x</p>"}}
        )
    ).json()
    challenge = (await client.post(f"{base}/challenges/", headers=headers, json=CHALLENGE_BASE)).json()
    _assert_refused_after_cleaning(
        await client.put(f"{base}/rallyes/{rallye['id']}", headers=headers, json={"description": {"de": long_text}}),
        ["description", "de"],
        RICH_TEXT_MAX_LENGTH,
    )
    _assert_refused_after_cleaning(
        await client.put(f"{base}/stations/{station['id']}", headers=headers, json={"description": {"de": long_text}}),
        ["description", "de"],
        RICH_TEXT_MAX_LENGTH,
    )
    _assert_refused_after_cleaning(
        await client.put(f"{base}/stations/{station['id']}", headers=headers, json={"tips": tips}),
        ["tips", "header", "de"],
        2000,
    )
    _assert_refused_after_cleaning(
        await client.put(
            f"{base}/challenges/{challenge['id']}", headers=headers, json={"instructions": {"de": long_text}}
        ),
        ["instructions", "de"],
        RICH_TEXT_MAX_LENGTH,
    )
    _assert_refused_after_cleaning(
        await client.put(
            f"{base}/challenges/{challenge['id']}", headers=headers, json={"suggested_solution": long_answer}
        ),
        ["suggested_solution"],
        REFERENCE_ANSWER_MAX_LENGTH,
    )
    unchanged = (await client.get(f"{base}/challenges/{challenge['id']}", headers=headers)).json()
    assert unchanged["instructions"] == CHALLENGE_BASE["instructions"]
    assert unchanged["suggested_solution"] is None


@pytest.mark.asyncio
async def test_a_cleaned_text_inside_the_limit_is_stored_as_cleaned(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    creator, org = await create_test_user_with_org(
        db_session, email="inside@example.com", role=UserRole.CREATOR, org_slug="inside-org"
    )
    text = "<p>" + "&" * 9998 + "abc</p>"
    created = await client.post(
        f"/api/orgs/{org.slug}/challenges/",
        headers=get_auth_headers(creator),
        json={**CHALLENGE_BASE, "instructions": {"de": text}},
    )
    assert created.status_code == 201, created.text
    stored = created.json()["instructions"]["de"]
    assert len(stored) == RICH_TEXT_MAX_LENGTH
    # The stored text can be saved again as it is.
    again = await client.put(
        f"/api/orgs/{org.slug}/challenges/{created.json()['id']}",
        headers=get_auth_headers(creator),
        json={"instructions": {"de": stored}},
    )
    assert again.status_code == 200, again.text
    assert again.json()["instructions"]["de"] == stored


@pytest.mark.asyncio
async def test_a_text_stored_over_the_limit_before_this_rule_stays_readable_and_says_why_it_cannot_be_saved(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A release before this rule could store a text over the limit. The update changes no stored text."""
    from app.models.challenge import Challenge

    creator, org = await create_test_user_with_org(
        db_session, email="legacy@example.com", role=UserRole.CREATOR, org_slug="legacy-org"
    )
    headers = get_auth_headers(creator)
    created = await client.post(f"/api/orgs/{org.slug}/challenges/", headers=headers, json=CHALLENGE_BASE)
    assert created.status_code == 201, created.text
    url = f"/api/orgs/{org.slug}/challenges/{created.json()['id']}"
    legacy = "<p>" + "&amp;" * 12000 + "</p>"
    assert len(legacy) > RICH_TEXT_MAX_LENGTH
    challenge = await db_session.get(Challenge, created.json()["id"])
    assert challenge is not None
    challenge.instructions = {"de": legacy}
    await db_session.commit()

    read = await client.get(url, headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["instructions"]["de"] == legacy

    # Saved again unchanged, the text is over the limit of the text that comes in.
    refused = await client.put(url, headers=headers, json={"instructions": {"de": legacy}})
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["type"] == "string_too_long"
    assert refused.json()["detail"][0]["loc"][-1] == "instructions"
    # Another field of the same record can still be saved, and the long text stays as it is.
    other = await client.put(url, headers=headers, json={"title": {"de": "Neuer Titel"}})
    assert other.status_code == 200, other.text
    assert other.json()["instructions"]["de"] == legacy
    # A shorter text is accepted.
    shorter = await client.put(url, headers=headers, json={"instructions": {"de": "<p>kurz &amp; gut</p>"}})
    assert shorter.status_code == 200, shorter.text


@pytest.mark.asyncio
async def test_the_operator_legal_text_is_refused_when_cleaning_makes_it_too_long(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.auth.instance import create_instance_admin
    from app.auth.tokens import create_instance_admin_token

    operator = await create_instance_admin(db_session, "operator-limit@example.com", "OperatorPass123!")
    headers = {"Authorization": f"Bearer {create_instance_admin_token(operator.id)}"}
    refused = await client.put(
        "/api/instance/legal", headers=headers, json={"legal_imprint": _ampersands(RICH_TEXT_MAX_LENGTH)}
    )
    _assert_refused_after_cleaning(refused, ["legal_imprint"], RICH_TEXT_MAX_LENGTH)
    stored = await client.get("/api/instance/legal", headers=headers)
    assert stored.json()["legal_imprint"] is None
