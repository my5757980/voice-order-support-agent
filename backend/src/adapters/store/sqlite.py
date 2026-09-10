"""SQLite-backed order repository.

The rule this file exists to enforce: **`customer_id` comes from the authenticated
session and is applied to every query here — never taken from a caller argument.**
If the model proposes a customer identifier it is ignored, not honoured (FR-003, TC-003).
That is why the repository is constructed *with* a customer id rather than accepting one
per call: there is no signature that lets a caller pass the wrong one.

sqlite3 is used through `asyncio.to_thread`. Queries here are sub-millisecond against
seeded data, so thread-hop overhead is far below the 300 ms fast-tool budget, and it
avoids a dependency for something this small.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.core.domain import (
    LineItem,
    Order,
    OrderStatus,
    Policy,
    Product,
    ReturnRequest,
    ReturnState,
    ReturnStatus,
    Shipment,
)

SCHEMA = Path(__file__).with_name("schema.sql")


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


class OrderNotFound(LookupError):
    """Raised when no order matches *for this customer*.

    Deliberately does not distinguish "does not exist" from "belongs to someone else" —
    the agent must never confirm or deny an order's existence on another account.
    """


class AmbiguousReference(LookupError):
    """A partial reference matched more than one of the customer's orders."""


class OrderRepository:
    def __init__(self, conn: sqlite3.Connection, customer_id: str) -> None:
        self._conn = conn
        self._customer_id = customer_id

    # -- reads ------------------------------------------------------------

    async def recent_orders(self, lookback_days: int = 90, limit: int = 5) -> list[Order]:
        return await asyncio.to_thread(self._recent_orders, lookback_days, limit)

    def _recent_orders(self, lookback_days: int, limit: int) -> list[Order]:
        cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM orders
               WHERE customer_id = ? AND placed_at >= ?
               ORDER BY placed_at DESC LIMIT ?""",
            (self._customer_id, cutoff, limit),
        ).fetchall()
        return [self._hydrate(r) for r in rows]

    async def get_order(self, order_ref: str) -> Order:
        return await asyncio.to_thread(self._get_order, order_ref)

    def _get_order(self, order_ref: str) -> Order:
        row = self._conn.execute(
            "SELECT * FROM orders WHERE customer_id = ? AND order_id = ?",
            (self._customer_id, order_ref),
        ).fetchone()
        if row:
            return self._hydrate(row)

        # Partial reference: final characters only (FR-021). Still scoped to this
        # customer, so a partial can never reach across accounts.
        rows = self._conn.execute(
            "SELECT * FROM orders WHERE customer_id = ? AND order_id LIKE ?",
            (self._customer_id, f"%{order_ref}"),
        ).fetchall()
        if len(rows) == 1:
            return self._hydrate(rows[0])
        if len(rows) > 1:
            raise AmbiguousReference(f"{len(rows)} orders end with {order_ref!r}")
        raise OrderNotFound(order_ref)

    def _hydrate(self, row: sqlite3.Row) -> Order:
        order_id = row["order_id"]
        items = tuple(
            LineItem(
                line_item_id=r["line_item_id"],
                order_id=order_id,
                product_id=r["product_id"],
                product_name=r["name"],
                quantity=r["quantity"],
                unit_price_cents=r["unit_price_cents"],
                return_state=ReturnState(r["return_state"]),
                return_window_closes_at=_ts(r["return_window_closes_at"]),
            )
            for r in self._conn.execute(
                """SELECT li.*, p.name FROM line_items li
                   JOIN products p ON p.product_id = li.product_id
                   WHERE li.order_id = ?""",
                (order_id,),
            )
        )
        shipments = tuple(
            Shipment(
                shipment_id=r["shipment_id"],
                order_id=order_id,
                carrier=r["carrier"],
                tracking_reference=r["tracking_reference"],
                latest_scan_location=r["latest_scan_location"],
                latest_scan_at=_ts(r["latest_scan_at"]),
                expected_delivery=_d(r["expected_delivery"]),
            )
            for r in self._conn.execute(
                "SELECT * FROM shipments WHERE order_id = ?", (order_id,)
            )
        )
        return Order(
            order_id=order_id,
            customer_id=row["customer_id"],
            placed_at=datetime.fromisoformat(row["placed_at"]),
            status=OrderStatus(row["status"]),
            total_cents=row["total_cents"],
            currency=row["currency"],
            line_items=items,
            shipments=shipments,
        )

    async def get_product(self, product_ref: str) -> Product | None:
        return await asyncio.to_thread(self._get_product, product_ref)

    def _get_product(self, product_ref: str) -> Product | None:
        row = self._conn.execute(
            "SELECT * FROM products WHERE product_id = ? OR name LIKE ?",
            (product_ref, f"%{product_ref}%"),
        ).fetchone()
        if not row:
            return None
        return Product(
            product_id=row["product_id"],
            name=row["name"],
            attributes=json.loads(row["attributes"]),
            care_text=row["care_text"],
        )

    async def get_policy(self, topic: str) -> Policy | None:
        return await asyncio.to_thread(self._get_policy, topic)

    def _get_policy(self, topic: str) -> Policy | None:
        row = self._conn.execute("SELECT * FROM policies WHERE topic = ?", (topic,)).fetchone()
        if not row:
            return None
        return Policy(topic=row["topic"], body=row["body"], params=json.loads(row["params"]))

    # -- writes -----------------------------------------------------------

    async def create_return(
        self,
        *,
        order_ref: str,
        line_item_ids: list[str],
        reason: str,
        turn_id: str,
    ) -> ReturnRequest:
        return await asyncio.to_thread(
            self._create_return, order_ref, line_item_ids, reason, turn_id
        )

    def _create_return(
        self, order_ref: str, line_item_ids: list[str], reason: str, turn_id: str
    ) -> ReturnRequest:
        order = self._get_order(order_ref)  # scoping + existence, in one place
        key = f"return:{turn_id}"

        # Idempotency first. A repeat returns the original rather than raising: the
        # shopper asking twice is normal conversation, not an error condition.
        existing = self._conn.execute(
            "SELECT * FROM return_requests WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if existing:
            return ReturnRequest(
                return_id=existing["return_id"],
                order_id=existing["order_id"],
                line_item_ids=tuple(json.loads(existing["line_item_ids"])),
                reason=existing["reason"],
                status=ReturnStatus(existing["status"]),
                refund_estimate_days=existing["refund_estimate_days"],
                idempotency_key=key,
                deduplicated=True,
            )

        # Eligibility is checked server-side, per line item, against the store — never
        # against what the model believed or what memory remembered (MC-013).
        by_id = {li.line_item_id: li for li in order.line_items}
        for lid in line_item_ids:
            item = by_id.get(lid)
            if item is None:
                raise OrderNotFound(f"line item {lid} is not on order {order.order_id}")
            if not item.is_returnable:
                raise ValueError(f"line item {lid} is {item.return_state.value}")

        return_id = f"R-{uuid.uuid4().hex[:6].upper()}"
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO return_requests
               (return_id, order_id, line_item_ids, reason, status,
                refund_estimate_days, idempotency_key, created_by_turn_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                return_id, order.order_id, json.dumps(line_item_ids), reason,
                ReturnStatus.CREATED.value, 5, key, turn_id, now,
            ),
        )
        self._conn.executemany(
            "UPDATE line_items SET return_state = 'returned' WHERE line_item_id = ?",
            [(lid,) for lid in line_item_ids],
        )
        self._conn.commit()

        return ReturnRequest(
            return_id=return_id,
            order_id=order.order_id,
            line_item_ids=tuple(line_item_ids),
            reason=reason,
            status=ReturnStatus.CREATED,
            refund_estimate_days=5,
            idempotency_key=key,
        )

    async def cancel_order(self, *, order_ref: str, turn_id: str) -> Order:
        return await asyncio.to_thread(self._cancel_order, order_ref, turn_id)

    def _cancel_order(self, order_ref: str, turn_id: str) -> Order:
        order = self._get_order(order_ref)
        if order.status is OrderStatus.CANCELLED:
            return order  # idempotent: already where the caller wants it
        if not order.can_cancel:
            raise ValueError("already_shipped")
        self._conn.execute(
            "UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND customer_id = ?",
            (order.order_id, self._customer_id),
        )
        self._conn.commit()
        return self._get_order(order.order_id)

    async def create_escalation(
        self,
        *,
        session_id: str,
        summary: str,
        order_refs: list[str],
        actions_taken: list[str],
        expected_response_hours: int,
    ) -> str:
        return await asyncio.to_thread(
            self._create_escalation,
            session_id, summary, order_refs, actions_taken, expected_response_hours,
        )

    def _create_escalation(
        self,
        session_id: str,
        summary: str,
        order_refs: list[str],
        actions_taken: list[str],
        expected_response_hours: int,
    ) -> str:
        ticket_id = f"T-{uuid.uuid4().hex[:6].upper()}"
        self._conn.execute(
            """INSERT INTO escalation_tickets
               (ticket_id, session_id, summary, order_refs, actions_taken,
                transcript_ref, expected_response_hours, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                ticket_id, session_id, summary,
                json.dumps(order_refs), json.dumps(actions_taken),
                # Attached server-side from the session, never passed by the model.
                f"session:{session_id}",
                expected_response_hours, datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
        return ticket_id

    async def record_audit(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        arguments_redacted: dict[str, object],
        outcome: str,
        duration_ms: int,
    ) -> None:
        await asyncio.to_thread(
            self._record_audit,
            session_id, turn_id, tool_name, arguments_redacted, outcome, duration_ms,
        )

    def _record_audit(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        arguments_redacted: dict[str, object],
        outcome: str,
        duration_ms: int,
    ) -> None:
        self._conn.execute(
            """INSERT INTO tool_audit
               (audit_id, session_id, turn_id, tool_name, arguments_redacted,
                outcome, duration_ms, actor, created_at)
               VALUES (?,?,?,?,?,?,?,'agent',?)""",
            (
                uuid.uuid4().hex, session_id, turn_id, tool_name,
                json.dumps(arguments_redacted), outcome, duration_ms,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
