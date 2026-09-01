"""
Journey test for two organizations on one instance.

One journey through the real API: the operator creates "Augsburg" and
"München", both first admins accept their invitations (the token comes from
the test mail outbox, never minted directly), each admin builds a station, a
challenge, a rallye and a run, starts it, and a guest joins by code and
submits. From there the test asserts isolation in both directions for rallyes,
stations, challenges, runs, teams, submissions, scores, result links, users
and logs. It checks the operator list's counts and activity, archives
München, checks the blocked doors and the untouched sibling, then restores
it and checks it works again.

Audit events are written to a real temporary log directory (the same wiring
``test_audit.py`` uses) so the per-organization ``/admin/logs`` endpoint has
real entries to isolate, instead of hand-written fixture rows.
"""

import re
from typing import Any

import pytest
import pytest_asyncio
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.logging as _log_module
from app.auth.instance import create_instance_admin
from app.auth.tokens import create_instance_admin_token
from app.logging import configure_logging
from app.main import app
from app.models.user import User
from app.routers.admin import get_log_dir
from app.services import mail
from app.services.audit import reset_integrity_chain
from app.services.mail.transport import LoggingTransport
from tests.factories import get_auth_headers

OPERATOR_EMAIL = "operator@instance.example"
OPERATOR_PASSWORD = "OperatorPass123!"
ADMIN_PASSWORD = "AdminPass123!"

_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_-]+)")


@pytest_asyncio.fixture
async def recording_mail():
    """Install a transport that records messages instead of sending them."""
    transport = LoggingTransport()
    mail.set_transport(transport)
    yield transport
    mail.set_transport(None)


@pytest.fixture
def configured_audit_logging(tmp_path):
    """Wire real structured logging to a temp directory (as ``test_audit.py``
    does) so ``record_audit_event`` calls land on disk and the per-org
    ``/admin/logs`` endpoint has real entries to filter by organization."""
    import contextlib
    import logging as _logging

    structlog.reset_defaults()
    _log_module._logging_configured = False
    configure_logging(environment="development", log_dir=str(tmp_path))
    reset_integrity_chain()

    app.dependency_overrides[get_log_dir] = lambda: str(tmp_path)

    yield str(tmp_path)

    del app.dependency_overrides[get_log_dir]
    for name in ("audit", "app", "error", "access", ""):
        logger = _logging.getLogger(name)
        for handler in list(logger.handlers):
            with contextlib.suppress(Exception):
                handler.close()
        logger.handlers.clear()
    _log_module._logging_configured = False
    structlog.reset_defaults()


async def _operator(db_session: AsyncSession) -> dict[str, str]:
    """Create an operator account and Bearer headers for its instance token."""
    admin = await create_instance_admin(db_session, OPERATOR_EMAIL, OPERATOR_PASSWORD)
    return {"Authorization": f"Bearer {create_instance_admin_token(admin.id)}"}


async def _create_org(
    client: AsyncClient,
    operator_headers: dict[str, str],
    *,
    name: str,
    slug: str,
    admin_email: str,
) -> dict[str, Any]:
    """Create an organization through the operator endpoint."""
    response = await client.post(
        "/api/instance/organizations",
        headers=operator_headers,
        json={
            "name": name,
            "slug": slug,
            "content_languages": ["de", "en"],
            "first_admin_email": admin_email,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _extract_invite_token(recording_mail: LoggingTransport, to: str) -> str:
    """Pull the raw invitation token out of the mail the outbox recorded."""
    message = next(m for m in recording_mail.sent if m.to == to)
    match = _TOKEN_RE.search(message.text_body)
    assert match is not None, f"no accept-invite link found in the mail to {to}"
    return match.group(1)


async def _accept_invitation(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    token: str,
    display_name: str,
) -> User:
    """Redeem an invitation and return the resulting user row."""
    response = await client.post(
        f"/api/invitations/{token}/accept",
        json={"display_name": display_name, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    email = response.json()["email"]
    result = await db_session.execute(select(User).where(User.email == email))
    return result.scalar_one()


async def _build_and_start_run(
    client: AsyncClient,
    db_session: AsyncSession,
    slug: str,
    admin_headers: dict[str, str],
    *,
    label: str,
) -> dict[str, Any]:
    """Create a station, a challenge, a rallye and a run, and start it,
    through the real API, the way a first admin would."""
    station = await client.post(
        f"/api/orgs/{slug}/stations/",
        headers=admin_headers,
        json={
            "title": {"de": f"Station {label}", "en": f"Station {label}"},
            "description": {"de": "Beschreibung", "en": "Description"},
            "location_lat": 48.3705,
            "location_lng": 10.8978,
        },
    )
    assert station.status_code == 201, station.text
    station_id = station.json()["id"]

    challenge = await client.post(
        f"/api/orgs/{slug}/challenges/",
        headers=admin_headers,
        json={
            "challenge_type": "text",
            "title": {"de": f"Aufgabe {label}", "en": f"Challenge {label}"},
            "instructions": {"de": "Anleitung", "en": "Instructions"},
            "scoring_rules": {"max_points": 10},
        },
    )
    assert challenge.status_code == 201, challenge.text
    challenge_id = challenge.json()["id"]

    rallye = await client.post(
        f"/api/orgs/{slug}/rallyes/",
        headers=admin_headers,
        json={
            "title": {"de": f"Rallye {label}", "en": f"Rallye {label}"},
            "description": {"de": "Beschreibung", "en": "Description"},
        },
    )
    assert rallye.status_code == 201, rallye.text
    rallye_id = rallye.json()["id"]

    link_station = await client.post(
        f"/api/orgs/{slug}/rallyes/{rallye_id}/stations/import",
        headers=admin_headers,
        json={"station_id": station_id},
    )
    assert link_station.status_code == 201, link_station.text
    rallye_station_id = link_station.json()["link_id"]

    link_challenge = await client.post(
        f"/api/orgs/{slug}/rallyes/{rallye_id}/stations/{rallye_station_id}/challenges/import",
        headers=admin_headers,
        json={"challenge_id": challenge_id},
    )
    assert link_challenge.status_code == 201, link_challenge.text

    # The test client's get_db override hands every request the same session,
    # so the rallye_station loaded during the imports above is still cached
    # in the identity map with an empty challenge_links collection (real
    # request-scoped sessions in production never see this). Expire it so the
    # run's snapshot re-reads the composition it was just linked into.
    db_session.expire_all()

    run = await client.post(
        f"/api/orgs/{slug}/runs/",
        headers=admin_headers,
        json={"rallye_id": rallye_id, "name": f"Lauf {label}"},
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    join_code = run.json()["join_code"]

    start = await client.post(f"/api/orgs/{slug}/runs/{run_id}/start", headers=admin_headers)
    assert start.status_code == 200, start.text

    return {
        "station_id": station_id,
        "challenge_id": challenge_id,
        "rallye_id": rallye_id,
        "rallye_station_id": rallye_station_id,
        "run_id": run_id,
        "join_code": join_code,
    }


async def _guest_join_and_submit(
    client: AsyncClient,
    slug: str,
    run_id: str,
    *,
    join_code: str,
    guest_name: str,
    team_name: str,
) -> dict[str, Any]:
    """A guest joins a run by code and submits the run's one challenge."""
    join = await client.post(
        f"/api/join/{join_code}",
        json={"mode": "create", "display_name": guest_name, "team_name": team_name},
    )
    assert join.status_code == 201, join.text
    join_data = join.json()
    guest_headers = {"Authorization": f"Bearer {join_data['token']}"}

    stations = await client.get(f"/api/orgs/{slug}/runs/{run_id}/stations/", headers=guest_headers)
    assert stations.status_code == 200
    run_station_id = stations.json()["items"][0]["id"]

    challenges = await client.get(
        f"/api/orgs/{slug}/runs/{run_id}/stations/{run_station_id}/challenges/",
        headers=guest_headers,
    )
    assert challenges.status_code == 200
    run_challenge_id = challenges.json()["items"][0]["id"]

    submission = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/submissions/",
        headers=guest_headers,
        json={"run_challenge_id": run_challenge_id, "content": {"text": "Antwort"}},
    )
    assert submission.status_code == 201, submission.text

    return {
        "guest_headers": guest_headers,
        "team_id": join_data["team_id"],
        "submission_id": submission.json()["id"],
    }


async def _score_end_and_publish(
    client: AsyncClient,
    slug: str,
    admin_headers: dict[str, str],
    *,
    run_id: str,
    submission_id: str,
) -> str:
    """Score the one submission, end the run, publish its results. Returns the
    published result link's slug."""
    score = await client.post(
        f"/api/orgs/{slug}/runs/{run_id}/scores/",
        headers=admin_headers,
        json={"submission_id": submission_id, "points": 8},
    )
    assert score.status_code == 201, score.text

    end = await client.post(f"/api/orgs/{slug}/runs/{run_id}/end", headers=admin_headers)
    assert end.status_code == 200, end.text

    publish = await client.post(f"/api/orgs/{slug}/runs/{run_id}/results/publish", headers=admin_headers)
    assert publish.status_code == 200, publish.text
    return str(publish.json()["slug"])


@pytest.mark.asyncio
async def test_two_municipalities_run_on_one_instance(
    client: AsyncClient,
    db_session: AsyncSession,
    recording_mail: LoggingTransport,
    configured_audit_logging: str,
) -> None:
    """Two organizations run side by side, isolated in both directions, correctly
    counted, and correctly archived and restored."""
    operator_headers = await _operator(db_session)

    # ------------------------------------------------------------------
    # The operator creates both organizations, and both first admins
    # accept their invitations. The token comes from the mail outbox.
    # ------------------------------------------------------------------
    augsburg = await _create_org(
        client, operator_headers, name="Augsburg", slug="augsburg", admin_email="admin@augsburg.example"
    )
    muenchen = await _create_org(
        client, operator_headers, name="München", slug="muenchen", admin_email="admin@muenchen.example"
    )

    augsburg_token = _extract_invite_token(recording_mail, "admin@augsburg.example")
    muenchen_token = _extract_invite_token(recording_mail, "admin@muenchen.example")

    augsburg_admin = await _accept_invitation(client, db_session, token=augsburg_token, display_name="Augsburg Admin")
    muenchen_admin = await _accept_invitation(client, db_session, token=muenchen_token, display_name="München Admin")
    augsburg_headers = get_auth_headers(augsburg_admin)
    muenchen_headers = get_auth_headers(muenchen_admin)

    augsburg_run = await _build_and_start_run(client, db_session, augsburg["slug"], augsburg_headers, label="Augsburg")
    muenchen_run = await _build_and_start_run(client, db_session, muenchen["slug"], muenchen_headers, label="München")

    augsburg_guest = await _guest_join_and_submit(
        client,
        augsburg["slug"],
        augsburg_run["run_id"],
        join_code=augsburg_run["join_code"],
        guest_name="Gast Augsburg",
        team_name="Team Augsburg",
    )
    muenchen_guest = await _guest_join_and_submit(
        client,
        muenchen["slug"],
        muenchen_run["run_id"],
        join_code=muenchen_run["join_code"],
        guest_name="Gast München",
        team_name="Team München",
    )

    # Score, end and publish. This also creates the results link
    # whose isolation is checked below.
    augsburg_run["results_slug"] = await _score_end_and_publish(
        client,
        augsburg["slug"],
        augsburg_headers,
        run_id=augsburg_run["run_id"],
        submission_id=augsburg_guest["submission_id"],
    )
    muenchen_run["results_slug"] = await _score_end_and_publish(
        client,
        muenchen["slug"],
        muenchen_headers,
        run_id=muenchen_run["run_id"],
        submission_id=muenchen_guest["submission_id"],
    )

    # ------------------------------------------------------------------
    # Neither organization sees the other's rallyes, stations,
    # challenges, runs, teams, submissions, scores or result links.
    # ------------------------------------------------------------------
    directions = [
        (augsburg, augsburg_headers, muenchen_run),
        (muenchen, muenchen_headers, augsburg_run),
    ]
    for own, own_headers, other_run in directions:
        resp = await client.get(f"/api/orgs/{own['slug']}/rallyes/{other_run['rallye_id']}", headers=own_headers)
        assert resp.status_code == 404

        resp = await client.get(f"/api/orgs/{own['slug']}/stations/{other_run['station_id']}", headers=own_headers)
        assert resp.status_code == 404

        resp = await client.get(f"/api/orgs/{own['slug']}/challenges/{other_run['challenge_id']}", headers=own_headers)
        assert resp.status_code == 404

        resp = await client.get(f"/api/orgs/{own['slug']}/runs/{other_run['run_id']}", headers=own_headers)
        assert resp.status_code == 404

        resp = await client.get(f"/api/orgs/{own['slug']}/runs/{other_run['run_id']}/teams/", headers=own_headers)
        assert resp.status_code == 404

        resp = await client.get(f"/api/orgs/{own['slug']}/runs/{other_run['run_id']}/submissions/", headers=own_headers)
        assert resp.status_code == 404

        resp = await client.get(f"/api/orgs/{own['slug']}/runs/{other_run['run_id']}/scores/", headers=own_headers)
        assert resp.status_code == 404

        resp = await client.get(f"/api/orgs/{own['slug']}/runs/{other_run['run_id']}/results/link", headers=own_headers)
        assert resp.status_code == 404

        resp = await client.post(
            f"/api/orgs/{own['slug']}/runs/{other_run['run_id']}/results/publish", headers=own_headers
        )
        assert resp.status_code == 404

    # Users: each org's admin sees only its own organization's users.
    resp = await client.get(f"/api/orgs/{augsburg['slug']}/users/", headers=augsburg_headers)
    assert resp.status_code == 200
    augsburg_user_ids = {u["id"] for u in resp.json()["items"]}
    assert str(augsburg_admin.id) in augsburg_user_ids
    assert str(muenchen_admin.id) not in augsburg_user_ids

    resp = await client.get(f"/api/orgs/{muenchen['slug']}/users/", headers=muenchen_headers)
    assert resp.status_code == 200
    muenchen_user_ids = {u["id"] for u in resp.json()["items"]}
    assert str(muenchen_admin.id) in muenchen_user_ids
    assert str(augsburg_admin.id) not in muenchen_user_ids

    # Logs: each org's admin log holds only that org's audit events — the
    # real events emitted by every step above, read back off disk.
    resp = await client.get(f"/api/orgs/{augsburg['slug']}/admin/logs", headers=augsburg_headers, params={"limit": 200})
    assert resp.status_code == 200
    assert resp.json()["total"] > 0
    augsburg_log_org_ids = {item["organization_id"] for item in resp.json()["items"]}
    assert augsburg_log_org_ids == {augsburg["id"]}

    resp = await client.get(f"/api/orgs/{muenchen['slug']}/admin/logs", headers=muenchen_headers, params={"limit": 200})
    assert resp.status_code == 200
    assert resp.json()["total"] > 0
    muenchen_log_org_ids = {item["organization_id"] for item in resp.json()["items"]}
    assert muenchen_log_org_ids == {muenchen["id"]}

    # ------------------------------------------------------------------
    # The operator list shows both organizations with correct counts
    # and activity.
    # ------------------------------------------------------------------
    listing = await client.get("/api/instance/organizations", headers=operator_headers)
    assert listing.status_code == 200
    by_slug = {item["slug"]: item for item in listing.json()["items"]}

    for slug in ("augsburg", "muenchen"):
        item = by_slug[slug]
        assert item["is_archived"] is False
        assert item["org_admin_count"] == 1
        assert item["pending_admin_invitation_count"] == 0
        assert item["rallye_count"] == 1
        assert item["run_count"] == 1
        # Both runs were ended above, so none is active, but both happened.
        assert item["active_run_count"] == 0
        assert item["last_activity_at"] is not None
    # The admin plus the one guest each accepted/joined.
    assert by_slug["augsburg"]["user_count"] == 2
    assert by_slug["muenchen"]["user_count"] == 2

    # ------------------------------------------------------------------
    # Archiving München blocks its members and guests, hides its
    # public reads, leaves Augsburg untouched, and restoring brings it back.
    # ------------------------------------------------------------------
    archive = await client.post(f"/api/instance/organizations/{muenchen['slug']}/archive", headers=operator_headers)
    assert archive.status_code == 200
    assert archive.json()["is_archived"] is True

    # München's admin session no longer authorizes.
    blocked_admin = await client.get("/api/auth/me", headers=muenchen_headers)
    assert blocked_admin.status_code == 403
    assert blocked_admin.json()["error_code"] == "ORG_ARCHIVED"

    # München's existing guest is blocked too.
    blocked_guest = await client.get(
        f"/api/orgs/{muenchen['slug']}/runs/{muenchen_run['run_id']}/stations/",
        headers=muenchen_guest["guest_headers"],
    )
    assert blocked_guest.status_code == 403
    assert blocked_guest.json()["error_code"] == "ORG_ARCHIVED"

    # A new guest cannot join München's run anymore.
    late_join = await client.post(
        f"/api/join/{muenchen_run['join_code']}",
        json={"mode": "create", "display_name": "Zu spät", "team_name": "Zu spätes Team"},
    )
    assert late_join.status_code == 403
    assert late_join.json()["error_code"] == "ORG_ARCHIVED"

    # München's public reads disappear: the run lookup, the results link,
    # and the organization picker.
    assert (await client.get(f"/api/public/runs/{muenchen_run['join_code']}")).status_code == 404
    lookup = await client.post("/api/public/results/lookup", json={"slug": muenchen_run["results_slug"]})
    assert lookup.status_code == 404

    picker = await client.get("/api/public/organizations")
    assert picker.status_code == 200
    picker_slugs = {o["slug"] for o in picker.json()["organizations"]}
    assert muenchen["slug"] not in picker_slugs
    assert augsburg["slug"] in picker_slugs

    # Augsburg is untouched throughout.
    assert (await client.get("/api/auth/me", headers=augsburg_headers)).status_code == 200
    assert (await client.get(f"/api/public/runs/{augsburg_run['join_code']}")).status_code == 200
    augsburg_lookup = await client.post("/api/public/results/lookup", json={"slug": augsburg_run["results_slug"]})
    assert augsburg_lookup.status_code == 200

    # The data stays: the operator still sees München, now flagged archived.
    listing = await client.get("/api/instance/organizations", headers=operator_headers)
    by_slug = {item["slug"]: item for item in listing.json()["items"]}
    assert by_slug["muenchen"]["is_archived"] is True
    assert by_slug["muenchen"]["user_count"] == 2
    assert by_slug["augsburg"]["is_archived"] is False

    # Restore reverses everything.
    restore = await client.post(f"/api/instance/organizations/{muenchen['slug']}/restore", headers=operator_headers)
    assert restore.status_code == 200
    assert restore.json()["is_archived"] is False

    assert (await client.get("/api/auth/me", headers=muenchen_headers)).status_code == 200
    assert (await client.get(f"/api/public/runs/{muenchen_run['join_code']}")).status_code == 200
    restored_lookup = await client.post("/api/public/results/lookup", json={"slug": muenchen_run["results_slug"]})
    assert restored_lookup.status_code == 200

    # The final list shows both organizations, neither archived.
    listing = await client.get("/api/instance/organizations", headers=operator_headers)
    by_slug = {item["slug"]: item for item in listing.json()["items"]}
    assert by_slug["augsburg"]["is_archived"] is False
    assert by_slug["muenchen"]["is_archived"] is False
