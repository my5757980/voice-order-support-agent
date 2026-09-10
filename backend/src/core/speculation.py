"""Speculative dispatch — the decision half.

The design's central latency argument. Measured on the deployed instance, `llm.ttft` is
1,063 ms; if that runs *after* the shopper stops talking, they hear a second of silence.
Dispatched on a high-confidence partial turn instead, it runs during their trailing
silence and the perceived wait collapses to synthesis alone.

Constitution Architectural Principle 8 states the rule this implements: *speculative
execution is allowed, commitment is not*. Four gates enforce it, and the third is the one
that makes the whole idea safe rather than reckless:

    1. Confidence      the provider must think the turn is nearly over
    2. Novelty         the partial must have actually changed
    3. Read-only       a speculative turn may look things up; it may never act
    4. One in flight   a newer speculation cancels the previous

Gate 3 is enforced in the ToolRegistry by a call-context flag, not by prompting. A model
that decides to create a return during speculation still cannot: the registry refuses it
before execution. Speculation is a performance trick, and a performance trick must never
be able to change the world.

This module decides. The orchestrator acts — kept apart so the decision is testable
without an event loop, a provider, or a clock (principle VII).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MIN_DELTA_CHARS = 3
"""A partial that grew by a character or two is the same utterance still arriving.
Re-dispatching on it burns a request and cancels a speculation that was already closer
to the answer."""

MIN_WORDS = 3
"""Below this there is not enough to reason about, and short utterances are exactly where
endpointing is least certain."""


class Verdict(str, Enum):
    DISPATCH = "dispatch"
    """Confident enough, and different enough, to be worth a request."""

    HOLD = "hold"
    """Not yet. The shopper is still forming the sentence."""

    SKIP = "skip"
    """Speculation is disabled, or this turn can never be speculated on."""


@dataclass(frozen=True, slots=True)
class SpeculationConfig:
    enabled: bool = True
    threshold: float = 0.7
    """`end_of_turn_confidence` from the provider. Start conservative: a speculation that
    fires too early is discarded, and a discarded speculation is a wasted request that
    also delayed the real one."""

    min_delta_chars: int = MIN_DELTA_CHARS
    min_words: int = MIN_WORDS


def should_dispatch(
    text: str,
    *,
    confidence: float,
    last_dispatched: str | None,
    awaiting_confirmation: bool,
    agent_speaking: bool,
    config: SpeculationConfig | None = None,
) -> Verdict:
    """Whether this partial turn is worth speculating on.

    `awaiting_confirmation` is a hard skip. While a read-back is pending, the shopper's
    next utterance decides whether a return gets created — and a speculative turn is
    forbidden from calling the tool that would do it. Speculating there would spend a
    request to produce something that cannot be used.
    """
    cfg = config or SpeculationConfig()

    if not cfg.enabled or agent_speaking or awaiting_confirmation:
        return Verdict.SKIP

    stripped = text.strip()
    if len(stripped.split()) < cfg.min_words:
        return Verdict.HOLD

    if confidence < cfg.threshold:
        return Verdict.HOLD

    if last_dispatched is not None:
        if stripped == last_dispatched.strip():
            return Verdict.HOLD
        if abs(len(stripped) - len(last_dispatched.strip())) < cfg.min_delta_chars:
            return Verdict.HOLD

    return Verdict.DISPATCH


def promotable(committed: str, speculated: str | None) -> bool:
    """Whether a staged speculation may be spoken now that the turn has committed.

    Compared on normalised text rather than exactly: providers routinely adjust casing
    and trailing punctuation between the last partial and the committed turn, and
    discarding a correct speculation over a full stop would waste the entire saving.

    A genuine difference means the shopper said something else, and the staged answer is
    to a question they did not ask. It is discarded — never spoken, never remembered.
    """
    if speculated is None:
        return False
    return _normalise(committed) == _normalise(speculated)


def _normalise(text: str) -> str:
    return " ".join(text.lower().strip().rstrip(".!?,").split())
