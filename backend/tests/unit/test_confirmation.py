"""The confirmation ritual, both halves — found broken by a recorded 13-turn run.

The agent said "I'll create a return for the Fold desk lamp from order ORD-4488. Do you
confirm?" The old detector looked for exactly "shall i" or "go ahead", matched neither,
and never armed the gate. The shopper said "yes please", the tool was refused, and the
model rationalised the refusal into "the desk lamp isn't eligible for return". The
headline safety feature worked only when the model happened to pick one of two phrasings
— and on the shopper's side only when they typed without punctuation.
"""

from __future__ import annotations

import pytest

from src.core.confirmation import is_affirmative, seeks_confirmation

# -- the agent's side: is it asking? --------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        # The exact reply from the recorded run that the old detector missed.
        "I'll create a return for the Fold desk lamp from order ORD-4488. Do you confirm?",
        "Shall I go ahead and start that return?",
        "Would you like me to cancel the mug order?",
        "I can start a return for the lamp. Is that okay?",
        'Do you want me to proceed?"',
    ],
)
def test_requests_for_permission_are_recognised(reply: str) -> None:
    assert seeks_confirmation(reply) is True


@pytest.mark.parametrize(
    "reply",
    [
        "I'll go ahead and check that for you.",           # a report, not a question
        "Your headphones arrive on the eleventh. Anything else?",  # a question, not permission
        "Which order would you like the status for?",
        "",
    ],
)
def test_ordinary_replies_do_not_arm_the_gate(reply: str) -> None:
    assert seeks_confirmation(reply) is False


# -- the shopper's side: is that a yes? -----------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "yes please",
        "Yes, go ahead.",          # failed before: first token was "yes,"
        "yes, please",
        "sure, go for it",
        "that's right",
        "That\u2019s right.",      # a curly apostrophe, as some keyboards type it
        "yes please do it",        # four words, over the old limit of three
        "yes, cancel it",          # when the pending action is a cancellation
        "Okay.",
    ],
)
def test_natural_consent_is_recognised(answer: str) -> None:
    assert is_affirmative(answer) is True


@pytest.mark.parametrize(
    "answer",
    [
        "no",
        "no, don't cancel it",
        "yes but the other one",
        "okay wait",
        "yeah, hold on",
        "yes and also I wanted to ask about the headphones",   # opens a new topic
        "maybe",
        "",
    ],
)
def test_anything_short_of_clear_consent_withholds_it(answer: str) -> None:
    """Asymmetric on purpose: a false negative costs one repeated question, a false
    positive creates a return nobody asked for."""
    assert is_affirmative(answer) is False
