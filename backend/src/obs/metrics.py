"""Metrics, exported from the start rather than added in week three.

Prometheus text format, no dependency — the exposition format is simple enough that a
client library would be more surface than value at this size.

The nine series below are the ones plan.md § 8 commits to. Each exists because a
specific success criterion or failure mode is graded on it, not because it was easy
to collect.
"""

from __future__ import annotations

import threading
from collections import defaultdict

_LOCK = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_observations: dict[str, list[float]] = defaultdict(list)

# Keep memory bounded: a long-running session must not accumulate samples forever
# (constitution NFR-011).
_MAX_SAMPLES = 2000

HELP = {
    "turn_latency_e2e_seconds": "End of shopper speech to first agent audio",
    "barge_in_latency_seconds": "Interrupt decision to agent audio silent (SC-006)",
    "backchannel_suppressed_total": "Acknowledgements correctly not treated as interruptions (SC-007)",
    "stt_reconnects_total": "Speech recognition reconnections — silent-degradation detector",
    "llm_errors_total": "Language model errors by class",
    "tool_failures_total": "Tool failures by name",
    "audio_frames_dropped_total": "Frames dropped by backpressure",
    "session_outcome_total": "Sessions by containment outcome (SC-001)",
    "speculative_dispatch_total": "Speculative dispatches, promoted vs discarded",
}


def inc(name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
    key = (name, tuple(sorted((labels or {}).items())))
    with _LOCK:
        _counters[key] += value


def observe(name: str, value: float) -> None:
    with _LOCK:
        samples = _observations[name]
        samples.append(value)
        if len(samples) > _MAX_SAMPLES:
            del samples[: len(samples) - _MAX_SAMPLES]


def percentile(name: str, p: float) -> float | None:
    with _LOCK:
        samples = sorted(_observations.get(name, ()))
    if not samples:
        return None
    idx = min(len(samples) - 1, int(round((p / 100) * (len(samples) - 1))))
    return samples[idx]


def snapshot() -> dict[str, dict[str, float | None]]:
    """Percentiles for the UI and the gate report."""
    return {
        name: {
            "p50": percentile(name, 50),
            "p95": percentile(name, 95),
            "p99": percentile(name, 99),
            "count": float(len(_observations.get(name, ()))),
        }
        for name in _observations
    }


def render() -> str:
    """Prometheus exposition format."""
    lines: list[str] = []
    with _LOCK:
        counters = dict(_counters)
        observations = {k: list(v) for k, v in _observations.items()}

    seen: set[str] = set()
    for (name, labels), value in sorted(counters.items()):
        if name not in seen and name in HELP:
            lines.append(f"# HELP {name} {HELP[name]}")
            lines.append(f"# TYPE {name} counter")
            seen.add(name)
        label_str = ",".join(f'{k}="{v}"' for k, v in labels)
        lines.append(f"{name}{{{label_str}}} {value:g}" if label_str else f"{name} {value:g}")

    for name, samples in sorted(observations.items()):
        if not samples:
            continue
        if name in HELP:
            lines.append(f"# HELP {name} {HELP[name]}")
        lines.append(f"# TYPE {name} summary")
        for p in (50, 95, 99):
            v = percentile(name, p)
            if v is not None:
                lines.append(f'{name}{{quantile="0.{p}"}} {v:g}')
        lines.append(f"{name}_count {len(samples)}")
        lines.append(f"{name}_sum {sum(samples):g}")

    return "\n".join(lines) + "\n"


def reset() -> None:
    """Test helper. Never called in production."""
    with _LOCK:
        _counters.clear()
        _observations.clear()
