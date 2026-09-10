"""Token stream → speakable clauses.

Waiting for a full sentence before synthesizing adds the whole sentence's generation
time to time-to-first-audio. Splitting on clause boundaries gets the first fragment to
TTS within a couple of tokens of the model starting.

The one thing it must never do is split inside a number, a currency amount, or an order
reference. "Your total is forty-two" / "dollars fifty" is not a prosody flaw, it is the
agent sounding broken — so digit runs hold the buffer open (VID-007).
"""

from __future__ import annotations

import re

BREAK_CHARS = frozenset(",;:—.?!")
SOFT_WORD_CAP = 12

# A trailing digit run means a number is still being spoken. Also catches currency and
# order references like "ORD-4471" and "R-EA8923".
_OPEN_NUMBER = re.compile(r"[\d$£€][\d\s.,:/-]*$|[A-Z]{2,}-[A-Z0-9]*$")


class ClauseSplitter:
    """Feed tokens in, get speakable clauses out."""

    def __init__(self, *, soft_word_cap: int = SOFT_WORD_CAP) -> None:
        self._buffer = ""
        self._cap = soft_word_cap

    def feed(self, token: str) -> list[str]:
        """Add a token; return any clauses that became speakable."""
        self._buffer += token
        out: list[str] = []

        while (clause := self._take()) is not None:
            out.append(clause)
        return out

    def flush(self) -> str | None:
        """Emit whatever remains at end of generation."""
        text = self._buffer.strip()
        self._buffer = ""
        return text or None

    def _take(self) -> str | None:
        stripped = self._buffer.rstrip()
        if not stripped:
            return None

        # Hard break on punctuation — unless a number is still open.
        if stripped[-1] in BREAK_CHARS and not self._number_open(stripped[:-1]):
            clause = self._buffer.strip()
            self._buffer = ""
            return clause or None

        # Soft cap keeps a long clause from delaying first audio indefinitely. Break on
        # the last whitespace so a word is never cut in half.
        if len(stripped.split()) > self._cap and not self._number_open(stripped):
            idx = stripped.rfind(" ")
            if idx > 0:
                clause = stripped[:idx].strip()
                # Slice the ORIGINAL buffer, not the rstripped copy. Tokens arrive with
                # their trailing space, and dropping it here welds the next token onto
                # this one — "Aurora" + "wireless " becomes "Aurorawireless".
                self._buffer = self._buffer[idx:].lstrip(" ")
                return clause or None
        return None

    @staticmethod
    def _number_open(text: str) -> bool:
        return bool(_OPEN_NUMBER.search(text.rstrip()))
