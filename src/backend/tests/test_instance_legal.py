"""
Instance legal texts.

Covers the operator endpoints GET/PUT /api/instance/legal (save is
stored and audited with the changed kinds, no row before the first save,
sanitisation, partial updates, explicit-null clearing, the wrong-realm and
anonymous refusals) and the public GET /api/public/legal/{kind} (all
three sources — organization, instance, none — an archived organization
falling back to the instance text, and the coded 404 for an unknown kind).

Also covers the singleton invariant of ``instance_settings`` itself: two
concurrent first writes leave exactly one row, and the database refuses a
second row outright.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.instance import create_instance_admin
from app.auth.tokens import create_instance_admin_token
from app.models.instance_admin import InstanceAdmin
from app.models.instance_setting import InstanceSetting
from app.models.organization import Organization
from app.models.user import UserRole
from app.repositories.instance_setting import InstanceSettingRepository
from app.repositories.organization import OrganizationRepository
from tests.factories import create_test_organization, create_test_user_with_org, get_auth_headers

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "OperatorPass123!"


class _AuditRecorder:
    """Spy that stands in for record_audit_event in the router module."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **kwargs: object) -> None:
        self.events.append({"event": event, **kwargs})

    def named(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]


async def _operator(db_session: AsyncSession) -> tuple[InstanceAdmin, dict[str, str]]:
    """Create an operator account and Bearer headers for its instance token."""
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    headers = {"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"}
    return admin, headers


@pytest.fixture
def audit_recorder(monkeypatch):
    from app.routers import instance as instance_module

    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)
    return recorder


# ============================================================================
# The operator saves its own legal texts
# ============================================================================


@pytest.mark.asyncio
async def test_get_before_any_save_answers_all_null(client: AsyncClient, db_session: AsyncSession):
    """No instance_settings row exists yet, so every text field is null."""
    _admin, headers = await _operator(db_session)

    response = await client.get("/api/instance/legal", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "legal_imprint": None,
        "legal_privacy": None,
        "legal_accessibility": None,
        "updated_at": None,
        "backup_retention_days": 30,
        "backup_retention_source": "default",
        "retention_days": {"LK_A": 90, "LK_B": 90, "LK_C": 365, "LK_H": 365},
        "technical_log_days": 90,
        "audit_log_days": 365,
        "abandoned_run_days": 60,
        "result_link_days": 90,
        # No place index in the test database and no upstream service: the
        # search asks nobody. The tile server is the default.
        "map_tile_url": "https://tile.openstreetmap.de/{z}/{x}/{y}.png",
        "address_search_sources": [],
    }


@pytest.mark.asyncio
async def test_legal_answer_carries_the_map_configuration_for_the_operator(
    client: AsyncClient, db_session: AsyncSession
):
    """The operator session gets the tile URL and the search sources.

    /api/geocode/config refuses an instance-only operator session, so the
    legal answer itself must carry what section 7 of the privacy template
    states. The 403 on the config endpoint proves it stays closed to the operator.
    """
    _admin, headers = await _operator(db_session)

    config_response = await client.get("/api/geocode/config", headers=headers)
    assert config_response.status_code == 403

    response = await client.get("/api/instance/legal", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["map_tile_url"] == "https://tile.openstreetmap.de/{z}/{x}/{y}.png"
    assert body["address_search_sources"] == []


@pytest.mark.asyncio
async def test_put_saves_and_audits_changed_kinds(client: AsyncClient, db_session: AsyncSession, audit_recorder):
    """Saving all three texts stores them and audits every changed kind."""
    _admin, headers = await _operator(db_session)

    response = await client.put(
        "/api/instance/legal",
        headers=headers,
        json={
            "legal_imprint": "<p>Impressum</p>",
            "legal_privacy": "<p>Datenschutz</p>",
            "legal_accessibility": "<p>Barrierefreiheit</p>",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["legal_imprint"] == "<p>Impressum</p>"
    assert body["legal_privacy"] == "<p>Datenschutz</p>"
    assert body["legal_accessibility"] == "<p>Barrierefreiheit</p>"
    assert body["updated_at"] is not None

    events = audit_recorder.named("instance.legal_updated")
    assert len(events) == 1
    assert events[0]["changed_kinds"] == ["accessibility", "imprint", "privacy"]
    assert events[0]["organization_id"] is None

    # A fresh GET reads the same values back.
    read_back = await client.get("/api/instance/legal", headers=headers)
    assert read_back.json()["legal_imprint"] == "<p>Impressum</p>"


@pytest.mark.asyncio
async def test_put_sanitizes_html_like_organization_legal_texts(
    client: AsyncClient, db_session: AsyncSession, audit_recorder
):
    """A <script> tag and a foreign image never survive the write."""
    _admin, headers = await _operator(db_session)

    response = await client.put(
        "/api/instance/legal",
        headers=headers,
        json={
            "legal_imprint": '<p>Hallo</p><script>alert(1)</script><img src="https://evil.example/x.png">',
        },
    )
    assert response.status_code == 200
    text = response.json()["legal_imprint"]
    assert "<script>" not in text
    assert "<img" not in text
    assert "<p>Hallo</p>" in text


@pytest.mark.asyncio
async def test_put_with_no_changes_is_not_audited(client: AsyncClient, db_session: AsyncSession, audit_recorder):
    """Saving the same value twice audits once; the second save changes nothing."""
    _admin, headers = await _operator(db_session)

    first = await client.put("/api/instance/legal", headers=headers, json={"legal_imprint": "<p>Impressum</p>"})
    assert first.status_code == 200

    second = await client.put("/api/instance/legal", headers=headers, json={"legal_imprint": "<p>Impressum</p>"})
    assert second.status_code == 200

    assert len(audit_recorder.named("instance.legal_updated")) == 1


@pytest.mark.asyncio
async def test_put_omitted_field_stays_untouched(client: AsyncClient, db_session: AsyncSession, audit_recorder):
    """A field not in the payload keeps its previous value."""
    _admin, headers = await _operator(db_session)

    await client.put(
        "/api/instance/legal",
        headers=headers,
        json={"legal_imprint": "<p>Impressum</p>", "legal_privacy": "<p>Datenschutz</p>"},
    )

    response = await client.put(
        "/api/instance/legal",
        headers=headers,
        json={"legal_privacy": "<p>Datenschutz v2</p>"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["legal_imprint"] == "<p>Impressum</p>"
    assert body["legal_privacy"] == "<p>Datenschutz v2</p>"

    events = audit_recorder.named("instance.legal_updated")
    assert events[-1]["changed_kinds"] == ["privacy"]


@pytest.mark.asyncio
async def test_put_explicit_null_clears_a_field(client: AsyncClient, db_session: AsyncSession, audit_recorder):
    """Sending null for a field that had a value clears it and is audited."""
    _admin, headers = await _operator(db_session)

    await client.put("/api/instance/legal", headers=headers, json={"legal_imprint": "<p>Impressum</p>"})

    response = await client.put("/api/instance/legal", headers=headers, json={"legal_imprint": None})
    assert response.status_code == 200
    assert response.json()["legal_imprint"] is None

    events = audit_recorder.named("instance.legal_updated")
    assert events[-1]["changed_kinds"] == ["imprint"]


@pytest.mark.asyncio
async def test_legal_endpoints_reject_missing_and_wrong_realm_tokens(client: AsyncClient, db_session: AsyncSession):
    """Anonymous callers get 401. An organization token gets 403, the wrong realm."""
    user, _org = await create_test_user_with_org(
        db_session, email="member@legal.example.com", org_slug="legal-member-org"
    )

    anonymous_get = await client.get("/api/instance/legal")
    assert anonymous_get.status_code == 401
    anonymous_put = await client.put("/api/instance/legal", json={"legal_imprint": "<p>x</p>"})
    assert anonymous_put.status_code == 401

    member_get = await client.get("/api/instance/legal", headers=get_auth_headers(user))
    assert member_get.status_code == 403
    member_put = await client.put(
        "/api/instance/legal", headers=get_auth_headers(user), json={"legal_imprint": "<p>x</p>"}
    )
    assert member_put.status_code == 403


# ============================================================================
# The public endpoint answers organization, instance, or none
# ============================================================================


async def _set_org_legal(db_session: AsyncSession, org: Organization, **fields: str | None) -> None:
    await OrganizationRepository(db_session).update(org.id, **fields)
    await db_session.commit()


async def _save_instance_legal(client: AsyncClient, headers: dict[str, str], **fields: str | None) -> None:
    response = await client.put("/api/instance/legal", headers=headers, json=fields)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_public_legal_prefers_the_organizations_own_text(client: AsyncClient, db_session: AsyncSession):
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Org", slug="pub-legal-org")
    await _set_org_legal(db_session, org, legal_imprint="<p>Org Impressum</p>")
    await _save_instance_legal(client, headers, legal_imprint="<p>Instance Impressum</p>")

    response = await client.get("/api/public/legal/imprint", params={"org": org.slug})
    assert response.status_code == 200
    body = response.json()
    assert body == {"kind": "imprint", "source": "organization", "text": "<p>Org Impressum</p>"}


@pytest.mark.asyncio
async def test_public_legal_falls_back_to_instance_when_org_has_none(client: AsyncClient, db_session: AsyncSession):
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Org", slug="pub-legal-noorg")
    await _save_instance_legal(client, headers, legal_privacy="<p>Instance Datenschutz</p>")

    response = await client.get("/api/public/legal/privacy", params={"org": org.slug})
    assert response.status_code == 200
    assert response.json() == {"kind": "privacy", "source": "instance", "text": "<p>Instance Datenschutz</p>"}


@pytest.mark.asyncio
async def test_public_legal_falls_back_to_instance_when_org_archived(client: AsyncClient, db_session: AsyncSession):
    """An archived organization's own text is not used even if it has one."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Archived Org", slug="pub-legal-archived")
    await _set_org_legal(db_session, org, legal_accessibility="<p>Org Barrierefreiheit</p>")
    await OrganizationRepository(db_session).update(org.id, archived_at=datetime.now(UTC))
    await db_session.commit()
    await _save_instance_legal(client, headers, legal_accessibility="<p>Instance Barrierefreiheit</p>")

    response = await client.get("/api/public/legal/accessibility", params={"org": org.slug})
    assert response.status_code == 200
    assert response.json() == {
        "kind": "accessibility",
        "source": "instance",
        "text": "<p>Instance Barrierefreiheit</p>",
    }


@pytest.mark.asyncio
async def test_public_legal_without_org_param_uses_instance_text(client: AsyncClient, db_session: AsyncSession):
    _admin, headers = await _operator(db_session)
    await _save_instance_legal(client, headers, legal_imprint="<p>Instance Impressum</p>")

    response = await client.get("/api/public/legal/imprint")
    assert response.status_code == 200
    assert response.json() == {"kind": "imprint", "source": "instance", "text": "<p>Instance Impressum</p>"}


@pytest.mark.asyncio
async def test_public_legal_source_none_when_neither_exists(client: AsyncClient, db_session: AsyncSession):
    org = await create_test_organization(db_session, name="Org", slug="pub-legal-none")

    response = await client.get("/api/public/legal/imprint", params={"org": org.slug})
    assert response.status_code == 200
    assert response.json() == {"kind": "imprint", "source": "none", "text": None}


@pytest.mark.asyncio
async def test_public_legal_unknown_kind_answers_404(client: AsyncClient, db_session: AsyncSession):
    response = await client.get("/api/public/legal/warranty")
    assert response.status_code == 404
    assert response.json()["error_code"] == "LEGAL_KIND_UNKNOWN"


@pytest.mark.asyncio
async def test_public_legal_unknown_org_slug_falls_back_to_instance(client: AsyncClient, db_session: AsyncSession):
    """An org slug that resolves to nothing behaves exactly like no org param."""
    _admin, headers = await _operator(db_session)
    await _save_instance_legal(client, headers, legal_imprint="<p>Instance Impressum</p>")

    response = await client.get("/api/public/legal/imprint", params={"org": "no-such-org"})
    assert response.status_code == 200
    assert response.json() == {"kind": "imprint", "source": "instance", "text": "<p>Instance Impressum</p>"}


# ============================================================================
# The singleton invariant of instance_settings itself: the database, not just
# the repository, refuses a second row.
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_first_writes_leave_exactly_one_row(db_session: AsyncSession, concurrent_client: AsyncClient):
    """Two concurrent first PUTs race on the same kind — the database's
    ``INSERT ... ON CONFLICT (id) DO UPDATE`` serializes them into exactly one
    row. Each handler re-reads (refreshes) its row after its own commit, and
    the other request's write may have landed in between — so a response can
    legitimately show either version, not necessarily the one it itself sent.
    What the singleton invariant actually promises, and all this asserts, is
    that both requests succeed and exactly one row survives, with one of the
    two versions — never two rows, never a corrupted mix."""
    _admin, headers = await _operator(db_session)
    await db_session.commit()

    responses = await asyncio.gather(
        concurrent_client.put("/api/instance/legal", headers=headers, json={"legal_imprint": "<p>Version A</p>"}),
        concurrent_client.put("/api/instance/legal", headers=headers, json={"legal_imprint": "<p>Version B</p>"}),
    )
    assert [r.status_code for r in responses] == [200, 200]

    rows = (await db_session.execute(select(InstanceSetting))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].legal_imprint in {"<p>Version A</p>", "<p>Version B</p>"}


@pytest.mark.asyncio
async def test_concurrent_repository_upserts_leave_exactly_one_row(test_engine, db_session: AsyncSession):
    """The same race, one level down: two repositories on two independent
    sessions upsert row 1 concurrently — still exactly one row."""
    from sqlalchemy.ext.asyncio import AsyncSession as AsyncSessionType
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_maker = async_sessionmaker(bind=test_engine, class_=AsyncSessionType, expire_on_commit=False)

    async def upsert_in_new_session(text: str) -> None:
        async with session_maker() as session:
            await InstanceSettingRepository(session).upsert(legal_privacy=text)
            await session.commit()

    await asyncio.gather(
        upsert_in_new_session("<p>Privacy A</p>"),
        upsert_in_new_session("<p>Privacy B</p>"),
    )

    rows = (await db_session.execute(select(InstanceSetting))).scalars().all()
    assert len(rows) == 1
    assert rows[0].legal_privacy in {"<p>Privacy A</p>", "<p>Privacy B</p>"}


@pytest.mark.asyncio
async def test_direct_insert_of_a_second_row_fails(db_session: AsyncSession):
    """A row with any id other than 1 violates ``ck_instance_settings_singleton``."""
    db_session.add(InstanceSetting(id=2, legal_imprint="<p>Sneaky</p>"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# The backup period: an installation setting that the privacy text states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_backup_retention_days_is_stored_audited_and_reverted(
    client: AsyncClient, db_session: AsyncSession, audit_recorder
):
    _admin, headers = await _operator(db_session)

    response = await client.put("/api/instance/legal", headers=headers, json={"backup_retention_days": 365})
    assert response.status_code == 200
    body = response.json()
    assert body["backup_retention_days"] == 365
    assert body["backup_retention_source"] == "instance"
    assert body["legal_privacy"] is None  # no text was touched

    events = audit_recorder.named("instance.legal_updated")
    assert len(events) == 1
    assert events[0]["changed_kinds"] == []
    assert events[0]["backup_retention_days"] == {"from": None, "to": 365}

    # A save of a text alone leaves the period as it is.
    await client.put("/api/instance/legal", headers=headers, json={"legal_imprint": "<p>Impressum</p>"})
    assert (await client.get("/api/instance/legal", headers=headers)).json()["backup_retention_days"] == 365

    # The same value again is no change and no audit event.
    await client.put("/api/instance/legal", headers=headers, json={"backup_retention_days": 365})
    assert len(audit_recorder.named("instance.legal_updated")) == 2

    # Explicit null reverts to BACKUP_ROTATION_DAYS.
    reverted = await client.put("/api/instance/legal", headers=headers, json={"backup_retention_days": None})
    assert reverted.json()["backup_retention_days"] == 30
    assert reverted.json()["backup_retention_source"] == "default"


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, -1, 3651])
async def test_put_backup_retention_days_out_of_bounds_is_refused(
    client: AsyncClient, db_session: AsyncSession, days: int
):
    _admin, headers = await _operator(db_session)
    response = await client.put("/api/instance/legal", headers=headers, json={"backup_retention_days": days})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_org_admin_reads_the_operators_backup_period(client: AsyncClient, db_session: AsyncSession):
    _admin, operator_headers = await _operator(db_session)
    user, org = await create_test_user_with_org(
        db_session, email="admin@backup.example.com", role=UserRole.ORG_ADMIN, org_slug="backup-period-org"
    )
    org_headers = get_auth_headers(user)

    before = await client.get(f"/api/orgs/{org.slug}/admin/retention", headers=org_headers)
    assert before.status_code == 200
    assert before.json()["backup_retention_days"] == 30

    await client.put("/api/instance/legal", headers=operator_headers, json={"backup_retention_days": 90})

    after = await client.get(f"/api/orgs/{org.slug}/admin/retention", headers=org_headers)
    assert after.json()["backup_retention_days"] == 90
    # The stated backup period is not the lower bound of a retention period.
    assert after.json()["min_days"] == before.json()["min_days"]


# ---------------------------------------------------------------------------
# The log periods the privacy template states: technical_log_days is
# the largest of the app, error and access log periods; audit_log_days is the
# security log period. Both come from the installation's settings.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instance_legal_states_log_periods_from_the_settings(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    _admin, headers = await _operator(db_session)

    response = await client.get("/api/instance/legal", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["technical_log_days"] == 90  # max(90, 90, 90) from the defaults
    assert body["audit_log_days"] == 365

    # A changed setting changes the answer: the largest technical period wins.
    monkeypatch.setattr("app.config.settings.logging.error_log_retention_days", 180)
    monkeypatch.setattr("app.config.settings.logging.audit_log_retention_days", 730)

    changed = await client.get("/api/instance/legal", headers=headers)
    assert changed.json()["technical_log_days"] == 180
    assert changed.json()["audit_log_days"] == 730


@pytest.mark.asyncio
async def test_instance_legal_states_the_sweep_periods_from_the_settings(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """The abandoned-run period and the result-link period come from the settings."""
    _admin, headers = await _operator(db_session)
    body = (await client.get("/api/instance/legal", headers=headers)).json()
    assert body["abandoned_run_days"] == 60
    assert body["result_link_days"] == 90

    monkeypatch.setattr("app.config.settings.retention.abandoned_run_days", 45)
    monkeypatch.setattr("app.config.settings.retention.result_link_days", 30)

    changed = (await client.get("/api/instance/legal", headers=headers)).json()
    assert changed["abandoned_run_days"] == 45
    assert changed["result_link_days"] == 30
