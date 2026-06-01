"""
The user grace-delete flow.

Covers:
- Default DELETE soft-deletes (restorable): PII kept, account deactivated,
  hidden from the default list, visible with include_deleted=true
- POST /users/{id}/restore round-trip within the grace window
- Restore after anonymization → 410 Gone
- Restore of a non-deleted user → 409 Conflict
- DELETE ?mode=immediate anonymizes right away (GDPR Art. 17 path)
- sweep_expired_grace_deletions anonymizes only users past the 30-day window
- Audit events: user_deleted carries deletion_mode; user_restored emitted
"""

import tempfile
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import configure_logging
from app.models.user import UserRole
from app.services.audit import reset_integrity_chain
from app.services.gdpr import (
    GRACE_DELETE_DAYS,
    sweep_expired_grace_deletions,
)
from tests.factories import (
    create_org_admin_user,
    create_test_user,
    get_auth_headers,
)


@pytest.fixture
def configured_logging():
    """Audit logging into a temp dir (same shape as the test_audit.py fixture
    — fixtures don't cross test modules, so it's duplicated here)."""
    import logging as _logging

    import app.logging as _log_module

    with tempfile.TemporaryDirectory() as tmpdir:
        structlog.reset_defaults()
        _log_module._logging_configured = False
        configure_logging(environment="development", log_dir=tmpdir)
        reset_integrity_chain()

        yield tmpdir

        import contextlib

        for name in ("audit", "app", "error", "access", ""):
            lg = _logging.getLogger(name)
            for h in list(lg.handlers):
                with contextlib.suppress(Exception):
                    h.close()
            lg.handlers.clear()
        _log_module._logging_configured = False
        structlog.reset_defaults()


async def _admin_and_target(db_session: AsyncSession, slug: str):
    admin, org = await create_org_admin_user(
        db_session,
        email=f"admin@{slug}.example.com",
        org_slug=slug,
    )
    target = await create_test_user(
        db_session,
        email=f"target@{slug}.example.com",
        display_name="Grace Target",
        role=UserRole.PARTICIPANT,
        organization=org,
    )
    return admin, org, target


@pytest.mark.asyncio
async def test_default_delete_is_grace_and_keeps_pii(client: AsyncClient, db_session: AsyncSession):
    admin, org, target = await _admin_and_target(db_session, "grace-default")
    headers = get_auth_headers(admin)

    response = await client.delete(f"/api/orgs/{org.slug}/users/{target.id}", headers=headers)
    assert response.status_code == 204

    await db_session.refresh(target)
    assert target.is_deleted is True
    assert target.deleted_at is not None
    assert target.is_active is False
    # Personal data stays intact during the grace window.
    assert target.email == "target@grace-default.example.com"
    assert target.display_name == "Grace Target"
    assert target.password_hash != ""


@pytest.mark.asyncio
async def test_grace_deleted_user_hidden_unless_include_deleted(client: AsyncClient, db_session: AsyncSession):
    admin, org, target = await _admin_and_target(db_session, "grace-list")
    headers = get_auth_headers(admin)

    await client.delete(f"/api/orgs/{org.slug}/users/{target.id}", headers=headers)

    default_list = await client.get(f"/api/orgs/{org.slug}/users/", headers=headers)
    assert default_list.status_code == 200
    ids = [u["id"] for u in default_list.json()["items"]]
    assert str(target.id) not in ids

    full_list = await client.get(f"/api/orgs/{org.slug}/users/?include_deleted=true", headers=headers)
    assert full_list.status_code == 200
    deleted_entry = next(u for u in full_list.json()["items"] if u["id"] == str(target.id))
    assert deleted_entry["is_deleted"] is True
    assert deleted_entry["deleted_at"] is not None
    assert deleted_entry["email"] == "target@grace-list.example.com"


@pytest.mark.asyncio
async def test_restore_within_grace_window(client: AsyncClient, db_session: AsyncSession):
    admin, org, target = await _admin_and_target(db_session, "grace-restore")
    headers = get_auth_headers(admin)

    await client.delete(f"/api/orgs/{org.slug}/users/{target.id}", headers=headers)

    response = await client.post(f"/api/orgs/{org.slug}/users/{target.id}/restore", headers=headers)
    assert response.status_code == 200
    body = response.json()
    # A restore never activates: the admin activates in a second step.
    assert body["is_active"] is False
    assert body["email"] == "target@grace-restore.example.com"

    await db_session.refresh(target)
    assert target.is_deleted is False
    assert target.deleted_at is None
    assert target.is_active is False


@pytest.mark.asyncio
async def test_restore_keeps_account_deactivated_before_delete_closed(client: AsyncClient, db_session: AsyncSession):
    """An account that was deactivated first and deleted later must not sign in after a restore."""
    admin, org, target = await _admin_and_target(db_session, "grace-leaver")
    headers = get_auth_headers(admin)

    target.is_active = False
    await db_session.flush()
    await client.delete(f"/api/orgs/{org.slug}/users/{target.id}", headers=headers)

    response = await client.post(f"/api/orgs/{org.slug}/users/{target.id}/restore", headers=headers)
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    await db_session.refresh(target)
    assert target.is_deleted is False
    assert target.is_active is False


@pytest.mark.asyncio
async def test_restore_after_anonymization_returns_410(client: AsyncClient, db_session: AsyncSession):
    admin, org, target = await _admin_and_target(db_session, "grace-410")
    headers = get_auth_headers(admin)

    await client.delete(f"/api/orgs/{org.slug}/users/{target.id}?mode=immediate", headers=headers)

    response = await client.post(f"/api/orgs/{org.slug}/users/{target.id}/restore", headers=headers)
    assert response.status_code == 410


@pytest.mark.asyncio
async def test_restore_non_deleted_user_returns_409(client: AsyncClient, db_session: AsyncSession):
    admin, org, target = await _admin_and_target(db_session, "grace-409")
    headers = get_auth_headers(admin)

    response = await client.post(f"/api/orgs/{org.slug}/users/{target.id}/restore", headers=headers)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_restore_requires_org_admin(client: AsyncClient, db_session: AsyncSession):
    admin, org, target = await _admin_and_target(db_session, "grace-perm")
    coordinator = await create_test_user(
        db_session,
        email="coord@grace-perm.example.com",
        role=UserRole.COORDINATOR,
        organization=org,
    )
    admin_headers = get_auth_headers(admin)
    await client.delete(f"/api/orgs/{org.slug}/users/{target.id}", headers=admin_headers)

    response = await client.post(
        f"/api/orgs/{org.slug}/users/{target.id}/restore",
        headers=get_auth_headers(coordinator),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_sweep_anonymizes_only_past_grace_window(db_session: AsyncSession):
    _admin, org = await create_org_admin_user(
        db_session,
        email="admin@grace-sweep.example.com",
        org_slug="grace-sweep",
    )
    expired = await create_test_user(
        db_session,
        email="expired@grace-sweep.example.com",
        display_name="Expired User",
        organization=org,
    )
    recent = await create_test_user(
        db_session,
        email="recent@grace-sweep.example.com",
        display_name="Recent User",
        organization=org,
    )

    now = datetime.now(UTC)
    expired.is_deleted = True
    expired.deleted_at = now - timedelta(days=GRACE_DELETE_DAYS + 1)
    expired.is_active = False
    recent.is_deleted = True
    recent.deleted_at = now - timedelta(days=GRACE_DELETE_DAYS - 1)
    recent.is_active = False
    await db_session.flush()

    anonymized = await sweep_expired_grace_deletions(db_session, reference_date=now)
    await db_session.commit()

    assert anonymized == 1
    await db_session.refresh(expired)
    await db_session.refresh(recent)
    assert expired.email.endswith("@anonymized.local")
    assert expired.display_name == "Deleted User"
    # Still inside the grace window, so untouched.
    assert recent.email == "recent@grace-sweep.example.com"
    assert recent.display_name == "Recent User"


@pytest.mark.asyncio
async def test_sweep_is_idempotent(db_session: AsyncSession):
    _admin, org = await create_org_admin_user(
        db_session,
        email="admin@grace-idem.example.com",
        org_slug="grace-idem",
    )
    user = await create_test_user(
        db_session,
        email="gone@grace-idem.example.com",
        organization=org,
    )
    now = datetime.now(UTC)
    user.is_deleted = True
    user.deleted_at = now - timedelta(days=GRACE_DELETE_DAYS + 5)
    await db_session.flush()

    assert await sweep_expired_grace_deletions(db_session, reference_date=now) == 1
    # Second run finds nothing, the user is already anonymized.
    assert await sweep_expired_grace_deletions(db_session, reference_date=now) == 0


@pytest.mark.asyncio
async def test_audit_events_for_grace_flow(client: AsyncClient, db_session: AsyncSession, configured_logging: str):
    import json
    from pathlib import Path

    admin, org, target = await _admin_and_target(db_session, "grace-audit")
    headers = get_auth_headers(admin)

    await client.delete(f"/api/orgs/{org.slug}/users/{target.id}", headers=headers)
    await client.post(f"/api/orgs/{org.slug}/users/{target.id}/restore", headers=headers)

    audit_log = Path(configured_logging) / "audit.log"
    entries = [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]

    deleted_events = [e for e in entries if e.get("event") == "user_deleted"]
    assert deleted_events[-1]["deletion_mode"] == "grace"
    assert deleted_events[-1]["target"]["email"] == "target@grace-audit.example.com"

    restored_events = [e for e in entries if e.get("event") == "user_restored"]
    assert len(restored_events) == 1
    assert restored_events[-1]["target"]["id"] == str(target.id)
    assert restored_events[-1]["actor"]["display_name"] == admin.display_name
