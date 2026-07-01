"""Org-admin-configurable retention periods.

Three layers:
- Resolver and validation (pure): the precedence order and the bounds. A period is not shorter than the backup rotation window.
- Sweep behaviour (DB and S3 store): an org override changes the cutoff. Without an override the sweep uses the class defaults unchanged.
- Endpoints (authed client): GET and PUT, role checks and the 422 guardrail.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.challenge import ChallengeType
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.submission import Submission
from app.models.team import Team
from app.models.user import UserRole
from app.services.gdpr import DeletionClass
from app.services.retention_config import (
    RetentionValidationError,
    apply_org_overrides,
    effective_retention_days,
    instance_default_days,
    read_org_override,
    resolve_source,
    retention_min_days,
    validate_period,
)
from app.services.retention_tasks import sweep_run_media
from app.storage import get_minio_client, upload_file
from tests.factories import create_test_user, get_auth_headers

LK_B_DEFAULT = DeletionClass.LK_B.retention_days  # 90
PNG = b"\x89PNG\r\n\x1a\n" + b"configurable-retention-test"


def _key() -> str:
    return f"retention-{uuid.uuid4().hex}.png"


def _object_exists(key: str) -> bool:
    from minio.error import S3Error

    try:
        get_minio_client().stat_object(settings.storage.bucket, key)
        return True
    except S3Error:
        return False


# ---------------------------------------------------------------------------
# Resolver & validation (pure)
# ---------------------------------------------------------------------------


def test_no_override_returns_constant():
    """Nothing configured resolves to the class constant.

    A guest account (LK_A) keeps 90 days. It is the only way back into a
    finished run, and scoring routinely lands days after the run ends.
    """
    assert effective_retention_days(DeletionClass.LK_A, None) == 90
    assert effective_retention_days(DeletionClass.LK_B, {}) == 90
    assert effective_retention_days(DeletionClass.LK_C, {"retention": {}}) == 365
    assert instance_default_days(DeletionClass.LK_B) == 90


def test_org_override_wins():
    settings_json = {"retention": {"LK_B": 120}}
    assert effective_retention_days(DeletionClass.LK_B, settings_json) == 120
    assert resolve_source(settings_json, DeletionClass.LK_B) == "override"
    # An untouched class still resolves to its default.
    assert effective_retention_days(DeletionClass.LK_A, settings_json) == 90
    assert resolve_source(settings_json, DeletionClass.LK_A) == "default"


@pytest.mark.parametrize(
    "value",
    [None, "ninety", -5, 0, True, {"nested": 1}],
)
def test_read_org_override_tolerates_bad_values(value):
    """A malformed override falls back to the default."""
    assert read_org_override({"retention": {"LK_B": value}}, DeletionClass.LK_B) is None
    assert effective_retention_days(DeletionClass.LK_B, {"retention": {"LK_B": value}}) == 90


def test_validate_period_bounds():
    floor = retention_min_days()  # backup_rotation_days, default 30
    validate_period(floor)  # ok at the floor
    validate_period(3650)  # ok at the ceiling
    with pytest.raises(RetentionValidationError):
        validate_period(floor - 1)  # below backup rotation → rejected
    with pytest.raises(RetentionValidationError):
        validate_period(3651)  # above ceiling
    with pytest.raises(RetentionValidationError):
        validate_period(True)  # bool is not a valid int period


def test_apply_org_overrides_set_and_clear():
    """Setting adds an override and clearing it with None drops the whole section.

    A class that is not configurable is ignored. An out-of-bounds value raises.
    """
    s1 = apply_org_overrides({}, {DeletionClass.LK_B: 120})
    assert s1 == {"retention": {"LK_B": 120}}
    s2 = apply_org_overrides(s1, {DeletionClass.LK_B: None})
    assert "retention" not in s2
    s3 = apply_org_overrides({}, {DeletionClass.LK_D: 5})
    assert s3 == {}
    with pytest.raises(RetentionValidationError):
        apply_org_overrides({}, {DeletionClass.LK_B: 1})


def test_apply_org_overrides_does_not_mutate_input():
    original = {"retention": {"LK_B": 90}, "other": 1}
    apply_org_overrides(original, {DeletionClass.LK_C: 400})
    assert original == {"retention": {"LK_B": 90}, "other": 1}


# ---------------------------------------------------------------------------
# Sweep behaviour (DB + S3 store)
#
# _run_with_media uploads, so every test that calls it takes the bucket from
# the storage_bucket fixture (tests/conftest.py). Nothing else makes it: this
# file can be the first one a worker runs against a Garage without a bucket.
# ---------------------------------------------------------------------------


async def _run_with_media(db: AsyncSession, org, *, ended_days_ago: int, now: datetime) -> str:
    """Create an ENDED run with one photo submission; return the object key."""
    run = Run(
        organization_id=org.id,
        name="R",
        state=RunState.ENDED,
        join_code=uuid.uuid4().hex[:6].upper(),
        ended_at=now - timedelta(days=ended_days_ago),
    )
    db.add(run)
    await db.flush()
    team = Team(organization_id=org.id, run_id=run.id, share_code=uuid.uuid4().hex[:8].upper(), name="T")
    db.add(team)
    await db.flush()
    rs = RunStation(organization_id=org.id, run_id=run.id, sort_order=0)
    db.add(rs)
    await db.flush()
    rc = RunChallenge(
        organization_id=org.id,
        run_id=run.id,
        run_station_id=rs.id,
        challenge_type=ChallengeType.PHOTO,
        title={"de": "Foto"},
        instructions={"de": "x"},
        scoring_rules={},
        sort_order=0,
    )
    db.add(rc)
    await db.flush()
    key = upload_file(PNG, _key(), "image/png")
    sub = Submission(
        organization_id=org.id,
        run_id=run.id,
        team_id=team.id,
        run_challenge_id=rc.id,
        content={"photo_url": key, "text": "answer"},
    )
    db.add(sub)
    await db.flush()
    return key


@pytest.mark.asyncio
@pytest.mark.usefixtures("storage_bucket")
async def test_override_shortens_cutoff(db_session: AsyncSession, org_a):
    """An org override of 30 days deletes the media of a run that ended 40 days ago. The default of 90 would keep it."""
    now = datetime.now(UTC)
    org_a.settings = {"retention": {"LK_B": 30}}
    await db_session.flush()
    key = await _run_with_media(db_session, org_a, ended_days_ago=40, now=now)
    assert _object_exists(key)

    deleted = await sweep_run_media(db_session, reference_date=now)

    assert deleted == 1
    assert not _object_exists(key)


@pytest.mark.asyncio
@pytest.mark.usefixtures("storage_bucket")
async def test_no_override_keeps_default_behaviour(db_session: AsyncSession, org_a):
    """Without an override, the media of a run that ended 40 days ago is kept (default 90)."""
    now = datetime.now(UTC)
    key = await _run_with_media(db_session, org_a, ended_days_ago=40, now=now)
    assert _object_exists(key)

    deleted = await sweep_run_media(db_session, reference_date=now)

    assert deleted == 0
    assert _object_exists(key)


@pytest.mark.asyncio
@pytest.mark.usefixtures("storage_bucket")
async def test_per_org_isolation(db_session: AsyncSession, org_a, org_b):
    """Only the org that overrides LK_B has its early media swept; the other is kept."""
    now = datetime.now(UTC)
    org_a.settings = {"retention": {"LK_B": 30}}
    await db_session.flush()
    key_a = await _run_with_media(db_session, org_a, ended_days_ago=40, now=now)
    key_b = await _run_with_media(db_session, org_b, ended_days_ago=40, now=now)

    deleted = await sweep_run_media(db_session, reference_date=now)

    assert deleted == 1
    assert not _object_exists(key_a)  # org_a override 30 → past window
    assert _object_exists(key_b)  # org_b default 90 → kept


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_retention_defaults(client_org_a: AsyncClient):
    resp = await client_org_a.get("/api/orgs/org-a/admin/retention")
    assert resp.status_code == 200
    body = resp.json()
    assert body["min_days"] == retention_min_days()
    assert body["max_days"] == 3650
    by_class = {p["deletion_class"]: p for p in body["periods"]}
    assert by_class["LK_A"]["days"] == 90
    assert by_class["LK_B"]["days"] == 90
    assert by_class["LK_C"]["days"] == 365
    assert by_class["LK_H"]["days"] == 365
    assert all(p["source"] == "default" for p in body["periods"])
    assert body["backup_retention_days"] == settings.retention.backup_rotation_days
    assert body["technical_log_days"] == 90  # max of the app/error/access defaults
    assert body["audit_log_days"] == 365
    assert body["abandoned_run_days"] == 60
    assert body["result_link_days"] == 90


@pytest.mark.asyncio
async def test_get_retention_states_log_periods_from_the_settings(client_org_a: AsyncClient, monkeypatch):
    """technical_log_days is the largest of the app, error and access log periods.

    audit_log_days is the security log period. A changed setting changes the answer.
    """
    monkeypatch.setattr("app.config.settings.logging.app_log_retention_days", 45)
    monkeypatch.setattr("app.config.settings.logging.access_log_retention_days", 200)
    monkeypatch.setattr("app.config.settings.logging.audit_log_retention_days", 730)

    resp = await client_org_a.get("/api/orgs/org-a/admin/retention")
    assert resp.status_code == 200
    body = resp.json()
    assert body["technical_log_days"] == 200  # max(45, 90, 200)
    assert body["audit_log_days"] == 730


@pytest.mark.asyncio
async def test_get_retention_states_the_sweep_periods_from_the_settings(client_org_a: AsyncClient, monkeypatch):
    """The periods of the abandoned-run sweep and of the result link come from the settings.

    The privacy template therefore states the installation's own periods.
    """
    monkeypatch.setattr("app.config.settings.retention.abandoned_run_days", 45)
    monkeypatch.setattr("app.config.settings.retention.result_link_days", 30)

    resp = await client_org_a.get("/api/orgs/org-a/admin/retention")
    assert resp.status_code == 200
    body = resp.json()
    assert body["abandoned_run_days"] == 45
    assert body["result_link_days"] == 30


@pytest.mark.asyncio
async def test_put_retention_valid_and_reflected(client_org_a: AsyncClient):
    resp = await client_org_a.put(
        "/api/orgs/org-a/admin/retention",
        json={"lk_a_days": 30, "lk_b_days": 120, "lk_c_days": 365},
    )
    assert resp.status_code == 200
    by_class = {p["deletion_class"]: p for p in resp.json()["periods"]}
    assert by_class["LK_B"]["days"] == 120
    assert by_class["LK_B"]["source"] == "override"

    resp2 = await client_org_a.get("/api/orgs/org-a/admin/retention")
    assert {p["deletion_class"]: p["days"] for p in resp2.json()["periods"]}["LK_B"] == 120

    resp3 = await client_org_a.put(
        "/api/orgs/org-a/admin/retention",
        json={"lk_a_days": None, "lk_b_days": None, "lk_c_days": None},
    )
    assert {p["deletion_class"]: p["source"] for p in resp3.json()["periods"]}["LK_B"] == "default"


@pytest.mark.asyncio
async def test_put_retention_guardrail_422(client_org_a: AsyncClient):
    resp = await client_org_a.put(
        "/api/orgs/org-a/admin/retention",
        json={"lk_a_days": 30, "lk_b_days": 7, "lk_c_days": 365},  # 7 < 30 backup rotation
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_retention_requires_org_admin(client: AsyncClient, db_session: AsyncSession, org_a):
    """A coordinator (below org_admin) is refused on both verbs."""
    coordinator = await create_test_user(
        db_session,
        email="coord-a@example.com",
        display_name="Coord A",
        role=UserRole.COORDINATOR,
        organization=org_a,
    )
    client.headers.update(get_auth_headers(coordinator))
    assert (await client.get("/api/orgs/org-a/admin/retention")).status_code == 403
    put = await client.put(
        "/api/orgs/org-a/admin/retention",
        json={"lk_a_days": 30, "lk_b_days": 90, "lk_c_days": 365},
    )
    assert put.status_code == 403


@pytest.mark.asyncio
async def test_put_retention_lkh_override_and_older_client(client_org_a: AsyncClient):
    """LK_H is set like the other classes. A client that leaves it out does not change it."""
    resp = await client_org_a.put(
        "/api/orgs/org-a/admin/retention",
        json={"lk_a_days": None, "lk_b_days": None, "lk_c_days": None, "lk_h_days": 180},
    )
    assert resp.status_code == 200
    by_class = {p["deletion_class"]: p for p in resp.json()["periods"]}
    assert by_class["LK_H"]["days"] == 180
    assert by_class["LK_H"]["source"] == "override"

    older = await client_org_a.put(
        "/api/orgs/org-a/admin/retention",
        json={"lk_a_days": None, "lk_b_days": 120, "lk_c_days": None},
    )
    assert {p["deletion_class"]: p["days"] for p in older.json()["periods"]}["LK_H"] == 180

    reverted = await client_org_a.put(
        "/api/orgs/org-a/admin/retention",
        json={"lk_a_days": None, "lk_b_days": None, "lk_c_days": None, "lk_h_days": None},
    )
    assert {p["deletion_class"]: p["source"] for p in reverted.json()["periods"]}["LK_H"] == "default"


@pytest.mark.asyncio
async def test_put_retention_lkh_guardrail_422(client_org_a: AsyncClient):
    resp = await client_org_a.put(
        "/api/orgs/org-a/admin/retention",
        json={"lk_a_days": 90, "lk_b_days": 90, "lk_c_days": 365, "lk_h_days": 7},
    )
    assert resp.status_code == 422
