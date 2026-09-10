"""PII redaction, applied before anything reaches a sink.

Constitution Security & Privacy: redaction runs before transcripts reach logs, traces,
analytics, or any third party. This module is deliberately dependency-free and
conservative — it over-redacts rather than risking a leak, because a redacted order
number in a log costs a debugging minute while a leaked card number costs far more.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# 13-19 digits, optionally separated by spaces or hyphens — card-shaped.
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# Long digit runs that are not card-length but still identifier-shaped.
_LONG_DIGITS = re.compile(r"\b\d{7,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[ -]?)?(?:\(\d{2,4}\)[ -]?)?\d{3,4}[ -]\d{3,4}(?:[ -]\d{2,4})?(?!\w)")

REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Remove obvious PII from a string.

    Order matters: emails first (they contain no digits we want to keep), then
    card-shaped runs, then phones, then any remaining long digit run.
    """
    if not text:
        return text
    out = _EMAIL.sub(REDACTED, text)
    out = _CARD.sub(REDACTED, out)
    out = _PHONE.sub(REDACTED, out)
    out = _LONG_DIGITS.sub(REDACTED, out)
    return out


def redact_mapping(data: dict[str, object]) -> dict[str, object]:
    """Redact every string value in a flat or nested mapping.

    Used for tool-call audit entries, where arguments are recorded but must not carry
    raw PII (FR-064).
    """
    result: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = redact(value)
        elif isinstance(value, dict):
            result[key] = redact_mapping(value)  # type: ignore[arg-type]
        elif isinstance(value, list):
            result[key] = [redact(v) if isinstance(v, str) else v for v in value]
        else:
            result[key] = value
    return result
