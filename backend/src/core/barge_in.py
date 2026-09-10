"""Interruption decision.

Pure function, no I/O. Returns a decision; stopping audio is the orchestrator's job.

This module resolves the collision the spec called out between two requirements that
disagree about the same word:

    FR-009  "mhm", "yeah", "okay" MUST NOT interrupt while the agent is speaking.
    FR-010  A confirmation-context affirmative ("yes") IS a real answer and MUST interrupt.

The fix is entirely in the ordering: the confirmation check runs *first*. A bare "yes"
is a backchannel during narration and an answer during "shall I start that return?".
Get the order wrong and the agent either talks over acknowledgements or silently ignores
the shopper confirming a refund. That is why it has its own named test.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from enum import Enum

# "yes" / "no" are deliberately ABSENT from the default set. In this domain a bare
# "yes" is load-bearing — it is how a return gets confirmed. The constitution requires
# this set be domain-configurable precisely so tokens can be removed per flow.
DEFAULT_BACKCHANNELS: frozenset[str] = frozenset(
    {
        "mhm", "mm", "mmhm", "mmhmm", "hmm", "hm",
        "uh", "uhhuh", "huh", "um", "umm", "uhm",
        "er", "erm", "ah", "oh",
        "okay", "ok", "right", "alright", "gotcha", "sure",
        "yeah", "yep", "yup",
    }
)

AFFIRMATIVES: frozenset[str] = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct", "confirm", "do", "go"}
)
NEGATIVES: frozenset[str] = frozenset({"no", "nope", "nah", "cancel", "stop", "wait", "don't"})

_PUNCT = str.maketrans("", "", string.punctuation)


class Decision(str, Enum):
    INTERRUPT = "interrupt"
    SUPPRESS = "suppress"


@dataclass(frozen=True, slots=True)
class BargeInConfig:
    min_words: int = 2
    """Utterances shorter than this are treated as filler while the agent speaks."""

    grace_seconds: float = 1.0
    """Keep filtering briefly after the agent stops, so a trailing "mhm" answering the
    last sentence does not register as a new turn."""

    backchannels: frozenset[str] = field(default=DEFAULT_BACKCHANNELS)


def _tokens(text: str) -> list[str]:
    return text.lower().translate(_PUNCT).split()


def is_all_backchannel(text: str, backchannels: frozenset[str] = DEFAULT_BACKCHANNELS) -> bool:
    toks = _tokens(text)
    return bool(toks) and all(t in backchannels for t in toks)


def is_confirmation_answer(text: str) -> bool:
    """True if this reads as a direct yes/no to a pending confirmation.

    Deliberately narrow: a short utterance whose tokens are affirmative or negative.
    A long sentence that happens to contain "yes" is not a confirmation answer — it is
    a new request, and treating it as consent would be exactly the failure the
    confirmation ritual exists to prevent.
    """
    toks = _tokens(text)
    if not toks or len(toks) > 3:
        return False
    return any(t in AFFIRMATIVES or t in NEGATIVES for t in toks)


def decide(
    text: str,
    *,
    agent_speaking: bool,
    awaiting_confirmation: bool,
    now: float,
    last_spoke_at: float,
    config: BargeInConfig | None = None,
) -> Decision:
    """Whether this incoming speech should interrupt the agent.

    `now` and `last_spoke_at` come from the injected clock — this function never reads
    time itself.
    """
    cfg = config or BargeInConfig()

    # 1. Confirmation context wins over everything. FR-010 before FR-009.
    if awaiting_confirmation and is_confirmation_answer(text):
        return Decision.INTERRUPT

    # 2. Not speaking and past the grace window: an ordinary turn, not an interruption.
    if not agent_speaking and (now - last_spoke_at) > cfg.grace_seconds:
        return Decision.INTERRUPT

    # 3. Filler while the agent holds the floor.
    if len(text.split()) < cfg.min_words or is_all_backchannel(text, cfg.backchannels):
        return Decision.SUPPRESS

    # 4. Real speech over the agent.
    return Decision.INTERRUPT
