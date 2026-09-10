"""Tool validation, authorization, and dispatch.

Four gates run before any handler is reached, in this order. The ordering is deliberate:
each gate is cheaper and more absolute than the next, and a refusal must never depend on
having executed something first.

    1. Known tool?           unknown names are refused outright
    2. Arguments valid?      schema-checked (FR-047) — invalid never reaches a handler
    3. Speculation legal?    state-changing tools are unreachable speculatively
    4. Confirmation held?    state-changing tools need a confirmed PendingAction (FR-023)

Gate 4 is why `create_return` cannot be reached by prompting alone. The confirmation
requirement is enforced *here*, structurally, rather than asked for in a system prompt —
a model that decides to skip the ritual still cannot execute the tool.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from src.core.ports import LatencyClass, ToolCall, ToolResult, ToolSpec
from src.obs.redaction import redact_mapping

from .definitions import load_specs, spec_for
from .schema_validate import ValidationError, validate

Handler = Callable[["ToolContext", dict[str, object]], Awaitable[dict[str, object]]]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a handler is allowed to know.

    Note what is absent: there is no way for a handler to learn a customer id other than
    through `repo`, which was constructed with one. A handler cannot act on the wrong
    customer even if its arguments say otherwise.
    """

    session_id: str
    turn_id: str
    repo: object  # OrderRepository — untyped here to keep tools store-agnostic
    speculative: bool = False


class AuditSink(Protocol):
    async def record_audit(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        arguments_redacted: dict[str, object],
        outcome: str,
        duration_ms: int,
    ) -> None: ...


class ToolRegistry:
    def __init__(self, audit: AuditSink | None = None) -> None:
        self._handlers: dict[str, Handler] = {}
        self._audit = audit
        # Turns that have received an affirmative confirmation for a pending action.
        self._confirmed: set[str] = set()

    # -- registration ------------------------------------------------------

    def register(self, name: str, handler: Handler) -> None:
        spec = spec_for(name)
        if spec is None:
            # TC-001: a tool with no contract has no latency class and no safety
            # attributes, so it must not be callable.
            raise ValueError(f"{name!r} has no entry in the tool contract")
        self._handlers[name] = handler

    def specs(self) -> tuple[ToolSpec, ...]:
        return load_specs()

    def registered(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    # -- confirmation ------------------------------------------------------

    def confirm(self, turn_id: str) -> None:
        """Record that the shopper affirmed the pending action for this turn."""
        self._confirmed.add(turn_id)

    def revoke(self, turn_id: str) -> None:
        self._confirmed.discard(turn_id)

    def is_confirmed(self, turn_id: str) -> bool:
        return turn_id in self._confirmed

    # -- dispatch ----------------------------------------------------------

    async def invoke(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        started = time.monotonic()
        spec = spec_for(call.name)

        def finish(ok: bool, content: dict[str, object], code: str | None) -> ToolResult:
            return ToolResult(
                id=call.id, name=call.name, ok=ok, content=content, error_code=code
            )

        # Gate 1 — known tool.
        if spec is None or call.name not in self._handlers:
            return await self._record(
                ctx, call, finish(False, {"error": "unknown tool"}, "unknown_tool"),
                "validation_failed", started,
            )

        # Gate 2 — argument schema.
        try:
            validate(call.arguments, spec.input_schema)
        except ValidationError as exc:
            # Returned as a structured error so the model can correct itself; the turn
            # is not terminated (FR-052, TC-007).
            return await self._record(
                ctx, call, finish(False, {"error": str(exc), "field": exc.path}, "invalid_arguments"),
                "validation_failed", started,
            )

        # Gate 3 — speculation is read-only, enforced rather than prompted.
        if ctx.speculative and not spec.speculative_safe:
            return await self._record(
                ctx, call,
                finish(False, {"error": "not permitted during speculative execution"},
                       "speculation_forbidden"),
                "authz_denied", started,
            )

        # Gate 4 — confirmation ritual (FR-023, SC-010).
        if spec.requires_confirmation and not self.is_confirmed(ctx.turn_id):
            return await self._record(
                ctx, call,
                finish(False, {"error": "requires explicit shopper confirmation first"},
                       "confirmation_required"),
                "authz_denied", started,
            )

        # Execute.
        try:
            content = await self._handlers[call.name](ctx, call.arguments)
        except Exception as exc:  # noqa: BLE001 — every failure becomes a structured result
            # A tool failure must never crash the turn, and must never be reported to the
            # shopper as success (FR-030).
            return await self._record(
                ctx, call, finish(False, {"error": str(exc)}, _error_code(exc)), "error", started,
            )

        outcome = "deduplicated" if content.get("deduplicated") else "success"
        return await self._record(ctx, call, finish(True, content, None), outcome, started)

    async def _record(
        self,
        ctx: ToolContext,
        call: ToolCall,
        result: ToolResult,
        outcome: str,
        started: float,
    ) -> ToolResult:
        duration_ms = int((time.monotonic() - started) * 1000)
        if self._audit is not None:
            # Arguments are redacted BEFORE the write, never after (FR-064).
            await self._audit.record_audit(
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                tool_name=call.name,
                arguments_redacted=redact_mapping(dict(call.arguments)),
                outcome=outcome,
                duration_ms=duration_ms,
            )
        return result

    # -- latency classification -------------------------------------------

    def is_slow(self, name: str) -> bool:
        """Whether a holding phrase is owed before the shopper hears silence (FR-050)."""
        spec = spec_for(name)
        return spec is not None and spec.latency_class is LatencyClass.SLOW


def _error_code(exc: Exception) -> str:
    name = type(exc).__name__
    return {
        "OrderNotFound": "order_not_found",
        "AmbiguousReference": "ambiguous_reference",
        "ValueError": "not_permitted",
    }.get(name, "backend_error")
