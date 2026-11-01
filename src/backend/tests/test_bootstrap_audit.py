"""The bootstrap registration records the organization it creates.

`POST /api/auth/register` with an `organization_name` creates an organization and its first admin. Like the
operator's creation of an organization, it records `instance.org_created` with no organization id and the new slug.
A registration into an existing organization creates no organization and records no such event.
"""

import contextlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.logging as log_module
from app.auth.instance import create_instance_admin
from app.auth.tokens import create_instance_admin_token
from app.logging import configure_logging
from app.routers.admin import get_log_dir
from app.services.audit import reset_integrity_chain
from tests.factories import create_test_organization


@pytest.fixture
def audit_log_dir(tmp_path: Path) -> Iterator[Path]:
    """Point the audit logger at a temporary directory, and restore the logging setup afterwards."""
    structlog.reset_defaults()
    log_module._logging_configured = False
    configure_logging(environment="development", log_dir=str(tmp_path))
    reset_integrity_chain()

    yield tmp_path

    for name in ("audit", "app", "error", "access", ""):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            with contextlib.suppress(Exception):
                handler.close()
        logger.handlers.clear()
    log_module._logging_configured = False
    structlog.reset_defaults()


def _audit_entries(log_dir: Path) -> list[dict]:
    """The entries of audit.log. A missing file gives an empty list."""
    path = log_dir / "audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_bootstrap_registration_records_the_organization_it_creates(
    client: AsyncClient, db_session: AsyncSession, audit_log_dir: Path
):
    """One bootstrap registration records one `instance.org_created`, beside `user_created`.

    The event has no organization id and carries the new slug. The actor is the account the registration creates.
    """
    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Audit Org",
            "organization_slug": "audit-org",
            "email": "founder@example.org",
            "password": "SecurePass123!",
            "display_name": "Founder",
        },
    )
    assert response.status_code == 201
    user_id = response.json()["user_id"]

    entries = _audit_entries(audit_log_dir)
    created = [e for e in entries if e.get("event") == "instance.org_created"]
    assert len(created) == 1
    assert "organization_id" not in created[0]
    assert created[0]["target_slug"] == "audit-org"
    assert created[0]["actor"]["user_id"] == user_id
    assert created[0]["actor"]["type"] == "registration"
    assert "email" not in created[0]["actor"]
    assert "display_name" not in created[0]["actor"]
    assert len([e for e in entries if e.get("event") == "user_created"]) == 1


@pytest.mark.asyncio
async def test_registration_into_an_existing_organization_records_no_organization_creation(
    client: AsyncClient, db_session: AsyncSession, audit_log_dir: Path
):
    """A self-signup creates no organization, so it records no `instance.org_created`."""
    await create_test_organization(db_session, slug="existing-org")

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_slug": "existing-org",
            "email": "member@example.org",
            "password": "SecurePass123!",
            "display_name": "Member",
        },
    )
    assert response.status_code == 201

    entries = _audit_entries(audit_log_dir)
    assert [e for e in entries if e.get("event") == "instance.org_created"] == []
    assert len([e for e in entries if e.get("event") == "user_created"]) == 1


@pytest.mark.asyncio
async def test_operator_log_lists_the_registration_event_without_personal_data(
    client: AsyncClient, db_session: AsyncSession, audit_log_dir: Path
):
    """The operator's log shows the event with actor type `registration` and no operator email or id."""
    from app.main import app

    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Audit Org",
            "organization_slug": "audit-org",
            "email": "founder@example.org",
            "password": "SecurePass123!",
            "display_name": "Founder",
        },
    )
    assert response.status_code == 201

    admin = await create_instance_admin(db_session, "operator@example.com", "OperatorPass123!")
    headers = {"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"}

    app.dependency_overrides[get_log_dir] = lambda: str(audit_log_dir)
    try:
        listing = await client.get("/api/instance/audit", headers=headers)
    finally:
        del app.dependency_overrides[get_log_dir]

    assert listing.status_code == 200
    items = [i for i in listing.json()["items"] if i["event"] == "instance.org_created"]
    assert len(items) == 1
    assert items[0]["actor_type"] == "registration"
    assert items[0]["operator_email"] is None
    assert items[0]["operator_id"] is None
    assert items[0]["target_slug"] == "audit-org"
    assert "founder@example.org" not in listing.text
