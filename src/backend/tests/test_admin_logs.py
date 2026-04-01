"""Admin log viewer endpoint: access by role, filters, pagination, CSV export, rotated files and tenant isolation."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from app.routers.admin import get_log_dir
from tests.factories import create_test_user_with_org, get_auth_headers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_log_entry(log_dir: str, category: str, entry: dict) -> None:
    """Write a JSON log line to the appropriate category file."""
    file_map = {
        "audit": "audit.log",
        "app": "application.log",
        "error": "error.log",
        "access": "access.log",
    }
    file_name = file_map[category]
    file_path = Path(log_dir) / file_name
    with file_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _make_entry(
    event: str,
    org_id: str,
    category: str = "audit",
    actor: dict | None = None,
    target: dict | None = None,
    timestamp: str | None = None,
) -> dict:
    return {
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
        "category": category,
        "event": event,
        "organization_id": org_id,
        "actor": actor or {"user_id": "test-user", "role": "org_admin", "ip": "127.0.0.1"},
        "target": target or {"entity": "user", "id": "abc"},
        "integrity_hash": "0" * 64,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_log_dir(tmp_path: Path):
    """A temporary log directory. Each test points get_log_dir at it."""
    return str(tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_logs_org_admin_access(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """org_admin receives 200 with an empty list when no log files exist."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="test-org"
    )
    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_logs_non_admin_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """coordinator role is denied with 403."""
    from app.main import app

    coord, org = await create_test_user_with_org(
        db_session, email="coord@test.com", role=UserRole.COORDINATOR, org_slug="test-org"
    )
    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(coord),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_logs_unauthenticated(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """Unauthenticated request is rejected with 401."""
    from app.main import app

    _, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="test-org"
    )
    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(f"/api/orgs/{org.slug}/admin/logs")
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_logs_category_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """category query param returns only entries from the requested log file."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="test-org"
    )
    org_id = str(org.id)

    _write_log_entry(tmp_log_dir, "audit", _make_entry("user_login", org_id, category="audit"))
    _write_log_entry(tmp_log_dir, "app", _make_entry("app.event", org_id, category="app"))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs?category=audit",
            headers=get_auth_headers(admin),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["event"] == "user_login"


@pytest.mark.asyncio
async def test_list_logs_date_range_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """from_date and to_date narrow results to the specified window."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="test-org"
    )
    org_id = str(org.id)

    old_ts = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    recent_ts = datetime.now(UTC).isoformat()

    _write_log_entry(tmp_log_dir, "audit", _make_entry("old_event", org_id, timestamp=old_ts))
    _write_log_entry(tmp_log_dir, "audit", _make_entry("recent_event", org_id, timestamp=recent_ts))

    from_dt = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs?from_date={from_dt}",
            headers=get_auth_headers(admin),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    data = response.json()
    events = [i["event"] for i in data["items"]]
    assert "recent_event" in events
    assert "old_event" not in events


@pytest.mark.asyncio
async def test_list_logs_search_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """search param performs case-insensitive substring match on event/actor/target."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="test-org"
    )
    org_id = str(org.id)

    _write_log_entry(tmp_log_dir, "audit", _make_entry("user_login", org_id))
    _write_log_entry(tmp_log_dir, "audit", _make_entry("score_created", org_id))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs?search=user_login",
            headers=get_auth_headers(admin),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["event"] == "user_login"


@pytest.mark.asyncio
async def test_list_logs_pagination(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """skip and limit parameters page through results correctly."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="test-org"
    )
    org_id = str(org.id)

    for i in range(5):
        _write_log_entry(tmp_log_dir, "audit", _make_entry(f"event_{i}", org_id))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        resp1 = await client.get(
            f"/api/orgs/{org.slug}/admin/logs?skip=0&limit=2",
            headers=get_auth_headers(admin),
        )
        resp2 = await client.get(
            f"/api/orgs/{org.slug}/admin/logs?skip=2&limit=2",
            headers=get_auth_headers(admin),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert resp1.status_code == 200
    d1 = resp1.json()
    assert d1["total"] == 5
    assert len(d1["items"]) == 2

    assert resp2.status_code == 200
    d2 = resp2.json()
    assert d2["total"] == 5
    assert len(d2["items"]) == 2

    events1 = {i["event"] for i in d1["items"]}
    events2 = {i["event"] for i in d2["items"]}
    assert events1.isdisjoint(events2)


@pytest.mark.asyncio
async def test_list_logs_csv_export(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """format=csv returns a CSV response with correct Content-Type."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="test-org"
    )
    org_id = str(org.id)
    _write_log_entry(tmp_log_dir, "audit", _make_entry("user_login", org_id))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs?format=csv",
            headers=get_auth_headers(admin),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "user_login" in response.text


@pytest.mark.asyncio
async def test_list_logs_cross_org_isolation(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """Log entries from another org are not visible to org_admin of a different org."""
    from app.main import app

    admin_a, org_a = await create_test_user_with_org(
        db_session, email="admin-a@test.com", role=UserRole.ORG_ADMIN, org_slug="org-a"
    )
    _, org_b = await create_test_user_with_org(
        db_session, email="admin-b@test.com", role=UserRole.ORG_ADMIN, org_slug="org-b"
    )

    # Both organizations write into the same log file
    _write_log_entry(tmp_log_dir, "audit", _make_entry("secret_event", str(org_b.id)))
    _write_log_entry(tmp_log_dir, "audit", _make_entry("org_a_event", str(org_a.id)))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org_a.slug}/admin/logs",
            headers=get_auth_headers(admin_a),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    data = response.json()
    events = [i["event"] for i in data["items"]]
    assert "org_a_event" in events
    assert "secret_event" not in events


@pytest.mark.asyncio
async def test_list_logs_excludes_entries_without_org_id(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """Entries lacking organization_id (e.g. generic app/error logs) must not leak across tenants."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="org-x"
    )

    orgless = _make_entry("orgless_event", str(org.id))
    orgless.pop("organization_id")
    _write_log_entry(tmp_log_dir, "app", orgless)
    _write_log_entry(tmp_log_dir, "audit", _make_entry("scoped_event", str(org.id)))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    events = [i["event"] for i in response.json()["items"]]
    assert "scoped_event" in events
    assert "orgless_event" not in events


@pytest.mark.asyncio
async def test_csv_export_neutralizes_formula_injection(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """Cells starting with =, +, -, @ must be prefixed with ' so spreadsheets treat them as text."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="admin@test.com", role=UserRole.ORG_ADMIN, org_slug="org-csv"
    )

    _write_log_entry(
        tmp_log_dir,
        "audit",
        _make_entry("=cmd|' /C calc'!A1", str(org.id)),
    )

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs?format=csv",
            headers=get_auth_headers(admin),
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    body = response.text
    assert "=cmd" not in body.split("\r\n", 1)[1] or "'=cmd" in body
    assert "'=cmd" in body


@pytest.mark.asyncio
async def test_list_logs_reads_rotated_files_and_skips_old_ones_on_a_date_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """The organization log reads rotated ``audit.log.YYYY-MM-DD`` files too.

    A ``from_date`` filter still finds an entry two days back, and an entry before it stays out.
    """
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="rotated@test.com", role=UserRole.ORG_ADMIN, org_slug="rotated-org"
    )
    now = datetime.now(UTC)
    for days_back, event in ((40, "very_old_event"), (2, "rotated_event")):
        stamp = now - timedelta(days=days_back)
        rotated = Path(tmp_log_dir) / f"audit.log.{stamp.date().isoformat()}"
        rotated.write_text(
            json.dumps(_make_entry(event, str(org.id), timestamp=stamp.isoformat())) + "\n",
            encoding="utf-8",
        )
    _write_log_entry(tmp_log_dir, "audit", _make_entry("current_event", str(org.id)))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        everything = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
            params={"category": "audit"},
        )
        last_week = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
            params={"category": "audit", "from_date": (now - timedelta(days=7)).isoformat()},
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert everything.status_code == 200
    assert [item["event"] for item in everything.json()["items"]] == [
        "current_event",
        "rotated_event",
        "very_old_event",
    ]
    assert last_week.status_code == 200
    assert [item["event"] for item in last_week.json()["items"]] == ["current_event", "rotated_event"]


# ---------------------------------------------------------------------------
# The export holds the whole period, and the compressed rotated files belong to the log.
# ---------------------------------------------------------------------------


def _write_rotated(log_dir: str, file_name: str, day: datetime, entries: list[dict], *, compress: bool) -> Path:
    """Write one rotated file the way rotation and ``log-cleanup.sh`` leave it."""
    import gzip

    text = "".join(json.dumps(entry) + "\n" for entry in entries)
    path = Path(log_dir) / f"{file_name}.{day.date().isoformat()}"
    if compress:
        path = path.with_name(path.name + ".gz")
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _csv_rows(text: str) -> list[list[str]]:
    import csv

    rows = list(csv.reader(text.splitlines()))
    return rows[1:]


@pytest.mark.asyncio
async def test_csv_export_holds_every_match_of_the_period_not_one_page(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """The export ignores the page size and holds every match of the period."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="export-all@test.com", role=UserRole.ORG_ADMIN, org_slug="export-all"
    )
    start = datetime.now(UTC) - timedelta(hours=3)
    for i in range(130):
        stamp = (start + timedelta(seconds=i)).isoformat()
        _write_log_entry(tmp_log_dir, "audit", _make_entry(f"event_{i:03d}", str(org.id), timestamp=stamp))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
            params={"format": "csv", "limit": 10, "skip": 5},
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    rows = _csv_rows(response.text)
    assert len(rows) == 130
    # Oldest first, the order of the integrity chain.
    assert [row[2] for row in rows[:2]] == ["event_000", "event_001"]
    assert rows[-1][2] == "event_129"


@pytest.mark.asyncio
async def test_csv_export_applies_category_search_and_period_like_the_view(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """Category, search and period of the view narrow the export, merged in time order."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="export-filter@test.com", role=UserRole.ORG_ADMIN, org_slug="export-filter"
    )
    org_id = str(org.id)
    now = datetime.now(UTC)
    _write_log_entry(
        tmp_log_dir,
        "app",
        _make_entry("export_app_b", org_id, "app", timestamp=(now - timedelta(minutes=2)).isoformat()),
    )
    _write_log_entry(
        tmp_log_dir,
        "error",
        _make_entry("export_error_a", org_id, "error", timestamp=(now - timedelta(minutes=3)).isoformat()),
    )
    _write_log_entry(
        tmp_log_dir,
        "error",
        _make_entry("export_error_c", org_id, "error", timestamp=(now - timedelta(minutes=1)).isoformat()),
    )
    _write_log_entry(
        tmp_log_dir, "audit", _make_entry("export_audit", org_id, timestamp=(now - timedelta(minutes=1)).isoformat())
    )
    _write_log_entry(
        tmp_log_dir, "app", _make_entry("other_app", org_id, "app", timestamp=(now - timedelta(minutes=1)).isoformat())
    )
    _write_log_entry(
        tmp_log_dir,
        "app",
        _make_entry("export_app_old", org_id, "app", timestamp=(now - timedelta(days=3)).isoformat()),
    )

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
            params=[
                ("format", "csv"),
                ("category", "app"),
                ("category", "error"),
                ("search", "EXPORT_"),
                ("from_date", (now - timedelta(hours=1)).isoformat()),
            ],
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    assert [row[2] for row in _csv_rows(response.text)] == ["export_error_a", "export_app_b", "export_error_c"]


@pytest.mark.asyncio
async def test_list_logs_and_export_read_compressed_rotated_files(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """A compressed rotated file is part of the log, in the view and in the export."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="gz@test.com", role=UserRole.ORG_ADMIN, org_slug="gz-org"
    )
    org_id = str(org.id)
    now = datetime.now(UTC)
    for days_back in (30, 20, 10):
        day = now - timedelta(days=days_back)
        _write_rotated(
            tmp_log_dir,
            "audit.log",
            day,
            [_make_entry(f"compressed_{days_back}", org_id, timestamp=day.isoformat())],
            compress=True,
        )
    plain_day = now - timedelta(days=2)
    _write_rotated(
        tmp_log_dir,
        "audit.log",
        plain_day,
        [_make_entry("plain_2", org_id, timestamp=plain_day.isoformat())],
        compress=False,
    )

    period = {"from_date": (now - timedelta(days=25)).isoformat(), "to_date": (now - timedelta(days=5)).isoformat()}
    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        listed = await client.get(
            f"/api/orgs/{org.slug}/admin/logs", headers=get_auth_headers(admin), params={"category": "audit"}
        )
        in_period = await client.get(
            f"/api/orgs/{org.slug}/admin/logs", headers=get_auth_headers(admin), params={"category": "audit", **period}
        )
        exported = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
            params={"category": "audit", "format": "csv", **period},
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert [item["event"] for item in listed.json()["items"]] == [
        "plain_2",
        "compressed_10",
        "compressed_20",
        "compressed_30",
    ]
    assert [item["event"] for item in in_period.json()["items"]] == ["compressed_10", "compressed_20"]
    assert [row[2] for row in _csv_rows(exported.text)] == ["compressed_20", "compressed_10"]


@pytest.mark.asyncio
async def test_list_logs_search_matches_the_request_id(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """The view shows the request id, so its search finds it."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="reqid@test.com", role=UserRole.ORG_ADMIN, org_slug="reqid-org"
    )
    entry = _make_entry("user_login", str(org.id))
    entry["request_id"] = "5f0c2e1a-feed-4bee-a11e-123456789abc"
    _write_log_entry(tmp_log_dir, "audit", entry)
    _write_log_entry(tmp_log_dir, "audit", _make_entry("user_logout", str(org.id)))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs", headers=get_auth_headers(admin), params={"search": "FEED-4bee"}
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert [item["event"] for item in response.json()["items"]] == ["user_login"]


@pytest.mark.asyncio
async def test_list_logs_pages_oldest_first_on_request(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """``order=asc`` sorts the page by time, oldest first, with the same total."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="order@test.com", role=UserRole.ORG_ADMIN, org_slug="order-org"
    )
    start = datetime.now(UTC) - timedelta(hours=1)
    for i in range(5):
        _write_log_entry(
            tmp_log_dir,
            "audit",
            _make_entry(f"e{i}", str(org.id), timestamp=(start + timedelta(minutes=i)).isoformat()),
        )

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
            params={"order": "asc", "skip": 1, "limit": 2},
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    data = response.json()
    assert data["total"] == 5
    assert [item["event"] for item in data["items"]] == ["e1", "e2"]


def test_a_rotated_day_reads_the_plain_file_while_its_gz_is_written(tmp_path: Path):
    """While gzip runs, the plain file is complete and the ``.gz`` is not."""
    from app.routers.admin import _category_files

    day = datetime(2026, 11, 1, tzinfo=UTC)
    plain = _write_rotated(str(tmp_path), "audit.log", day, [{"event": "x"}], compress=False)
    _write_rotated(str(tmp_path), "audit.log", day, [{"event": "x"}], compress=True)
    other = _write_rotated(str(tmp_path), "audit.log", day - timedelta(days=1), [{"event": "y"}], compress=True)
    (tmp_path / "audit.log.bak").write_text("{}\n", encoding="utf-8")

    assert _category_files(tmp_path, "audit.log", None) == [other, plain, tmp_path / "audit.log"]


def test_a_truncated_gz_file_yields_what_it_holds(tmp_path: Path):
    """A broken ``.gz`` neither breaks the view nor hides the other files."""
    from app.routers.admin import _read_log_entries

    day = datetime(2026, 11, 1, tzinfo=UTC)
    complete = _write_rotated(
        str(tmp_path), "audit.log", day, [{"event": f"e{i}", "pad": "x" * 200} for i in range(300)], compress=True
    )
    truncated = complete.read_bytes()
    complete.write_bytes(truncated[: len(truncated) // 2])
    _write_rotated(str(tmp_path), "audit.log", day + timedelta(days=1), [{"event": "next_day"}], compress=True)

    events = [entry["event"] for entry in _read_log_entries(str(tmp_path), ["audit"])]

    # The readable part of the broken day comes first, then the next day.
    assert events[0] == "e0"
    assert events[-1] == "next_day"
    assert len(events) > 1


def test_the_csv_export_streams_before_the_entries_are_read():
    """The first chunk leaves before the last entry is read, so a long period is no memory peak."""
    from app.routers.admin import _CSV_ROWS_PER_CHUNK, _iter_csv

    consumed = 0

    def entries():
        nonlocal consumed
        for i in range(_CSV_ROWS_PER_CHUNK * 3):
            consumed += 1
            yield {"timestamp": f"2026-11-01T00:00:{i % 60:02d}Z", "event": f"e{i}", "category": "audit"}

    chunks = _iter_csv(entries())
    first = next(chunks)

    assert first.startswith("timestamp,category,event")
    assert consumed == _CSV_ROWS_PER_CHUNK
    rest = "".join(chunks)
    assert len((first + rest).splitlines()) == _CSV_ROWS_PER_CHUNK * 3 + 1


@pytest.mark.asyncio
async def test_search_also_matches_the_event_keys_of_matching_labels(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """The labels live in the interface. "Bereinig" finds the media purge through its key."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="labels@test.com", role=UserRole.ORG_ADMIN, org_slug="labels-org"
    )
    for event in ("org.media_purged", "user_login", "retention_swept"):
        _write_log_entry(tmp_log_dir, "audit", _make_entry(event, str(org.id)))

    params = [("search", "Bereinig"), ("search_event", "org.media_purged"), ("search_event", "retention_swept")]
    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        listed = await client.get(f"/api/orgs/{org.slug}/admin/logs", headers=get_auth_headers(admin), params=params)
        exported = await client.get(
            f"/api/orgs/{org.slug}/admin/logs", headers=get_auth_headers(admin), params=[*params, ("format", "csv")]
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert sorted(item["event"] for item in listed.json()["items"]) == ["org.media_purged", "retention_swept"]
    assert sorted(row[2] for row in _csv_rows(exported.text)) == ["org.media_purged", "retention_swept"]


@pytest.mark.asyncio
async def test_a_media_purge_entry_passes_the_kind_and_the_count(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_log_dir: str,
):
    """The log names which kind of media a purge deleted and how many files.

    The audit entry holds both as its own keys. Another event with a key of
    the same name passes neither, and a value of the wrong type is dropped.
    """
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="purge-log@test.com", role=UserRole.ORG_ADMIN, org_slug="purge-log-org"
    )
    base = datetime.now(UTC)

    def entry(event: str, offset: int, **extra: object) -> dict:
        made = _make_entry(event, str(org.id), timestamp=(base + timedelta(seconds=offset)).isoformat())
        return {**made, **extra}

    _write_log_entry(tmp_log_dir, "audit", entry("org.media_purged", 0, media_type="photo", purged=12))
    _write_log_entry(tmp_log_dir, "audit", entry("org.media_purged", 1, media_type="audio", purged=0))
    _write_log_entry(tmp_log_dir, "audit", entry("org.media_purged", 2, media_type=["video"], purged=True))
    _write_log_entry(tmp_log_dir, "audit", entry("user_login", 3, media_type="photo", purged=3))

    app.dependency_overrides[get_log_dir] = lambda: tmp_log_dir
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs", headers=get_auth_headers(admin), params={"order": "asc"}
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(item["event"], item["media_type"], item["purged"]) for item in items] == [
        ("org.media_purged", "photo", 12),
        ("org.media_purged", "audio", 0),
        ("org.media_purged", None, None),
        ("user_login", None, None),
    ]


@pytest.fixture
def configured_logging():
    """Audit logging into a temp dir (same shape as test_media_purge.py)."""
    import contextlib
    import logging as _logging
    import tempfile

    import structlog

    import app.logging as _log_module
    from app.logging import configure_logging
    from app.services.audit import reset_integrity_chain

    with tempfile.TemporaryDirectory() as tmpdir:
        structlog.reset_defaults()
        _log_module._logging_configured = False
        configure_logging(environment="development", log_dir=tmpdir)
        reset_integrity_chain()

        yield tmpdir

        for name in ("audit", "app", "error", "access", ""):
            lg = _logging.getLogger(name)
            for h in list(lg.handlers):
                with contextlib.suppress(Exception):
                    h.close()
            lg.handlers.clear()
        _log_module._logging_configured = False
        structlog.reset_defaults()


@pytest.mark.asyncio
async def test_the_purge_endpoint_writes_an_entry_the_log_shows_with_kind_and_count(
    client: AsyncClient,
    db_session: AsyncSession,
    configured_logging: str,
):
    """A purge through the endpoint writes an entry that the organization's log shows with kind and count."""
    from app.main import app

    admin, org = await create_test_user_with_org(
        db_session, email="purge-e2e@test.com", role=UserRole.ORG_ADMIN, org_slug="purge-e2e-org"
    )
    purge = await client.post(
        f"/api/orgs/{org.slug}/admin/media/purge",
        headers=get_auth_headers(admin),
        json={"type": "video", "confirm_slug": org.slug},
    )
    assert purge.status_code == 200
    assert purge.json() == {"type": "video", "purged": 0}

    app.dependency_overrides[get_log_dir] = lambda: configured_logging
    try:
        response = await client.get(
            f"/api/orgs/{org.slug}/admin/logs",
            headers=get_auth_headers(admin),
            params={"event_type": "org.media_purged"},
        )
    finally:
        del app.dependency_overrides[get_log_dir]

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["media_type"] == "video"
    assert items[0]["purged"] == 0
    assert items[0]["actor"]["user_id"] == str(admin.id)
    assert items[0]["target"]["slug"] == org.slug
