"""Injectable time.

Constitution principle VII: time MUST be injected. Direct `time.monotonic()` or
`asyncio.sleep` calls in core logic are violations, because tests must be able to
advance time deterministically rather than waiting on a wall clock.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    """Monotonic time source. Monotonic, not wall-clock: every core decision is about
    elapsed duration (grace windows, silence timeouts, holding-phrase delays), and
    wall-clock time can jump backwards."""

    def now(self) -> float:
        """Seconds from an arbitrary origin. Only differences are meaningful."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Real time. Used everywhere outside tests."""

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeClock:
    """Test clock advanced explicitly by the test.

    `sleep` does not yield to the event loop and does not advance time on its own —
    a test that expects time to pass must say so with `advance()`. That makes the
    passage of time an assertion rather than a race.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._now

    async def sleep(self, seconds: float) -> None:
        # Recorded, not honoured. Advancing is the test's decision.
        self.sleeps.append(seconds)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time does not run backwards")
        self._now += seconds
