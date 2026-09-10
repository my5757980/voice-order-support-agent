"""Domain entities.

Pure dataclasses and enums — no persistence, no vendor types. The store adapter maps
rows onto these; the orchestrator and tools reason about these and never about rows.

Money is integer cents throughout. Floats for currency produce the kind of error that
survives review and surfaces in a refund.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class OrderStatus(str, Enum):
    PLACED = "placed"
    PROCESSING = "processing"
    PARTIALLY_SHIPPED = "partially_shipped"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @property
    def has_shipped(self) -> bool:
        """Whether cancellation is still possible (FR-027)."""
        return self in (
            OrderStatus.PARTIALLY_SHIPPED,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
        )


class ReturnState(str, Enum):
    ELIGIBLE = "eligible"
    WINDOW_CLOSED = "window_closed"
    RETURNED = "returned"
    NOT_RETURNABLE = "not_returnable"


class ReturnStatus(str, Enum):
    CREATED = "created"
    LABEL_ISSUED = "label_issued"
    IN_TRANSIT = "in_transit"
    REFUNDED = "refunded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Customer:
    customer_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class Product:
    product_id: str
    name: str
    attributes: dict[str, str] = field(default_factory=dict)
    care_text: str | None = None


@dataclass(frozen=True, slots=True)
class LineItem:
    line_item_id: str
    order_id: str
    product_id: str
    product_name: str
    quantity: int
    unit_price_cents: int
    return_state: ReturnState
    return_window_closes_at: datetime | None = None

    @property
    def is_returnable(self) -> bool:
        return self.return_state is ReturnState.ELIGIBLE


@dataclass(frozen=True, slots=True)
class Shipment:
    shipment_id: str
    order_id: str
    carrier: str
    tracking_reference: str
    latest_scan_location: str | None = None
    latest_scan_at: datetime | None = None
    expected_delivery: date | None = None


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    customer_id: str
    placed_at: datetime
    status: OrderStatus
    total_cents: int
    currency: str = "USD"
    line_items: tuple[LineItem, ...] = ()
    shipments: tuple[Shipment, ...] = ()

    @property
    def can_cancel(self) -> bool:
        """Cancellation is legal only before anything ships (FR-027)."""
        return not self.status.has_shipped and self.status is not OrderStatus.CANCELLED

    @property
    def item_summary(self) -> str:
        """A speakable description used for disambiguation (FR-016).

        Names the first item and counts the rest — reading a full manifest aloud is
        exactly the behaviour VID-005 forbids.
        """
        if not self.line_items:
            return "an order"
        first = self.line_items[0].product_name
        extra = len(self.line_items) - 1
        return first if extra == 0 else f"{first} and {extra} more item{'s' if extra > 1 else ''}"


@dataclass(frozen=True, slots=True)
class Policy:
    topic: str
    body: str
    params: dict[str, str | int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReturnRequest:
    return_id: str
    order_id: str
    line_item_ids: tuple[str, ...]
    reason: str
    status: ReturnStatus
    refund_estimate_days: int
    idempotency_key: str
    deduplicated: bool = False
    """True when this row already existed — a repeat request returns the original
    rather than creating a second return or raising (FR-029)."""


@dataclass(frozen=True, slots=True)
class EscalationTicket:
    ticket_id: str
    session_id: str
    summary: str
    order_refs: tuple[str, ...]
    actions_taken: tuple[str, ...]
    expected_response_hours: int


def format_money(cents: int, currency: str = "USD") -> str:
    """Speakable money. VID-007 requires natural spoken amounts, not '42.50 USD'."""
    symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency, "")
    return f"{symbol}{cents // 100}.{cents % 100:02d}"
