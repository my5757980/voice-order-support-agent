"""Partials carry every word heard so far, final or not.

On a v3 Turn message `transcript` holds finalized words only. Partials built from it
surfaced each word about a second after it was spoken: on the deployed app a shopper
talking over the agent produced a single partial, "Sorry,", 1.6 s in — one word, so
suppressed as a backchannel — and the agent talked on until the whole sentence had been
committed. The words still being recognised arrive in `words`, and barge-in needs them.
"""

from __future__ import annotations

from src.adapters.stt.assemblyai import AssemblyAISpeechRecognizer
from src.core.events import UserPartial, UserTurnCommitted


def _stt() -> AssemblyAISpeechRecognizer:
    return AssemblyAISpeechRecognizer(api_key="test")


def _turn(transcript: str, words: list[tuple[str, bool]], end: bool = False) -> dict:
    return {
        "type": "Turn",
        "turn_order": 0,
        "end_of_turn": end,
        "turn_is_formatted": end,
        "transcript": transcript,
        "end_of_turn_confidence": 0.1,
        "words": [{"text": t, "word_is_final": f, "start": 0, "end": 0, "confidence": 0.9}
                  for t, f in words],
    }


def test_a_partial_includes_words_not_yet_final() -> None:
    stt = _stt()
    stt._on_turn(_turn("Sorry,", [("Sorry,", True), ("where's", False), ("my", False)]))
    event = stt._events.get_nowait()
    assert isinstance(event, UserPartial)
    assert event.text == "Sorry, where's my"


def test_a_partial_with_nothing_final_yet_still_arrives() -> None:
    """The earliest partial of a turn has no finalized words at all — and it is exactly
    the one barge-in wants."""
    stt = _stt()
    stt._on_turn(_turn("", [("Sorry", False)]))
    event = stt._events.get_nowait()
    assert isinstance(event, UserPartial) and event.text == "Sorry"


def test_the_committed_turn_is_the_final_transcript() -> None:
    """Only partials use the live words. What reaches memory is the final transcript."""
    stt = _stt()
    stt._on_turn(_turn("Sorry, where's my order?",
                       [("Sorry,", True), ("where's", True), ("my", True), ("order?", True)],
                       end=True))
    event = stt._events.get_nowait()
    assert isinstance(event, UserTurnCommitted)
    assert event.text == "Sorry, where's my order?"


def test_an_empty_turn_emits_nothing() -> None:
    stt = _stt()
    stt._on_turn(_turn("", []))
    assert stt._events.empty()
