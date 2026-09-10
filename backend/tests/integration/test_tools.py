"""T037–T039 — the four gates, and the handlers behind them.

The tests that matter most here are the ones proving a tool is *unreachable*, not the
ones proving it works. A `create_return` that runs when it should not is a real return
on a real order; a `create_return` that fails to run is a conversation the shopper
retries.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.adapters.store.seed import DEMO_CUSTOMER_ID, seed
from src.adapters.store.sqlite import OrderRepository, connect
from src.core.ports import LatencyClass, ToolCall
from src.tools.definitions import load_specs, openai_tools, spec_for
from src.tools.handlers import build_registry
from src.tools.registry import ToolContext


@pytest.fixture
def repo(tmp_path: Path) -> OrderRepository:
    conn: sqlite3.Connection = connect(str(tmp_path / "t.db"))
    seed(conn)
    return OrderRepository(conn, DEMO_CUSTOMER_ID)


@pytest.fixture
def registry():  # type: ignore[no-untyped-def]
    return build_registry()


def ctx(repo: OrderRepository, *, speculative: bool = False, turn: str = "turn-1") -> ToolContext:
    return ToolContext(session_id="sess-1", turn_id=turn, repo=repo, speculative=speculative)


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=args)


# -- the contract ---------------------------------------------------------


def test_all_ten_tools_are_contracted_and_handled(registry) -> None:  # type: ignore[no-untyped-def]
    assert len(load_specs()) == 10
    assert len(registry.registered()) == 10


def test_every_tool_is_latency_classified() -> None:
    """TC-001 — an unclassified tool has no holding-phrase policy and must not exist."""
    assert all(isinstance(s.latency_class, LatencyClass) for s in load_specs())


def test_no_schema_exposes_customer_id() -> None:
    """FR-003 — scoping comes from the session. There is no field to smuggle it through."""
    import json

    for spec in load_specs():
        assert "customer_id" not in json.dumps(spec.input_schema)


def test_state_changing_tools_are_never_speculative_safe() -> None:
    """Speculation is read-only by construction; a guess must not create a return."""
    for spec in load_specs():
        if spec.state_changing:
            assert spec.speculative_safe is False


def test_provider_definitions_are_strict() -> None:
    """The schemas the adapter actually sends must be strict-validatable.

    `additionalProperties: false` plus an explicit `required` list is what stops a model
    smuggling an unexpected field past the provider — the first of the four gates.
    """
    tools = openai_tools()
    assert len(tools) == 10
    for tool in tools:
        assert tool["type"] == "function"
        schema = tool["function"]["parameters"]  # type: ignore[index]
        assert schema["additionalProperties"] is False
        assert "required" in schema


def test_unregistered_tool_cannot_be_registered(registry) -> None:  # type: ignore[no-untyped-def]
    async def noop(_c: ToolContext, _a: dict[str, object]) -> dict[str, object]:
        return {}

    with pytest.raises(ValueError, match="no entry in the tool contract"):
        registry.register("delete_everything", noop)


# -- gate 1: unknown tool -------------------------------------------------


async def test_unknown_tool_is_refused(registry, repo: OrderRepository) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(call("drop_database"), ctx(repo))
    assert result.ok is False
    assert result.error_code == "unknown_tool"


# -- gate 2: argument schema ----------------------------------------------


async def test_missing_required_argument_never_reaches_the_handler(registry, repo) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(call("get_order"), ctx(repo))
    assert result.ok is False
    assert result.error_code == "invalid_arguments"


async def test_unexpected_field_is_rejected(registry, repo) -> None:  # type: ignore[no-untyped-def]
    """additionalProperties:false is what stops a model smuggling in an extra field."""
    result = await registry.invoke(
        call("get_order", order_ref="ORD-4471", customer_id="cus_someone_else"), ctx(repo)
    )
    assert result.ok is False
    assert result.error_code == "invalid_arguments"


async def test_wrong_type_is_rejected(registry, repo) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(
        call("list_recent_orders", lookback_days="ninety", limit=5), ctx(repo)
    )
    assert result.ok is False


async def test_enum_violation_is_rejected(registry, repo) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(call("get_policy", topic="make_something_up"), ctx(repo))
    assert result.ok is False


async def test_validation_failure_is_structured_not_fatal(registry, repo) -> None:  # type: ignore[no-untyped-def]
    """FR-052 — the model gets a correctable error; the turn survives."""
    result = await registry.invoke(call("get_order", order_ref="x"), ctx(repo))
    assert result.ok is False
    assert "field" in result.content


# -- gate 3: speculation is read-only -------------------------------------


async def test_speculative_state_changing_tool_is_refused(registry, repo) -> None:  # type: ignore[no-untyped-def]
    """The whole safety argument for speculative dispatch rests on this test."""
    registry.confirm("turn-1")  # even confirmed, speculation must not execute it
    result = await registry.invoke(
        call("create_return", order_ref="ORD-4302", line_item_ids=["li_4302_1"], reason="x"),
        ctx(repo, speculative=True),
    )
    assert result.ok is False
    assert result.error_code == "speculation_forbidden"


async def test_speculative_read_only_tool_is_allowed(registry, repo) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(
        call("get_order", order_ref="ORD-4471"), ctx(repo, speculative=True)
    )
    assert result.ok is True


# -- gate 4: the confirmation ritual --------------------------------------


async def test_return_without_confirmation_is_refused(registry, repo) -> None:  # type: ignore[no-untyped-def]
    """SC-010 — a single violation is a release blocker. Enforced here, not by prompting:
    a model that decides to skip the ritual still cannot execute the tool."""
    result = await registry.invoke(
        call("create_return", order_ref="ORD-4302", line_item_ids=["li_4302_1"], reason="x"),
        ctx(repo),
    )
    assert result.ok is False
    assert result.error_code == "confirmation_required"


async def test_return_after_confirmation_succeeds(registry, repo) -> None:  # type: ignore[no-untyped-def]
    registry.confirm("turn-1")
    result = await registry.invoke(
        call("create_return", order_ref="ORD-4302", line_item_ids=["li_4302_1"], reason="too small"),
        ctx(repo),
    )
    assert result.ok is True
    assert result.content["return_id"].startswith("R-")  # type: ignore[union-attr]


async def test_revoking_confirmation_blocks_the_action(registry, repo) -> None:  # type: ignore[no-untyped-def]
    registry.confirm("turn-1")
    registry.revoke("turn-1")
    result = await registry.invoke(
        call("cancel_order", order_ref="ORD-4488"), ctx(repo)
    )
    assert result.error_code == "confirmation_required"


async def test_confirmation_does_not_leak_across_turns(registry, repo) -> None:  # type: ignore[no-untyped-def]
    """Confirming one action must not authorise the next one silently."""
    registry.confirm("turn-1")
    result = await registry.invoke(
        call("cancel_order", order_ref="ORD-4488"), ctx(repo, turn="turn-2")
    )
    assert result.error_code == "confirmation_required"


# -- handler behaviour ----------------------------------------------------


async def test_unshipped_order_reports_not_yet_shipped(registry, repo) -> None:  # type: ignore[no-untyped-def]
    """FR-019 — absence of a shipment is an answer, and no carrier may be invented."""
    result = await registry.invoke(call("get_shipment_tracking", order_ref="ORD-4488"), ctx(repo))
    assert result.ok is True
    assert result.content["note"] == "not_yet_shipped"
    assert result.content["shipments"] == []


async def test_split_shipments_are_reported_separately(registry, repo) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(call("get_shipment_tracking", order_ref="ORD-4302"), ctx(repo))
    assert len(result.content["shipments"]) == 2  # type: ignore[arg-type]


async def test_order_detail_carries_per_item_return_state(registry, repo) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(call("get_order", order_ref="ORD-4302"), ctx(repo))
    states = {li["return_state"] for li in result.content["line_items"]}  # type: ignore[union-attr]
    assert states == {"eligible", "window_closed"}


async def test_window_closed_item_reports_its_closing_date(registry, repo) -> None:  # type: ignore[no-untyped-def]
    """So the agent can say when it closed instead of refusing flatly (VID-015)."""
    result = await registry.invoke(call("get_order", order_ref="ORD-4302"), ctx(repo))
    closed = [
        li for li in result.content["line_items"]  # type: ignore[union-attr]
        if li["return_state"] == "window_closed"
    ]
    assert closed and closed[0]["return_window_closes_at"] is not None


async def test_cancelling_a_shipped_order_fails_honestly(registry, repo) -> None:  # type: ignore[no-untyped-def]
    registry.confirm("turn-1")
    result = await registry.invoke(call("cancel_order", order_ref="ORD-4471"), ctx(repo))
    assert result.ok is False
    assert result.error_code == "not_permitted"


async def test_cross_customer_order_is_not_found(registry, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    conn = connect(str(tmp_path / "x.db"))
    seed(conn)
    intruder = OrderRepository(conn, "cus_someone_else")
    result = await registry.invoke(call("get_order", order_ref="ORD-4471"), ctx(intruder))
    assert result.ok is False
    assert result.error_code == "order_not_found"


async def test_unknown_policy_says_so_rather_than_inventing(registry, repo) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(call("get_policy", topic="shipping"), ctx(repo))
    assert result.content["found"] is True
    assert "3 to 5" in str(result.content["body"])


async def test_escalation_creates_a_ticket_with_context(registry, repo) -> None:  # type: ignore[no-untyped-def]
    result = await registry.invoke(
        call(
            "escalate_to_human",
            summary="Shopper wants a refund outside policy",
            order_refs=["ORD-4302"],
            actions_taken=["checked return window"],
        ),
        ctx(repo),
    )
    assert result.ok is True
    assert result.content["ticket_id"].startswith("T-")  # type: ignore[union-attr]


# -- latency classification ------------------------------------------------


def test_slow_tools_are_identified_for_holding_phrases(registry) -> None:  # type: ignore[no-untyped-def]
    assert registry.is_slow("escalate_to_human") is True
    assert registry.is_slow("get_order") is False


def test_read_only_tools_are_all_fast() -> None:
    for name in ("list_recent_orders", "get_order", "get_shipment_tracking",
                 "get_product_info", "get_policy"):
        spec = spec_for(name)
        assert spec is not None and spec.latency_class is LatencyClass.FAST
