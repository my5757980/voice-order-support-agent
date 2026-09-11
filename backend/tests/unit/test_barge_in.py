"""T049 — interruption decision.

The headline test here is `test_bare_yes_during_confirmation_interrupts`. It guards the
FR-009 / FR-010 collision: "yes" is a backchannel while the agent narrates, and a real
answer while the agent is asking "shall I start that return?". Ordering the confirmation
check first is the entire fix, and it is the most likely thing to regress silently.
"""

from __future__ import annotations

import pytest

from src.core.barge_in import (
    DEFAULT_BACKCHANNELS,
    BargeInConfig,
    Decision,
    decide,
    is_all_backchannel,
    is_confirmation_answer,
)

CFG = BargeInConfig()


def _decide(text: str, *, speaking: bool = True, confirming: bool = False, since: float = 0.0):
    """`since` = seconds since the agent stopped speaking."""
    return decide(
        text,
        agent_speaking=speaking,
        awaiting_confirmation=confirming,
        now=100.0,
        last_spoke_at=100.0 - since,
        config=CFG,
    )


# -- the collision --------------------------------------------------------


def test_bare_yes_during_confirmation_interrupts() -> None:
    """FR-010 beats FR-009. Without this, a shopper confirming a return is ignored."""
    assert _decide("yes", speaking=True, confirming=True) is Decision.INTERRUPT


def test_bare_yes_without_confirmation_is_suppressed() -> None:
    """FR-009. The same word, no pending confirmation, is an acknowledgement."""
    assert _decide("yeah", speaking=True, confirming=False) is Decision.SUPPRESS


def test_no_during_confirmation_interrupts() -> None:
    """A refusal must land as surely as an acceptance."""
    assert _decide("no", speaking=True, confirming=True) is Decision.INTERRUPT


def test_long_sentence_containing_yes_is_not_a_confirmation_answer() -> None:
    """Consent must be unambiguous. A sentence that merely contains 'yes' is a new
    request, and treating it as consent is the failure the ritual exists to prevent."""
    assert not is_confirmation_answer("yes but actually where is my other order")


# -- backchannel suppression ----------------------------------------------


@pytest.mark.parametrize("token", ["mhm", "uh huh", "okay", "right", "hmm", "gotcha"])
def test_backchannels_do_not_interrupt_while_agent_speaks(token: str) -> None:
    assert _decide(token, speaking=True) is Decision.SUPPRESS


def test_real_speech_interrupts() -> None:
    assert _decide("no wait the other order", speaking=True) is Decision.INTERRUPT


def test_a_single_slight_word_is_filler_while_speaking() -> None:
    """What a stray noise transcribes as: not enough to stop the agent."""
    assert _decide("the", speaking=True) is Decision.SUPPRESS


def test_a_single_real_word_interrupts_while_speaking() -> None:
    """"cancel" over the agent is not filler. The rule used to demand two words, and
    streaming STT delivers the second about 1.3 s after the first (plan.md, amended)."""
    assert _decide("cancel", speaking=True) is Decision.INTERRUPT


def test_two_words_meets_the_minimum() -> None:
    assert _decide("cancel that", speaking=True) is Decision.INTERRUPT


# -- grace window ---------------------------------------------------------


def test_backchannel_inside_grace_window_is_still_suppressed() -> None:
    """A trailing 'mhm' answering the agent's last sentence is not a new turn."""
    assert _decide("mhm", speaking=False, since=0.5) is Decision.SUPPRESS


def test_backchannel_after_grace_window_is_a_normal_turn() -> None:
    assert _decide("mhm", speaking=False, since=2.0) is Decision.INTERRUPT


# -- configurability ------------------------------------------------------


def test_yes_and_no_are_not_default_backchannels() -> None:
    """Constitution: tokens that are load-bearing in the active flow must be removable.
    In this domain a bare 'yes' confirms a refund, so it is absent by default."""
    assert "yes" not in DEFAULT_BACKCHANNELS
    assert "no" not in DEFAULT_BACKCHANNELS


def test_backchannel_set_is_configurable_per_domain() -> None:
    strict = BargeInConfig(backchannels=frozenset({"mhm"}))
    assert is_all_backchannel("okay", strict.backchannels) is False
    assert is_all_backchannel("mhm", strict.backchannels) is True


def test_min_words_is_configurable() -> None:
    lenient = BargeInConfig(min_words=1)
    assert (
        decide(
            "stop",
            agent_speaking=True,
            awaiting_confirmation=False,
            now=1.0,
            last_spoke_at=1.0,
            config=lenient,
        )
        is Decision.INTERRUPT
    )


# -- a word that means stop ----------------------------------------------------------

import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("word", ["Sorry,", "So", "wait", "Stop!", "no", "actually", "hello?"])
def test_a_single_stop_word_interrupts_at_once(word: str) -> None:
    """Measured with real streaming STT: "Sorry, where's my order?" produced its first
    partial about 0.5 s in and its second 1.8 s in. Holding one-word partials back as
    possible filler kept the agent talking over the shopper for that whole gap."""
    from src.core.barge_in import Decision, decide

    assert decide(word, agent_speaking=True, awaiting_confirmation=False,
                  now=10.0, last_spoke_at=9.0) is Decision.INTERRUPT


@_pytest.mark.parametrize("word", ["mhm", "okay", "yeah", "the", "uh"])
def test_a_single_filler_word_still_does_not(word: str) -> None:
    """SC-007 still holds: a one-word backchannel, or a stray word, is not a barge-in."""
    from src.core.barge_in import Decision, decide

    assert decide(word, agent_speaking=True, awaiting_confirmation=False,
                  now=10.0, last_spoke_at=9.0) is Decision.SUPPRESS
