"""
Integration tests for audit logging.

Tests cover:
- Audit event emission for security-relevant actions
- HMAC-SHA256 integrity chain, its recovery after a restart, rotation and two writers
- Sensitive field redaction in logs
- Request ID propagation across log entries
- Audit log entry structure
"""

import json
import logging
import tempfile
from pathlib import Path
from uuid import UUID

import pyotp
import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import configure_logging
from app.models.user import UserRole
from app.services.audit import (
    GENESIS_HASH,
    audit_config_changed,
    audit_user_created,
    audit_user_login,
    audit_user_logout,
    audit_user_role_changed,
    compute_integrity_hash,
    record_audit_event,
    reset_integrity_chain,
)
from tests.factories import create_org_admin_user, create_test_user, create_test_user_with_org


@pytest.fixture
def temp_log_dir():
    """Create a temporary directory for test logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def configured_logging(temp_log_dir):
    """Configure logging for tests with temporary log directory."""
    import logging as _logging

    import app.logging as _log_module

    structlog.reset_defaults()

    # Allow configure_logging to re-run by resetting its idempotency flag.
    _log_module._logging_configured = False

    configure_logging(environment="development", log_dir=temp_log_dir)
    reset_integrity_chain()

    yield temp_log_dir

    # Close TimedRotatingFileHandler instances so the temp dir can
    # be removed on Windows (file handles otherwise block tempdir teardown).
    import contextlib

    for name in ("audit", "app", "error", "access", ""):
        lg = _logging.getLogger(name)
        for h in list(lg.handlers):
            with contextlib.suppress(Exception):
                h.close()
        lg.handlers.clear()
    _log_module._logging_configured = False
    structlog.reset_defaults()


def read_application_log(log_dir: str) -> str:
    """The text of application.log. The file must exist, or a check of its content would check nothing."""
    app_log_path = Path(log_dir) / "application.log"
    assert app_log_path.exists(), f"{app_log_path} is missing: the application log is not where the test reads"
    return app_log_path.read_text(encoding="utf-8")


def read_audit_log(log_dir: str) -> list[dict]:
    """Parse the entries of audit.log. A missing file gives an empty list."""
    audit_log_path = Path(log_dir) / "audit.log"
    if not audit_log_path.exists():
        return []

    entries = []
    with open(audit_log_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries


def verify_integrity_chain(entries: list[dict]) -> bool:
    """
    Verify the HMAC-SHA256 integrity chain across audit entries.

    Replays the chain from genesis and verifies each entry's hash matches
    the expected value computed from the previous hash.

    ``segment_start`` entries (written as the first entry of a rotated file)
    embed ``previous_file_last_hash``, the last hash of the preceding file.
    When encountered, ``previous_hash`` is reset to that value before the entry
    itself is verified. So a rotated-file slice that begins with a
    ``segment_start`` verifies without the caller supplying the cross-file
    boundary hash.
    """
    if not entries:
        return True

    previous_hash = GENESIS_HASH

    # Strip set must match the writer in app.audit_handler._META_KEYS.
    # Timestamp and request_id stay in the hash so neither can be rewritten
    # without breaking verification.
    STRUCTLOG_FIELDS = {"integrity_hash", "level", "logger", "service", "category"}

    for entry in entries:
        integrity_hash = entry.get("integrity_hash")
        if not integrity_hash:
            return False

        # segment_start marks a file-rotation boundary.  Its hash was computed
        # against the previous file's last hash (embedded in
        # ``previous_file_last_hash``), not against genesis.  Reset the chain
        # seed so the entry verifies correctly even when this helper is called
        # on a rotated-file slice rather than the full log from the beginning.
        if entry.get("event") == "segment_start":
            prev = entry.get("previous_file_last_hash")
            if isinstance(prev, str) and len(prev) == 64 and all(c in "0123456789abcdef" for c in prev):
                previous_hash = prev

        entry_without_structlog = {k: v for k, v in entry.items() if k not in STRUCTLOG_FIELDS}

        expected_hash = compute_integrity_hash(entry_without_structlog, previous_hash)

        if integrity_hash != expected_hash:
            return False

        previous_hash = integrity_hash

    return True


# ==================== Test: Event Emission ====================


@pytest.mark.asyncio
async def test_login_emits_audit_event(client: AsyncClient, db_session: AsyncSession, configured_logging: str):
    """
    A successful login writes a user_login entry with actor, target and organization.

    A coordinator logs in, because an org_admin needs 2FA setup first.
    """
    user, org = await create_test_user_with_org(
        db_session,
        email="coordinator@test.com",
        password="SecurePass123!",
        org_name="Test Org",
        org_slug="test-org",
        role=UserRole.COORDINATOR,
    )

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "coordinator@test.com",
            "password": "SecurePass123!",
        },
    )

    assert response.status_code == 200

    audit_entries = read_audit_log(configured_logging)

    login_events = [e for e in audit_entries if e.get("event") == "user_login"]
    assert len(login_events) >= 1

    login_event = login_events[0]

    assert login_event["event"] == "user_login"
    assert "actor" in login_event
    assert login_event["actor"]["user_id"] == str(user.id)
    assert login_event["actor"]["role"] == "coordinator"
    assert "ip" in login_event["actor"]
    assert "target" in login_event
    assert login_event["target"]["entity"] == "user"
    assert login_event["target"]["id"] == str(user.id)
    assert login_event["organization_id"] == str(org.id)
    assert "timestamp" in login_event
    assert "integrity_hash" in login_event


@pytest.mark.asyncio
async def test_failed_login_emits_audit_event(client: AsyncClient, db_session: AsyncSession, configured_logging: str):
    """A failed login writes a user_login_failed entry with the reason, the email and the IP."""
    await create_test_user(db_session, email="user@test.com", password="CorrectPass123!")

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "user@test.com",
            "password": "WrongPassword123!",
        },
    )

    assert response.status_code == 401

    audit_entries = read_audit_log(configured_logging)

    failed_events = [e for e in audit_entries if e.get("event") == "user_login_failed"]
    assert len(failed_events) >= 1

    failed_event = failed_events[0]

    assert failed_event["event"] == "user_login_failed"
    assert "actor" in failed_event
    assert "ip" in failed_event["actor"]
    assert "target" in failed_event
    assert failed_event["target"]["entity"] == "user"
    assert failed_event["target"]["email"] == "user@test.com"
    assert "reason" in failed_event
    assert failed_event["reason"] == "invalid_password"


@pytest.mark.asyncio
async def test_logout_emits_audit_event(client: AsyncClient, db_session: AsyncSession, configured_logging: str):
    """
    A logout writes a user_logout entry of type manual.

    A coordinator logs in, because an org_admin needs 2FA setup first.
    """
    user, org = await create_test_user_with_org(
        db_session,
        email="coordinator@test.com",
        password="CoordinatorPassword123!",
        role=UserRole.COORDINATOR,
    )

    login_response = await client.post(
        "/api/auth/login",
        json={"email": "coordinator@test.com", "password": "CoordinatorPassword123!"},
    )
    assert login_response.status_code == 200

    logout_response = await client.post("/api/auth/logout")
    assert logout_response.status_code == 200

    audit_entries = read_audit_log(configured_logging)

    logout_events = [e for e in audit_entries if e.get("event") == "user_logout"]
    assert len(logout_events) >= 1

    logout_event = logout_events[0]

    assert logout_event["event"] == "user_logout"
    assert logout_event["actor"]["user_id"] == str(user.id)
    assert logout_event["logout_type"] == "manual"


@pytest.mark.asyncio
async def test_role_change_emits_with_changes(db_session: AsyncSession, configured_logging: str):
    """A role change writes an entry with the from and to values of the role."""
    admin, org = await create_org_admin_user(
        db_session,
        email="admin@test.com",
        org_name="Test Org",
        org_slug="test-org",
    )

    target_user = await create_test_user(
        db_session,
        email="user@test.com",
        organization=org,
    )

    audit_user_role_changed(
        actor_user=admin,
        actor_ip="192.168.1.100",
        target_user=target_user,
        old_role="participant",
        new_role="coordinator",
        organization_id=org.id,
    )

    audit_entries = read_audit_log(configured_logging)

    role_events = [e for e in audit_entries if e.get("event") == "user_role_changed"]
    assert len(role_events) == 1

    role_event = role_events[0]

    assert "changes" in role_event
    assert "role" in role_event["changes"]
    assert role_event["changes"]["role"]["from"] == "participant"
    assert role_event["changes"]["role"]["to"] == "coordinator"
    assert role_event["actor"]["user_id"] == str(admin.id)
    assert role_event["target"]["id"] == str(target_user.id)


# ==================== Test: HMAC Chain Integrity ====================


@pytest.mark.asyncio
async def test_hmac_chain_integrity_single_event(db_session: AsyncSession, configured_logging: str):
    """A single entry verifies against GENESIS_HASH."""
    admin, org = await create_org_admin_user(db_session)

    audit_user_login(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    audit_entries = read_audit_log(configured_logging)
    assert len(audit_entries) == 1

    assert verify_integrity_chain(audit_entries) is True


@pytest.mark.asyncio
async def test_hmac_chain_integrity_multiple_events(db_session: AsyncSession, configured_logging: str):
    """Four different events verify as one chain."""
    admin, org = await create_org_admin_user(db_session)
    user = await create_test_user(db_session, organization=org)

    audit_user_login(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    audit_user_created(
        actor_user=admin,
        actor_ip="192.168.1.100",
        new_user=user,
        organization_id=org.id,
    )

    audit_config_changed(
        actor_user=admin,
        actor_ip="192.168.1.100",
        config_key="session_timeout",
        old_value=60,
        new_value=120,
        organization_id=org.id,
    )

    audit_user_logout(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    audit_entries = read_audit_log(configured_logging)
    assert len(audit_entries) == 4

    assert verify_integrity_chain(audit_entries) is True


@pytest.mark.asyncio
async def test_integrity_chain_tamper_detection(db_session: AsyncSession, configured_logging: str):
    """Changing one field of an entry breaks the chain."""
    admin, org = await create_org_admin_user(db_session)

    audit_user_login(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    audit_user_logout(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    audit_entries = read_audit_log(configured_logging)
    assert len(audit_entries) == 2

    assert verify_integrity_chain(audit_entries) is True

    tampered_entries = audit_entries.copy()
    tampered_entries[0]["event"] = "user_login_tampered"

    assert verify_integrity_chain(tampered_entries) is False


# ==================== Test: Sensitive Field Redaction ====================


@pytest.mark.asyncio
async def test_sensitive_fields_redacted_in_audit_log(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """A token sent with an audit event reaches audit.log as [REDACTED], and the login password stays out of the
    application log."""
    admin, org = await create_org_admin_user(
        db_session,
        email="admin@test.com",
        password="SuperSecret123!",
    )

    response = await client.post(
        "/api/auth/login",
        json={
            "email": "admin@test.com",
            "password": "SuperSecret123!",
        },
    )
    assert response.status_code == 200
    assert "SuperSecret123!" not in read_application_log(configured_logging)

    record_audit_event(
        event="redaction_probe",
        actor={"user_id": str(admin.id)},
        organization_id=org.id,
        session_token="probe-token-4f1c9e",
    )

    probes = [e for e in read_audit_log(configured_logging) if e.get("event") == "redaction_probe"]
    assert len(probes) == 1, "the probe event did not reach audit.log"
    assert probes[0]["session_token"] == "[REDACTED]"
    audit_text = (Path(configured_logging) / "audit.log").read_text(encoding="utf-8")
    assert "probe-token-4f1c9e" not in audit_text


@pytest.mark.asyncio
async def test_password_redacted_in_registration(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """The password of a registration does not appear in the application log."""
    response = await client.post(
        "/api/auth/register",
        json={
            "organization_name": "Test Company",
            "organization_slug": "test-company",
            "email": "admin@testcompany.com",
            "password": "MySecretPassword123!",
            "display_name": "Admin User",
        },
    )
    assert response.status_code == 201

    assert "MySecretPassword123!" not in read_application_log(configured_logging)


# ==================== Test: Request ID Propagation ====================


@pytest.mark.asyncio
async def test_request_id_propagates_to_all_logs(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """
    A request ID sent in X-Request-ID comes back in the response and tags the audit entries of that request.

    A coordinator logs in, because an org_admin needs 2FA setup first.
    """
    user, org = await create_test_user_with_org(
        db_session,
        email="coordinator@test.com",
        password="CoordinatorPassword123!",
        role=UserRole.COORDINATOR,
    )

    custom_request_id = "test-request-12345"

    response = await client.post(
        "/api/auth/login",
        json={"email": "coordinator@test.com", "password": "CoordinatorPassword123!"},
        headers={"X-Request-ID": custom_request_id},
    )
    assert response.status_code == 200

    assert response.headers.get("X-Request-ID") == custom_request_id

    audit_entries = read_audit_log(configured_logging)

    matching_entries = [e for e in audit_entries if e.get("request_id") == custom_request_id]

    assert len(matching_entries) >= 1

    for entry in matching_entries:
        assert entry["request_id"] == custom_request_id


@pytest.mark.asyncio
async def test_request_id_generated_if_not_provided(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """A request without X-Request-ID gets a generated UUID, which is returned in the header and logged.

    A coordinator logs in, because an org_admin needs 2FA setup first.
    """
    user, org = await create_test_user_with_org(
        db_session,
        email="coordinator@test.com",
        password="CoordinatorPassword123!",
        role=UserRole.COORDINATOR,
    )

    response = await client.post(
        "/api/auth/login",
        json={"email": "coordinator@test.com", "password": "CoordinatorPassword123!"},
    )
    assert response.status_code == 200

    response_request_id = response.headers.get("X-Request-ID")
    assert response_request_id is not None
    assert len(response_request_id) > 0

    try:
        UUID(response_request_id)
    except ValueError:
        pytest.fail(f"Generated request_id '{response_request_id}' is not a valid UUID")

    audit_entries = read_audit_log(configured_logging)

    matching_entries = [e for e in audit_entries if e.get("request_id") == response_request_id]

    assert len(matching_entries) >= 1


@pytest.mark.asyncio
async def test_multiple_requests_have_different_request_ids(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """Two requests get different request IDs, and the audit log holds entries under both.

    A coordinator logs in, because an org_admin needs 2FA setup first.
    """
    user, org = await create_test_user_with_org(
        db_session,
        email="coordinator@test.com",
        password="CoordinatorPassword123!",
        role=UserRole.COORDINATOR,
    )

    response1 = await client.post(
        "/api/auth/login",
        json={"email": "coordinator@test.com", "password": "CoordinatorPassword123!"},
    )
    assert response1.status_code == 200
    request_id_1 = response1.headers.get("X-Request-ID")

    await client.post("/api/auth/logout")

    response2 = await client.post(
        "/api/auth/login",
        json={"email": "coordinator@test.com", "password": "CoordinatorPassword123!"},
    )
    assert response2.status_code == 200
    request_id_2 = response2.headers.get("X-Request-ID")

    assert request_id_1 != request_id_2

    audit_entries = read_audit_log(configured_logging)

    entries_req1 = [e for e in audit_entries if e.get("request_id") == request_id_1]
    entries_req2 = [e for e in audit_entries if e.get("request_id") == request_id_2]

    assert len(entries_req1) >= 1
    assert len(entries_req2) >= 1


# ==================== Test: Audit Event Structure ====================


@pytest.mark.asyncio
async def test_audit_event_contains_all_required_fields(db_session: AsyncSession, configured_logging: str):
    """An audit entry holds timestamp, level, category, event, actor, target, organization_id, integrity_hash and service."""
    admin, org = await create_org_admin_user(db_session)

    audit_user_login(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    audit_entries = read_audit_log(configured_logging)
    assert len(audit_entries) == 1

    entry = audit_entries[0]

    assert "timestamp" in entry
    assert "level" in entry
    assert "category" in entry
    assert entry["category"] == "audit"
    assert "event" in entry
    assert entry["event"] == "user_login"
    assert "actor" in entry
    assert "target" in entry
    assert "organization_id" in entry
    assert "integrity_hash" in entry
    assert "service" in entry
    assert entry["service"] == "clavis-backend"


@pytest.mark.asyncio
async def test_record_audit_event_with_extra_context(db_session: AsyncSession, configured_logging: str):
    """Extra keyword context appears in the entry and leaves the required fields intact."""
    admin, org = await create_org_admin_user(db_session)

    record_audit_event(
        event="custom_event",
        actor={"user_id": str(admin.id), "role": "org_admin", "ip": "192.168.1.100"},
        target={"entity": "system", "id": "config"},
        organization_id=org.id,
        custom_field_1="value1",
        custom_field_2={"nested": "data"},
    )

    audit_entries = read_audit_log(configured_logging)
    assert len(audit_entries) == 1

    entry = audit_entries[0]

    assert entry["custom_field_1"] == "value1"
    assert entry["custom_field_2"]["nested"] == "data"

    assert entry["event"] == "custom_event"
    assert entry["actor"]["user_id"] == str(admin.id)
    assert entry["organization_id"] == str(org.id)
    assert "integrity_hash" in entry


# ==================== Test: Chain Persistence Across Restart ====================


@pytest.mark.asyncio
async def test_chain_recovers_from_sidecar_after_restart(db_session: AsyncSession, configured_logging: str):
    """
    After "restart" (re-init the handler with a fresh in-memory state) the
    chain continues from the last hash on disk via the sidecar — not from
    genesis.
    """
    import contextlib
    import logging as _logging

    from app.audit_handler import AuditLogHandler, get_handler, init_audit_chain, register_handler

    admin, org = await create_org_admin_user(db_session)

    audit_user_login(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    pre_restart_entries = read_audit_log(configured_logging)
    assert len(pre_restart_entries) == 1
    pre_restart_hash = pre_restart_entries[0]["integrity_hash"]

    sidecar = Path(configured_logging) / "audit.last-hash"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").strip() == pre_restart_hash

    # Simulate a restart: detach the existing audit handler and install a
    # fresh AuditLogHandler instance whose in-memory chain starts at genesis.
    audit_logger = _logging.getLogger("audit")
    old = get_handler()
    assert old is not None
    audit_logger.removeHandler(old)
    with contextlib.suppress(Exception):
        old.close()

    fresh = AuditLogHandler(
        str(Path(configured_logging) / "audit.log"),
        when="midnight",
        interval=1,
        backupCount=365,
        encoding="utf-8",
        utc=True,
    )
    fresh.setFormatter(old.formatter)
    fresh.setLevel(old.level)
    register_handler(fresh)
    audit_logger.addHandler(fresh)
    init_audit_chain()

    # Write another entry; it must chain to the pre-restart hash, not genesis.
    audit_user_logout(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    post_restart_entries = read_audit_log(configured_logging)
    assert len(post_restart_entries) == 2
    assert verify_integrity_chain(post_restart_entries) is True


@pytest.mark.asyncio
async def test_chain_recovers_from_tail_scan_when_sidecar_missing(db_session: AsyncSession, configured_logging: str):
    """A corrupt sidecar makes the handler tail-scan audit.log, so the chain still continues."""
    import contextlib
    import logging as _logging

    from app.audit_handler import AuditLogHandler, get_handler, init_audit_chain, register_handler

    admin, org = await create_org_admin_user(db_session)

    audit_user_login(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )
    pre = read_audit_log(configured_logging)
    assert len(pre) == 1

    # Corrupt the sidecar (simulate a partial write that didn't finish).
    sidecar = Path(configured_logging) / "audit.last-hash"
    sidecar.write_text("not-a-valid-hash", encoding="utf-8")

    audit_logger = _logging.getLogger("audit")
    old = get_handler()
    assert old is not None
    audit_logger.removeHandler(old)
    with contextlib.suppress(Exception):
        old.close()

    fresh = AuditLogHandler(
        str(Path(configured_logging) / "audit.log"),
        when="midnight",
        interval=1,
        backupCount=365,
        encoding="utf-8",
        utc=True,
    )
    fresh.setFormatter(old.formatter)
    fresh.setLevel(old.level)
    register_handler(fresh)
    audit_logger.addHandler(fresh)
    init_audit_chain()

    audit_user_logout(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )
    post = read_audit_log(configured_logging)
    assert len(post) == 2
    assert verify_integrity_chain(post) is True


# ==================== Test: Rotation + segment_start ====================


@pytest.mark.asyncio
async def test_rotation_writes_segment_start_and_chain_continues(db_session: AsyncSession, configured_logging: str):
    """
    A forced rotation leaves a ``segment_start`` entry as the first line of
    the new file, and the next regular entry chains to that segment_start.
    """
    from app.audit_handler import get_handler

    admin, org = await create_org_admin_user(db_session)

    audit_user_login(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    handler = get_handler()
    assert handler is not None
    # Force a rollover under the lock just like an emit() would.
    with handler._audit_lock:  # type: ignore[attr-defined]
        handler._do_rollover_locked()  # type: ignore[attr-defined]

    audit_user_logout(
        user=admin,
        ip="192.168.1.100",
        organization_id=org.id,
    )

    new_file_entries = read_audit_log(configured_logging)
    assert len(new_file_entries) >= 2
    assert new_file_entries[0]["event"] == "segment_start"
    assert "previous_file_last_hash" in new_file_entries[0]
    assert new_file_entries[1]["event"] == "user_logout"

    assert verify_integrity_chain(new_file_entries) is True


# ==================== Test: Directory-mode verifier ====================


@pytest.mark.asyncio
async def test_directory_verifier_walks_rotated_files(db_session: AsyncSession, configured_logging: str):
    """The CLI verifier walks the rotated files and the active log as one chain."""
    from app.audit_handler import get_handler
    from app.tools.verify_audit_log import verify_audit_log

    admin, org = await create_org_admin_user(db_session)

    audit_user_login(
        user=admin,
        ip="10.0.0.1",
        organization_id=org.id,
    )

    handler = get_handler()
    assert handler is not None
    with handler._audit_lock:  # type: ignore[attr-defined]
        handler._do_rollover_locked()  # type: ignore[attr-defined]

    audit_user_logout(
        user=admin,
        ip="10.0.0.1",
        organization_id=org.id,
    )

    assert verify_audit_log(Path(configured_logging)) is True


# ==================== Test: lock-ordering regression ====================


@pytest.mark.asyncio
async def test_format_under_audit_lock_does_not_deadlock(db_session: AsyncSession, configured_logging: str):
    """
    The formatter is safe to call from inside the audit handler's lock.

    A formatter that tried to re-enter the audit logger would deadlock.
    """
    from app.audit_handler import get_handler

    admin, org = await create_org_admin_user(db_session)

    audit_user_login(
        user=admin,
        ip="127.0.0.1",
        organization_id=org.id,
    )

    handler = get_handler()
    assert handler is not None

    audit_log_path = Path(configured_logging) / "audit.log"
    with open(audit_log_path, encoding="utf-8") as f:
        first_line = f.readline().strip()
    record = json.loads(first_line)
    rebuilt = logging.LogRecord(
        name="audit",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=record.get("event", "synthetic"),
        args=(),
        exc_info=None,
    )

    with handler._audit_lock:  # type: ignore[attr-defined]
        rendered = handler.format(rebuilt)
    assert rendered  # any non-empty string is enough — the point is no deadlock


# ==================== Test: Actor/Target Denormalization ====================


@pytest.mark.asyncio
async def test_audit_actor_and_target_denormalized(db_session: AsyncSession, configured_logging: str):
    """
    Actor and target dicts carry display_name + email at emit time.

    Audit entries must stay meaningful after the referenced user row is
    anonymized, because the log is tamper-evident and self-contained. So the
    PII is embedded when the event is written instead of joined at read time.
    """
    user = await create_test_user(
        db_session,
        email="denorm@test.com",
        display_name="Denorm Tester",
        role=UserRole.COORDINATOR,
    )

    audit_user_login(
        user=user,
        ip="192.168.1.50",
        organization_id=user.organization_id,
    )

    audit_entries = read_audit_log(configured_logging)
    login_event = [e for e in audit_entries if e.get("event") == "user_login"][0]

    assert login_event["actor"]["user_id"] == str(user.id)
    assert login_event["actor"]["display_name"] == "Denorm Tester"
    assert login_event["actor"]["email"] == "denorm@test.com"
    assert login_event["actor"]["role"] == "coordinator"
    assert login_event["target"]["display_name"] == "Denorm Tester"
    assert login_event["target"]["email"] == "denorm@test.com"


@pytest.mark.asyncio
async def test_guest_actor_has_no_email_key(db_session: AsyncSession, configured_logging: str):
    """Users without email (guests) emit actor/target dicts without an email key."""
    from app.services.audit import actor_from_user, target_from_user

    guest = await create_test_user(
        db_session,
        email="placeholder@test.com",
        display_name="Guest Player",
        role=UserRole.PARTICIPANT,
    )
    guest.email = None

    actor = actor_from_user(guest, "10.0.0.1")
    target = target_from_user(guest)

    assert "email" not in actor
    assert actor["display_name"] == "Guest Player"
    assert "email" not in target
    assert target["display_name"] == "Guest Player"


@pytest.mark.asyncio
async def test_anonymize_user_audit_target_keeps_pii(db_session: AsyncSession, configured_logging: str):
    """
    The data_deletion_requested event captures the target's display_name/email
    BEFORE anonymization rewrites them — the log keeps the original identity.
    """
    from app.services.audit import actor_from_user
    from app.services.gdpr import anonymize_user

    admin, org = await create_org_admin_user(db_session)
    victim = await create_test_user(
        db_session,
        email="victim@test.com",
        display_name="Victim User",
        organization=org,
    )

    assert await anonymize_user(
        session=db_session,
        user_id=victim.id,
        actor=actor_from_user(admin, "192.168.1.99"),
    )
    await db_session.commit()

    audit_entries = read_audit_log(configured_logging)
    deletion_event = [e for e in audit_entries if e.get("event") == "data_deletion_requested"][0]

    assert deletion_event["target"]["id"] == str(victim.id)
    assert deletion_event["target"]["display_name"] == "Victim User"
    assert deletion_event["target"]["email"] == "victim@test.com"
    assert deletion_event["actor"]["display_name"] == admin.display_name
    # DB row is anonymized while the log keeps the original identity
    await db_session.refresh(victim)
    assert victim.email.endswith("@anonymized.local")
    assert victim.display_name == "Deleted User"


# ============================================================================
# Second-factor failures
#
# The 2FA endpoints log their failures as well as their successes. Each of the
# five rejection branches names itself in the audit log. The access log shows
# only the status and the length of a response, so only the audit log tells the
# branches apart. That makes brute force visible and an incident attributable.
# ============================================================================


@pytest.mark.asyncio
async def test_wrong_totp_code_emits_audit_event(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """A wrong TOTP code is recorded, with the branch that rejected it."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="totp@test.com",
        password="CorrectPass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": []},
    )

    # The password step of the login sets the proof cookie the second factor
    # demands. This test is about the wrong-code branch, not the missing-proof one.
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "CorrectPass123!"},
    )
    assert login.status_code == 200

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": "000000"},
    )
    assert response.status_code == 401

    events = [
        e
        for e in read_audit_log(configured_logging)
        if e.get("event") == "user_login_failed" and e.get("reason") == "2fa_invalid_totp_code"
    ]
    assert len(events) >= 1
    event = events[0]
    assert event["target"]["email"] == "totp@test.com"
    assert "ip" in event["actor"]


@pytest.mark.asyncio
async def test_2fa_attempt_on_unknown_email_emits_audit_event(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """
    An unknown address is recorded too.

    This is the branch that matters most for spotting enumeration: the response
    is deliberately vague ("Invalid credentials or session expired") so it gives
    an attacker nothing, which is exactly why the log has to be specific.
    """
    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": "nobody@test.com", "code": "000000"},
    )
    assert response.status_code == 401

    events = [
        e
        for e in read_audit_log(configured_logging)
        if e.get("event") == "user_login_failed" and e.get("reason") == "2fa_user_not_found"
    ]
    assert len(events) >= 1
    assert events[0]["target"]["email"] == "nobody@test.com"


@pytest.mark.asyncio
async def test_2fa_on_account_without_2fa_emits_its_own_reason(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """
    A 2FA attempt on an account without 2FA logs its own reason.

    Three of the five rejection branches answer 401 with a different `detail`
    but the same status. If they all logged the same reason, the log would be
    no better than the status code.
    """
    user = await create_test_user(
        db_session,
        email="no2fa@test.com",
        password="CorrectPass123!",
    )

    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": "000000"},
    )
    assert response.status_code == 401

    reasons = {e.get("reason") for e in read_audit_log(configured_logging) if e.get("event") == "user_login_failed"}
    assert "2fa_not_enabled" in reasons
    assert "2fa_invalid_totp_code" not in reasons


@pytest.mark.asyncio
async def test_wrong_recovery_code_emits_audit_event(
    client: AsyncClient, db_session: AsyncSession, configured_logging: str
):
    """A wrong recovery code is its own branch, not folded into the TOTP one."""
    secret = pyotp.random_base32()
    user = await create_test_user(
        db_session,
        email="recovery@test.com",
        password="CorrectPass123!",
        totp_enabled=True,
        totp_secret=secret,
        totp_recovery_codes={"codes": ["$argon2id$fake"]},
    )

    # The password step of the login sets the proof cookie. This
    # test is about the wrong-code branch, not the missing-proof one.
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "CorrectPass123!"},
    )
    assert login.status_code == 200

    # 8 characters routes into the recovery-code branch.
    response = await client.post(
        "/api/auth/2fa/totp/authenticate",
        json={"email": user.email, "code": "ABCD1234"},
    )
    assert response.status_code == 401

    reasons = [e.get("reason") for e in read_audit_log(configured_logging) if e.get("event") == "user_login_failed"]
    assert "2fa_invalid_recovery_code" in reasons


# ============================================================================
# Two writers, one audit log
# ============================================================================


def test_two_handlers_alternating_appends_keep_chain(configured_logging: str):
    """
    Two AuditLogHandler instances on the same directory — the API process and
    a server command such as ``python -m app.cli demo-content load`` — append
    alternating entries, and the whole chain still verifies.

    Each handler's in-memory chain goes stale the moment the other one writes,
    so only taking the predecessor hash from the shared sidecar keeps the
    chain intact. An in-memory predecessor would link the second writer to a
    stale hash and verification would break at that entry.
    """
    from app.audit_handler import AuditLogHandler, get_handler
    from app.tools.verify_audit_log import verify_audit_log

    primary = get_handler()
    assert primary is not None

    # The second writer: a fresh handler on the same directory whose in-memory
    # chain starts at genesis — exactly what the CLI process brings along.
    secondary = AuditLogHandler(
        str(Path(configured_logging) / "audit.log"),
        when="midnight",
        interval=1,
        backupCount=365,
        encoding="utf-8",
        utc=True,
    )
    secondary.setFormatter(primary.formatter)
    secondary.setLevel(primary.level)
    # Mirrors the real split: the API process rotates, the one-shot CLI
    # command never does.
    secondary.rotation_owner = False

    def _record(event: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="audit",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=event,
            args=(),
            exc_info=None,
        )

    for index in range(4):
        writer = primary if index % 2 == 0 else secondary
        writer.emit(_record(f"alternating_event_{index}"))

    secondary.close()

    entries = read_audit_log(configured_logging)
    assert [e.get("event") for e in entries] == [f"alternating_event_{i}" for i in range(4)]
    assert verify_integrity_chain(entries) is True
    assert verify_audit_log(Path(configured_logging)) is True


def test_non_owner_write_lands_in_file_after_forced_rollover(configured_logging: str):
    """
    A forced rollover of the rotating (owner) handler between two writes of
    the non-rotating handler: the whole chain verifies across both files, and
    the non-owner's post-rollover entry is in the new file, not the archive.

    Two handlers that both rotate keep separate streams. After one rotates,
    the other would keep writing into the now-archived file with a predecessor
    that lives in the new one, and directory-order verification would break. A
    non-owner must never keep a stream across calls, or the same drift happens
    to it.
    """
    from app.audit_handler import AuditLogHandler, get_handler
    from app.tools.verify_audit_log import verify_audit_log

    owner = get_handler()
    assert owner is not None

    non_owner = AuditLogHandler(
        str(Path(configured_logging) / "audit.log"),
        when="midnight",
        interval=1,
        backupCount=365,
        encoding="utf-8",
        utc=True,
    )
    non_owner.setFormatter(owner.formatter)
    non_owner.setLevel(owner.level)
    non_owner.rotation_owner = False

    def _record(event: str) -> logging.LogRecord:
        return logging.LogRecord(
            name="audit",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=event,
            args=(),
            exc_info=None,
        )

    non_owner.emit(_record("before_rollover"))

    archive_glob = sorted(Path(configured_logging).glob("audit.log.*"))
    assert archive_glob == []

    with owner._audit_lock:  # type: ignore[attr-defined]
        owner._do_rollover_locked()  # type: ignore[attr-defined]

    non_owner.emit(_record("after_rollover"))
    owner.emit(_record("owner_after_rollover"))
    non_owner.close()

    archives = sorted(Path(configured_logging).glob("audit.log.*"))
    assert len(archives) == 1

    archived_entries = [json.loads(line) for line in archives[0].read_text().splitlines() if line.strip()]
    assert [e.get("event") for e in archived_entries] == ["before_rollover"]

    active_entries = read_audit_log(configured_logging)
    assert [e.get("event") for e in active_entries] == [
        "segment_start",
        "after_rollover",
        "owner_after_rollover",
    ]

    assert verify_audit_log(Path(configured_logging)) is True
