"""Claude streaming adapter.

Configuration decisions worth the comment, because two of them look wrong at first
glance and are argued in research.md R3:

**Adaptive thinking stays ON.** Thinking precedes the first visible token, so it is a
direct latency cost, and disabling it is the obvious optimisation. We refuse it. With
thinking disabled, Opus 5 can write a tool call into its *visible text* instead of
emitting a `tool_use` block — the turn succeeds, the tool never runs, nothing raises. In
this domain that is the agent saying "I've started your return" when no return exists:
FR-030 and SC-011, the one zero-tolerance failure. A latency win that can silently
fabricate a completed action is not a trade we are willing to price.

**`effort: "low"` is the supported latency lever instead.** It reduces thinking depth and
yields terser output — which the two-sentence voice persona wants anyway.

The tool loop lives in the orchestrator, not here. This adapter streams and stops; the
actor owns dispatch, cancellation and spans (principle IV — adapters translate, they do
not decide).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from anthropic import AsyncAnthropic

from src.core.ports import LlmChunk, ToolCall
from src.obs import metrics
from src.tools.definitions import anthropic_tools


class ClaudeLanguageModel:
    """Implements the LanguageModel port."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-opus-5",
        effort: str = "low",
        max_tokens: int = 320,
        system_prompt: str = "",
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._system = system_prompt
        self._tools = anthropic_tools()

    def _system_blocks(self) -> list[dict[str, Any]]:
        """System prompt with a cache breakpoint.

        Render order is tools → system → messages, so the stable prefix (persona, policy,
        ten tool schemas) caches and stops being reprocessed every turn. That is a direct
        cut to time-to-first-token. Verify with `usage.cache_read_input_tokens`: a
        persistent zero means a silent invalidator, and is itself the finding.
        """
        return [
            {
                "type": "text",
                "text": self._system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    async def stream(
        self,
        *,
        messages: Sequence[dict[str, object]],
        speculative: bool = False,
    ) -> AsyncIterator[LlmChunk]:
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            # Deliberately low: replies are capped at two sentences by VID-003, and a
            # deliberately short output is one of the few valid reasons to set this low.
            "output_config": {"effort": self._effort},
            "system": self._system_blocks(),
            "tools": self._tools,
            "messages": _to_anthropic(messages),
        }

        try:
            async with self._client.messages.stream(**request) as stream:
                async for event in stream:
                    chunk = _translate(event)
                    if chunk is not None:
                        yield chunk

                final = await stream.get_final_message()

                # Tool calls are surfaced complete. A half-parsed argument must never
                # reach a handler, so partial tool input is never emitted.
                calls = [
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                    for block in final.content
                    if getattr(block, "type", None) == "tool_use"
                ]
                if calls:
                    yield LlmChunk(tool_calls=tuple(calls), stop_reason="tool_use")
                else:
                    yield LlmChunk(stop_reason=final.stop_reason or "end_turn")

                usage = getattr(final, "usage", None)
                if usage is not None:
                    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
                    metrics.inc("llm_cache_read_tokens_total", value=float(cached))

        except Exception as exc:  # noqa: BLE001
            metrics.inc("llm_errors_total", {"class": type(exc).__name__})
            raise


def _translate(event: Any) -> LlmChunk | None:
    """Anthropic stream event → our domain chunk. Nothing vendor-shaped escapes."""
    if getattr(event, "type", None) == "content_block_delta":
        delta = getattr(event, "delta", None)
        if getattr(delta, "type", None) == "text_delta":
            return LlmChunk(text=delta.text)
    return None


def _to_anthropic(messages: Sequence[dict[str, object]]) -> list[dict[str, Any]]:
    """Map our transcript onto the wire format.

    Tool results are collapsed into a single user message per hop. Splitting parallel
    results across messages silently trains the model out of parallel calling, which
    costs latency on compound turns ("where is it and can I still change the address?").
    """
    out: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush_results() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_use_id", "")),
                    "content": str(content),
                }
            )
            continue

        flush_results()
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content})

    flush_results()
    return out
