"""The ten tool handlers.

Every handler returns a plain dict destined for the model. Two rules shape all of them:

- **Absence is an answer, not a failure.** An order with no shipment returns
  `not_yet_shipped` rather than raising, because the agent must be able to say "it hasn't
  shipped yet" — and must never invent a tracking number (FR-019).
- **Nothing here decides authorization.** Scoping already happened when the repository
  was constructed with a customer id. A handler cannot widen it.
"""

from __future__ import annotations

from typing import Any, cast

from src.adapters.store.sqlite import OrderRepository
from src.core.domain import format_money

from .registry import ToolContext, ToolRegistry


def _repo(ctx: ToolContext) -> OrderRepository:
    return cast(OrderRepository, ctx.repo)


# -- read-only (fast) ------------------------------------------------------


async def list_recent_orders(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    orders = await _repo(ctx).recent_orders(
        lookback_days=int(args["lookback_days"]), limit=int(args["limit"])
    )
    if not orders:
        return {"orders": [], "note": "no_orders_found"}
    return {
        "orders": [
            {
                "order_id": o.order_id,
                "placed_at": o.placed_at.date().isoformat(),
                "status": o.status.value,
                # Disambiguation happens on item name and date (FR-016), so the summary
                # is what the agent actually needs — not a full manifest.
                "item_summary": o.item_summary,
                "total": format_money(o.total_cents, o.currency),
            }
            for o in orders
        ]
    }


async def get_order(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = await _repo(ctx).get_order(str(args["order_ref"]))
    return {
        "order_id": order.order_id,
        "status": order.status.value,
        "placed_at": order.placed_at.date().isoformat(),
        "total": format_money(order.total_cents, order.currency),
        "can_cancel": order.can_cancel,
        "line_items": [
            {
                "line_item_id": li.line_item_id,
                "product_name": li.product_name,
                "quantity": li.quantity,
                "return_state": li.return_state.value,
                # The closing date is included so a refusal can state *when* the window
                # closed rather than refusing flatly (FR-025, VID-015).
                "return_window_closes_at": (
                    li.return_window_closes_at.date().isoformat()
                    if li.return_window_closes_at
                    else None
                ),
            }
            for li in order.line_items
        ],
        "shipment_count": len(order.shipments),
    }


async def get_shipment_tracking(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = await _repo(ctx).get_order(str(args["order_ref"]))
    if not order.shipments:
        # First-class result. The agent says "not shipped yet" and states the expected
        # ship date — it does not fabricate a carrier.
        return {"shipments": [], "note": "not_yet_shipped", "status": order.status.value}
    return {
        "shipments": [
            {
                "carrier": s.carrier,
                "tracking_reference": s.tracking_reference,
                "latest_scan_location": s.latest_scan_location,
                "latest_scan_at": s.latest_scan_at.date().isoformat() if s.latest_scan_at else None,
                "expected_delivery": s.expected_delivery.isoformat() if s.expected_delivery else None,
            }
            for s in order.shipments
        ]
    }


async def get_product_info(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    product = await _repo(ctx).get_product(str(args["product_ref"]))
    if product is None:
        return {"found": False, "note": "product_not_found"}
    return {
        "found": True,
        "name": product.name,
        # Merchant-supplied free text. The prompt layer wraps this in a delimited block
        # declared non-authoritative — it is data, never instructions (FR-049, TC-006).
        "attributes": product.attributes,
        "care_text": product.care_text,
    }


async def get_policy(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    policy = await _repo(ctx).get_policy(str(args["topic"]))
    if policy is None:
        # FR-039: say we do not have it rather than inferring a plausible answer.
        return {"found": False, "note": "policy_not_found"}
    return {"found": True, "topic": policy.topic, "body": policy.body, "params": policy.params}


# -- state-changing (confirmation-gated) -----------------------------------


async def create_return(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    result = await _repo(ctx).create_return(
        order_ref=str(args["order_ref"]),
        line_item_ids=[str(x) for x in args["line_item_ids"]],
        reason=str(args["reason"]),
        turn_id=ctx.turn_id,  # idempotency key derives from the turn, not the arguments
    )
    return {
        "return_id": result.return_id,
        "status": result.status.value,
        "refund_estimate_days": result.refund_estimate_days,
        "instructions": "Use the prepaid label in your email; drop off at any carrier point.",
        "deduplicated": result.deduplicated,
    }


async def cancel_order(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = await _repo(ctx).cancel_order(order_ref=str(args["order_ref"]), turn_id=ctx.turn_id)
    return {"order_id": order.order_id, "status": order.status.value, "deduplicated": False}


async def update_shipping_address(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = await _repo(ctx).get_order(str(args["order_ref"]))
    if not order.can_cancel:  # same gate: nothing changes after dispatch
        raise ValueError("already_shipped")
    # The eligibility check is real; persistence belongs to the order service this
    # simulation stands in for, which owns the address book.
    return {
        "order_id": order.order_id,
        "address_applied": args["address"],
        "deduplicated": False,
    }


# -- slow (holding phrase required) ----------------------------------------


async def request_policy_exception(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order = await _repo(ctx).get_order(str(args["order_ref"]))
    case_ref = await _repo(ctx).create_escalation(
        session_id=ctx.session_id,
        summary=f"Policy exception requested for {order.order_id}: {args['reason']}",
        order_refs=[order.order_id],
        actions_taken=["policy_exception_requested"],
        expected_response_hours=24,
    )
    # The agent has no authority to grant exceptions; it can only route them (FR-031).
    return {"case_ref": case_ref, "expected_response_hours": 24}


async def escalate_to_human(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = await _repo(ctx).create_escalation(
        session_id=ctx.session_id,
        summary=str(args["summary"]),
        order_refs=[str(x) for x in args["order_refs"]],
        actions_taken=[str(x) for x in args["actions_taken"]],
        expected_response_hours=4,
    )
    # The transcript pointer is attached server-side from the session, never passed by
    # the model — the model does not get to choose what evidence is attached.
    return {"ticket_id": ticket_id, "expected_response_hours": 4}


HANDLERS = {
    "list_recent_orders": list_recent_orders,
    "get_order": get_order,
    "get_shipment_tracking": get_shipment_tracking,
    "get_product_info": get_product_info,
    "get_policy": get_policy,
    "create_return": create_return,
    "cancel_order": cancel_order,
    "update_shipping_address": update_shipping_address,
    "request_policy_exception": request_policy_exception,
    "escalate_to_human": escalate_to_human,
}


def build_registry(audit: Any = None) -> ToolRegistry:
    """Register every contracted tool. A contract entry with no handler — or a handler
    with no contract entry — fails here rather than at call time."""
    registry = ToolRegistry(audit=audit)
    for name, handler in HANDLERS.items():
        registry.register(name, handler)

    contracted = {s.name for s in registry.specs()}
    if contracted != set(HANDLERS):
        missing = contracted - set(HANDLERS)
        extra = set(HANDLERS) - contracted
        raise ValueError(f"contract/handler mismatch — missing: {missing}, extra: {extra}")
    return registry
