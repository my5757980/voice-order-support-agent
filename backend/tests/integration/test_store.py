"""T034–T036 — seeded store, customer scoping, and idempotency.

No network, no vendor account: a temp SQLite file seeded from the same fixtures the demo
uses. The two properties worth protecting here are the ones that would be security or
correctness incidents in production, not merely bugs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.adapters.store.seed import DEMO_CUSTOMER_ID, seed
from src.adapters.store.sqlite import (
    AmbiguousReference,
    OrderNotFound,
    OrderRepository,
    connect,
)
from src.core.domain import OrderStatus, ReturnState


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(str(tmp_path / "test.db"))
    seed(c)
    return c


@pytest.fixture
def repo(conn: sqlite3.Connection) -> OrderRepository:
    return OrderRepository(conn, DEMO_CUSTOMER_ID)


# -- customer scoping: the security property ------------------------------


async def test_another_customer_cannot_read_this_customers_order(
    conn: sqlite3.Connection,
) -> None:
    """FR-003 / TC-003. Scoping comes from the session, so there is no signature that
    lets a caller pass someone else's id and be believed."""
    intruder = OrderRepository(conn, "cus_someone_else")
    with pytest.raises(OrderNotFound):
        await intruder.get_order("ORD-4471")


async def test_missing_and_forbidden_are_indistinguishable(conn: sqlite3.Connection) -> None:
    """The agent must never confirm an order exists on another account."""
    intruder = OrderRepository(conn, "cus_someone_else")
    with pytest.raises(OrderNotFound):
        await intruder.get_order("ORD-4471")  # exists, but not theirs
    with pytest.raises(OrderNotFound):
        await intruder.get_order("ORD-0000")  # does not exist at all


async def test_recent_orders_are_scoped(conn: sqlite3.Connection) -> None:
    assert await OrderRepository(conn, "cus_nobody").recent_orders() == []
    assert len(await OrderRepository(conn, DEMO_CUSTOMER_ID).recent_orders()) == 3


# -- idempotency: the correctness property --------------------------------


async def test_repeated_return_in_same_turn_does_not_duplicate(repo: OrderRepository) -> None:
    """FR-029. The shopper asking twice is normal conversation, not an error — so a
    repeat returns the original row rather than creating a second return or raising."""
    first = await repo.create_return(
        order_ref="ORD-4302", line_item_ids=["li_4302_1"], reason="too small", turn_id="turn-1"
    )
    second = await repo.create_return(
        order_ref="ORD-4302", line_item_ids=["li_4302_1"], reason="too small", turn_id="turn-1"
    )
    assert first.return_id == second.return_id
    assert first.deduplicated is False
    assert second.deduplicated is True


async def test_idempotency_is_enforced_by_the_database(conn: sqlite3.Connection) -> None:
    """The UNIQUE constraint is what makes FR-029 hold even if calling logic is wrong.
    An application-level check alone would not survive a bug above it."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.executemany(
            """INSERT INTO return_requests
               (return_id, order_id, line_item_ids, reason, status,
                refund_estimate_days, idempotency_key, created_by_turn_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                ("R-1", "ORD-4302", "[]", "x", "created", 5, "dup-key", "t1", "2026-09-09"),
                ("R-2", "ORD-4302", "[]", "x", "created", 5, "dup-key", "t2", "2026-09-09"),
            ],
        )


# -- return eligibility is per line item ----------------------------------


async def test_return_eligibility_is_per_line_item_not_per_order(repo: OrderRepository) -> None:
    """An order can be half returnable, and the agent must be able to say so."""
    order = await repo.get_order("ORD-4302")
    states = {li.product_name: li.return_state for li in order.line_items}
    assert ReturnState.ELIGIBLE in states.values()
    assert ReturnState.WINDOW_CLOSED in states.values()


async def test_returning_an_out_of_window_item_is_refused(repo: OrderRepository) -> None:
    with pytest.raises(ValueError, match="window_closed"):
        await repo.create_return(
            order_ref="ORD-4302", line_item_ids=["li_4302_2"], reason="x", turn_id="t"
        )


async def test_cannot_return_an_item_from_a_different_order(repo: OrderRepository) -> None:
    with pytest.raises(OrderNotFound):
        await repo.create_return(
            order_ref="ORD-4302", line_item_ids=["li_4471_1"], reason="x", turn_id="t"
        )


# -- cancellation ---------------------------------------------------------


async def test_unshipped_order_can_be_cancelled(repo: OrderRepository) -> None:
    order = await repo.get_order("ORD-4488")
    assert order.can_cancel is True
    cancelled = await repo.cancel_order(order_ref="ORD-4488", turn_id="t")
    assert cancelled.status is OrderStatus.CANCELLED


async def test_shipped_order_cannot_be_cancelled(repo: OrderRepository) -> None:
    """FR-027 — and the caller is expected to offer a return instead of refusing flatly."""
    assert (await repo.get_order("ORD-4471")).can_cancel is False
    with pytest.raises(ValueError, match="already_shipped"):
        await repo.cancel_order(order_ref="ORD-4471", turn_id="t")


async def test_cancelling_twice_is_idempotent(repo: OrderRepository) -> None:
    await repo.cancel_order(order_ref="ORD-4488", turn_id="t1")
    again = await repo.cancel_order(order_ref="ORD-4488", turn_id="t2")
    assert again.status is OrderStatus.CANCELLED


# -- references and shipments ---------------------------------------------


async def test_partial_order_reference_resolves(repo: OrderRepository) -> None:
    """FR-021 — the shopper reads out the last four characters, not the whole id."""
    assert (await repo.get_order("4471")).order_id == "ORD-4471"


async def test_ambiguous_partial_reference_is_reported_not_guessed(
    conn: sqlite3.Connection, repo: OrderRepository
) -> None:
    """FR-014: never guess a referent. Two matches must surface as ambiguity."""
    conn.execute(
        "INSERT INTO orders VALUES ('ORD-9471', ?, ?, 'placed', 100, 'USD')",
        (DEMO_CUSTOMER_ID, "2026-09-01T00:00:00+00:00"),
    )
    conn.commit()
    with pytest.raises(AmbiguousReference):
        await repo.get_order("471")


async def test_split_shipments_are_preserved(repo: OrderRepository) -> None:
    """Collapsing two shipments into one answer would mislead the shopper."""
    assert len((await repo.get_order("ORD-4302")).shipments) == 2


async def test_unshipped_order_has_no_tracking(repo: OrderRepository) -> None:
    """FR-019 — absence of a shipment is a first-class answer, not a missing value."""
    assert (await repo.get_order("ORD-4488")).shipments == ()


# -- product and policy ---------------------------------------------------


async def test_product_lookup_by_name_fragment(repo: OrderRepository) -> None:
    product = await repo.get_product("headphones")
    assert product is not None
    assert product.attributes["colour"] == "midnight blue"


async def test_policy_carries_params_for_application(repo: OrderRepository) -> None:
    policy = await repo.get_policy("return_window")
    assert policy is not None
    assert policy.params["window_days"] == 30


async def test_unknown_policy_returns_none_rather_than_inventing(repo: OrderRepository) -> None:
    """FR-039 — the agent says it does not have the information; it does not infer."""
    assert await repo.get_policy("warranty_extension") is None
