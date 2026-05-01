"""
Failure rate limit for team-code attempts, per (client_ip, run_id).

In memory and per process (see the module state below). Every recorded failure
first drops the entries older than the window, so memory does not grow without
bound.

Two ways to use it:

- ``is_blocked``, ``check_and_record_failure`` and ``reset`` are three steps.
  Each is atomic on its own. A sequence of them is not.
- ``admission`` is the one-step form for a join by team code. The question, the
  lookup of the code and the record of a failure or the reset run under one lock
  per (client_ip, run_id), so no other request of the same client and run can
  interleave.
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from uuid import UUID

# Tunables
WINDOW_SECONDS = 60.0
MAX_FAILURES = 5

# Module state, per process.
#
# docker-compose.yml, deployment/standalone/docker-compose.yml and the API image
# start uvicorn without a worker count, so one process runs and its budget is
# the deployment's. With more workers or replicas every process has its own
# counters and locks: up to MAX_FAILURES * workers failures per window, and the
# admission serializes requests only inside one process. A budget across
# processes needs a shared store (Redis, or the database) with a lock in it,
# which is not built before it is needed.
_failures: dict[tuple[str, UUID], deque[float]] = {}
_lock = asyncio.Lock()


class _KeyLock:
    """One asyncio lock of the admission table, with the number of users holding or awaiting it."""

    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


# One lock per (client_ip, run_id) with an admission open or queued. An entry
# goes when its last user leaves, so the table holds no more entries than there
# are requests in flight.
_key_locks: dict[tuple[str, UUID], _KeyLock] = {}


def _count_in_window(key: tuple[str, UUID], cutoff: float) -> int:
    bucket = _failures.get(key)
    if bucket is None:
        return 0
    return sum(1 for t in bucket if t >= cutoff)


def _record_failure(key: tuple[str, UUID]) -> bool:
    """Record one failure for ``key`` unless the budget is spent. No awaits, so atomic in the event loop."""
    now = monotonic()
    cutoff = now - WINDOW_SECONDS
    bucket = _failures.get(key)
    if bucket is None:
        bucket = deque()
        _failures[key] = bucket

    while bucket and bucket[0] < cutoff:
        bucket.popleft()

    if len(bucket) >= MAX_FAILURES:
        return False

    bucket.append(now)
    return True


class Admission:
    """
    The open admission of one (client_ip, run_id), handed out by ``admission``.

    ``blocked`` is fixed when the admission opens. ``record_failure`` and
    ``reset`` change the counters at once, without awaiting, inside the
    admission's lock.
    """

    __slots__ = ("_key", "blocked")

    def __init__(self, key: tuple[str, UUID], blocked: bool) -> None:
        self._key = key
        self.blocked = blocked

    def record_failure(self) -> bool:
        """Record a failed attempt. True: recorded, answer 404. False: budget spent, answer 429."""
        return _record_failure(self._key)

    def reset(self) -> None:
        """Clear the counter after the code resolved."""
        _failures.pop(self._key, None)


@asynccontextmanager
async def admission(client_ip: str, run_id: UUID) -> AsyncIterator[Admission]:
    """
    Admit one team-code attempt of ``client_ip`` for ``run_id``, one at a time.

    Use it around the whole decision of a join by code::

        async with team_code_rate_limit.admission(client_ip, run.id) as slot:
            if slot.blocked:
                raise 429
            team = await team_repo.get_by_share_code(run.id, code)
            if team is None:
                if not slot.record_failure():
                    raise 429
                raise 404
            slot.reset()

    Another request of the same client and run waits at ``async with`` until
    this block has left. So N parallel wrong codes cause at most
    ``MAX_FAILURES`` lookups, and a correct code cannot clear a block another
    request set during its lookup. Requests of other clients or runs do not wait.

    Keep the block short, because it holds the key's lock. Do the work after the
    resolution of the code (capacity checks, inserts) after the block. The lock
    is per process (see the module state).
    """
    key = (client_ip, run_id)
    entry = _key_locks.get(key)
    if entry is None:
        entry = _KeyLock()
        _key_locks[key] = entry
    entry.users += 1
    try:
        await entry.lock.acquire()
    except BaseException:
        _leave(key, entry)
        raise
    try:
        blocked = _count_in_window(key, monotonic() - WINDOW_SECONDS) >= MAX_FAILURES
        yield Admission(key, blocked)
    finally:
        entry.lock.release()
        _leave(key, entry)


def _leave(key: tuple[str, UUID], entry: _KeyLock) -> None:
    entry.users -= 1
    if entry.users == 0 and _key_locks.get(key) is entry:
        del _key_locks[key]


async def is_blocked(client_ip: str, run_id: UUID) -> bool:
    """
    Whether (client_ip, run_id) is out of failure budget now.

    Failures older than the window do not count. Changes no counter, so a
    caller can ask before it looks up a code.
    """
    async with _lock:
        return _count_in_window((client_ip, run_id), monotonic() - WINDOW_SECONDS) >= MAX_FAILURES


async def check_and_record_failure(client_ip: str, run_id: UUID) -> bool:
    """
    Record a failure of (client_ip, run_id) while the budget lasts.

    True: recorded, the caller may answer 404. False: the budget is spent, the
    caller should answer 429.

    ``client_ip`` is the address of ``app.client_address.client_address(request)``.
    Behind a trusted proxy that is the client, not the proxy
    (``FORWARDED_ALLOW_IPS``).
    """
    async with _lock:
        return _record_failure((client_ip, run_id))


async def reset(client_ip: str, run_id: UUID) -> None:
    """
    Clear the failure counter after the code resolved, so a user who mistyped
    and then succeeded does not keep the bucket warm.
    """
    key = (client_ip, run_id)
    async with _lock:
        _failures.pop(key, None)
