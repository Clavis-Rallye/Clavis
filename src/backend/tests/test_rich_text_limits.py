"""Every rich text of a request has at most ``RICH_TEXT_MAX_LENGTH`` characters per language."""

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
