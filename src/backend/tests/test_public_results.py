"""Tests for published run-result links.

Covers the coordinator-facing publish/status/revoke endpoints
(``routers/runs.py``) and the public, unauthenticated read endpoint
(``routers/public.py``). Also checks two security rules:

- The slug itself is never persisted or written to the audit log. Only ``slug_hash`` is stored,
  and the slug is recomputed at request time from the row's ``id`` (``app/repositories/run_result_link.py``).
- The slug travels in the POST body of ``/api/public/results/lookup``, never the URL,
  so it cannot appear in an access-log request line the way a path or query value would.
"""

import asyncio
import contextlib
import json
import logging as _logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import app.logging as _log_module
from app.config import settings
from app.logging import configure_logging
from app.models.feed_event import FeedEvent, FeedEventType
from app.models.run_result_link import RunResultLink
from app.repositories.feed_event import FeedEventRepository
from app.services.audit import reset_integrity_chain
from app.services.retention_tasks import sweep_expired_result_links
from app.services.ws_manager import manager as ws_manager
from tests.journey_helpers import (
    JourneyEnv,
    activate_run,
    create_team_and_join,
    setup_journey_environment,
    submit_challenge,
)


@pytest.fixture
def configured_logging():
    """Audit logging into a temp dir (same shape as test_retention_sweep.py)."""
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


def _read_audit_log(log_dir: str) -> list[dict]:
    audit_log_path = Path(log_dir) / "audit.log"
    if not audit_log_path.exists():
        return []
    with open(audit_log_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def _setup_run_to_ended_state(
    client: AsyncClient,
    db_session: AsyncSession,
    org_slug: str,
    content_languages: list[str] | None = None,
) -> tuple[JourneyEnv, str, str, str]:
    """One rallye (1 station, 1 challenge), one team, one unscored submission.

    Returns (env, run_id, run_challenge_id, submission_id). The run is
    started but not yet ended — callers score and/or end it as needed.
    """
    env = await setup_journey_environment(
        db_session, client, org_slug=org_slug, num_stations=1, challenges_per_station=1
    )
    coordinator_headers = env.auth_headers["coordinator"]

    run_body: dict[str, Any] = {"rallye_id": str(env.rallye_id), "name": "Test Run"}
    if content_languages is not None:
        run_body["content_languages"] = content_languages

    run_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator_headers,
        json=run_body,
    )
    run_id = run_resp.json()["id"]
    await activate_run(client, env.slug, run_id, coordinator_headers)

    stations_resp = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/stations/", headers=coordinator_headers)
    run_station_id = stations_resp.json()["items"][0]["id"]
    challenges_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{run_station_id}/challenges/", headers=coordinator_headers
    )
    run_challenge_id = challenges_resp.json()["items"][0]["id"]

    await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant1"], "Team Alpha", "#FF0000")
    submission = await submit_challenge(
        client, env.slug, run_id, env.auth_headers["participant1"], run_challenge_id, {"text": "Answer"}
    )

    return env, run_id, run_challenge_id, submission["id"]


async def _publish_ready_run(
    client: AsyncClient,
    db_session: AsyncSession,
    org_slug: str,
    content_languages: list[str] | None = None,
) -> tuple[JourneyEnv, str, str]:
    """An ended run with one team, one scored submission — ready to publish.

    Returns (env, run_id, score_id).
    """
    env, run_id, _run_challenge_id, submission_id = await _setup_run_to_ended_state(
        client, db_session, org_slug, content_languages=content_languages
    )
    coordinator_headers = env.auth_headers["coordinator"]

    score_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={"submission_id": submission_id, "points": 10, "comment": "Great"},
    )
    assert score_resp.status_code == 201
    score_id = score_resp.json()["id"]

    end_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coordinator_headers)
    assert end_resp.status_code == 200

    return env, run_id, score_id


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_refused_while_unscored(client: AsyncClient, db_session: AsyncSession):
    env, run_id, _run_challenge_id, _submission_id = await _setup_run_to_ended_state(
        client, db_session, "results-unscored"
    )
    coordinator_headers = env.auth_headers["coordinator"]

    end_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coordinator_headers)
    assert end_resp.status_code == 200

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    assert publish_resp.status_code == 409


@pytest.mark.asyncio
async def test_publish_not_ended_refused(client: AsyncClient, db_session: AsyncSession):
    env, run_id, _run_challenge_id, submission_id = await _setup_run_to_ended_state(
        client, db_session, "results-not-ended"
    )
    coordinator_headers = env.auth_headers["coordinator"]

    score_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator_headers,
        json={"submission_id": submission_id, "points": 10},
    )
    assert score_resp.status_code == 201

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    assert publish_resp.status_code == 409


@pytest.mark.asyncio
async def test_publish_creates_link(client: AsyncClient, db_session: AsyncSession, configured_logging: str):
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-publish")
    coordinator_headers = env.auth_headers["coordinator"]

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    assert publish_resp.status_code == 200
    data = publish_resp.json()

    assert data["slug"]
    assert len(data["slug"]) == 22
    # Fragment, not a path segment: a path/query slug would land in every access-log request line.
    assert data["url"] == f"{settings.mail.public_base_url}/results#{data['slug']}"
    assert f"/results/{data['slug']}" not in data["url"]
    assert data["published_at"]
    assert data["expires_at"]

    # The row carries no plaintext slug column at all.
    column_names = set(RunResultLink.__table__.columns.keys())
    assert "slug" not in column_names
    assert "slug_hash" in column_names

    row = (await db_session.execute(select(RunResultLink).where(RunResultLink.run_id == run_id))).scalar_one()
    assert row.slug_hash != data["slug"]

    # The audit log records the event, but never the slug string.
    audit_entries = _read_audit_log(configured_logging)
    published_events = [e for e in audit_entries if e.get("event") == "run.results_published"]
    assert len(published_events) == 1
    serialized = json.dumps(published_events[0])
    assert data["slug"] not in serialized

    # The status endpoint reflects "published" with the same url.
    status_resp = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/results/link", headers=coordinator_headers)
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "published"
    assert status_data["url"] == data["url"]
    assert status_data["expires_at"] == data["expires_at"]


@pytest.mark.asyncio
async def test_publish_uses_run_first_content_language(client: AsyncClient, db_session: AsyncSession):
    """The public snapshot carries the run's first content language: an English-first run
    publishes ``language == "en"``, and the public lookup returns it unchanged."""
    env, run_id, _score_id = await _publish_ready_run(
        client, db_session, "results-language-en-first", content_languages=["en", "de"]
    )
    coordinator_headers = env.auth_headers["coordinator"]

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    assert publish_resp.status_code == 200
    slug = publish_resp.json()["slug"]

    public_resp = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert public_resp.status_code == 200
    assert public_resp.json()["language"] == "en"


@pytest.mark.asyncio
async def test_republish_keeps_slug_and_replaces_snapshot(client: AsyncClient, db_session: AsyncSession):
    env, run_id, score_id = await _publish_ready_run(client, db_session, "results-republish")
    coordinator_headers = env.auth_headers["coordinator"]

    first = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    assert first.status_code == 200
    slug1 = first.json()["slug"]

    update_resp = await client.put(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/{score_id}",
        headers=coordinator_headers,
        json={"points": 3},
    )
    assert update_resp.status_code == 200

    second = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    assert second.status_code == 200
    slug2 = second.json()["slug"]
    assert slug2 == slug1
    assert second.json()["expires_at"] >= first.json()["expires_at"]

    rows = (await db_session.execute(select(RunResultLink).where(RunResultLink.run_id == run_id))).scalars().all()
    assert len(rows) == 1

    public_resp = await client.post("/api/public/results/lookup", json={"slug": slug1})
    assert public_resp.status_code == 200
    assert public_resp.json()["entries"][0]["points"] == 3


# ---------------------------------------------------------------------------
# Public read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_read_without_cookie(client: AsyncClient, db_session: AsyncSession):
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-public-read")
    coordinator_headers = env.auth_headers["coordinator"]

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    slug = publish_resp.json()["slug"]

    resp = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert resp.status_code == 200
    data = resp.json()

    # Nothing beyond the frozen public projection: no member or user data,
    # not even the organization's name, which the page never showed.
    assert set(data.keys()) == {"run_name", "ended_at", "language", "entries"}
    assert data["run_name"] == "Test Run"
    assert env.org.name not in resp.text
    assert data["language"] == "de"
    assert len(data["entries"]) == 1

    entry = data["entries"][0]
    assert set(entry.keys()) == {"team_name", "points", "rank", "is_winner"}
    assert entry["team_name"] == "Team Alpha"
    assert entry["points"] == 10
    assert entry["rank"] == 1
    assert entry["is_winner"] is True


@pytest.mark.asyncio
async def test_unknown_slug_gives_404(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post("/api/public/results/lookup", json={"slug": "this-slug-does-not-exist"})
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "result_link_not_found"


@pytest.mark.asyncio
async def test_revoke_gives_410(client: AsyncClient, db_session: AsyncSession, configured_logging: str):
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-revoke")
    coordinator_headers = env.auth_headers["coordinator"]

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    slug = publish_resp.json()["slug"]

    revoke_resp = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers
    )
    assert revoke_resp.status_code == 204

    public_resp = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert public_resp.status_code == 410
    assert public_resp.json()["error_code"] == "result_link_revoked"

    status_resp = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/results/link", headers=coordinator_headers)
    assert status_resp.json()["status"] == "revoked"

    audit_entries = _read_audit_log(configured_logging)
    revoked_events = [e for e in audit_entries if e.get("event") == "run.results_revoked"]
    assert len(revoked_events) == 1
    assert slug not in json.dumps(revoked_events[0])


@pytest.mark.asyncio
async def test_revoke_then_republish_gives_new_slug_old_one_stays_dead(client: AsyncClient, db_session: AsyncSession):
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-revoke-republish")
    coordinator_headers = env.auth_headers["coordinator"]

    first = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    slug1 = first.json()["slug"]

    revoke_resp = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers
    )
    assert revoke_resp.status_code == 204

    second = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    assert second.status_code == 200
    slug2 = second.json()["slug"]
    assert slug2 != slug1

    old_resp = await client.post("/api/public/results/lookup", json={"slug": slug1})
    assert old_resp.status_code == 410
    assert old_resp.json()["error_code"] == "result_link_revoked"

    new_resp = await client.post("/api/public/results/lookup", json={"slug": slug2})
    assert new_resp.status_code == 200

    # Two rows now exist for this run: the dead one and the live one.
    rows = (await db_session.execute(select(RunResultLink).where(RunResultLink.run_id == run_id))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_expired_gives_410(client: AsyncClient, db_session: AsyncSession):
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-expired")
    coordinator_headers = env.auth_headers["coordinator"]

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    slug = publish_resp.json()["slug"]

    link = (await db_session.execute(select(RunResultLink).where(RunResultLink.run_id == run_id))).scalar_one()
    link.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    resp = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert resp.status_code == 410
    assert resp.json()["error_code"] == "result_link_expired"


@pytest.mark.asyncio
async def test_public_page_shows_anonymized_names_after_the_lkc_sweep(client: AsyncClient, db_session: AsyncSession):
    """When the period for team names ends, the page keeps points and ranks, not the names."""
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-anonymized")
    coordinator_headers = env.auth_headers["coordinator"]
    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    slug = publish_resp.json()["slug"]

    before = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert before.json()["entries"][0]["team_name"] == "Team Alpha"

    from app.models.run import Run
    from app.services.gdpr import DeletionClass
    from app.services.retention_tasks import sweep_run_anonymize

    run = (await db_session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    run.ended_at = datetime.now(UTC) - timedelta(days=DeletionClass.LK_C.retention_days + 1)
    await db_session.commit()
    await sweep_run_anonymize(db_session, reference_date=datetime.now(UTC))
    await db_session.commit()

    after = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert after.status_code == 200
    assert "Team Alpha" not in after.text
    entry = after.json()["entries"][0]
    assert entry == {"team_name": "Team 1", "points": 10, "rank": 1, "is_winner": True}


@pytest.mark.asyncio
async def test_public_results_rate_limited(client: AsyncClient, db_session: AsyncSession):
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-rate-limit")
    coordinator_headers = env.auth_headers["coordinator"]

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    slug = publish_resp.json()["slug"]

    for i in range(30):
        resp = await client.post("/api/public/results/lookup", json={"slug": slug})
        assert resp.status_code == 200, f"Request {i + 1} failed with {resp.status_code}"

    resp = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_public_lookup_never_logs_the_slug(client: AsyncClient, db_session: AsyncSession, caplog):
    """No emitted log record may contain the slug.

    The app/error/access category loggers set ``propagate = False``
    (``app/logging.py``), so pytest's default root-only ``caplog`` handler
    would miss them; the handler is attached to those loggers directly here
    so the assertion holds regardless of which loggers end up firing for this
    request. A path or query slug would show up in every access-log request
    line — the POST body does not, and nothing on this request path logs the
    body or the slug itself.
    """
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-log-safety")
    coordinator_headers = env.auth_headers["coordinator"]

    publish_resp = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator_headers)
    slug = publish_resp.json()["slug"]

    caplog.set_level(_logging.DEBUG)
    # app/error/access set propagate=False (app/logging.py) — attach directly
    # so records reaching them are captured too, not just root-propagating ones.
    watched_loggers = [_logging.getLogger(name) for name in ("app", "error", "access", None)]
    for logger in watched_loggers:
        logger.addHandler(caplog.handler)
    try:
        resp = await client.post("/api/public/results/lookup", json={"slug": slug})
    finally:
        for logger in watched_loggers:
            logger.removeHandler(caplog.handler)

    assert resp.status_code == 200
    assert slug not in caplog.text
    for record in caplog.records:
        assert slug not in str(record.__dict__)


@pytest.mark.asyncio
async def test_the_teams_get_the_live_link_and_a_feed_entry(client: AsyncClient, db_session: AsyncSession):
    """The people who played see the published link, and only while it lives."""
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "result-link-teams")
    coordinator = env.auth_headers["coordinator"]
    my_link = f"/api/orgs/{env.slug}/runs/{run_id}/results/my-link"

    before = await client.get(my_link, headers=env.auth_headers["participant1"])
    assert before.status_code == 200
    assert before.json()["status"] == "unpublished"

    published = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator)
    assert published.status_code == 200
    url = published.json()["url"]

    member = await client.get(my_link, headers=env.auth_headers["participant1"])
    assert member.json() == {
        "status": "published",
        "url": url,
        "published_at": member.json()["published_at"],
        "expires_at": member.json()["expires_at"],
    }
    outsider = await client.get(my_link, headers=env.auth_headers["participant2"])
    assert outsider.status_code == 403

    feed = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=env.auth_headers["participant1"])
    entries = [e for e in feed.json()["items"] if (e.get("content") or {}).get("result_link")]
    assert len(entries) == 1
    assert url not in str(entries[0])  # the feed points to the leaderboard, never at a link that may die

    await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator)
    feed = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=env.auth_headers["participant1"])
    assert len([e for e in feed.json()["items"] if (e.get("content") or {}).get("result_link")]) == 1
    await client.delete(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator)
    after = await client.get(my_link, headers=env.auth_headers["participant1"])
    assert after.json()["status"] == "unpublished"
    assert after.json()["url"] is None


@pytest.mark.asyncio
async def test_nobody_wins_with_zero_points(client: AsyncClient, db_session: AsyncSession):
    """A run without points has no winner: no team is published as a winner."""
    env, run_id, _rc, submission_id = await _setup_run_to_ended_state(client, db_session, "zero-points")
    coordinator = env.auth_headers["coordinator"]
    scored = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator,
        json={"submission_id": submission_id, "points": 0, "comment": "Leider falsch"},
    )
    assert scored.status_code == 201
    await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coordinator)

    board = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/scores/scoreboard", headers=coordinator)
    assert board.json()["is_final"] is True
    assert [e["is_winner"] for e in board.json()["entries"]] == [False]

    slug = (await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator)).json()[
        "slug"
    ]
    public = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert [e["is_winner"] for e in public.json()["entries"]] == [False]


async def _result_notices(client: AsyncClient, env: JourneyEnv, run_id: Any) -> list[dict]:
    feed = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=env.auth_headers["participant1"])
    assert feed.status_code == 200
    return [e for e in feed.json()["items"] if (e.get("content") or {}).get("result_link")]


@pytest.mark.asyncio
async def test_result_notice_is_single_and_removed_with_the_withdrawal(client: AsyncClient, db_session: AsyncSession):
    """One notice while published, none after the withdrawal, one again after a new publish."""
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "result-notice-lifecycle")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"
    feed_url = f"/api/orgs/{env.slug}/runs/{run_id}/feed/"

    # The run's own notices (end of run, no result_link) must survive every publish and withdrawal.
    def _others(items: list[dict]) -> list[str]:
        return sorted(e["id"] for e in items if not (e.get("content") or {}).get("result_link"))

    baseline = await client.get(feed_url, headers=env.auth_headers["participant1"])
    others_before = _others(baseline.json()["items"])
    assert others_before

    for _ in range(3):
        assert (await client.post(publish_url, headers=coordinator)).status_code == 200
    assert len(await _result_notices(client, env, run_id)) == 1

    assert (await client.delete(publish_url, headers=coordinator)).status_code == 204
    assert await _result_notices(client, env, run_id) == []

    assert (await client.post(publish_url, headers=coordinator)).status_code == 200
    notices = await _result_notices(client, env, run_id)
    assert len(notices) == 1

    after = await client.get(feed_url, headers=env.auth_headers["participant1"])
    assert _others(after.json()["items"]) == others_before


class _RunSocket:
    """Stands in for one open page on the run's WebSocket and keeps what it is sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_publish_and_withdrawal_tell_the_open_pages_that_the_feed_changed(
    client: AsyncClient, db_session: AsyncSession
):
    """One ``feed_changed`` on the run's feed channel after each publish and each withdrawal."""
    env, run_id, _score_id = await _publish_ready_run(client, db_session, "result-feed-changed")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"

    socket = _RunSocket()
    other_run_socket = _RunSocket()
    await ws_manager.subscribe(socket, f"feed:{run_id}")  # type: ignore[arg-type]
    await ws_manager.subscribe(other_run_socket, "feed:01900000-0000-7000-8000-000000000000")  # type: ignore[arg-type]

    def _changes() -> list[dict[str, Any]]:
        return [m for m in socket.sent if m["type"] == "feed_changed"]

    try:
        # A withdrawal that finds nothing to withdraw changes nothing and says nothing.
        assert (await client.delete(publish_url, headers=coordinator)).status_code == 404
        assert _changes() == []

        assert (await client.post(publish_url, headers=coordinator)).status_code == 200
        assert len(_changes()) == 1
        assert _changes()[0]["data"] == {"run_id": str(run_id)}
        assert _changes()[0]["timestamp"]

        # The second publish replaces the notice of the first.
        assert (await client.post(publish_url, headers=coordinator)).status_code == 200
        assert len(_changes()) == 2

        # The withdrawal adds no feed event, so this is the only word an open page gets.
        assert (await client.delete(publish_url, headers=coordinator)).status_code == 204
        assert len(_changes()) == 3

        assert other_run_socket.sent == []
    finally:
        await ws_manager.disconnect(socket)  # type: ignore[arg-type]
        await ws_manager.disconnect(other_run_socket)  # type: ignore[arg-type]


SIGNAL_TIMEOUT = 10.0  # a missing arrival must fail the test, never pass it


@pytest.mark.asyncio
async def test_sweep_of_expired_links_waits_for_a_publish_that_holds_the_run_row(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    """The sweep waits at the run row of an expired link while a publish holds it.

    The link of the run has expired. A publish renews it and writes its notice
    and is held before its commit. The sweep has read the expired link by then,
    asks for the run row and waits. After the publish committed, the sweep
    reads the links of the run again under the lock, finds none expired and
    removes neither the link nor the notice.
    """
    env, run_id, _score_id = await _publish_ready_run(concurrent_client, db_session, "result-sweep-waits")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"
    assert (await concurrent_client.post(publish_url, headers=coordinator)).status_code == 200

    sessions = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions() as setup:
        await setup.execute(
            update(RunResultLink).where(RunResultLink.run_id == UUID(run_id)).values(expires_at=now - timedelta(days=1))
        )
        await setup.commit()

    held = asyncio.Event()
    release = asyncio.Event()
    original_create = FeedEventRepository.create

    async def gated_create(self: FeedEventRepository, **kwargs: Any) -> FeedEvent:
        created = await original_create(self, **kwargs)
        if (kwargs.get("content") or {}).get("result_link"):
            # Link and notice are written and not committed; the run row is held.
            held.set()
            await asyncio.wait_for(release.wait(), SIGNAL_TIMEOUT)
        return created

    monkeypatch.setattr(FeedEventRepository, "create", gated_create)

    async def run_sweep() -> int:
        async with sessions() as session:
            deleted = await sweep_expired_result_links(session, reference_date=now)
            await session.commit()
            return deleted

    waits = text(
        """
        SELECT (SELECT min(l.relation::regclass::text) FROM pg_locks l
                 WHERE l.pid = a.pid AND l.locktype = 'tuple') AS waits_in
          FROM pg_stat_activity a
         WHERE a.datname = current_database() AND a.wait_event_type = 'Lock'
        """
    )

    async def lock_waits() -> list[str]:
        async with test_engine.connect() as connection:
            while True:
                rows = (await connection.execute(waits)).scalars().all()
                if rows:
                    return list(rows)
                assert not sweep.done(), f"the sweep did not wait for the run row: {sweep.result()} deleted"
                await asyncio.sleep(0.01)

    publish: asyncio.Task = asyncio.create_task(concurrent_client.post(publish_url, headers=coordinator))
    sweep: asyncio.Task = asyncio.create_task(asyncio.sleep(0))
    try:
        await asyncio.wait_for(held.wait(), SIGNAL_TIMEOUT)
        sweep = asyncio.create_task(run_sweep())
        # The sweep waits, and it waits at the run row, not at the link.
        assert await asyncio.wait_for(lock_waits(), SIGNAL_TIMEOUT) == ["runs"]
        assert not sweep.done()
        release.set()
        response = await asyncio.wait_for(publish, SIGNAL_TIMEOUT)
        deleted = await asyncio.wait_for(sweep, SIGNAL_TIMEOUT)
    finally:
        release.set()
        for task in (publish, sweep):
            task.cancel()
        await asyncio.gather(publish, sweep, return_exceptions=True)

    assert response.status_code == 200, response.text
    assert deleted == 0
    async with sessions() as check:
        links = (await check.execute(select(RunResultLink).where(RunResultLink.run_id == UUID(run_id)))).scalars().all()
        assert len(links) == 1
        assert links[0].revoked_at is None
        assert links[0].expires_at > now
        notices = (
            (
                await check.execute(
                    select(FeedEvent.content)
                    .where(FeedEvent.run_id == UUID(run_id))
                    .where(FeedEvent.event_type == FeedEventType.RESULTS_AVAILABLE)
                )
            )
            .scalars()
            .all()
        )
        assert len([c for c in notices if c.get("result_link")]) == 1


INTERIM_NOTICE_DE = "Der Lauf ist beendet. Die Bewertung läuft noch, die Rangliste ist ein Zwischenstand."


@pytest.mark.asyncio
async def test_interim_notice_leaves_with_the_publication_and_stays_away(client: AsyncClient, db_session: AsyncSession):
    """The publication takes the interim notice away, and a withdrawal does not bring it back.

    After a withdrawal the interim notice stays away. A result is only
    published once every answer is graded, so "Die Bewertung läuft noch" would be
    false after the withdrawal. The feed then holds neither the result notice nor
    the interim notice, and the app's own leaderboard is the final one.
    """
    env, run_id, _rc, submission_id = await _setup_run_to_ended_state(client, db_session, "interim-notice-leaves")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"
    feed_url = f"/api/orgs/{env.slug}/runs/{run_id}/feed/"

    async def _feed() -> list[dict]:
        feed = await client.get(feed_url, headers=env.auth_headers["participant1"])
        assert feed.status_code == 200
        return feed.json()["items"]

    def _interim(items: list[dict]) -> list[dict]:
        return [e for e in items if (e.get("content") or {}).get("de") == INTERIM_NOTICE_DE]

    def _results(items: list[dict]) -> list[dict]:
        return [e for e in items if e["event_type"] == "results_available"]

    def _others(items: list[dict]) -> list[str]:
        return sorted(e["id"] for e in items if e["event_type"] != "results_available")

    # The run ends while an answer is still ungraded: the feed holds the interim notice.
    assert (await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coordinator)).status_code == 200
    items = await _feed()
    assert len(_interim(items)) == 1
    assert len(_results(items)) == 1
    others_before = _others(items)
    assert others_before  # the feedback prompt, which no publish or withdrawal may touch

    # Grading alone changes nothing in the feed.
    scored = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator,
        json={"submission_id": submission_id, "points": 10, "comment": "Great"},
    )
    assert scored.status_code == 201

    # After the publication the result notice stands alone.
    assert (await client.post(publish_url, headers=coordinator)).status_code == 200
    items = await _feed()
    assert _interim(items) == []
    assert len(_results(items)) == 1
    assert _results(items)[0]["content"].get("result_link") is True
    assert _others(items) == others_before

    # A second publish keeps it that way.
    assert (await client.post(publish_url, headers=coordinator)).status_code == 200
    items = await _feed()
    assert _interim(items) == []
    assert len(_results(items)) == 1

    # The withdrawal brings no interim notice back (not once, not twice) and leaves no orphan.
    assert (await client.delete(publish_url, headers=coordinator)).status_code == 204
    items = await _feed()
    assert _interim(items) == []
    assert _results(items) == []
    assert _others(items) == others_before

    # Publishing again gives the one result notice and nothing else.
    assert (await client.post(publish_url, headers=coordinator)).status_code == 200
    items = await _feed()
    assert _interim(items) == []
    assert len(_results(items)) == 1
    assert _results(items)[0]["content"].get("result_link") is True
    assert _others(items) == others_before


@pytest.mark.asyncio
async def test_interim_notice_written_before_the_marker_leaves_too(client: AsyncClient, db_session: AsyncSession):
    """An interim notice stored without the ``interim`` key, as older rows in the database are,
    also leaves with the publication."""
    env, run_id, _rc, submission_id = await _setup_run_to_ended_state(client, db_session, "interim-notice-old-row")
    coordinator = env.auth_headers["coordinator"]
    feed_url = f"/api/orgs/{env.slug}/runs/{run_id}/feed/"
    assert (await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coordinator)).status_code == 200

    # An interim notice without the ``interim`` key: only the two text lines.
    old_rows = (
        (
            await db_session.execute(
                select(FeedEvent).where(
                    FeedEvent.run_id == run_id, FeedEvent.event_type == FeedEventType.RESULTS_AVAILABLE
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(old_rows) == 1
    old_rows[0].content = {k: v for k, v in old_rows[0].content.items() if k in ("de", "en")}
    await db_session.commit()

    scored = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=coordinator,
        json={"submission_id": submission_id, "points": 10, "comment": "Great"},
    )
    assert scored.status_code == 201
    assert (
        await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator)
    ).status_code == 200

    feed = await client.get(feed_url, headers=env.auth_headers["participant1"])
    results = [e for e in feed.json()["items"] if e["event_type"] == "results_available"]
    assert [e["content"].get("de") for e in results] != [INTERIM_NOTICE_DE]
    assert len(results) == 1
    assert results[0]["content"].get("result_link") is True


@pytest.mark.asyncio
async def test_public_page_of_a_run_whose_last_team_dissolved_shows_no_entries(
    client: AsyncClient, db_session: AsyncSession
):
    """The page answers as for a run without teams, on the same link."""
    from app.models.team import Team

    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-last-team")
    coordinator = env.auth_headers["coordinator"]
    published = (await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator)).json()
    slug = published["slug"]
    before = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert [e["team_name"] for e in before.json()["entries"]] == ["Team Alpha"]

    team_id = (await db_session.execute(select(Team.id).where(Team.run_id == run_id))).scalar_one()
    removed = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        params={"confirm_dissolution": "true"},
        headers=coordinator,
    )
    assert removed.status_code == 204

    after = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert after.status_code == 200
    assert "Team Alpha" not in after.text
    assert after.json()["entries"] == []
    assert after.json()["run_name"] == before.json()["run_name"]
    assert after.json()["ended_at"] == before.json()["ended_at"]

    status_resp = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/results/link", headers=coordinator)
    assert status_resp.json()["status"] == "published"
    assert status_resp.json()["url"].endswith(f"#{slug}")
    assert status_resp.json()["published_at"] == published["published_at"]
    assert status_resp.json()["expires_at"] == published["expires_at"]


@pytest.mark.asyncio
async def test_public_page_of_a_run_whose_last_team_had_a_pending_name_shows_no_entries(
    client: AsyncClient, db_session: AsyncSession
):
    """The last team never got a name. Its entry has none either, and goes all the same."""
    from sqlalchemy import update

    from app.models.team import Team

    env, run_id, _score_id = await _publish_ready_run(client, db_session, "results-last-pending")
    coordinator = env.auth_headers["coordinator"]
    team_id = (await db_session.execute(select(Team.id).where(Team.run_id == run_id))).scalar_one()
    await db_session.execute(update(Team).where(Team.id == team_id).values(name=None))
    await db_session.commit()

    slug = (await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/results/publish", headers=coordinator)).json()[
        "slug"
    ]
    before = await client.post("/api/public/results/lookup", json={"slug": slug})
    # The stored entry knows its team; the public answer never shows the id.
    assert before.json()["entries"] == [{"team_name": None, "points": 10, "rank": 1, "is_winner": True}]
    assert str(team_id) not in before.text

    removed = await client.delete(
        f"/api/orgs/{env.slug}/runs/{run_id}/teams/{team_id}",
        params={"confirm_dissolution": "true"},
        headers=coordinator,
    )
    assert removed.status_code == 204

    after = await client.post("/api/public/results/lookup", json={"slug": slug})
    assert after.status_code == 200
    assert after.json()["entries"] == []
