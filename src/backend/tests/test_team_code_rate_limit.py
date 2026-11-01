"""Unit tests for the team-code failure limiter."""

import asyncio
from uuid import uuid4

import pytest

from app.services import team_code_rate_limit as limiter

SIGNAL_TIMEOUT = 10.0  # a missing signal must fail the test, never pass it
NO_WAIT_LIMIT = 1.0  # uncontended admissions enter in microseconds; a wait for another key's lock never ends


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    limiter._failures.clear()
    yield
    limiter._failures.clear()


@pytest.mark.asyncio
async def test_is_blocked_false_for_unknown_client_and_records_nothing() -> None:
    run_id = uuid4()
    assert await limiter.is_blocked("10.0.0.1", run_id) is False
    assert limiter._failures == {}


@pytest.mark.asyncio
async def test_is_blocked_reads_without_recording() -> None:
    run_id = uuid4()
    for _ in range(limiter.MAX_FAILURES - 1):
        assert await limiter.check_and_record_failure("10.0.0.1", run_id) is True
    before = list(limiter._failures[("10.0.0.1", run_id)])

    for _ in range(10):
        assert await limiter.is_blocked("10.0.0.1", run_id) is False

    assert list(limiter._failures[("10.0.0.1", run_id)]) == before


@pytest.mark.asyncio
async def test_is_blocked_true_when_budget_spent_and_scoped_to_client_and_run() -> None:
    run_id = uuid4()
    for _ in range(limiter.MAX_FAILURES):
        await limiter.check_and_record_failure("10.0.0.1", run_id)
    count = len(limiter._failures[("10.0.0.1", run_id)])

    assert await limiter.is_blocked("10.0.0.1", run_id) is True
    assert len(limiter._failures[("10.0.0.1", run_id)]) == count
    assert await limiter.is_blocked("10.0.0.2", run_id) is False
    assert await limiter.is_blocked("10.0.0.1", uuid4()) is False


@pytest.mark.asyncio
async def test_is_blocked_ends_when_failures_leave_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = uuid4()
    now = [1000.0]
    monkeypatch.setattr(limiter, "monotonic", lambda: now[0])
    for _ in range(limiter.MAX_FAILURES):
        await limiter.check_and_record_failure("10.0.0.1", run_id)
    assert await limiter.is_blocked("10.0.0.1", run_id) is True

    now[0] += limiter.WINDOW_SECONDS + 1
    assert await limiter.is_blocked("10.0.0.1", run_id) is False


# --- One-step admission per client and run ----------------------


@pytest.fixture(autouse=True)
def _clean_key_locks() -> None:
    limiter._key_locks.clear()
    yield
    limiter._key_locks.clear()


@pytest.mark.asyncio
async def test_admission_asks_records_and_resets_like_the_three_calls() -> None:
    run_id = uuid4()
    async with limiter.admission("10.0.0.1", run_id) as slot:
        assert slot.blocked is False
        for _ in range(limiter.MAX_FAILURES):
            assert slot.record_failure() is True
        assert slot.record_failure() is False
    assert await limiter.is_blocked("10.0.0.1", run_id) is True

    async with limiter.admission("10.0.0.1", run_id) as slot:
        assert slot.blocked is True
        slot.reset()
    assert await limiter.is_blocked("10.0.0.1", run_id) is False
    assert limiter._failures == {}


@pytest.mark.asyncio
async def test_admission_of_one_client_and_run_runs_one_at_a_time() -> None:
    """A second admission of the same key waits until the first has left."""
    run_id = uuid4()
    first_in = asyncio.Event()
    release_first = asyncio.Event()
    second_in = asyncio.Event()
    second_arrived = asyncio.Event()

    async def first() -> None:
        async with limiter.admission("10.0.0.1", run_id):
            first_in.set()
            await asyncio.wait_for(release_first.wait(), SIGNAL_TIMEOUT)

    async def second() -> None:
        await first_in.wait()
        second_arrived.set()  # no await between this and the queueing on the key's lock
        async with limiter.admission("10.0.0.1", run_id):
            second_in.set()

    t1, t2 = asyncio.create_task(first()), asyncio.create_task(second())
    await asyncio.wait_for(first_in.wait(), SIGNAL_TIMEOUT)
    await asyncio.wait_for(second_arrived.wait(), SIGNAL_TIMEOUT)
    # The second task set its signal and went straight into the admission: it is queued on the key's lock.
    assert limiter._key_locks[("10.0.0.1", run_id)].users == 2
    assert not second_in.is_set()

    release_first.set()
    await asyncio.gather(t1, t2)
    assert second_in.is_set()


@pytest.mark.asyncio
async def test_admission_of_another_client_or_run_does_not_wait() -> None:
    """While one admission is open, an admission of another client and one of another run enter.

    The first admission stays open, so a wait would never end: the timeout turns it into a failure.
    """
    run_id = uuid4()
    async with (
        asyncio.timeout(NO_WAIT_LIMIT),
        limiter.admission("10.0.0.1", run_id),
        limiter.admission("10.0.0.2", run_id),
        limiter.admission("10.0.0.1", uuid4()),
    ):
        pass


@pytest.mark.asyncio
async def test_late_reset_cannot_clear_a_block_another_admission_set() -> None:
    """Four failures, a correct-code admission is held, a fifth failure waits its turn.

    The fifth failure cannot land while the held admission is open, so the
    reset of the held admission never erases a block it has not seen.
    """
    run_id = uuid4()
    for _ in range(limiter.MAX_FAILURES - 1):
        await limiter.check_and_record_failure("10.0.0.1", run_id)
    held = asyncio.Event()
    release = asyncio.Event()
    landed = asyncio.Event()
    wrong_arrived = asyncio.Event()

    async def correct() -> None:
        async with limiter.admission("10.0.0.1", run_id) as slot:
            assert slot.blocked is False
            held.set()
            await asyncio.wait_for(release.wait(), SIGNAL_TIMEOUT)
            slot.reset()

    async def wrong() -> None:
        await held.wait()
        wrong_arrived.set()  # no await between this and the queueing on the key's lock
        async with limiter.admission("10.0.0.1", run_id) as slot:
            landed.set()
            slot.record_failure()

    t1, t2 = asyncio.create_task(correct()), asyncio.create_task(wrong())
    await asyncio.wait_for(held.wait(), SIGNAL_TIMEOUT)
    await asyncio.wait_for(wrong_arrived.wait(), SIGNAL_TIMEOUT)
    assert limiter._key_locks[("10.0.0.1", run_id)].users == 2
    assert not landed.is_set()
    assert len(limiter._failures[("10.0.0.1", run_id)]) == limiter.MAX_FAILURES - 1

    release.set()
    await asyncio.gather(t1, t2)
    # The reset came first, then the failure: no block, one failure on the books.
    assert len(limiter._failures[("10.0.0.1", run_id)]) == 1


@pytest.mark.asyncio
async def test_admission_leaves_no_lock_behind_and_releases_on_error() -> None:
    run_id = uuid4()
    with pytest.raises(RuntimeError):
        async with limiter.admission("10.0.0.1", run_id):
            raise RuntimeError("boom")
    assert limiter._key_locks == {}

    for i in range(50):
        async with limiter.admission(f"10.0.1.{i}", run_id):
            pass
    assert limiter._key_locks == {}
    async with limiter.admission("10.0.0.1", run_id) as slot:  # the lock was released
        assert slot.blocked is False
