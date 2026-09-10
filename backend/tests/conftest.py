"""Every test runs on the scripted adapters, whatever happens to be in `.env`.

`src.app` loads a local `.env` at import, so that following quickstart.md actually
reaches the real providers instead of silently falling back to fakes. That is right for
running the app and wrong for the suite: with keys present these tests would call
AssemblyAI and Groq on every run — slow, billed, and failing whenever the network does,
which is the opposite of what a test is for. `test_app_e2e.py` says so in its first line:
*the app end to end, with no API keys*.

`factory.build_*()` reads the environment on every call, so clearing the keys here is
enough — nothing needs re-importing, and no test needs to know this happened.
"""

from __future__ import annotations

import pytest

from src.adapters.llm.openai_compat import PROVIDERS

_PROVIDER_KEYS = (
    "ASSEMBLYAI_API_KEY",
    *(f"{name.upper()}_API_KEY" for name in PROVIDERS),
    # Selection is by key, but LLM_PROVIDER/LLM_MODEL would still steer the adapter if a
    # key survived. Clearing them keeps a stray .env line from reaching a test at all.
    "LLM_PROVIDER",
    "LLM_MODEL",
)


@pytest.fixture(autouse=True)
def _scripted_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
