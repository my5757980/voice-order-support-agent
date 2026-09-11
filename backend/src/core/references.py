"""What goes on the screen instead of into the shopper's ear.

The persona forbids reading a tracking number, a URL or a full order reference aloud —
a spoken string of digits is unusable, and asking someone to write one down mid-call is
worse. It tells the agent to say it has put the reference on screen instead.

Nothing ever put anything on screen. The control message existed, the browser handled it,
the panel was there captioned "Tracking numbers and order references appear here instead
of being read aloud" — and the backend never sent one. So every time the agent followed
its instructions it told the shopper something untrue, and the panel it pointed at stayed
empty. That is worse than reading the number aloud.

Extraction lives here rather than in the tools because it is a property of the *result
shape*, not of any one tool: two tools return a tracking reference and three return an
order id. A tool that starts returning one gets this for free, and a pure function over
a dict is testable without a session, a socket or a browser (principle VII).
"""

from __future__ import annotations

from typing import Any

_MAX_REFERENCES = 6
"""Per tool result. The panel is a glance, not a report — and a model that asked for
every order at once should not be able to paste a page of identifiers into it."""

# Result key -> (kind, human label). Order is the order they appear on screen.
_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("tracking_reference", "tracking", "Tracking"),
    ("tracking_url", "url", "Track online"),
    ("order_ref", "order", "Order"),
    ("order_id", "order", "Order"),
    ("return_id", "return", "Return"),
    ("return_reference", "return", "Return"),
    ("rma", "return", "RMA"),
)

_CARRIER_KEY = "carrier"
"""A tracking number is unusable without knowing who to give it to, so the carrier is
folded into the label rather than shown as a reference of its own."""


def extract(result: Any) -> list[tuple[str, str, str]]:
    """Every displayable reference in a tool result, as (kind, label, value).

    Walks nested dicts and lists because the shapes genuinely nest: `get_shipment_tracking`
    returns `{"shipments": [{"carrier": ..., "tracking_reference": ...}]}`, and the
    reference the shopper needs is two levels down.

    Deduplicated by (kind, value): the same order id appears in most results, and showing
    it three times is noise rather than information.
    """
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    _walk(result, found, seen)
    return found[:_MAX_REFERENCES]


def _walk(node: Any, found: list[tuple[str, str, str]], seen: set[tuple[str, str]]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, found, seen)
        return
    if not isinstance(node, dict):
        return

    carrier = node.get(_CARRIER_KEY)
    for key, kind, label in _FIELDS:
        value = node.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if (kind, value) in seen:
            continue
        seen.add((kind, value))
        if kind in {"tracking", "url"} and isinstance(carrier, str) and carrier.strip():
            label = f"{label} · {carrier.strip()}"
        found.append((kind, label, value))

    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk(value, found, seen)
