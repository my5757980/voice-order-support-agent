"""The mandatory spans.

Constitution principle VI: every pipeline hop records a timed span, tagged with
`session_id` and `turn_id`. "It felt slow" is not a bug report we accept — if a latency
regression cannot be attributed to a span, the missing span is the first defect to fix.

Seven are mandated by the constitution. `playback.stop` is added by this feature because
SC-006 is graded on interrupt-to-silence and it cannot be derived from the other seven.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

MANDATORY_SPANS = (
    "audio.capture",
    "stt.turn",
    "llm.ttft",
    "llm.complete",
    "tool",
    "tts.ttfb",
    "playback.start",
    "playback.stop",
)


@dataclass
class TurnTimings:
    """Spans collected for one turn, in milliseconds."""

    session_id: str
    turn_id: str
    spans: dict[str, float] = field(default_factory=dict)
    _started: float = field(default_factory=time.monotonic)

    def record(self, name: str, ms: float) -> None:
        self.spans[name] = ms

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        start = time.monotonic()
        try:
            yield
        finally:
            self.record(name, (time.monotonic() - start) * 1000)

    def mark_from_turn_start(self, name: str) -> float:
        """Record elapsed time since the turn began — used for cumulative marks like
        `llm.ttft` and `tts.ttfb`, which are measured from the committed turn rather
        than from the start of their own operation."""
        ms = (time.monotonic() - self._started) * 1000
        self.record(name, ms)
        return ms

    def finish(self) -> dict[str, float]:
        """Close the turn and add the end-to-end figure.

        Measured directly rather than summed from component spans: summing p95s would
        overstate the total, and the constitution requires the headline number be
        measured, never computed.
        """
        self.spans["e2e"] = (time.monotonic() - self._started) * 1000
        return dict(self.spans)

    @property
    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._started) * 1000
