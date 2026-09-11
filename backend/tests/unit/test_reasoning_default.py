"""gpt-oss must not spend its whole token budget reasoning.

`max_tokens` covers the reasoning as well as the answer. At the provider's default effort
the deployed agent produced one turn with no words at all and one that stopped at "I
wasn't able to". Local runs were fine only because `.env` set LLM_REASONING_EFFORT=low,
and `.env` is not what a deployment reads. The default now lives with the model.
"""

from __future__ import annotations

import json

import httpx

from src.adapters.llm.openai_compat import OpenAICompatLanguageModel


def _sent_payload(model: OpenAICompatLanguageModel) -> dict:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        chunk = '{"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'
        body = f"data: {chunk}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=body.encode())

    model._client = httpx.AsyncClient(
        base_url="https://api.groq.com/openai/v1", transport=httpx.MockTransport(handler)
    )
    return captured


async def _one_turn(model: OpenAICompatLanguageModel) -> None:
    async for _ in model.stream(messages=[{"role": "user", "content": "hi"}]):
        pass


async def test_gpt_oss_reasons_at_low_effort_by_default() -> None:
    m = OpenAICompatLanguageModel(api_key="k", provider="groq", system_prompt="")
    sent = _sent_payload(m)
    await _one_turn(m)
    assert sent["model"].startswith("openai/gpt-oss")
    assert sent["reasoning_effort"] == "low"


async def test_an_explicit_effort_still_wins() -> None:
    m = OpenAICompatLanguageModel(api_key="k", provider="groq", system_prompt="",
                                  reasoning_effort="medium")
    sent = _sent_payload(m)
    await _one_turn(m)
    assert sent["reasoning_effort"] == "medium"


async def test_models_that_do_not_reason_are_sent_no_effort() -> None:
    """Sending the parameter to a model without it is a rejected request."""
    m = OpenAICompatLanguageModel(api_key="k", provider="groq", system_prompt="",
                                  model="llama-3.3-70b-versatile")
    sent = _sent_payload(m)
    await _one_turn(m)
    assert "reasoning_effort" not in sent
