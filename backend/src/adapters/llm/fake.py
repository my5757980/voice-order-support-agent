"""Scripted language model.

Two jobs. In tests it replays deterministic token streams. In `DEMO_MODE` without an
Anthropic key it drives a genuinely working conversation against the *real* tool
registry and the *real* seeded store — so the whole pipeline, including the four tool
gates and the confirmation ritual, is exercised before any key exists.

It is a rule engine, not a model. It is not pretending otherwise, and it is never on
the path once `ANTHROPIC_API_KEY` is set.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence

from src.core.ports import LlmChunk, ToolCall

# Rough per-token pacing so the clause splitter and TTS handoff see realistic timing
# rather than an instantaneous burst that would hide ordering bugs.
TOKEN_DELAY_S = 0.012


def _last_user_text(messages: Sequence[dict[str, object]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content.lower()
    return ""


def _tool_results(messages: Sequence[dict[str, object]]) -> dict[str, object]:
    """Most recent tool result from THIS turn.

    Scanning stops at the last user message. Without that boundary, turn two would
    re-render turn one's tool result — which made a shopper saying "yes" hear the
    confirmation read-back a second time instead of the return being created.
    """
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return {}
        if msg.get("role") == "tool":
            result = msg.get("content")
            if isinstance(result, dict):
                return result
    return {}


AFFIRMATIVE = re.compile(r"\b(yes|yeah|yep|sure|go ahead|do it|confirm|please do)\b")


class FakeLanguageModel:
    """Implements the LanguageModel port."""

    def __init__(self, *, token_delay_s: float = TOKEN_DELAY_S) -> None:
        self._delay = token_delay_s
        self.calls: list[str] = []  # test introspection

    async def stream(
        self,
        *,
        messages: Sequence[dict[str, object]],
        speculative: bool = False,
    ) -> AsyncIterator[LlmChunk]:
        text = _last_user_text(messages)
        results = _tool_results(messages)

        # --- second pass: a tool already ran, so speak the answer -------------
        if results:
            async for chunk in self._speak(self._render(results, text)):
                yield chunk
            return

        # --- first pass: choose a tool ---------------------------------------
        tool = self._choose_tool(text)
        if tool is not None:
            self.calls.append(tool.name)
            yield LlmChunk(tool_calls=(tool,), stop_reason="tool_use")
            return

        async for chunk in self._speak(
            "I can help with your orders — where they are, returns, or cancelling "
            "something that hasn't shipped. What do you need?"
        ):
            yield chunk

    # -- routing -----------------------------------------------------------

    def _choose_tool(self, text: str) -> ToolCall | None:
        if not text:
            return None

        if AFFIRMATIVE.search(text):
            # The confirmation ritual is enforced in the registry, not here. This call
            # is refused unless the orchestrator recorded an affirmative for the turn.
            return ToolCall(
                id="c-return",
                name="create_return",
                arguments={
                    "order_ref": "ORD-4302",
                    "line_item_ids": ["li_4302_1"],
                    "reason": "shopper requested",
                },
            )

        if "return" in text or "send.*back" in text or "refund" in text:
            return ToolCall(id="c-order", name="get_order", arguments={"order_ref": "ORD-4302"})

        if "cancel" in text:
            return ToolCall(id="c-cancel", name="get_order", arguments={"order_ref": "ORD-4488"})

        if "track" in text or "deliver" in text or "arrive" in text:
            return ToolCall(
                id="c-track", name="get_shipment_tracking", arguments={"order_ref": "ORD-4471"}
            )

        if "policy" in text or "how long" in text:
            return ToolCall(id="c-policy", name="get_policy", arguments={"topic": "refund_timing"})

        if "order" in text or "where" in text or "status" in text:
            return ToolCall(
                id="c-list",
                name="list_recent_orders",
                arguments={"lookback_days": 90, "limit": 5},
            )
        return None

    # -- rendering ---------------------------------------------------------

    def _render(self, results: dict[str, object], text: str) -> str:
        """Turn a tool result into two sentences.

        The two-sentence cap is VID-003, and it matters more than it looks: a voice
        agent that monologues cannot be interrupted politely.
        """
        if "orders" in results:
            orders = results.get("orders") or []
            if not orders:
                return "I can't find a recent order on your account. Want me to get a person?"
            if len(orders) == 1:  # type: ignore[arg-type]
                o = orders[0]  # type: ignore[index]
                return f"Your {o['item_summary']} order is {o['status']}. Anything else?"
            first, second = orders[0], orders[1]  # type: ignore[index]
            return (
                f"You have two — the {first['item_summary']} from {first['placed_at']} "
                f"and the {second['item_summary']} from {second['placed_at']}. Which one?"
            )

        if "shipments" in results:
            shipments = results.get("shipments") or []
            if not shipments:
                # FR-019: never invent a carrier for something that hasn't shipped.
                return "That one hasn't shipped yet. I'll let you know as soon as it does."
            s = shipments[0]  # type: ignore[index]
            return (
                f"It's with {s['carrier']}, last seen in {s['latest_scan_location']}. "
                f"Arriving {s['expected_delivery']}."
            )

        if "line_items" in results:
            if "cancel" in text:
                if results.get("can_cancel"):
                    return "That one hasn't shipped, so I can cancel it. Shall I go ahead?"
                return "That's already shipped, so I can't cancel it — but I can start a return."
            items = results.get("line_items") or []
            eligible = [li for li in items if li["return_state"] == "eligible"]  # type: ignore[index]
            if eligible:
                # The read-back names the item and ends in a direct yes/no (VID-016).
                return f"That's the {eligible[0]['product_name']} — shall I start that return?"
            return "That item's return window has closed. I can ask a colleague to look at it."

        if results.get("return_id"):
            return (
                f"Done — your return reference is {results['return_id']}. "
                f"The refund lands within {results.get('refund_estimate_days', 5)} working days."
            )

        if results.get("found") and "body" in results:
            return str(results["body"])

        if results.get("error"):
            # FR-030: a failed action is reported as failed. Never as success.
            return "That didn't go through, sorry. Want me to get a person to sort it out?"

        return "I've got that. Anything else?"

    async def _speak(self, sentence: str) -> AsyncIterator[LlmChunk]:
        for word in sentence.split(" "):
            await asyncio.sleep(self._delay)
            yield LlmChunk(text=word + " ")
        yield LlmChunk(stop_reason="end_turn")
