"""The rate limits count a moving window.

With a fixed window, five wrong codes at the end of one window and five
at the start of the next would pass within seconds.
The sixth attempt within any minute is refused.
"""

from types import SimpleNamespace

import limits.storage.memory as memory_storage
import pytest
from limits import parse

from app.middleware.rate_limit import limiter


def test_six_attempts_within_any_minute_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SimpleNamespace(now=1_000_000.0)
    monkeypatch.setattr(memory_storage, "time", SimpleNamespace(time=lambda: clock.now))
    limiter.reset()
    strategy = limiter._limiter
    item = parse("5/minute")
    key = "f050-moving-window"

    assert strategy.hit(item, key)
    clock.now += 59
    for _ in range(4):
        assert strategy.hit(item, key)

    # A fixed window opened by the first hit is over here and would let five
    # more pass. The moving window drops only the first hit.
    clock.now += 2
    assert strategy.hit(item, key)
    assert not strategy.hit(item, key)
