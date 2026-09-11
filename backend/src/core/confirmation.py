"""Is the agent waiting for the shopper to authorise something?

The answer decides whether the next "yes" unlocks a state-changing tool, so getting it
wrong is not cosmetic in either direction: a false negative strands the shopper in a loop
where they agree and nothing happens, and a false positive lets an unrelated "yeah"
create a return.

It used to be two string literals — `"shall i"` and `"go ahead"` — neither of which the
persona ever asked the model to say. In a recorded run the agent said *"I'll create a
return for the Fold desk lamp from order ORD-4488. Do you confirm?"*, matched nothing,
and the shopper's "yes please" opened no gate; the tool was refused and the model
rationalised the refusal into "the desk lamp isn't eligible for return". The safety
ritual the whole design leads with worked only when the model happened to pick one of
two phrasings.

So there are two signals now, and the machine one is authoritative:

  * The registry refused a state-changing call for want of confirmation. That is a fact
    the system observed, not a guess about wording, and it is what `SessionActor` reads.
  * The reply reads as a request to authorise an action. Needed because a well-behaved
    model asks *before* it reaches for the tool, in which case nothing was refused and
    there is no fact to read.

Either is enough. Both are cheap. Missing both is what stranded the shopper.
"""

from __future__ import annotations

import re

_ASKS = (
    "shall i",
    "shall we",
    "should i",
    "may i",
    "do you confirm",
    "can you confirm",
    "please confirm",
    "confirm that",
    "go ahead",
    "would you like me to",
    "do you want me to",
    "want me to",
    "would you like to proceed",
    "shall i proceed",
    "proceed with",
    "is that ok",
    "is that okay",
    "is that right",
    "if you confirm",
)
"""Ways a model actually asks permission. Deliberately generous: a false positive only
arms a gate that still requires an affirmative, while a false negative silently breaks
the ritual — which is exactly what happened."""

_TRAILING = re.compile(r"[\s\"')\]]*$")


def seeks_confirmation(text: str) -> bool:
    """Whether an agent turn is asking the shopper to authorise an action.

    Requires a question, because a statement is not a request for permission — "I'll go
    ahead and cancel it" reports, it does not ask. Trailing quotes and brackets are
    tolerated so a reply that ends `...proceed?"` still counts.
    """
    stripped = _TRAILING.sub("", text.strip())
    if not stripped.endswith("?"):
        return False
    lowered = stripped.lower()
    return any(phrase in lowered for phrase in _ASKS)


# -- the shopper's side of the ritual -------------------------------------

_OPENERS = frozenset({
    "yes", "yeah", "yep", "yup", "ya", "sure", "ok", "okay", "alright", "fine",
    "confirm", "confirmed", "correct", "right", "absolutely", "definitely", "certainly",
    "please", "go", "do", "proceed",
})

_PHRASES = (
    "that's right", "that is right", "that's correct", "that is correct",
    "sounds good", "that works", "go for it",
)

_NEGATORS = frozenset({
    "no", "not", "nope", "nah", "don't", "dont", "never", "wait", "stop", "hold",
    "but", "instead", "wrong",
})
"""Any of these anywhere withdraws consent. "Cancel" is deliberately absent: when the
pending action *is* a cancellation, "yes, cancel it" is the clearest possible yes."""

_MAX_WORDS = 6
"""Consent is short. "Yes, and also I wanted to ask about the headphones order" opens a
new topic; treating its first word as authorisation would act on half a sentence."""

_PUNCT = re.compile(r"[^\w\s']")


def is_affirmative(text: str) -> bool:
    """Whether the shopper's reply authorises the pending action.

    The previous check split on whitespace without removing punctuation, so the first
    token of "Yes, go ahead." was `yes,` — not in the set. "yes, please", "sure, go for
    it" and "that's right" all failed the same way; only an unpunctuated "yes please"
    worked, which is what a test types and not what a person does.

    Asymmetric by design. A false negative costs one repeated question; a false positive
    creates a return nobody asked for. So a negator anywhere wins, and long replies do
    not count however they begin.
    """
    normalised = " ".join(_PUNCT.sub(" ", text.lower().replace("\u2019", "'")).split())
    if not normalised:
        return False
    words = normalised.split()
    if len(words) > _MAX_WORDS or any(w in _NEGATORS for w in words):
        return False
    return words[0] in _OPENERS or normalised.startswith(_PHRASES)
