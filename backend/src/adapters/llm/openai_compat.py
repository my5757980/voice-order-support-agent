"""OpenAI-compatible language model adapter.

One adapter, four providers. Groq, Gemini, OpenAI and AssemblyAI's LLM Gateway all speak
the same `/chat/completions` shape, so switching between them is a base URL and a model
name in `.env` — no code change, no core change. That is constitution principle IV doing
the work it was put there to do: when the Claude plan turned out to need a paid key, the
swap cost one adapter file.

Measured on Groq `openai/gpt-oss-120b` (2026-09-09):
    tool calling   works
    streaming      works
    TTFT           ~1500 ms measured from Pakistan, of which ~930 ms is network RTT to
                   US-hosted inference. The model's own contribution is ~600 ms.

That geography caveat is the whole reason T077b exists: this must be re-measured from the
deployed instance, not from a developer laptop half a world from the inference. A budget
that fails locally and passes deployed is a budget we have not actually tested.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from src.core.ports import LlmChunk, ModelUnavailable, ToolCall
from src.obs import metrics
from src.tools.definitions import openai_tools

# Known providers. `base_url` is all that distinguishes them.
_RATE_LIMIT_ATTEMPTS = 2
"""One retry. A second refusal means the window is genuinely exhausted — keep waiting
and the shopper is sitting in silence for half a minute."""

_MAX_RATE_LIMIT_WAIT_S = 15.0
"""Longer than this is a daily limit, not a per-minute one, and no pause fixes it."""

_TRY_AGAIN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.IGNORECASE)


def _retry_after(headers: Any, body: str) -> float | None:
    """How long the provider asked us to wait, from whichever place it said so.

    The standard `retry-after` header when present; otherwise Groq's own sentence in the
    body, "Please try again in 11.235s" — which is the one that actually arrived.
    """
    raw = headers.get("retry-after") if headers is not None else None
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    match = _TRY_AGAIN.search(body or "")
    if match:
        minutes = float(match.group(1) or 0)
        return minutes * 60 + float(match.group(2))
    return None


def _classify(status: int) -> str:
    """Provider status to a cause the shopper can be told about — coarse on purpose."""
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "unauthorized"
    return "unavailable"


PROVIDERS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openai": "https://api.openai.com/v1",
    "assemblyai": "https://llm-gateway.assemblyai.com/v1",
}

DEFAULT_MODEL: dict[str, str] = {
    # Chosen by measurement, not reputation: of the four Groq models tested it was the
    # only fast one that actually emitted tool_calls, and every tool in this project
    # depends on that.
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4.1-mini",
    "assemblyai": "qwen3.5-4b-32k-fast",
}


class OpenAICompatLanguageModel:
    """Implements the LanguageModel port against any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        provider: str = "groq",
        model: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 320,
        temperature: float = 0.3,
        system_prompt: str = "",
        timeout_s: float = 10.0,
        reasoning_effort: str | None = None,
    ) -> None:
        self._base = (base_url or PROVIDERS.get(provider) or PROVIDERS["groq"]).rstrip("/")
        self._model = model or DEFAULT_MODEL.get(provider, "openai/gpt-oss-120b")
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._system = system_prompt
        # gpt-oss is a reasoning model, and max_tokens covers its reasoning as well as its
        # answer. At the provider's default effort the reasoning spent the whole budget:
        # on the deployed app one turn produced no words at all and the next stopped at
        # "I wasn't able to". Locally `.env` said LLM_REASONING_EFFORT=low, so it never
        # happened there — and `.env` is not what a deployment reads. The default belongs
        # here, next to the model that needs it.
        if reasoning_effort is None and "gpt-oss" in self._model:
            reasoning_effort = "low"
        self._reasoning_effort = reasoning_effort
        self.provider = provider

        # One client for the process. Connection reuse matters here: a cold TLS handshake
        # costs more than the model's own time-to-first-token on a good day.
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
                # Cloudflare in front of some providers rejects default client UAs.
                "user-agent": "voice-order-support-agent/0.1",
            },
        )
        self._tools = openai_tools()

    async def stream(
        self,
        *,
        messages: Sequence[dict[str, object]],
        speculative: bool = False,
    ) -> AsyncIterator[LlmChunk]:
        """Stream a reply, waiting out one rate-limit refusal if the wait is short.

        Groq's free tier allows 8,000 tokens a minute over a rolling window, and one
        turn with a tool call spends about 4,000 of them. A refusal arrives with the
        provider's own estimate of when the window frees up — typically ten to fifteen
        seconds. Waiting that long once is a pause; not waiting is a failed turn and a
        shopper asked to repeat themselves.

        Retried only before anything has been yielded. After the first chunk the reply
        is already being spoken, and a second attempt would speak it twice. Speculative
        turns are never retried: a speculation that has to wait has already lost the
        race it exists to win.
        """
        for attempt in range(_RATE_LIMIT_ATTEMPTS):
            started = False
            try:
                async for chunk in self._stream_once(messages=messages):
                    started = True
                    yield chunk
                return
            except ModelUnavailable as exc:
                wait = exc.retry_after
                retryable = (
                    exc.kind == "rate_limited"
                    and not started
                    and not speculative
                    and attempt + 1 < _RATE_LIMIT_ATTEMPTS
                    and wait is not None
                    and wait <= _MAX_RATE_LIMIT_WAIT_S
                )
                if not retryable:
                    raise
                metrics.inc("llm_retries_total", {"class": "rate_limited"})
                await asyncio.sleep(wait)

    async def _stream_once(
        self, *, messages: Sequence[dict[str, object]]
    ) -> AsyncIterator[LlmChunk]:
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": True,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": self._to_openai(messages),
            "tools": self._tools,
            "tool_choice": "auto",
        }
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort

        # Tool call fragments arrive spread across deltas and must be reassembled before
        # anything is emitted. A half-parsed argument must never reach a handler.
        partial: dict[int, dict[str, Any]] = {}
        stop_reason = "end_turn"

        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode()[:300]
                    metrics.inc("llm_errors_total", {"class": f"http_{response.status_code}"})
                    raise ModelUnavailable(
                        _classify(response.status_code),
                        body,
                        retry_after=_retry_after(response.headers, body),
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if not body or body == "[DONE]":
                        continue

                    choice = (json.loads(body).get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}

                    if text := delta.get("content"):
                        yield LlmChunk(text=text)

                    for call in delta.get("tool_calls") or []:
                        idx = call.get("index", 0)
                        slot = partial.setdefault(idx, {"id": "", "name": "", "args": ""})
                        if call.get("id"):
                            slot["id"] = call["id"]
                        fn = call.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]

                    if choice.get("finish_reason"):
                        stop_reason = choice["finish_reason"]

        except httpx.TimeoutException as exc:
            metrics.inc("llm_errors_total", {"class": "timeout"})
            raise ModelUnavailable("timeout") from exc
        except httpx.HTTPError as exc:
            metrics.inc("llm_errors_total", {"class": "transport"})
            raise ModelUnavailable("unavailable") from exc

        if partial:
            yield LlmChunk(tool_calls=tuple(_finish_calls(partial)), stop_reason="tool_use")
        else:
            yield LlmChunk(stop_reason=stop_reason)

    def _to_openai(self, messages: Sequence[dict[str, object]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self._system:
            out.append({"role": "system", "content": self._system})
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "tool":
                # Tool results are serialized as JSON so the model sees structure rather
                # than a Python repr.
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(message.get("tool_use_id", "call")),
                        "content": json.dumps(content, default=str),
                    }
                )
            elif role == "assistant" and message.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    # Arguments go back as a JSON *string*, not an object.
                                    "arguments": json.dumps(tc.get("arguments") or {}),
                                },
                            }
                            for tc in message["tool_calls"]  # type: ignore[index]
                        ],
                    }
                )
            elif role in ("user", "assistant") and isinstance(content, str) and content.strip():
                out.append({"role": role, "content": content})
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


def _finish_calls(partial: dict[int, dict[str, Any]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for idx in sorted(partial):
        slot = partial[idx]
        if not slot["name"]:
            continue
        try:
            args = json.loads(slot["args"]) if slot["args"] else {}
        except json.JSONDecodeError:
            # Malformed arguments are dropped rather than guessed at. The registry would
            # reject them anyway; dropping here keeps the failure legible.
            metrics.inc("llm_errors_total", {"class": "bad_tool_json"})
            continue
        calls.append(ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"], arguments=args))
    return calls


def configured_provider() -> tuple[str, str] | None:
    """Which OpenAI-compatible provider `.env` selects, if any.

    Checked in preference order so a project with several keys present uses the one the
    operator named, rather than whichever happens to be first.
    """
    named = os.environ.get("LLM_PROVIDER", "").strip().lower()
    candidates = [named] if named in PROVIDERS else list(PROVIDERS)
    for provider in candidates:
        key = os.environ.get(f"{provider.upper()}_API_KEY", "").strip()
        if key:
            return provider, key
    return None
