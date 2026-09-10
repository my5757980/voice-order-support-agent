"""T011 — injected time.

`FakeClock` deliberately does not advance on `sleep`. A test that expects time to pass
must say so with `advance()`, which turns the passage of time into an assertion rather
than a race against a real scheduler.
"""

from __future__ import annotations

import pytest

from src.core.clock import FakeClock, SystemClock


def test_fake_clock_starts_where_told() -> None:
    assert FakeClock(start=42.0).now() == 42.0


def test_advance_moves_time_forward() -> None:
    clock = FakeClock()
    clock.advance(1.5)
    clock.advance(0.5)
    assert clock.now() == 2.0


def test_time_does_not_run_backwards() -> None:
    with pytest.raises(ValueError):
        FakeClock().advance(-1.0)


async def test_fake_sleep_records_without_advancing() -> None:
    """The point of the fake: sleeping does not silently move the clock."""
    clock = FakeClock()
    await clock.sleep(5.0)
    assert clock.now() == 0.0
    assert clock.sleeps == [5.0]


def test_system_clock_is_monotonic() -> None:
    clock = SystemClock()
    first = clock.now()
    second = clock.now()
    assert second >= first


def test_both_clocks_satisfy_the_protocol() -> None:
    for clock in (SystemClock(), FakeClock()):
        assert callable(clock.now)
        assert callable(clock.sleep)
