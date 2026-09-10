"""Seeded fixture data.

Every acceptance scenario in spec.md needs data to exercise it, including the awkward
ones — split shipments, an expired return window, an already-returned item, an order
that cannot be cancelled because it shipped. Those branches are the ones that break
live, so they get fixtures rather than hope.

Run: python -m src.adapters.store.seed
Idempotent — safe to re-run between demo takes (T078).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from .sqlite import apply_schema, connect

DEMO_CUSTOMER_ID = "cus_demo_001"
DEMO_CUSTOMER_NAME = "Alex"


def _iso(days_ago: int = 0, days_ahead: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago) + timedelta(days=days_ahead)).isoformat()


PRODUCTS = [
    (
        "prd_headphones",
        "Aurora wireless headphones",
        {"colour": "midnight blue", "battery": "30 hours", "warranty": "2 years"},
        "Wipe the earcups with a dry cloth. Do not submerge.",
    ),
    (
        "prd_lamp",
        "Fold desk lamp",
        {"colour": "warm white", "power": "9W LED", "warranty": "1 year"},
        "Dust with a dry cloth. Do not use solvents.",
    ),
    (
        "prd_mug",
        "Ridge ceramic mug",
        {"colour": "sand", "capacity": "350ml", "material": "stoneware"},
        "Dishwasher safe. Not suitable for microwave use.",
    ),
    (
        "prd_cable",
        "Braided USB-C cable, 2m",
        {"colour": "graphite", "length": "2m", "rating": "100W"},
        None,
    ),
]

POLICIES = [
    (
        "return_window",
        "Items can be returned within 30 days of delivery, as long as they are unused "
        "and in their original packaging.",
        {"window_days": 30},
    ),
    (
        "refund_timing",
        "Refunds are issued to the original payment method within 5 working days of "
        "the returned item arriving at our warehouse.",
        {"working_days": 5},
    ),
    (
        "shipping",
        "Standard delivery arrives in 3 to 5 working days. Express delivery arrives the "
        "next working day if ordered before 2pm.",
        {"standard_days": "3-5", "express_cutoff": "14:00"},
    ),
    (
        "cancellation",
        "Orders can be cancelled free of charge at any point before they are dispatched. "
        "Once dispatched, a return is needed instead.",
        {},
    ),
    (
        "address_change",
        "The delivery address can be changed at any point before dispatch.",
        {},
    ),
]


def seed(conn: sqlite3.Connection) -> None:
    apply_schema(conn)

    conn.execute(
        "INSERT OR REPLACE INTO customers VALUES (?,?,?)",
        (DEMO_CUSTOMER_ID, DEMO_CUSTOMER_NAME, _iso(400)),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO products VALUES (?,?,?,?)",
        [(pid, name, json.dumps(attrs), care) for pid, name, attrs, care in PRODUCTS],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO policies VALUES (?,?,?)",
        [(topic, body, json.dumps(params)) for topic, body, params in POLICIES],
    )

    # --- ORD-4471: shipped, in transit. The default answer to "where's my order?" ---
    conn.execute(
        "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?)",
        ("ORD-4471", DEMO_CUSTOMER_ID, _iso(6), "shipped", 12900, "USD"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO line_items VALUES (?,?,?,?,?,?,?)",
        ("li_4471_1", "ORD-4471", "prd_headphones", 1, 12900, "not_returnable", None),
    )
    conn.execute(
        "INSERT OR REPLACE INTO shipments VALUES (?,?,?,?,?,?,?)",
        (
            "shp_4471", "ORD-4471", "UPS", "1Z999AA10123456784",
            "Memphis, TN", _iso(0), (datetime.now(UTC) + timedelta(days=1)).date().isoformat(),
        ),
    )

    # --- ORD-4488: not yet shipped. Exercises cancellation and address change, and
    #     the "no tracking number exists" branch (FR-019). ---
    conn.execute(
        "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?)",
        ("ORD-4488", DEMO_CUSTOMER_ID, _iso(2), "processing", 4500, "USD"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO line_items VALUES (?,?,?,?,?,?,?)",
        ("li_4488_1", "ORD-4488", "prd_lamp", 1, 4500, "not_returnable", None),
    )

    # --- ORD-4302: delivered, SPLIT SHIPMENT, mixed return eligibility.
    #     One item still returnable, one past its window. This is the fixture that
    #     proves eligibility is per line item rather than per order. ---
    conn.execute(
        "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?)",
        ("ORD-4302", DEMO_CUSTOMER_ID, _iso(20), "delivered", 3400, "USD"),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO line_items VALUES (?,?,?,?,?,?,?)",
        [
            # Still inside its 30-day window.
            ("li_4302_1", "ORD-4302", "prd_mug", 2, 1200, "eligible", _iso(0, 12)),
            # Window already closed — the agent must say when it closed, not just refuse.
            ("li_4302_2", "ORD-4302", "prd_cable", 1, 1000, "window_closed", _iso(3)),
        ],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO shipments VALUES (?,?,?,?,?,?,?)",
        [
            ("shp_4302_a", "ORD-4302", "FedEx", "7712 3456 7890",
             "Delivered — front porch", _iso(16), _iso(16)[:10]),
            ("shp_4302_b", "ORD-4302", "FedEx", "7712 3456 7891",
             "Delivered — front porch", _iso(14), _iso(14)[:10]),
        ],
    )

    conn.commit()


def main() -> None:
    from src.config import get_settings

    try:
        db_path = get_settings().database_path
    except Exception:
        # Seeding must work before any API key exists — it touches no vendor at all.
        db_path = "./data/orders.db"

    conn = connect(db_path)
    seed(conn)
    orders = conn.execute(
        "SELECT order_id, status FROM orders WHERE customer_id = ? ORDER BY placed_at DESC",
        (DEMO_CUSTOMER_ID,),
    ).fetchall()
    print(f"seeded {db_path}")
    print(f"customer: {DEMO_CUSTOMER_NAME} ({DEMO_CUSTOMER_ID})")
    for row in orders:
        print(f"  {row['order_id']:<10} {row['status']}")
    conn.close()


if __name__ == "__main__":
    main()
