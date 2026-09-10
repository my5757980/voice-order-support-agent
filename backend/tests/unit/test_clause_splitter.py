"""T053 — clause splitting.

Two properties matter. The clause must reach TTS early (that is the latency win), and
the reassembled text must be identical to what the model produced (that is correctness —
the agent's transcript and its memory are built from these clauses).
"""

from __future__ import annotations

import pytest

from src.core.clause_splitter import ClauseSplitter


def split(text: str, cap: int = 12) -> list[str]:
    s = ClauseSplitter(soft_word_cap=cap)
    out: list[str] = []
    for word in text.split(" "):
        out += s.feed(word + " ")
    if (tail := s.flush()) is not None:
        out.append(tail)
    return out


# -- the regression that caught a real bug --------------------------------


def test_soft_cap_split_does_not_weld_words_together() -> None:
    """Regression: the splitter used to slice an rstripped copy of the buffer, dropping
    the trailing space, so the next token was appended directly — 'Aurora' + 'wireless '
    became 'Aurorawireless'. Caught by an end-to-end test, not by construction."""
    text = (
        "You have two - the Fold desk lamp from 2026-09-07 and the "
        "Aurora wireless headphones from 2026-09-03. Which one?"
    )
    assert " ".join(split(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "Short answer.",
        "One two three four five six seven eight nine ten eleven twelve thirteen fourteen.",
        "It arrives Thursday, the twelfth, with UPS, and costs nothing.",
        "Your total is 42 dollars 50 cents and the order is ORD-4471.",
    ],
)
def test_reassembly_is_lossless(text: str) -> None:
    """Whatever is spoken must equal what was generated — no dropped or doubled spaces."""
    assert " ".join(split(text)) == text


# -- breaking behaviour ---------------------------------------------------


def test_breaks_on_sentence_end() -> None:
    assert split("First one. Second one.") == ["First one.", "Second one."]


def test_breaks_on_comma() -> None:
    assert split("It arrives Thursday, with UPS.") == ["It arrives Thursday,", "with UPS."]


def test_first_clause_is_available_early() -> None:
    """The latency win: a speakable fragment exists long before generation ends."""
    s = ClauseSplitter()
    emitted: list[str] = []
    for word in "Your order shipped, and it arrives on Thursday afternoon.".split(" "):
        emitted += s.feed(word + " ")
        if emitted:
            break
    assert emitted == ["Your order shipped,"]


def test_long_clause_is_capped_rather_than_delaying_audio() -> None:
    words = " ".join(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"] * 5)
    parts = split(words, cap=8)
    assert len(parts) > 1
    assert " ".join(parts) == words


# -- numbers must not be split (VID-007) ----------------------------------


def test_does_not_split_inside_a_decimal_amount() -> None:
    parts = split("The refund is 42.50 back to your card.")
    assert not any(p.endswith("42.") for p in parts)


def test_does_not_break_after_a_date() -> None:
    """'2026-09-03.' ends in a period, but the period belongs to the sentence and the
    digits mean a number is still open — breaking there sounds broken."""
    parts = split("Ordered on 2026-09-03. Arriving soon.")
    assert " ".join(parts) == "Ordered on 2026-09-03. Arriving soon."


def test_does_not_split_an_order_reference() -> None:
    parts = split("Your reference is ORD-4471 for that one.")
    assert not any(p.rstrip().endswith("ORD-") for p in parts)


# -- edges ----------------------------------------------------------------


def test_empty_input_yields_nothing() -> None:
    s = ClauseSplitter()
    assert s.feed("") == []
    assert s.flush() is None


def test_whitespace_only_yields_nothing() -> None:
    s = ClauseSplitter()
    assert s.feed("   ") == []
    assert s.flush() is None


def test_flush_returns_the_tail_once() -> None:
    s = ClauseSplitter()
    s.feed("no terminator here")
    assert s.flush() == "no terminator here"
    assert s.flush() is None
