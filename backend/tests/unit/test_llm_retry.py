"""Waiting out a rate-limit refusal — once, briefly, and never mid-reply.

Groq's free tier allows 8,000 tokens a minute, and one turn with a tool call spends
about 4,000. The first 429 in a rehearsal killed turn five outright. The refusal carries
the provider's own estimate of when the window frees up — "Please try again in 11.235s"
— and waiting that long once turns a failed turn into a pause.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.adapters.llm import openai_compat
from src.adapters.llm.openai_compat import OpenAICompatLanguageModel, _retry_after
from src.core.ports import ModelUnavailable

REFUSAL = (
    '{"error":{"message":"Rate limit reached for model on tokens per minute (TPM): '
    'Limit 8000, Used 7314, Requested 2184. Please try again in 0.01s."}}'
)


def _sse(text: str) -> bytes:
    chunk = {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _model(responses: list[httpx.Response]) -> tuple[OpenAICompatLanguageModel, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return responses[min(len(calls), len(responses)) - 1]

    m = OpenAICompatLanguageModel(api_key="test", provider="groq", system_prompt="")
    m._client = httpx.AsyncClient(
        base_url="https://api.groq.com/openai/v1", transport=httpx.MockTransport(handler)
    )
    return m, calls


async def _collect(m: OpenAICompatLanguageModel, **kw: Any) -> str:
    out = []
    async for chunk in m.stream(messages=[{"role": "user", "content": "hi"}], **kw):
        if chunk.text:
            out.append(chunk.text)
    return "".join(out)


async def test_a_short_refusal_is_waited_out_once() -> None:
    m, calls = _model([httpx.Response(429, text=REFUSAL), httpx.Response(200, content=_sse("ok"))])
    assert await _collect(m) == "ok"
    assert len(calls) == 2


async def test_a_second_refusal_is_final() -> None:
    """One retry. Waiting indefinitely is its own failure — a shopper in silence."""
    m, calls = _model([httpx.Response(429, text=REFUSAL)] * 3)
    with pytest.raises(ModelUnavailable) as exc:
        await _collect(m)
    assert exc.value.kind == "rate_limited"
    assert len(calls) == 2


async def test_a_long_wait_is_not_waited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minutes, not seconds, means a daily limit; no pause fixes it, so fail fast."""
    daily = REFUSAL.replace("0.01s", "20m3.5s")
    m, calls = _model([httpx.Response(429, text=daily), httpx.Response(200, content=_sse("ok"))])
    with pytest.raises(ModelUnavailable):
        await _collect(m)
    assert len(calls) == 1


async def test_speculation_never_waits() -> None:
    """A speculation that has to wait has already lost the race it exists to win."""
    m, calls = _model([httpx.Response(429, text=REFUSAL), httpx.Response(200, content=_sse("ok"))])
    with pytest.raises(ModelUnavailable):
        await _collect(m, speculative=True)
    assert len(calls) == 1


async def test_other_failures_are_not_retried() -> None:
    m, calls = _model([httpx.Response(503, text="down"), httpx.Response(200, content=_sse("ok"))])
    with pytest.raises(ModelUnavailable) as exc:
        await _collect(m)
    assert exc.value.kind == "unavailable"
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ({}, "Please try again in 11.235s. Need more tokens?", 11.235),
        ({"retry-after": "7"}, "", 7.0),
        ({}, "try again in 2m3.5s", 123.5),
        ({}, "no hint at all", None),
    ],
)
def test_the_providers_own_wait_is_read(headers: dict, body: str, expected: float | None) -> None:
    assert _retry_after(headers, body) == expected


def test_the_retry_budget_is_one() -> None:
    assert openai_compat._RATE_LIMIT_ATTEMPTS == 2
