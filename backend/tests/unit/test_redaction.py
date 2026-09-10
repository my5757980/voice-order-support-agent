"""T004 — PII redaction runs before anything reaches a sink."""

from __future__ import annotations

import pytest

from src.obs.redaction import REDACTED, redact, redact_mapping


@pytest.mark.parametrize(
    "text",
    [
        "my email is shopper@example.com thanks",
        "contact me at first.last+tag@sub.domain.co.uk",
    ],
)
def test_emails_are_redacted(text: str) -> None:
    assert "@" not in redact(text)
    assert REDACTED in redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "my card is 4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "card 4111111111111111 expires soon",
    ],
)
def test_card_shaped_numbers_are_redacted(text: str) -> None:
    """FR-063: a spoken card number must never be stored or logged."""
    out = redact(text)
    assert "4111" not in out
    assert REDACTED in out


def test_long_identifiers_are_redacted() -> None:
    assert "123456789" not in redact("reference 123456789 please")


def test_ordinary_speech_survives_untouched() -> None:
    """Over-redaction that destroys the transcript is its own failure — a log nobody
    can read is not a safe log, it is a useless one."""
    text = "where is my order for the blue headphones"
    assert redact(text) == text


def test_short_numbers_survive() -> None:
    """Quantities and short references are not PII and stay legible."""
    assert redact("I ordered 2 of them") == "I ordered 2 of them"


def test_empty_string_is_safe() -> None:
    assert redact("") == ""


def test_mapping_redaction_is_recursive() -> None:
    data = {
        "order_ref": "A1B2",
        "note": "call me at 555 123 4567",
        "nested": {"email": "a@b.com"},
        "items": ["fine", "mail me at c@d.com"],
        "count": 3,
    }
    out = redact_mapping(data)
    assert out["order_ref"] == "A1B2"
    assert REDACTED in out["note"]  # type: ignore[operator]
    assert REDACTED in out["nested"]["email"]  # type: ignore[index]
    assert REDACTED in out["items"][1]  # type: ignore[index]
    assert out["count"] == 3
