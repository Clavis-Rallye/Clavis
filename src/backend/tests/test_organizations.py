"""POST /api/organizations is closed for organization tokens.

The endpoint answers a coded 403 `ORG_CREATE_INSTANCE_ONLY` for every organization token,
org_admin included. The operator endpoint `POST /api/instance/organizations` creates organizations.
These tests pin the refusal and that it has no side effects.
"""

import json
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import is_unreadable_json, tolerant_json_loads
from app.models.organization import Organization
from app.models.user import UserRole
from app.repositories.organization import OrganizationRepository
from app.routers.users import _org_language
from app.services.retention_config import MAX_SETTINGS_DEPTH
from app.services.run_start import auto_start_enabled
from tests.factories import create_test_user_with_org, get_auth_headers


async def _org_count(db_session: AsyncSession) -> int:
    return await OrganizationRepository(db_session).count()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", list(UserRole))
async def test_create_refuses_every_organization_role(client: AsyncClient, db_session: AsyncSession, role: UserRole):
    """An org_admin gets 403, and so does every other organization token."""
    user, _org = await create_test_user_with_org(
        db_session,
        email=f"{role.value}@example.com",
        role=role,
        org_slug=f"refuse-{role.value}",
    )
    before = await _org_count(db_session)

    resp = await client.post(
        "/api/organizations",
        headers=get_auth_headers(user),
        json={"name": "New Org", "slug": f"new-org-{role.value}", "languages": ["de"], "settings": {}},
    )

    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ORG_CREATE_INSTANCE_ONLY"
    assert await _org_count(db_session) == before


@pytest.mark.asyncio
async def test_create_requires_authentication(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(
        "/api/organizations",
        json={"name": "Anonymous Org", "slug": "anonymous-org", "languages": ["de"], "settings": {}},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_org_admin_saves_legal_texts_through_the_allowlist(client: AsyncClient, db_session: AsyncSession):
    """A legal text is one HTML string. A non-empty save goes through the allowlist."""
    admin, org = await create_test_user_with_org(
        db_session, email="legal@test.com", role=UserRole.ORG_ADMIN, org_slug="legal-org"
    )
    own_image = f"/api/orgs/{org.slug}/media/{org.id}/media/0001_logo.png"

    response = await client.put(
        f"/api/orgs/{org.slug}",
        headers=get_auth_headers(admin),
        json={
            "legal_imprint": (
                f'<p>Impressum</p><script>alert(1)</script><img src="https://tracker.example/p.gif">'
                f'<img src="{own_image}" alt="Logo">'
            ),
            "legal_privacy": "<p>Datenschutz</p>",
            "legal_accessibility": None,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["legal_imprint"] == f'<p>Impressum</p><img src="{own_image}" alt="Logo">'
    assert data["legal_privacy"] == "<p>Datenschutz</p>"
    assert data["legal_accessibility"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [["de"], ["pt-BR"], ["zh-Hant-TW"], ["de", "pt-BR", "zh-Hant-TW"]])
async def test_update_accepts_language_tags_and_stores_them_as_sent(
    client: AsyncClient, db_session: AsyncSession, languages: list[str]
):
    """The languages of an organization are free content codes: the server checks the shape, not a list."""
    admin, org = await create_test_user_with_org(
        db_session, email="tags@test.com", role=UserRole.ORG_ADMIN, org_slug="tags-org"
    )

    response = await client.put(f"/api/orgs/{org.slug}", headers=get_auth_headers(admin), json={"languages": languages})

    assert response.status_code == 200, response.text
    assert response.json()["languages"] == languages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "languages",
    [
        [""],
        ["de en"],
        [" de"],
        ["de\n"],
        ["d" * 200],
        ["de/en"],
        ["<de>"],
        ["d"],
        ["de-"],
        ["de-x"],
        ["de-Latn-DE-1996-x-private-abcdefgh-ijk"],
        ["de", "de"],
        ["pt-BR", "pt-br"],
        [f"de-{n:04d}" for n in range(51)],
    ],
    ids=[
        "empty",
        "inner-space",
        "leading-space",
        "trailing-newline",
        "200-characters",
        "slash",
        "angle-bracket",
        "one-letter",
        "trailing-hyphen",
        "one-character-part",
        "over-35-characters",
        "duplicate",
        "duplicate-by-case",
        "51-codes",
    ],
)
async def test_update_refuses_a_malformed_list_of_languages(
    client: AsyncClient, db_session: AsyncSession, languages: list[str]
):
    """A code that is no language tag, a duplicate and a list above 50 codes are refused, and nothing is stored."""
    admin, org = await create_test_user_with_org(
        db_session, email="badtags@test.com", role=UserRole.ORG_ADMIN, org_slug="badtags-org"
    )
    before = list(org.languages)

    response = await client.put(f"/api/orgs/{org.slug}", headers=get_auth_headers(admin), json={"languages": languages})

    assert response.status_code == 422, response.text
    await db_session.refresh(org)
    assert org.languages == before


@pytest.mark.asyncio
async def test_update_accepts_fifty_languages(client: AsyncClient, db_session: AsyncSession):
    admin, org = await create_test_user_with_org(
        db_session, email="fifty@test.com", role=UserRole.ORG_ADMIN, org_slug="fifty-org"
    )
    languages = [f"de-{n:04d}" for n in range(50)]

    response = await client.put(f"/api/orgs/{org.slug}", headers=get_auth_headers(admin), json={"languages": languages})

    assert response.status_code == 200, response.text
    assert response.json()["languages"] == languages


_UNREADABLE = {"__unreadable__": True}


async def _store_settings(db_session: AsyncSession, org_id: UUID, value: dict[str, Any]) -> None:
    """Write the settings as stored and forget what the session loaded, so the route reads the row."""
    await db_session.execute(update(Organization).where(Organization.id == org_id).values(settings=value))
    await db_session.commit()
    db_session.expunge_all()


async def _stored_settings(db_session: AsyncSession, org_id: UUID) -> Any:
    return (await db_session.execute(select(Organization.settings).where(Organization.id == org_id))).scalar_one()


@pytest.mark.asyncio
async def test_update_refuses_settings_that_carry_the_unreadable_marker(client: AsyncClient, db_session: AsyncSession):
    """The marker stands for stored settings nobody could read. It is never written."""
    admin, org = await create_test_user_with_org(
        db_session, email="marker@test.com", role=UserRole.ORG_ADMIN, org_slug="marker-org"
    )
    await _store_settings(db_session, org.id, {"auto_start_runs": False})

    response = await client.put(
        f"/api/orgs/{org.slug}",
        headers=get_auth_headers(admin),
        json={"settings": {**_UNREADABLE, "auto_start_runs": True}},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "ORG_SETTINGS_UNREADABLE"
    assert await _stored_settings(db_session, org.id) == {"auto_start_runs": False}


def _nested(levels: int) -> str:
    """The JSON text of an object nested ``levels`` deep, the outermost one counted."""
    return '{"a":' * (levels - 1) + "{}" + "}" * (levels - 1)


async def _put_settings_text(client: AsyncClient, admin: Any, slug: str, settings_text: str) -> Any:
    """PUT the settings as JSON text: some of these values no client library would encode."""
    return await client.put(
        f"/api/orgs/{slug}",
        headers={**get_auth_headers(admin), "Content-Type": "application/json"},
        content='{"settings": ' + settings_text + "}",
    )


# The settings route stores nothing the decoder of the stored value refuses,
# and nothing that breaks the write or a later read.
_REFUSED_SETTINGS = {
    # The marker key below the top level, under an object and under a list.
    "marker_in_object": ('{"retention": {"__unreadable__": true}}', "ORG_SETTINGS_UNREADABLE"),
    "marker_in_list": ('{"a": [1, {"b": {"__unreadable__": true}}]}', "ORG_SETTINGS_UNREADABLE"),
    # The key is refused whatever it holds: nobody reads a setting of that name.
    "marker_key_not_true": ('{"__unreadable__": false}', "ORG_SETTINGS_UNREADABLE"),
    "marker_key_nested_not_true": ('{"a": {"__unreadable__": "x"}}', "ORG_SETTINGS_UNREADABLE"),
    # Nesting: one level over the limit, and depths that would break every
    # later read of the organization with a 500.
    "one_level_too_deep": (_nested(MAX_SETTINGS_DEPTH + 1), "ORG_SETTINGS_NOT_STORABLE"),
    "one_list_too_deep": (
        '{"a":' + "[" * MAX_SETTINGS_DEPTH + "]" * MAX_SETTINGS_DEPTH + "}",
        "ORG_SETTINGS_NOT_STORABLE",
    ),
    "depth_900": (_nested(900), "ORG_SETTINGS_NOT_STORABLE"),
    "depth_9000": (_nested(9000), "ORG_SETTINGS_NOT_STORABLE"),
    # Numbers the request parser reads and JSON does not have.
    "nan": ('{"a": NaN}', "ORG_SETTINGS_NOT_STORABLE"),
    "infinity": ('{"a": [Infinity]}', "ORG_SETTINGS_NOT_STORABLE"),
    "float_overflow": ('{"a": {"b": -1e999}}', "ORG_SETTINGS_NOT_STORABLE"),
    # Strings PostgreSQL does not store in jsonb.
    "nul_in_string": ('{"a": "x\\u0000y"}', "ORG_SETTINGS_NOT_STORABLE"),
    "nul_in_key": ('{"a": {"b\\u0000": 1}}', "ORG_SETTINGS_NOT_STORABLE"),
    "lone_surrogate": ('{"a": ["\\ud800"]}', "ORG_SETTINGS_NOT_STORABLE"),
    "lone_surrogate_in_key": ('{"\\udfff": 1}', "ORG_SETTINGS_NOT_STORABLE"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(_REFUSED_SETTINGS))
async def test_update_refuses_settings_the_stored_value_could_not_be_read_from(
    client: AsyncClient, db_session: AsyncSession, case: str
):
    """422 before the write, and the stored settings stay as they were."""
    settings_text, error_code = _REFUSED_SETTINGS[case]
    slug = f"refused-{case.replace('_', '-')}"
    admin, org = await create_test_user_with_org(
        db_session, email=f"{case}@refused.test", role=UserRole.ORG_ADMIN, org_slug=slug
    )
    org_id = org.id
    await _store_settings(db_session, org_id, {"auto_start_runs": False})

    response = await _put_settings_text(client, admin, slug, settings_text)

    assert response.status_code == 422, response.text[:300]
    assert response.json()["error_code"] == error_code
    assert "__unreadable__" not in response.text
    assert await _stored_settings(db_session, org_id) == {"auto_start_runs": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("levels", [12_000, 200_000])
async def test_update_does_not_store_nesting_the_request_parser_refuses(
    client: AsyncClient, db_session: AsyncSession, levels: int
):
    """Nesting the parser of the request body gives up on never reaches the route. Nothing is stored."""
    slug = f"unparsed-{levels}"
    admin, org = await create_test_user_with_org(
        db_session, email=f"unparsed-{levels}@refused.test", role=UserRole.ORG_ADMIN, org_slug=slug
    )
    org_id = org.id
    await _store_settings(db_session, org_id, {"auto_start_runs": False})

    response = await _put_settings_text(client, admin, slug, _nested(levels))

    assert response.status_code == 400, response.text[:300]
    assert await _stored_settings(db_session, org_id) == {"auto_start_runs": False}


# What the route accepts, as JSON text, and what the row then decodes to.
_ACCEPTED_SETTINGS: dict[str, tuple[str, Any]] = {
    "ordinary": (
        '{"auto_start_runs": false, "retention": {"LK_A": 60}, "tags": ["a", {"b": null}]}',
        {"auto_start_runs": False, "retention": {"LK_A": 60}, "tags": ["a", {"b": None}]},
    ),
    # The deepest nesting the route takes, of objects and of lists.
    "deepest_objects": (_nested(MAX_SETTINGS_DEPTH), json.loads(_nested(MAX_SETTINGS_DEPTH))),
    "deepest_lists": (
        '{"a":' + "[" * (MAX_SETTINGS_DEPTH - 1) + "]" * (MAX_SETTINGS_DEPTH - 1) + "}",
        json.loads('{"a":' + "[" * (MAX_SETTINGS_DEPTH - 1) + "]" * (MAX_SETTINGS_DEPTH - 1) + "}"),
    ),
    # The longest integer the request parser reads is one the decoder reads as well.
    "longest_integer": ('{"a": ' + "9" * 4300 + "}", {"a": int("9" * 4300)}),
    # jsonb keeps a number, not its spelling: the float comes back as the integer it equals.
    "large_float": ('{"a": 1e300}', {"a": 10**300}),
    "smallest_float": ('{"a": 5e-324}', {"a": 5e-324}),
    "long_fraction": ('{"a": 1.' + "1" * 5000 + "}", {"a": 1.1111111111111112}),
    # A surrogate pair is one character, and the marker's name as a value is only text.
    "surrogate_pair": ('{"a": "\\ud83d\\ude00"}', {"a": "\U0001f600"}),
    "marker_name_as_value": (
        '{"a": "__unreadable__", "b": ["__unreadable__"]}',
        {"a": "__unreadable__", "b": ["__unreadable__"]},
    ),
    "long_string": ('{"a": "' + "x" * 100_000 + '"}', {"a": "x" * 100_000}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(_ACCEPTED_SETTINGS))
async def test_update_stores_settings_the_decoder_reads_back(client: AsyncClient, db_session: AsyncSession, case: str):
    """Written through the route, then the row is read back through the decoder and the route."""
    settings_text, decoded = _ACCEPTED_SETTINGS[case]
    slug = f"accepted-{case.replace('_', '-')}"
    admin, org = await create_test_user_with_org(
        db_session, email=f"{case}@accepted.test", role=UserRole.ORG_ADMIN, org_slug=slug
    )
    org_id = org.id

    response = await _put_settings_text(client, admin, slug, settings_text)

    assert response.status_code == 200, response.text[:300]
    db_session.expunge_all()
    stored = await _stored_settings(db_session, org_id)
    assert stored == decoded
    assert not is_unreadable_json(stored)

    after = await client.get(f"/api/orgs/{slug}", headers=get_auth_headers(admin))
    assert after.status_code == 200
    assert after.json()["settings_unreadable"] is False
    assert after.json()["settings"] == decoded


def test_the_depth_the_route_takes_is_far_below_what_the_decoder_reads():
    """The decoder reads many times the nesting the route stores, also from deep inside a call stack."""

    def from_a_deep_stack(frames: int, text: str) -> Any:
        return tolerant_json_loads(text) if frames == 0 else from_a_deep_stack(frames - 1, text)

    ten_times = _nested(MAX_SETTINGS_DEPTH * 10)

    # 500 frames: far more than the driver and the ORM put under the decoder.
    assert from_a_deep_stack(500, ten_times) == json.loads(ten_times)


@pytest.mark.asyncio
async def test_update_replaces_unreadable_settings_with_whole_new_ones(client: AsyncClient, db_session: AsyncSession):
    """A deliberate write of whole settings is the repair. Nothing of the marker stays."""
    admin, org = await create_test_user_with_org(
        db_session, email="repair@test.com", role=UserRole.ORG_ADMIN, org_slug="repair-org"
    )
    await _store_settings(db_session, org.id, dict(_UNREADABLE))

    response = await client.put(
        f"/api/orgs/{org.slug}", headers=get_auth_headers(admin), json={"settings": {"auto_start_runs": False}}
    )

    assert response.status_code == 200
    assert response.json()["settings"] == {"auto_start_runs": False}
    assert response.json()["settings_unreadable"] is False
    assert await _stored_settings(db_session, org.id) == {"auto_start_runs": False}

    after = await client.get(f"/api/orgs/{org.slug}", headers=get_auth_headers(admin))
    assert after.status_code == 200
    assert after.json()["settings"] == {"auto_start_runs": False}
    assert after.json()["settings_unreadable"] is False


@pytest.mark.asyncio
async def test_get_shows_unreadable_settings_as_empty_with_a_flag(client: AsyncClient, db_session: AsyncSession):
    """The marker of the decoder never leaves the server."""
    admin, org = await create_test_user_with_org(
        db_session, email="flag@test.com", role=UserRole.ORG_ADMIN, org_slug="flag-org"
    )
    await _store_settings(db_session, org.id, dict(_UNREADABLE))

    response = await client.get(f"/api/orgs/{org.slug}", headers=get_auth_headers(admin))

    assert response.status_code == 200
    assert response.json()["settings"] == {}
    assert response.json()["settings_unreadable"] is True


@pytest.mark.asyncio
async def test_get_of_readable_settings_carries_no_flag(client: AsyncClient, db_session: AsyncSession):
    admin, org = await create_test_user_with_org(
        db_session, email="noflag@test.com", role=UserRole.ORG_ADMIN, org_slug="noflag-org"
    )
    await _store_settings(db_session, org.id, {"auto_start_runs": False})

    response = await client.get(f"/api/orgs/{org.slug}", headers=get_auth_headers(admin))

    assert response.status_code == 200
    assert response.json()["settings"] == {"auto_start_runs": False}
    assert response.json()["settings_unreadable"] is False


@pytest.mark.asyncio
async def test_empty_whole_settings_repair_unreadable_ones(client: AsyncClient, db_session: AsyncSession):
    """Unreadable settings read as empty, and empty settings sent whole are still a write."""
    admin, org = await create_test_user_with_org(
        db_session, email="empty-repair@test.com", role=UserRole.ORG_ADMIN, org_slug="empty-repair-org"
    )
    await _store_settings(db_session, org.id, dict(_UNREADABLE))

    response = await client.put(f"/api/orgs/{org.slug}", headers=get_auth_headers(admin), json={"settings": {}})

    assert response.status_code == 200
    assert response.json()["settings_unreadable"] is False
    assert await _stored_settings(db_session, org.id) == {}


@pytest.mark.asyncio
async def test_update_without_settings_leaves_unreadable_settings_as_stored(
    client: AsyncClient, db_session: AsyncSession
):
    """A save without whole settings cannot repair the row: it is refused and changes nothing."""
    admin, org = await create_test_user_with_org(
        db_session, email="name-only@test.com", role=UserRole.ORG_ADMIN, org_slug="name-only-org"
    )
    await _store_settings(db_session, org.id, dict(_UNREADABLE))

    for body in ({"name": "Renamed"}, {"name": "Renamed", "settings": None}):
        response = await client.put(f"/api/orgs/{org.slug}", headers=get_auth_headers(admin), json=body)

        assert response.status_code == 409, response.text
        assert response.json()["error_code"] == "ORG_SETTINGS_UNREADABLE"
        assert "__unreadable__" not in response.text

    assert await _stored_settings(db_session, org.id) == _UNREADABLE
    stored_name = (await db_session.execute(select(Organization.name).where(Organization.id == org.id))).scalar_one()
    assert stored_name != "Renamed"


@pytest.mark.asyncio
async def test_no_response_about_an_organization_carries_the_marker(client: AsyncClient, db_session: AsyncSession):
    """Every organization route, on stored settings that cannot be read, searched for the marker's key."""
    admin, org = await create_test_user_with_org(
        db_session, email="leak@test.com", role=UserRole.ORG_ADMIN, org_slug="leak-org"
    )
    await _store_settings(db_session, org.id, dict(_UNREADABLE))
    headers = get_auth_headers(admin)
    base = f"/api/orgs/{org.slug}"

    responses = [
        await client.get(base, headers=headers),
        # Partial updates: refused, none of them carries whole settings.
        await client.put(base, headers=headers, json={"name": org.name}),
        await client.put(base, headers=headers, json={"name": "Leak Renamed"}),
        await client.put(base, headers=headers, json={"legal_imprint": "<p>Imprint</p>"}),
        await client.get(f"{base}/admin/retention", headers=headers),
        await client.put(
            f"{base}/admin/retention", headers=headers, json={"lk_a_days": 60, "lk_b_days": 120, "lk_c_days": 400}
        ),
        await client.put(base, headers=headers, json={"settings": dict(_UNREADABLE)}),
    ]
    assert await _stored_settings(db_session, org.id) == _UNREADABLE
    responses += [
        # The repair, last: every response before it was built from the unreadable row.
        await client.put(base, headers=headers, json={"settings": {"auto_start_runs": True}}),
        await client.get(base, headers=headers),
    ]

    assert [response.status_code for response in responses] == [200, 409, 409, 409, 409, 409, 422, 200, 200]
    for response in responses:
        assert "__unreadable__" not in response.text, response.request.url
        assert "__unreadable__" not in str(response.headers)


def test_readers_of_unreadable_settings_fall_to_their_defaults():
    """The accessors of the model: an empty object for the readers, and the flag for those that must know."""
    broken = Organization(name="Broken", slug="broken", settings=dict(_UNREADABLE))
    merged = Organization(name="Merged", slug="merged", settings={**_UNREADABLE, "auto_start_runs": False})
    no_object = Organization(name="List", slug="list", settings=["de"])
    healthy = Organization(name="Healthy", slug="healthy", settings={"auto_start_runs": False})

    assert (broken.readable_settings, broken.settings_unreadable) == ({}, True)
    assert (merged.readable_settings, merged.settings_unreadable) == ({}, True)
    assert (no_object.readable_settings, no_object.settings_unreadable) == ({}, False)
    assert (healthy.readable_settings, healthy.settings_unreadable) == ({"auto_start_runs": False}, False)

    # An organization without settings gets automatic starts on and German mails.
    assert auto_start_enabled(broken) is auto_start_enabled(Organization(name="New", slug="new", settings={})) is True
    assert auto_start_enabled(healthy) is False
    assert _org_language(broken) == _org_language(Organization(name="New", slug="new", settings={}))


@pytest.mark.asyncio
async def test_retention_page_does_not_show_unreadable_settings_as_periods(
    client: AsyncClient, db_session: AsyncSession
):
    """Unreadable settings have no effective period: the page answers 409, not the defaults."""
    admin, org = await create_test_user_with_org(
        db_session, email="retention-read@test.com", role=UserRole.ORG_ADMIN, org_slug="retention-read-org"
    )
    await _store_settings(db_session, org.id, dict(_UNREADABLE))

    response = await client.get(f"/api/orgs/{org.slug}/admin/retention", headers=get_auth_headers(admin))

    assert response.status_code == 409
    assert response.json()["error_code"] == "ORG_SETTINGS_UNREADABLE"


@pytest.mark.asyncio
async def test_retention_page_does_not_save_into_unreadable_settings(client: AsyncClient, db_session: AsyncSession):
    """Saving a period merges into the stored settings, and would write the marker back with it."""
    admin, org = await create_test_user_with_org(
        db_session, email="retention-write@test.com", role=UserRole.ORG_ADMIN, org_slug="retention-write-org"
    )
    await _store_settings(db_session, org.id, dict(_UNREADABLE))

    response = await client.put(
        f"/api/orgs/{org.slug}/admin/retention",
        headers=get_auth_headers(admin),
        json={"lk_a_days": 60, "lk_b_days": 120, "lk_c_days": 400},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "ORG_SETTINGS_UNREADABLE"
    assert "replaced whole" in response.json()["detail"]
    assert await _stored_settings(db_session, org.id) == _UNREADABLE
