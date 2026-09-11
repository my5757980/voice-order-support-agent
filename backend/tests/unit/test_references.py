"""What goes on screen instead of into the shopper's ear.

Found by a recorded run: the agent said "I've placed the tracking number on your screen",
exactly as the persona instructs — and the panel stayed empty, because nothing ever sent
one. The control message, the browser handler and the captioned panel all existed. The
agent was telling the shopper something untrue on every tracking request.
"""

from __future__ import annotations

from src.core.references import extract


def test_tracking_number_two_levels_down_is_found() -> None:
    """The real shape of `get_shipment_tracking` — the reference is nested."""
    result = {
        "shipments": [
            {"carrier": "UPS", "tracking_reference": "1Z999AA10123456784",
             "latest_scan_location": "Memphis"},
        ]
    }
    assert extract(result) == [("tracking", "Tracking · UPS", "1Z999AA10123456784")]


def test_carrier_is_folded_into_the_label() -> None:
    """A tracking number is unusable without knowing who to give it to."""
    (_, label, _), = extract({"carrier": "FedEx", "tracking_reference": "7489"})
    assert "FedEx" in label


def test_order_and_return_references_are_found() -> None:
    kinds = {k for k, _, _ in extract({"order_ref": "ORD-4488", "return_id": "R-EA8923"})}
    assert kinds == {"order", "return"}


def test_the_same_reference_is_shown_once() -> None:
    result = {"order_ref": "ORD-4488", "items": [{"order_ref": "ORD-4488"}]}
    assert len(extract(result)) == 1


def test_a_result_with_nothing_to_show_shows_nothing() -> None:
    assert extract({"shipments": [], "note": "not_yet_shipped"}) == []
    assert extract({"error": "requires explicit shopper confirmation first"}) == []


def test_the_panel_is_bounded() -> None:
    """A model that asked for every order at once should not be able to paste a page of
    identifiers into a panel meant to be glanced at."""
    many = {"orders": [{"order_ref": f"ORD-{i:04d}"} for i in range(50)]}
    assert len(extract(many)) <= 6


def test_blank_and_non_string_values_are_ignored() -> None:
    assert extract({"order_ref": "  ", "return_id": None, "tracking_reference": 12345}) == []
