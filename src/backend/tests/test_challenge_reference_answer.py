"""
The reference answer of a challenge is rich text (the editor works in all
editorial fields).

The Spielleitung reads it in the grading view. Every write path sanitises it with the
allowlist of the other long texts, and an explicit null clears it.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.factories import (
    create_test_challenge,
    create_test_rallye,
    create_test_station,
    create_test_user_with_org,
    get_auth_headers,
    link_station_to_rallye,
)

DIRTY = (
    '<p><strong>1620</strong>, <a href="https://wiki.example/rathaus">Quelle</a></p>'
    '<script>alert(1)</script><img src="https://tracker.example/p.gif" alt="">'
)
CLEAN = '<p><strong>1620</strong>, <a href="https://wiki.example/rathaus" rel="noopener noreferrer">Quelle</a></p>'


def _body(**overrides):
    body = {
        "challenge_type": "photo",
        "title": {"de": "Der Saal"},
        "instructions": {"de": "<p>Macht ein Foto.</p>"},
        "scoring_rules": {"max_points": 10},
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_library_create_sanitises_the_reference_answer(client: AsyncClient, db_session: AsyncSession):
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    response = await client.post(
        f"/api/orgs/{org.slug}/challenges/",
        headers=get_auth_headers(creator),
        json=_body(suggested_solution=DIRTY),
    )
    assert response.status_code == 201, response.text
    assert response.json()["suggested_solution"] == CLEAN


@pytest.mark.asyncio
async def test_create_inside_a_rallye_sanitises_the_reference_answer(client: AsyncClient, db_session: AsyncSession):
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    rallye = await create_test_rallye(db_session, org)
    station = await create_test_station(db_session, org)
    link = await link_station_to_rallye(db_session, org, rallye, station, sort_order=0)
    response = await client.post(
        f"/api/orgs/{org.slug}/rallyes/{rallye.id}/stations/{link.id}/challenges",
        headers=get_auth_headers(creator),
        json=_body(suggested_solution=DIRTY),
    )
    assert response.status_code == 201, response.text
    assert response.json()["challenge"]["suggested_solution"] == CLEAN


@pytest.mark.asyncio
async def test_update_sanitises_and_an_explicit_null_clears(client: AsyncClient, db_session: AsyncSession):
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    headers = get_auth_headers(creator)
    challenge = await create_test_challenge(db_session, org, suggested_solution="1620")
    url = f"/api/orgs/{org.slug}/challenges/{challenge.id}"

    response = await client.put(url, headers=headers, json={"suggested_solution": DIRTY})
    assert response.status_code == 200, response.text
    assert response.json()["suggested_solution"] == CLEAN

    # A field left out keeps the answer.
    response = await client.put(url, headers=headers, json={"title": {"de": "Neu"}})
    assert response.json()["suggested_solution"] == CLEAN

    # The editor sends null for an emptied field. The reference answer is then removed.
    response = await client.put(url, headers=headers, json={"suggested_solution": None})
    assert response.status_code == 200, response.text
    assert response.json()["suggested_solution"] is None

    # An editor emptied to its bare paragraph clears it too.
    await client.put(url, headers=headers, json={"suggested_solution": "<p>x</p>"})
    response = await client.put(url, headers=headers, json={"suggested_solution": "<p></p>"})
    assert response.json()["suggested_solution"] is None


@pytest.mark.asyncio
async def test_reference_answer_takes_markup_beyond_the_old_bound(client: AsyncClient, db_session: AsyncSession):
    """The bound is 10000 characters, counted with the markup."""
    creator, org = await create_test_user_with_org(
        db_session, email="creator@example.com", role=UserRole.CREATOR, org_slug="test-org"
    )
    long_answer = "<p>" + "<strong>a</strong> " * 300 + "</p>"
    assert 4000 < len(long_answer) < 10000
    response = await client.post(
        f"/api/orgs/{org.slug}/challenges/",
        headers=get_auth_headers(creator),
        json=_body(suggested_solution=long_answer),
    )
    assert response.status_code == 201, response.text
    too_long = await client.post(
        f"/api/orgs/{org.slug}/challenges/",
        headers=get_auth_headers(creator),
        json=_body(suggested_solution="a" * 10001),
    )
    assert too_long.status_code == 422
    assert too_long.json()["detail"][0]["loc"][-1] == "suggested_solution"
