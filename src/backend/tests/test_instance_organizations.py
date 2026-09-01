"""
Instance-organization management.

Covers the operator endpoints on /api/instance/organizations:

- The list with per-organization counts, the active-run count and the last-activity timestamp.
- Create with exactly one first-admin invitation and its audit event, and the taken-slug 409.
- The archive/restore pair: blocked password login, blocked guest join, an open
  session going dark, runs hidden from the public endpoints, and the WebSocket session guard.
- The PATCH endpoint that edits name and content languages: rename, language
  change, each coded refusal, the slug staying put, and the wrong-realm token.

Mail never leaves the process (LoggingTransport).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.instance import create_instance_admin
from app.auth.tokens import create_access_token, create_instance_admin_token
from app.config import get_settings
from app.models.challenge import ChallengeType
from app.models.organization import Organization
from app.models.rallye import Rallye
from app.models.run import Run, RunState
from app.models.run_challenge import RunChallenge
from app.models.run_station import RunStation
from app.models.submission import Submission
from app.models.team import Team
from app.models.user import User, UserRole
from app.models.user_invitation import UserInvitation
from app.repositories.organization import OrganizationRepository
from app.services import mail
from app.services.mail.transport import LoggingTransport
from tests.factories import (
    create_test_organization,
    create_test_user,
    create_test_user_with_org,
    get_auth_headers,
)

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "OperatorPass123!"
MEMBER_PASSWORD = "SecurePassword123!"


@pytest_asyncio.fixture
async def recording_mail():
    """Install a transport that records messages instead of sending them."""
    transport = LoggingTransport()
    mail.set_transport(transport)
    yield transport
    mail.set_transport(None)


class _AuditRecorder:
    """Spy that stands in for record_audit_event in the router module."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: str, **kwargs: object) -> None:
        self.events.append({"event": event, **kwargs})

    def named(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]


async def _operator(db_session: AsyncSession) -> tuple[object, dict[str, str]]:
    """Create an operator account and Bearer headers for its instance token."""
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    headers = {"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"}
    return admin, headers


async def _add_run(
    db_session: AsyncSession,
    org_id: UUID,
    join_code: str,
    *,
    state: RunState = RunState.ACTIVE,
    started_at: datetime | None = None,
) -> Run:
    run = Run(
        organization_id=org_id,
        rallye_id=None,
        name="Archiv Run",
        state=state,
        join_code=join_code,
        started_at=started_at,
        settings={},
        content_languages=["de"],
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


async def _add_submission(db_session: AsyncSession, org_id: UUID, run_id: UUID, *, created_at: datetime) -> Submission:
    """Insert one submission on ``run_id``, with the team/station/challenge it needs."""
    team = Team(organization_id=org_id, run_id=run_id, name="Team", color="#123456", share_code=uuid4().hex[:6].upper())
    db_session.add(team)
    await db_session.flush()

    station = RunStation(
        organization_id=org_id,
        run_id=run_id,
        source_station_id=None,
        title={"de": "Station"},
        description={},
        sort_order=0,
    )
    db_session.add(station)
    await db_session.flush()

    challenge = RunChallenge(
        organization_id=org_id,
        run_id=run_id,
        run_station_id=station.id,
        source_challenge_id=None,
        challenge_type=ChallengeType.TEXT,
        title={"de": "Challenge"},
        instructions={},
        scoring_rules={"max_points": 10},
        sort_order=0,
    )
    db_session.add(challenge)
    await db_session.flush()

    submission = Submission(
        organization_id=org_id,
        run_id=run_id,
        team_id=team.id,
        run_challenge_id=challenge.id,
        content={"text": "answer"},
        created_at=created_at,
    )
    db_session.add(submission)
    await db_session.commit()
    await db_session.refresh(submission)
    return submission


# ============================================================================
# List with counts
# ============================================================================


@pytest.mark.asyncio
async def test_operator_lists_organizations_with_counts(client: AsyncClient, db_session: AsyncSession):
    """The list shows every organization with aggregate counts — and nothing else."""
    _admin, headers = await _operator(db_session)

    org_a = await create_test_organization(db_session, name="Org A", slug="inst-a", languages=["de", "en"])
    org_b = await create_test_organization(db_session, name="Org B", slug="inst-b")

    await create_test_user(db_session, email="a1@example.com", organization=org_a)
    await create_test_user(db_session, email="a2@example.com", organization=org_a)
    await create_test_user(db_session, email="b1@example.com", organization=org_b)

    # Separate flushes: two rallyes in one batch hit an insertmanyvalues
    # sentinel collision on the identical server-default created_at.
    db_session.add(Rallye(organization_id=org_a.id, title={"de": "Rallye A"}, description={}))
    await db_session.flush()
    db_session.add(Rallye(organization_id=org_a.id, title={"de": "Rallye A2"}, description={}))
    await db_session.flush()
    db_session.add(
        Run(
            organization_id=org_a.id,
            rallye_id=None,
            name="Run A",
            state=RunState.CONFIGURED,
            join_code="INSTR1",
            settings={},
            content_languages=["de"],
        )
    )
    await db_session.commit()

    response = await client.get("/api/instance/organizations", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2

    by_slug = {item["slug"]: item for item in body["items"]}
    a = by_slug["inst-a"]
    assert a["name"] == "Org A"
    assert a["content_languages"] == ["de", "en"]
    assert a["is_archived"] is False
    assert a["created_at"] is not None
    assert a["user_count"] == 2
    assert a["rallye_count"] == 2
    assert a["run_count"] == 1

    b = by_slug["inst-b"]
    assert b["user_count"] == 1
    assert b["rallye_count"] == 0
    assert b["run_count"] == 0

    # Counts only: the item carries no user, team or submission data.
    assert set(a.keys()) == {
        "name",
        "slug",
        "content_languages",
        "created_at",
        "is_archived",
        "user_count",
        "rallye_count",
        "run_count",
        "org_admin_count",
        "pending_admin_invitation_count",
        "active_run_count",
        "last_activity_at",
        "deletion_due_at",
    }
    # Neither organization has a scheduled deletion.
    assert a["deletion_due_at"] is None
    assert b["deletion_due_at"] is None
    # Neither organization has an org_admin or an open admin invitation here.
    assert a["org_admin_count"] == 0
    assert a["pending_admin_invitation_count"] == 0
    assert b["org_admin_count"] == 0
    assert b["pending_admin_invitation_count"] == 0

    # Org A's run is CONFIGURED, not ACTIVE, and never started: no activity yet.
    assert a["active_run_count"] == 0
    assert a["last_activity_at"] is None
    assert b["active_run_count"] == 0
    assert b["last_activity_at"] is None


@pytest.mark.asyncio
async def test_list_shows_active_run_count_and_last_activity(client: AsyncClient, db_session: AsyncSession):
    """Each row's active-run count and last activity come from one grouped
    query per kind, computed independently per organization."""
    _admin, headers = await _operator(db_session)

    org_alpha = await create_test_organization(db_session, name="Aktiv Alpha", slug="akt-alpha")
    org_beta = await create_test_organization(db_session, name="Aktiv Beta", slug="akt-beta")
    org_gamma = await create_test_organization(db_session, name="Aktiv Gamma", slug="akt-gamma")

    now = datetime.now(UTC)
    run_started = now - timedelta(hours=2)
    submission_created = now - timedelta(hours=1)

    # Alpha: an active run, then a later submission on it — last activity is
    # the submission, not the run start.
    run_alpha = await _add_run(db_session, org_alpha.id, "AKTALP", state=RunState.ACTIVE, started_at=run_started)
    await _add_submission(db_session, org_alpha.id, run_alpha.id, created_at=submission_created)

    # Beta: a configured run that never started, no submissions — no
    # activity and no active run.
    await _add_run(db_session, org_beta.id, "AKTBET", state=RunState.CONFIGURED, started_at=None)

    # Gamma: a paused (no longer active) run that did start — it does not
    # count as active, but its start still counts as activity.
    await _add_run(db_session, org_gamma.id, "AKTGAM", state=RunState.PAUSED, started_at=run_started)

    response = await client.get("/api/instance/organizations", headers=headers)
    assert response.status_code == 200
    by_slug = {item["slug"]: item for item in response.json()["items"]}

    alpha = by_slug["akt-alpha"]
    assert alpha["active_run_count"] == 1
    assert datetime.fromisoformat(alpha["last_activity_at"].replace("Z", "+00:00")) == submission_created

    beta = by_slug["akt-beta"]
    assert beta["active_run_count"] == 0
    assert beta["last_activity_at"] is None

    gamma = by_slug["akt-gamma"]
    assert gamma["active_run_count"] == 0
    assert datetime.fromisoformat(gamma["last_activity_at"].replace("Z", "+00:00")) == run_started


# ============================================================================
# Create with first-admin invitation
# ============================================================================


@pytest.mark.asyncio
async def test_create_sends_exactly_one_org_admin_invitation(
    client: AsyncClient,
    db_session: AsyncSession,
    recording_mail: LoggingTransport,
    monkeypatch,
):
    """Create stores the org, exactly one org-admin invitation, and one mail."""
    from app.routers import instance as instance_module

    admin, headers = await _operator(db_session)
    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)

    response = await client.post(
        "/api/instance/organizations",
        headers=headers,
        json={
            "name": "Neue Organisation",
            "slug": "neue-org",
            "content_languages": ["de", "en"],
            "first_admin_email": "first.admin@example.com",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "neue-org"
    assert body["name"] == "Neue Organisation"
    assert body["is_archived"] is False

    # Exactly one invitation row — org admin, invited in the brand's name.
    result = await db_session.execute(select(UserInvitation))
    invitations = list(result.scalars().all())
    assert len(invitations) == 1
    invitation = invitations[0]
    assert invitation.email == "first.admin@example.com"
    assert invitation.role == UserRole.ORG_ADMIN
    assert invitation.organization_id == UUID(body["id"])
    assert invitation.invited_by_user_id is None
    assert invitation.invited_by_name == get_settings().brand_name
    assert invitation.language == "de"

    # Exactly one invitation mail, to the first admin.
    assert len(recording_mail.sent) == 1
    assert recording_mail.sent[0].to == "first.admin@example.com"

    # instance.org_created: operator as actor, no organization id, the slug named.
    created = recorder.named("instance.org_created")
    assert len(created) == 1
    assert created[0]["actor"]["instance_admin_id"] == str(admin.id)
    assert created[0]["organization_id"] is None
    assert created[0]["target_slug"] == "neue-org"

    # The raw token lives in the mail only — never in a response or audit entry.
    assert "token" not in body
    for entry in recorder.events:
        assert invitation.token_hash not in str(entry)


@pytest.mark.asyncio
async def test_create_with_taken_slug_answers_409(client: AsyncClient, db_session: AsyncSession):
    """A taken slug is a coded 409, and nothing is created alongside the refusal."""
    _admin, headers = await _operator(db_session)
    await create_test_organization(db_session, name="Bestehend", slug="vergeben")

    response = await client.post(
        "/api/instance/organizations",
        headers=headers,
        json={
            "name": "Zweite Org",
            "slug": "vergeben",
            "content_languages": ["de"],
            "first_admin_email": "admin@example.com",
        },
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "ORG_SLUG_TAKEN"

    # No invitation was sent for an organization that does not exist.
    result = await db_session.execute(select(UserInvitation))
    assert list(result.scalars().all()) == []


@pytest.mark.asyncio
async def test_create_with_soft_deleted_slug_answers_409(client: AsyncClient, db_session: AsyncSession):
    """A slug still held by a soft-deleted organization counts as taken: the unique constraint spans deleted rows, and the refused create
    leaves no invitation behind."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Alte Org", slug="alt-gewesen")
    await OrganizationRepository(db_session).soft_delete(org.id)
    await db_session.commit()

    response = await client.post(
        "/api/instance/organizations",
        headers=headers,
        json={
            "name": "Neue Org",
            "slug": "alt-gewesen",
            "content_languages": ["de"],
            "first_admin_email": "admin@example.com",
        },
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "ORG_SLUG_TAKEN"

    # No invitation was sent for an organization that does not exist.
    result = await db_session.execute(select(UserInvitation))
    assert list(result.scalars().all()) == []


@pytest.mark.asyncio
async def test_create_rejects_missing_and_wrong_realm_tokens(client: AsyncClient, db_session: AsyncSession):
    """Anonymous callers get 401; an organization token gets 403 — wrong realm."""
    user, _org = await create_test_user_with_org(db_session, email="member@realm.example.com", org_slug="realm-org")

    anonymous = await client.post(
        "/api/instance/organizations",
        json={
            "name": "Nope",
            "slug": "nope-org",
            "content_languages": ["de"],
            "first_admin_email": "a@example.com",
        },
    )
    assert anonymous.status_code == 401

    member = await client.post(
        "/api/instance/organizations",
        headers=get_auth_headers(user),
        json={
            "name": "Nope",
            "slug": "nope-org",
            "content_languages": ["de"],
            "first_admin_email": "a@example.com",
        },
    )
    assert member.status_code == 403


# ============================================================================
# The operator edits name and content languages
# ============================================================================


@pytest.mark.asyncio
async def test_update_renames_and_audits_changed_field_names(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """A rename changes the organization and audits the field name, not the value."""
    from app.routers import instance as instance_module

    admin, headers = await _operator(db_session)
    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)

    org = await create_test_organization(db_session, name="Alter Name", slug="update-org")

    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"name": "Neuer Name"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Neuer Name"
    assert body["slug"] == "update-org"

    await db_session.refresh(org)
    assert org.name == "Neuer Name"

    updated = recorder.named("instance.org_updated")
    assert len(updated) == 1
    assert updated[0]["actor"]["instance_admin_id"] == str(admin.id)
    assert updated[0]["organization_id"] is None
    assert updated[0]["target_slug"] == "update-org"
    assert updated[0]["changed_fields"] == ["name"]
    # No values of people (here: no name text) in the audit entry.
    assert "Neuer Name" not in str(updated[0])
    assert "Alter Name" not in str(updated[0])


@pytest.mark.asyncio
async def test_update_changes_content_languages(client: AsyncClient, db_session: AsyncSession):
    """Changing content_languages is applied and reported back."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Sprach Org", slug="lang-org", languages=["de"])

    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"content_languages": ["de", "en"]},
    )
    assert response.status_code == 200
    assert response.json()["content_languages"] == ["de", "en"]

    await db_session.refresh(org)
    assert org.languages == ["de", "en"]


@pytest.mark.asyncio
async def test_update_rejects_unknown_language_code(client: AsyncClient, db_session: AsyncSession):
    """An unsupported language code is a coded 422, and nothing changes."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Org", slug="bad-lang-org", languages=["de"])

    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"content_languages": ["de", "xx"]},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "ORG_LANGUAGE_UNSUPPORTED"

    await db_session.refresh(org)
    assert org.languages == ["de"]


@pytest.mark.asyncio
async def test_update_keeps_languages_the_organization_already_carries(client: AsyncClient, db_session: AsyncSession):
    """A rename must not fail on languages the organization's own admin enabled.

    The organization settings accept any code, and the edit dialog sends the
    whole list back. An organization that carries de, en, hi and th would
    otherwise be unable to rename.
    """
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(
        db_session, name="Stadt", slug="city-langs-org", languages=["de", "en", "hi", "th"]
    )

    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"name": "Stadt Neu", "content_languages": ["de", "en", "hi", "th"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Stadt Neu"
    assert response.json()["content_languages"] == ["de", "en", "hi", "th"]

    # Dropping a carried language works too.
    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"content_languages": ["de", "hi"]},
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(org)
    assert org.languages == ["de", "hi"]

    # A language that is neither carried nor supported stays refused.
    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"content_languages": ["de", "hi", "fr"]},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "ORG_LANGUAGE_UNSUPPORTED"
    await db_session.refresh(org)
    assert org.languages == ["de", "hi"]


@pytest.mark.asyncio
async def test_update_rejects_empty_language_list(client: AsyncClient, db_session: AsyncSession):
    """An empty content_languages list is a coded 422, and nothing changes."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Org", slug="empty-lang-org", languages=["de"])

    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"content_languages": []},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "ORG_LANGUAGES_EMPTY"

    await db_session.refresh(org)
    assert org.languages == ["de"]


@pytest.mark.asyncio
async def test_update_rejects_blank_name(client: AsyncClient, db_session: AsyncSession):
    """A blank (or whitespace-only) name is a coded 422, and nothing changes."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Org", slug="blank-name-org")

    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"name": "   "},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "ORG_NAME_BLANK"

    await db_session.refresh(org)
    assert org.name == "Org"


@pytest.mark.asyncio
async def test_update_works_for_archived_organization(client: AsyncClient, db_session: AsyncSession):
    """Editing an archived organization still works."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Archiv Org", slug="archived-update-org")
    await OrganizationRepository(db_session).update(org.id, archived_at=datetime.now(UTC))
    await db_session.commit()

    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"name": "Archiv Org Neu"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Archiv Org Neu"
    assert response.json()["is_archived"] is True


@pytest.mark.asyncio
async def test_update_ignores_slug_in_body_and_slug_stays(client: AsyncClient, db_session: AsyncSession):
    """The slug is not accepted: a slug in the body is ignored, and the URL slug decides the target."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Org", slug="stays-put")

    response = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=headers,
        json={"name": "Org Neu", "slug": "should-be-ignored"},
    )
    assert response.status_code == 200
    assert response.json()["slug"] == "stays-put"

    await db_session.refresh(org)
    assert org.slug == "stays-put"


@pytest.mark.asyncio
async def test_update_rejects_missing_and_wrong_realm_tokens(client: AsyncClient, db_session: AsyncSession):
    """Anonymous callers get 401; an organization token gets 403 — wrong realm."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Org", slug="realm-update-org")
    user, _member_org = await create_test_user_with_org(
        db_session, email="member@realm-update.example.com", org_slug="realm-update-member-org"
    )

    anonymous = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        json={"name": "Nope"},
    )
    assert anonymous.status_code == 401

    member = await client.patch(
        f"/api/instance/organizations/{org.slug}",
        headers=get_auth_headers(user),
        json={"name": "Nope"},
    )
    assert member.status_code == 403

    await db_session.refresh(org)
    assert org.name == "Org"


@pytest.mark.asyncio
async def test_update_unknown_slug_answers_404(client: AsyncClient, db_session: AsyncSession):
    _admin, headers = await _operator(db_session)
    response = await client.patch(
        "/api/instance/organizations/keine-org",
        headers=headers,
        json={"name": "Egal"},
    )
    assert response.status_code == 404


# ============================================================================
# The operator replaces a lost organization admin
# ============================================================================


@pytest.mark.asyncio
async def test_admin_invitation_sends_one_mail_and_audits_without_address(
    client: AsyncClient,
    db_session: AsyncSession,
    recording_mail: LoggingTransport,
    monkeypatch,
):
    """Inviting an org admin sends one mail and audits the slug, never the address."""
    from app.routers import instance as instance_module

    admin, headers = await _operator(db_session)
    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)

    org = await create_test_organization(db_session, name="Ersatz Org", slug="ersatz-org", languages=["de", "en"])

    response = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=headers,
        json={"email": "neuer.admin@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "neuer.admin@example.com"
    assert body["language"] == "de"
    assert "token" not in body

    result = await db_session.execute(select(UserInvitation).where(UserInvitation.organization_id == org.id))
    invitations = list(result.scalars().all())
    assert len(invitations) == 1
    invitation = invitations[0]
    assert invitation.email == "neuer.admin@example.com"
    assert invitation.role == UserRole.ORG_ADMIN
    assert invitation.invited_by_user_id is None
    assert invitation.invited_by_name == get_settings().brand_name

    # Exactly one invitation mail, to the invited address.
    assert len(recording_mail.sent) == 1
    assert recording_mail.sent[0].to == "neuer.admin@example.com"

    # instance.org_admin_invited: operator as actor, no organization id, the slug named.
    invited = recorder.named("instance.org_admin_invited")
    assert len(invited) == 1
    assert invited[0]["actor"]["instance_admin_id"] == str(admin.id)
    assert invited[0]["organization_id"] is None
    assert invited[0]["target_slug"] == "ersatz-org"

    # The invited address appears in neither the audit record nor the token.
    for entry in recorder.events:
        assert "neuer.admin@example.com" not in str(entry)
        assert invitation.token_hash not in str(entry)


@pytest.mark.asyncio
async def test_second_admin_invitation_renews_instead_of_duplicating(
    client: AsyncClient,
    db_session: AsyncSession,
    recording_mail: LoggingTransport,
):
    """A second invite for the same address renews the open one instead of duplicating it."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Ersatz Org 2", slug="ersatz-org-2", languages=["de", "en"])

    first = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=headers,
        json={"email": "wieder.admin@example.com"},
    )
    assert first.status_code == 201
    first_body = first.json()

    second = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=headers,
        json={"email": "wieder.admin@example.com", "language": "en"},
    )
    assert second.status_code == 201
    second_body = second.json()

    # Same invitation row, renewed — not a second one.
    assert second_body["id"] == first_body["id"]
    assert second_body["language"] == "en"

    result = await db_session.execute(select(UserInvitation).where(UserInvitation.email == "wieder.admin@example.com"))
    invitations = list(result.scalars().all())
    assert len(invitations) == 1
    assert invitations[0].language == "en"

    # One mail per call — the renewal still sends a fresh invitation mail.
    assert len(recording_mail.sent) == 2


@pytest.mark.asyncio
async def test_operator_refuses_an_address_that_already_has_an_account(client: AsyncClient, db_session: AsyncSession):
    """An address with an account anywhere cannot accept an invitation, so the operator learns it at once.

    Otherwise the invited person would meet a dead end on the accept page.
    Both the admin invitation and the first admin of a new organization are
    refused with a code, and no invitation, no organization and no mail are left behind.
    """
    _admin, headers = await _operator(db_session)
    other = await create_test_organization(db_session, name="Andere", slug="account-elsewhere-org")
    await create_test_user(db_session, email="vergeben@example.com", organization=other)
    target = await create_test_organization(db_session, name="Ziel", slug="account-target-org")

    invite = await client.post(
        f"/api/instance/organizations/{target.slug}/admin-invitations",
        headers=headers,
        json={"email": "Vergeben@Example.com"},
    )
    assert invite.status_code == 409
    assert invite.json()["error_code"] == "EMAIL_HAS_ACCOUNT"

    create = await client.post(
        "/api/instance/organizations",
        headers=headers,
        json={
            "name": "Neue Org",
            "slug": "neue-org-account",
            "content_languages": ["de"],
            "first_admin_email": "vergeben@example.com",
        },
    )
    assert create.status_code == 409
    assert create.json()["error_code"] == "EMAIL_HAS_ACCOUNT"

    result = await db_session.execute(select(UserInvitation).where(UserInvitation.email == "vergeben@example.com"))
    assert list(result.scalars().all()) == []
    result = await db_session.execute(select(Organization).where(Organization.slug == "neue-org-account"))
    assert result.scalars().first() is None


@pytest.mark.asyncio
async def test_admin_invitation_for_address_in_another_organization_answers_409(
    client: AsyncClient, db_session: AsyncSession
):
    """An address already invited elsewhere is refused, not silently rerouted."""
    _admin, headers = await _operator(db_session)
    org_a = await create_test_organization(db_session, name="Org A", slug="admin-inv-org-a")
    org_b = await create_test_organization(db_session, name="Org B", slug="admin-inv-org-b")

    first = await client.post(
        f"/api/instance/organizations/{org_a.slug}/admin-invitations",
        headers=headers,
        json={"email": "geteilt@example.com"},
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/instance/organizations/{org_b.slug}/admin-invitations",
        headers=headers,
        json={"email": "geteilt@example.com"},
    )
    assert second.status_code == 409
    assert second.json()["error_code"] == "EMAIL_HAS_PENDING_INVITATION"

    result = await db_session.execute(select(UserInvitation).where(UserInvitation.email == "geteilt@example.com"))
    invitations = list(result.scalars().all())
    assert len(invitations) == 1
    assert invitations[0].organization_id == org_a.id


@pytest.mark.asyncio
async def test_admin_invitation_for_archived_organization_answers_409(client: AsyncClient, db_session: AsyncSession):
    """An archived organization refuses the invite with the coded error."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Archiv Ziel", slug="archiv-ziel")
    await OrganizationRepository(db_session).update(org.id, archived_at=datetime.now(UTC))
    await db_session.commit()

    response = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=headers,
        json={"email": "egal@example.com"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "ORG_ARCHIVED"

    result = await db_session.execute(select(UserInvitation).where(UserInvitation.organization_id == org.id))
    assert list(result.scalars().all()) == []


@pytest.mark.asyncio
async def test_admin_invitation_unknown_slug_answers_404(client: AsyncClient, db_session: AsyncSession):
    _admin, headers = await _operator(db_session)
    response = await client.post(
        "/api/instance/organizations/keine-org/admin-invitations",
        headers=headers,
        json={"email": "egal@example.com"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_invitation_rejects_missing_and_wrong_realm_tokens(client: AsyncClient, db_session: AsyncSession):
    """Anonymous callers get 401; an organization token gets 403 — wrong realm."""
    org = await create_test_organization(db_session, name="Realm Org", slug="admin-inv-realm-org")
    user, _member_org = await create_test_user_with_org(
        db_session, email="member@admin-inv-realm.example.com", org_slug="admin-inv-realm-member-org"
    )

    anonymous = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        json={"email": "a@example.com"},
    )
    assert anonymous.status_code == 401

    member = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=get_auth_headers(user),
        json={"email": "a@example.com"},
    )
    assert member.status_code == 403

    result = await db_session.execute(select(UserInvitation).where(UserInvitation.organization_id == org.id))
    assert list(result.scalars().all()) == []


@pytest.mark.asyncio
async def test_list_counts_org_admins_and_pending_admin_invitations(client: AsyncClient, db_session: AsyncSession):
    """The list shows the number of org admins and open admin invitations, never an address."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Zaehl Org", slug="zaehl-org")

    await create_test_user(db_session, email="admin1@example.com", organization=org, role=UserRole.ORG_ADMIN)
    await create_test_user(db_session, email="admin2@example.com", organization=org, role=UserRole.ORG_ADMIN)
    await create_test_user(db_session, email="participant@example.com", organization=org, role=UserRole.PARTICIPANT)

    invite_response = await client.post(
        f"/api/instance/organizations/{org.slug}/admin-invitations",
        headers=headers,
        json={"email": "offen@example.com"},
    )
    assert invite_response.status_code == 201

    response = await client.get("/api/instance/organizations", headers=headers)
    assert response.status_code == 200
    by_slug = {item["slug"]: item for item in response.json()["items"]}
    item = by_slug["zaehl-org"]
    assert item["org_admin_count"] == 2
    assert item["pending_admin_invitation_count"] == 1
    assert "offen@example.com" not in str(item)


@pytest.mark.asyncio
async def test_list_counts_only_org_admins_that_can_sign_in(client: AsyncClient, db_session: AsyncSession):
    """A deactivated org admin does not count: one active and one deactivated administrator show 1."""
    _admin, headers = await _operator(db_session)
    org = await create_test_organization(db_session, name="Uebernahme Org", slug="uebernahme-org")

    await create_test_user(db_session, email="neu@example.com", organization=org, role=UserRole.ORG_ADMIN)
    await create_test_user(
        db_session, email="alt@example.com", organization=org, role=UserRole.ORG_ADMIN, is_active=False
    )

    response = await client.get("/api/instance/organizations", headers=headers)
    assert response.status_code == 200
    by_slug = {item["slug"]: item for item in response.json()["items"]}
    assert by_slug["uebernahme-org"]["org_admin_count"] == 1


# ============================================================================
# Archive blocks logins, sessions, guest join and public visibility
# ============================================================================


@pytest.mark.asyncio
async def test_archive_blocks_logins_sessions_guest_join_and_hides_runs(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
):
    """Archiving shuts every door: password login, guest join, open sessions, public runs."""
    from app.routers import instance as instance_module

    _admin, headers = await _operator(db_session)
    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)

    user, org = await create_test_user_with_org(
        db_session,
        email="mitglied@example.com",
        org_slug="archiv-org",
    )
    run = await _add_run(db_session, org.id, "ARCHIV")

    # Before the archive: password login works and opens the session.
    login = await client.post(
        "/api/auth/login",
        json={"email": "mitglied@example.com", "password": MEMBER_PASSWORD},
    )
    assert login.status_code == 200
    assert login.json()["status"] == "success"
    assert (await client.get("/api/auth/me")).status_code == 200

    # Archive through the operator endpoint.
    archive = await client.post("/api/instance/organizations/archiv-org/archive", headers=headers)
    assert archive.status_code == 200
    assert archive.json()["is_archived"] is True

    archived_events = recorder.named("instance.org_archived")
    assert len(archived_events) == 1
    assert archived_events[0]["organization_id"] is None
    assert archived_events[0]["target_slug"] == "archiv-org"

    # The password login is blocked with the coded error.
    blocked_login = await client.post(
        "/api/auth/login",
        json={"email": "mitglied@example.com", "password": MEMBER_PASSWORD},
    )
    assert blocked_login.status_code == 403
    assert blocked_login.json()["error_code"] == "ORG_ARCHIVED"

    # The session opened before the archive stops authorizing.
    open_session = await client.get("/api/auth/me")
    assert open_session.status_code == 403
    assert open_session.json()["error_code"] == "ORG_ARCHIVED"

    # The guest join is blocked with the same coded error.
    guest_join = await client.post(
        "/api/join/ARCHIV",
        json={"mode": "create", "display_name": "Gast", "team_name": "Team Gast"},
    )
    assert guest_join.status_code == 403
    assert guest_join.json()["error_code"] == "ORG_ARCHIVED"

    # The run leaves the public lookup and the org leaves the picker.
    assert (await client.get(f"/api/public/runs/{run.join_code}")).status_code == 404
    picker = await client.get("/api/public/organizations")
    assert picker.status_code == 200
    assert "archiv-org" not in [o["slug"] for o in picker.json()["organizations"]]

    # The data stays: the operator still sees the org, now flagged archived.
    listing = await client.get("/api/instance/organizations", headers=headers)
    assert listing.status_code == 200
    by_slug = {item["slug"]: item for item in listing.json()["items"]}
    assert by_slug["archiv-org"]["is_archived"] is True
    assert by_slug["archiv-org"]["run_count"] == 1

    # Restore reverses everything and is audited.
    restore = await client.post("/api/instance/organizations/archiv-org/restore", headers=headers)
    assert restore.status_code == 200
    assert restore.json()["is_archived"] is False

    restored_events = recorder.named("instance.org_restored")
    assert len(restored_events) == 1
    assert restored_events[0]["organization_id"] is None
    assert restored_events[0]["target_slug"] == "archiv-org"

    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await client.get(f"/api/public/runs/{run.join_code}")).status_code == 200


@pytest.mark.asyncio
async def test_self_signup_into_archived_organization_refused(client: AsyncClient, db_session: AsyncSession):
    """Self-registration into an archived organization is a coded 403,
    answered before a user is created or a cookie is set."""
    _user, org = await create_test_user_with_org(
        db_session,
        email="vorhanden@example.com",
        org_slug="reg-archiv-org",
    )
    # Re-fetch through the identity map: the user-creating helper's refresh
    # reloaded the organization relationship, and mutating the stale factory
    # instance would trip the identity-map guard (SAWarning as error).
    org = await db_session.get(Organization, org.id)
    assert org is not None
    org.archived_at = datetime.now(UTC)
    await db_session.commit()

    response = await client.post(
        "/api/auth/register",
        json={
            "email": "neu@example.com",
            "password": MEMBER_PASSWORD,
            "display_name": "Neues Mitglied",
            "organization_slug": "reg-archiv-org",
        },
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "ORG_ARCHIVED"
    assert "set-cookie" not in response.headers

    # Nothing was created: no user, and the caller has no session.
    result = await db_session.execute(select(User).where(User.email == "neu@example.com"))
    assert list(result.scalars().all()) == []
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_archive_and_restore_are_idempotent_without_duplicate_audit(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
):
    """Repeating an action changes nothing and audits nothing — only transitions do."""
    from app.routers import instance as instance_module

    _admin, headers = await _operator(db_session)
    recorder = _AuditRecorder()
    monkeypatch.setattr(instance_module, "record_audit_event", recorder)

    _user, org = await create_test_user_with_org(db_session, email="m@example.com", org_slug="idem-org")

    # Restore on an active org: no-op.
    restore_first = await client.post("/api/instance/organizations/idem-org/restore", headers=headers)
    assert restore_first.status_code == 200
    assert restore_first.json()["is_archived"] is False
    assert recorder.named("instance.org_restored") == []

    await client.post("/api/instance/organizations/idem-org/archive", headers=headers)
    recorder.events.clear()

    # Archive on an archived org: no-op.
    archive_again = await client.post("/api/instance/organizations/idem-org/archive", headers=headers)
    assert archive_again.status_code == 200
    assert recorder.named("instance.org_archived") == []

    await db_session.refresh(org)
    assert org.archived_at is not None


@pytest.mark.asyncio
async def test_archive_unknown_slug_answers_404(client: AsyncClient, db_session: AsyncSession):
    _admin, headers = await _operator(db_session)
    response = await client.post("/api/instance/organizations/keine-org/archive", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_websocket_of_archived_organization_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    ws_client: AsyncClient,
):
    """A token from before the archive opens no WebSocket: closed 1008 like every
    other rejected token."""
    _admin, headers = await _operator(db_session)
    user, org = await create_test_user_with_org(db_session, email="ws@example.com", org_slug="ws-archiv-org")
    run = await _add_run(db_session, org.id, "WSARCH")

    token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        organization_id=user.organization_id,
    )

    await client.post("/api/instance/organizations/ws-archiv-org/archive", headers=headers)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with aconnect_ws(
            f"/api/orgs/ws-archiv-org/runs/{run.id}/ws?token={token}",
            ws_client,
        ):
            pass

    assert exc_info.value.code == 1008
    assert "Organization archived" in exc_info.value.reason
