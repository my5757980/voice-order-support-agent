"""T054 — speculative dispatch decisions.

Speculation is the design's answer to a measured 1,063 ms of model latency: run the model
during the shopper's trailing silence instead of after it. That is only acceptable
because it can never act — and the tests that matter here are the ones about *not*
dispatching and *not* promoting, because those are the ones that keep a performance trick
from changing the world.
"""

from __future__ import annotations

import pytest

from src.core.speculation import SpeculationConfig, Verdict, promotable, should_dispatch

CFG = SpeculationConfig()


def decide(text: str, *, conf: float = 0.9, last: str | None = None,
           confirming: bool = False, speaking: bool = False,
           cfg: SpeculationConfig = CFG) -> Verdict:
    return should_dispatch(
        text, confidence=conf, last_dispatched=last,
        awaiting_confirmation=confirming, agent_speaking=speaking, config=cfg,
    )


# -- when it fires --------------------------------------------------------


def test_confident_partial_dispatches() -> None:
    assert decide("where is my order", conf=0.9) is Verdict.DISPATCH


def test_confidence_below_threshold_holds() -> None:
    """A speculation that fires too early is discarded, and a discarded speculation cost
    a request *and* delayed the real one."""
    assert decide("where is my order", conf=0.3) is Verdict.HOLD


def test_threshold_is_configurable() -> None:
    eager = SpeculationConfig(threshold=0.2)
    assert decide("where is my order", conf=0.3, cfg=eager) is Verdict.DISPATCH


# -- when it must not fire ------------------------------------------------


def test_never_speculates_while_a_confirmation_is_pending() -> None:
    """The next utterance decides whether a return is created — and a speculative turn is
    forbidden from calling that tool. Speculating here spends a request on an answer that
    could never be used."""
    assert decide("yes", conf=0.99, confirming=True) is Verdict.SKIP


def test_never_speculates_while_the_agent_is_speaking() -> None:
    """That partial is a barge-in candidate, not a turn to answer."""
    assert decide("no wait the other one", conf=0.95, speaking=True) is Verdict.SKIP


def test_disabled_means_disabled() -> None:
    off = SpeculationConfig(enabled=False)
    assert decide("where is my order", conf=0.99, cfg=off) is Verdict.SKIP


def test_short_utterance_holds() -> None:
    """Endpointing is least certain on short utterances, which is where a wasted
    speculation is most likely."""
    assert decide("umm", conf=0.99) is Verdict.HOLD


# -- novelty: do not re-dispatch on the same sentence still arriving -------


def test_identical_partial_does_not_redispatch() -> None:
    assert decide("where is my order", last="where is my order") is Verdict.HOLD


def test_trivially_grown_partial_does_not_redispatch() -> None:
    """One or two more characters is the same utterance still arriving. Re-dispatching
    cancels a speculation that was already closer to the answer."""
    assert decide("where is my orders", last="where is my order") is Verdict.HOLD


def test_materially_changed_partial_redispatches() -> None:
    assert decide("where is my headphones order", last="where is my") is Verdict.DISPATCH


# -- promotion: the gate between speculation and speech -------------------


def test_matching_commit_is_promotable() -> None:
    assert promotable("where is my order", "where is my order") is True


def test_punctuation_and_case_do_not_block_promotion() -> None:
    """Providers routinely adjust casing and trailing punctuation between the last
    partial and the committed turn. Discarding a correct speculation over a full stop
    would waste the entire saving."""
    assert promotable("Where is my order?", "where is my order") is True


def test_different_commit_is_not_promotable() -> None:
    """The shopper said something else. The staged answer is to a question they did not
    ask, so it is discarded rather than spoken."""
    assert promotable("cancel my order", "where is my order") is False


def test_nothing_staged_is_not_promotable() -> None:
    assert promotable("where is my order", None) is False


@pytest.mark.parametrize(
    ("committed", "speculated"),
    [
        ("where is my order", "where is my order   "),
        ("  where is my order  ", "where is my order"),
        ("Where Is My Order.", "where is my order"),
    ],
)
def test_normalisation_is_forgiving_about_whitespace_and_case(
    committed: str, speculated: str
) -> None:
    assert promotable(committed, speculated) is True
