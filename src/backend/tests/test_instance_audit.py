"""
The operator's own audit log, ``GET /api/instance/audit``.

Only `instance.*` events are returned, never an organization's own event.
They come newest first, paged and filterable by `event`. An organization token is refused with 403.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.instance import create_instance_admin
from app.auth.tokens import create_instance_admin_token
from app.routers.admin import get_log_dir
from tests.factories import create_test_user_with_org, get_auth_headers

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "OperatorPass123!"


def _write_log_entry(log_dir: str, entry: dict) -> None:
    """Append one JSON line to audit.log, the file `_read_log_entries` reads."""
    file_path = Path(log_dir) / "audit.log"
    with file_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _instance_entry(
    event: str,
    *,
    operator_id: str = "11111111-1111-1111-1111-111111111111",
    email: str = "operator@example.com",
    target_slug: str | None = "some-org",
    timestamp: str | None = None,
) -> dict:
    """An `instance.*` audit entry as record_audit_event/actor_from_instance_admin emit it."""
    entry: dict = {
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
        "category": "audit",
        "event": event,
        "actor": {"instance_admin_id": operator_id, "email": email, "ip": "127.0.0.1"},
        "integrity_hash": "0" * 64,
    }
    if target_slug is not None:
        entry["target_slug"] = target_slug
    return entry


def _organization_entry(
    event: str = "user_login",
    *,
    organization_id: str = "22222222-2222-2222-2222-222222222222",
    timestamp: str | None = None,
) -> dict:
    """An organization-scoped audit entry. It must never appear in the operator's log."""
    return {
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
        "category": "audit",
        "event": event,
        "organization_id": organization_id,
        "actor": {"user_id": "test-user", "role": "org_admin", "ip": "127.0.0.1"},
        "target": {"entity": "user", "id": "abc"},
        "integrity_hash": "0" * 64,
    }


async def _operator_headers(db_session: AsyncSession) -> dict[str, str]:
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    return {"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"}


@pytest.fixture
def tmp_log_dir(tmp_path: Path) -> str:
    return str(tmp_path)


@pytest.mark.asyncio
async def test_operator_events_appear_organization_events_do_not(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """instance.* events show with time, event, operator and target slug. An organization event is filtered out."""
    from app.main import app

    headers = await _operator_headers(db_session)
    _write_log_entry(tmp_log_dir, _instance_entry("instance.org_created", target_slug="augsburg"))
    _write_log_entry(tmp_log_dir, _organization_entry("user_login"))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get("/api/instance/audit", headers=headers)
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["event"] == "instance.org_created"
    assert item["operator_id"] == "11111111-1111-1111-1111-111111111111"
    assert item["operator_email"] == OPERATOR_EMAIL
    assert item["target_slug"] == "augsburg"
    assert item["timestamp"] is not None


@pytest.mark.asyncio
async def test_rotated_files_are_read_too(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """Events from rotated ``audit.log.YYYY-MM-DD`` files stay in the operator log.

    Reading only ``audit.log`` would drop every event before the last midnight
    rotation from the console. A file with another suffix is not a rotated
    sibling and stays out.
    """
    from app.main import app

    headers = await _operator_headers(db_session)
    now = datetime.now(UTC)
    two_days_ago = now - timedelta(days=2)
    rotated = Path(tmp_log_dir) / f"audit.log.{two_days_ago.date().isoformat()}"
    rotated.write_text(
        json.dumps(_instance_entry("instance.org_created", target_slug="old-org", timestamp=two_days_ago.isoformat()))
        + "\n",
        encoding="utf-8",
    )
    (Path(tmp_log_dir) / "audit.log.bak").write_text(
        json.dumps(_instance_entry("instance.org_created", target_slug="backup-copy")) + "\n",
        encoding="utf-8",
    )
    _write_log_entry(tmp_log_dir, _instance_entry("instance.org_updated", target_slug="new-org"))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get("/api/instance/audit", headers=headers)
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    slugs = [item["target_slug"] for item in response.json()["items"]]
    assert slugs == ["new-org", "old-org"]


@pytest.mark.asyncio
async def test_newest_first_and_paging(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """Entries sort newest first, and offset/limit page through them."""
    from app.main import app

    headers = await _operator_headers(db_session)
    base = datetime.now(UTC)
    for i in range(5):
        _write_log_entry(
            tmp_log_dir,
            _instance_entry(
                "instance.org_updated",
                target_slug=f"org-{i}",
                timestamp=(base + timedelta(minutes=i)).isoformat(),
            ),
        )

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        first_page = await client.get(
            "/api/instance/audit",
            headers=headers,
            params={"limit": 2, "offset": 0},
        )
        second_page = await client.get(
            "/api/instance/audit",
            headers=headers,
            params={"limit": 2, "offset": 2},
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert first_page.status_code == 200
    first_data = first_page.json()
    assert first_data["total"] == 5
    assert [i["target_slug"] for i in first_data["items"]] == ["org-4", "org-3"]

    second_data = second_page.json()
    assert [i["target_slug"] for i in second_data["items"]] == ["org-2", "org-1"]


@pytest.mark.asyncio
async def test_event_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """The `event` query filters to an exact event type."""
    from app.main import app

    headers = await _operator_headers(db_session)
    _write_log_entry(tmp_log_dir, _instance_entry("instance.org_created", target_slug="augsburg"))
    _write_log_entry(tmp_log_dir, _instance_entry("instance.org_archived", target_slug="muenchen"))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            "/api/instance/audit",
            headers=headers,
            params={"event": "instance.org_archived"},
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["event"] == "instance.org_archived"
    assert data["items"][0]["target_slug"] == "muenchen"


@pytest.mark.asyncio
async def test_organization_token_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """An organization token gets 403, not the operator's log."""
    from app.main import app

    user, _org = await create_test_user_with_org(db_session, email="member@realm.example.com", org_slug="realm-org")

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            "/api/instance/audit",
            headers=get_auth_headers(user),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_rejected(
    client: AsyncClient,
    tmp_log_dir: str,
):
    """No credentials at all gets 401."""
    from app.main import app

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get("/api/instance/audit")
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_old_organizations_prefixed_path_does_not_answer(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """The route lives on operator_router at /api/instance, not on the
    organizations router mounted at /api/instance/organizations. That
    sibling path must not answer 200."""
    from app.main import app

    headers = await _operator_headers(db_session)

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get("/api/instance/organizations/audit", headers=headers)
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code != 200
