"""Result-notice concurrency tests.

Publish, withdrawal and the end of a run change the same two things: the run's
result link and the result notices in its feed. They take one lock on the run
row before they read either, and hold it until the transaction with the link
and the feed change commits. These tests send two real requests through the
routes, each with its own database session (``concurrent_client``, as in
test_join_capacity_race.py), and assert the feed and the link agree afterwards.

Two requests sent with ``asyncio.gather`` meet where the scheduler lets them.
To get each order on purpose, most tests hold the first request at a named
point inside its route (a ``_Gate`` around one repository call) and send the
second one while it waits. Without the lock the second request runs past the
first and the feed ends wrong. With the lock the second request waits on the
run row, the gate opens after ``SECOND_REQUEST_WAIT``, and the two run one
after the other.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.feed_event import INTERIM_RESULT_NOTICE, FeedEventRepository
from tests.journey_helpers import (
    JourneyEnv,
    activate_run,
    create_team_and_join,
    setup_journey_environment,
    submit_challenge,
)

# How long the test gives the second request before it lets the first one go.
# With the lock the second request is still waiting on the run row then.
SECOND_REQUEST_WAIT = 1.0
# The gate opens by itself after this long, so a broken test ends and does not hang.
GATE_LIMIT = 15.0


class _Gate:
    """Holds the first request that reaches it until the test opens it."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.open = asyncio.Event()

    async def hold_first(self) -> None:
        if self.reached.is_set():
            return
        self.reached.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.open.wait(), timeout=GATE_LIMIT)


def _hold_before_notice(monkeypatch: pytest.MonkeyPatch, gate: _Gate, key: str) -> None:
    """Hold the first request that is about to insert a notice whose content has ``key``."""
    original = FeedEventRepository.create

    async def create(self: FeedEventRepository, **kwargs: Any) -> Any:
        if (kwargs.get("content") or {}).get(key):
            await gate.hold_first()
        return await original(self, **kwargs)

    monkeypatch.setattr(FeedEventRepository, "create", create)


def _hold_after_notice_removal(monkeypatch: pytest.MonkeyPatch, gate: _Gate) -> None:
    """Hold the first request that has just removed the result notices, before it commits."""
    original = FeedEventRepository.delete_result_link_notices

    async def delete_result_link_notices(self: FeedEventRepository, run_id: Any) -> int:
        removed = await original(self, run_id)
        await gate.hold_first()
        return removed

    monkeypatch.setattr(FeedEventRepository, "delete_result_link_notices", delete_result_link_notices)


async def _first_held_then_second(
    first: Callable[[], Awaitable[Response]],
    gate: _Gate,
    second: Callable[[], Awaitable[Response]],
) -> tuple[Response, Response]:
    """Start ``first``, wait until it is held at the gate, send ``second``, then open the gate."""
    first_task = asyncio.create_task(first())
    await asyncio.wait_for(gate.reached.wait(), timeout=GATE_LIMIT)
    second_task = asyncio.create_task(second())
    await asyncio.wait({second_task}, timeout=SECOND_REQUEST_WAIT)
    gate.open.set()
    return await first_task, await second_task


async def _run_with_one_answer(
    db_session: AsyncSession, client: AsyncClient, org_slug: str
) -> tuple[JourneyEnv, str, str]:
    """An active run with one team and one ungraded answer. Returns (env, run_id, submission_id)."""
    env = await setup_journey_environment(
        db_session, client, org_slug=org_slug, num_stations=1, challenges_per_station=1
    )
    await db_session.commit()
    coordinator = env.auth_headers["coordinator"]

    run_resp = await client.post(
        f"/api/orgs/{env.slug}/runs/",
        headers=coordinator,
        json={"rallye_id": str(env.rallye_id), "name": "Result Notice Race Run"},
    )
    assert run_resp.status_code == 201, run_resp.text
    run_id = run_resp.json()["id"]
    await activate_run(client, env.slug, run_id, coordinator)

    stations = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/stations/", headers=coordinator)
    station_id = stations.json()["items"][0]["id"]
    challenges = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/stations/{station_id}/challenges/", headers=coordinator
    )
    challenge_id = challenges.json()["items"][0]["id"]

    await create_team_and_join(client, env.slug, run_id, env.auth_headers["participant1"], "Team Alpha", "#FF0000")
    submission = await submit_challenge(
        client, env.slug, run_id, env.auth_headers["participant1"], challenge_id, {"text": "Answer"}
    )
    return env, run_id, submission["id"]


async def _grade(client: AsyncClient, env: JourneyEnv, run_id: str, submission_id: str) -> None:
    scored = await client.post(
        f"/api/orgs/{env.slug}/runs/{run_id}/scores/",
        headers=env.auth_headers["coordinator"],
        json={"submission_id": submission_id, "points": 10, "comment": "Great"},
    )
    assert scored.status_code == 201, scored.text


async def _publish_ready_run(db_session: AsyncSession, client: AsyncClient, org_slug: str) -> tuple[JourneyEnv, str]:
    """An ended run whose one answer is graded: ready to publish. Returns (env, run_id)."""
    env, run_id, submission_id = await _run_with_one_answer(db_session, client, org_slug)
    await _grade(client, env, run_id, submission_id)
    ended = await client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=env.auth_headers["coordinator"])
    assert ended.status_code == 200, ended.text
    return env, run_id


async def _feed(client: AsyncClient, env: JourneyEnv, run_id: str) -> list[dict[str, Any]]:
    """The feed as a participant who opens the page now gets it."""
    feed = await client.get(f"/api/orgs/{env.slug}/runs/{run_id}/feed/", headers=env.auth_headers["participant1"])
    assert feed.status_code == 200, feed.text
    items: list[dict[str, Any]] = feed.json()["items"]
    return items


def _result_notices(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in items if (e.get("content") or {}).get("result_link")]


def _interim_notices(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in items if (e.get("content") or {}).get("de") == INTERIM_RESULT_NOTICE["de"]]


def _other_notices(items: list[dict[str, Any]]) -> list[str]:
    """Ids of everything that is neither a result notice nor an interim notice."""
    skip = {e["id"] for e in _result_notices(items)} | {e["id"] for e in _interim_notices(items)}
    return sorted(e["id"] for e in items if e["id"] not in skip)


async def _link_status(client: AsyncClient, env: JourneyEnv, run_id: str) -> dict[str, Any]:
    status_resp = await client.get(
        f"/api/orgs/{env.slug}/runs/{run_id}/results/link", headers=env.auth_headers["coordinator"]
    )
    assert status_resp.status_code == 200, status_resp.text
    body: dict[str, Any] = status_resp.json()
    return body


@pytest.mark.asyncio
async def test_publishes_at_once_leave_one_result_notice(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
) -> None:
    """Publishes sent at the same moment, as the capacity races are sent.

    Each replaces the notice of the earlier publish. Without the lock a publish
    removes the old notice, misses the new one of another and inserts its own.
    Four are sent, not two: two alone often meet in an order that hides it.
    """
    env, run_id = await _publish_ready_run(db_session, concurrent_client, "notice-race-gather")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"

    first = await concurrent_client.post(publish_url, headers=coordinator)
    assert first.status_code == 200, first.text
    others_before = _other_notices(await _feed(concurrent_client, env, run_id))

    results = await asyncio.gather(*[concurrent_client.post(publish_url, headers=coordinator) for _ in range(4)])
    assert [r.status_code for r in results] == [200] * 4, [r.text for r in results]

    items = await _feed(concurrent_client, env, run_id)
    assert len(_result_notices(items)) == 1, f"Publishes at once must leave one result notice, got {items}"
    assert _other_notices(items) == others_before
    assert (await _link_status(concurrent_client, env, run_id))["status"] == "published"


@pytest.mark.asyncio
async def test_second_publish_during_the_first_leaves_one_result_notice(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second publish arrives while the first has removed the old notice and not yet written its own."""
    env, run_id = await _publish_ready_run(db_session, concurrent_client, "notice-race-publish")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"

    first = await concurrent_client.post(publish_url, headers=coordinator)
    assert first.status_code == 200, first.text
    others_before = _other_notices(await _feed(concurrent_client, env, run_id))

    gate = _Gate()
    _hold_before_notice(monkeypatch, gate, "result_link")

    async def publish() -> Response:
        return await concurrent_client.post(publish_url, headers=coordinator)

    held, arriving = await _first_held_then_second(publish, gate, publish)
    assert held.status_code == 200, held.text
    assert arriving.status_code == 200, arriving.text

    items = await _feed(concurrent_client, env, run_id)
    assert len(_result_notices(items)) == 1, f"Two publishes must leave one result notice, got {items}"
    assert _other_notices(items) == others_before
    status = await _link_status(concurrent_client, env, run_id)
    assert status["status"] == "published"
    assert status["url"] == held.json()["url"] == arriving.json()["url"]


@pytest.mark.asyncio
async def test_withdrawal_during_a_publish_leaves_no_notice(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The withdrawal arrives while the publish has its link and not yet its notice.

    The withdrawal comes last. Without the lock it runs past the publish, finds
    the committed link and no notice, and the publish then inserts its notice
    for a withdrawn result.
    """
    env, run_id = await _publish_ready_run(db_session, concurrent_client, "notice-race-withdraw-last")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"
    others_before = _other_notices(await _feed(concurrent_client, env, run_id))

    gate = _Gate()
    _hold_before_notice(monkeypatch, gate, "result_link")

    async def publish() -> Response:
        return await concurrent_client.post(publish_url, headers=coordinator)

    async def withdraw() -> Response:
        return await concurrent_client.delete(publish_url, headers=coordinator)

    published, withdrawn = await _first_held_then_second(publish, gate, withdraw)
    assert published.status_code == 200, published.text
    assert withdrawn.status_code == 204, withdrawn.text

    items = await _feed(concurrent_client, env, run_id)
    assert (await _link_status(concurrent_client, env, run_id))["status"] == "revoked"
    assert _result_notices(items) == [], f"No notice may stay after the withdrawal, got {items}"
    assert _other_notices(items) == others_before


@pytest.mark.asyncio
async def test_publish_during_a_withdrawal_ends_published_with_one_notice(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The publish arrives while the withdrawal has revoked the link and not yet committed.

    The publish comes last. Without the lock it still reads the live link,
    writes its snapshot onto the row the withdrawal then revokes, answers 200
    with a dead address and leaves a notice for a result nobody can open.
    """
    env, run_id = await _publish_ready_run(db_session, concurrent_client, "notice-race-publish-last")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"

    first = await concurrent_client.post(publish_url, headers=coordinator)
    assert first.status_code == 200, first.text
    others_before = _other_notices(await _feed(concurrent_client, env, run_id))

    gate = _Gate()
    _hold_after_notice_removal(monkeypatch, gate)

    async def withdraw() -> Response:
        return await concurrent_client.delete(publish_url, headers=coordinator)

    async def publish() -> Response:
        return await concurrent_client.post(publish_url, headers=coordinator)

    withdrawn, published = await _first_held_then_second(withdraw, gate, publish)
    assert withdrawn.status_code == 204, withdrawn.text
    assert published.status_code == 200, published.text

    items = await _feed(concurrent_client, env, run_id)
    status = await _link_status(concurrent_client, env, run_id)
    assert status["status"] == "published", "The publish came last, so the result must be live"
    assert status["url"] == published.json()["url"]
    assert status["url"] != first.json()["url"], "A withdrawn link never comes back to life"
    assert len(_result_notices(items)) == 1, f"A published result has exactly one notice, got {items}"
    assert _other_notices(items) == others_before


@pytest.mark.asyncio
async def test_delayed_end_does_not_put_the_interim_notice_back(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end of the run saw an ungraded answer and is delayed before it writes the interim notice.

    Meanwhile the answer is graded and the result published. Without the lock
    the publish completes first and the delayed end then inserts "grading is
    still under way" beside the published result.
    """
    env, run_id, submission_id = await _run_with_one_answer(db_session, concurrent_client, "notice-race-end")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"

    gate = _Gate()
    _hold_before_notice(monkeypatch, gate, "interim")

    async def end() -> Response:
        return await concurrent_client.post(f"/api/orgs/{env.slug}/runs/{run_id}/end", headers=coordinator)

    async def grade_and_publish() -> Response:
        await _grade(concurrent_client, env, run_id, submission_id)
        return await concurrent_client.post(publish_url, headers=coordinator)

    ended, published = await _first_held_then_second(end, gate, grade_and_publish)
    assert ended.status_code == 200, ended.text
    assert published.status_code == 200, published.text

    items = await _feed(concurrent_client, env, run_id)
    assert _interim_notices(items) == [], f"The interim notice must not stand beside a published result, got {items}"
    assert len(_result_notices(items)) == 1
    # The feedback prompt of the run's end is there, once.
    assert [e["event_type"] for e in items].count("feedback_prompt") == 1
    assert (await _link_status(concurrent_client, env, run_id))["status"] == "published"


@pytest.mark.asyncio
async def test_failed_notice_leaves_no_live_link(
    db_session: AsyncSession,
    concurrent_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Link and notice are one transaction: when the notice cannot be written, no link is live."""
    env, run_id = await _publish_ready_run(db_session, concurrent_client, "notice-one-transaction")
    coordinator = env.auth_headers["coordinator"]
    publish_url = f"/api/orgs/{env.slug}/runs/{run_id}/results/publish"
    feed_before = await _feed(concurrent_client, env, run_id)

    original = FeedEventRepository.create

    async def create(self: FeedEventRepository, **kwargs: Any) -> Any:
        if (kwargs.get("content") or {}).get("result_link"):
            raise RuntimeError("the feed is not writable")
        return await original(self, **kwargs)

    monkeypatch.setattr(FeedEventRepository, "create", create)
    with pytest.raises(RuntimeError, match="the feed is not writable"):
        await concurrent_client.post(publish_url, headers=coordinator)
    monkeypatch.setattr(FeedEventRepository, "create", original)

    assert (await _link_status(concurrent_client, env, run_id))["status"] == "unpublished"
    assert await _feed(concurrent_client, env, run_id) == feed_before

    # The run is not stuck: the next publish goes through and has its notice.
    again = await concurrent_client.post(publish_url, headers=coordinator)
    assert again.status_code == 200, again.text
    assert len(_result_notices(await _feed(concurrent_client, env, run_id))) == 1
