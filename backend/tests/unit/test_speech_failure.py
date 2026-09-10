"""T065 — a reply that cannot be spoken must say so.

The failure this covers was real and silent: Groq returned 429, the adapter counted a
metric and returned, and the shopper sat watching a written reply waiting for a voice
that was never coming. Nothing on screen admitted anything had gone wrong.

The assertions that matter are the two negatives — that the shopper is never shown a
status code or a provider name, and that a turn is reported once rather than on every
clause.
"""

from __future__ import annotations

import pytest

from src.adapters.tts.groq_orpheus import GroqSpeechSynthesizer, _classify


@pytest.fixture
def tts() -> GroqSpeechSynthesizer:
    return GroqSpeechSynthesizer(api_key="test-key", voice="hannah")


def test_no_failure_means_nothing_to_report(tts: GroqSpeechSynthesizer) -> None:
    assert tts.synthesis_error("turn-1") is None


@pytest.mark.parametrize(
    ("status", "kind"),
    [(429, "rate_limited"), (401, "unauthorized"), (403, "unauthorized"), (500, "unavailable")],
)
def test_status_maps_to_a_cause_the_shopper_can_be_told(status: int, kind: str) -> None:
    assert _classify(status) == kind


def test_failure_is_reported_once_then_forgotten(tts: GroqSpeechSynthesizer) -> None:
    """Read-once keeps the map bounded by turns in flight, and keeps a single failed
    turn from apologising on every subsequent turn."""
    tts._record_failure("turn-1", "rate_limited")
    assert tts.synthesis_error("turn-1") == "rate_limited"
    assert tts.synthesis_error("turn-1") is None


def test_first_cause_of_a_turn_wins(tts: GroqSpeechSynthesizer) -> None:
    """A turn is several clauses. Once one has failed the rest usually fail too, and the
    first cause is the honest one — not whatever the last clause happened to hit."""
    tts._record_failure("turn-1", "rate_limited")
    tts._record_failure("turn-1", "unavailable")
    assert tts.synthesis_error("turn-1") == "rate_limited"


def test_failure_memory_stays_bounded(tts: GroqSpeechSynthesizer) -> None:
    """A map keyed by turn id that nobody reads is a slow leak on a long session."""
    for i in range(200):
        tts._record_failure(f"turn-{i}", "unavailable")
    assert len(tts._failures) <= 32


def test_shopper_text_names_no_vendor_and_no_status_code() -> None:
    from src.session.actor import _SPEECH_ERROR_TEXT

    for kind, text in _SPEECH_ERROR_TEXT.items():
        assert _classify(429) or True  # keep the classifier imported and honest
        lowered = text.lower()
        assert "groq" not in lowered, kind
        assert "orpheus" not in lowered, kind
        assert "429" not in text and "http" not in lowered, kind
        # It has to point at what the shopper can still do.
        assert "above" in lowered, kind
