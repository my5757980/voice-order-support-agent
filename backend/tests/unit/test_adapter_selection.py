"""Which adapters the environment selects — and how loudly.

`DEMO_MODE` was documented in `app.py` from the first commit and never implemented, so
`.env.example` shipped `DEMO_MODE=true` harmlessly for weeks. The moment the flag started
working, that line turned the whole product into a scripted mock — real-looking, healthy
on `/api/health`, and proving nothing. These tests exist so a configuration that fakes
everything can never again be indistinguishable from one that works.
"""

from __future__ import annotations

import pytest

from src.adapters import factory


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both credentials present — the configuration a judge would open."""
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.delenv("TTS_PROVIDER", raising=False)


def test_keys_present_means_every_slot_is_real(keys: None) -> None:
    assert factory.modes() == {"stt": "assemblyai", "llm": "groq", "tts": "groq-orpheus"}


def test_demo_mode_fakes_every_slot(keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    assert factory.demo_mode() is True
    assert factory.modes() == {"stt": "text-only", "llm": "scripted", "tts": "tone"}


def test_demo_mode_is_reported_not_hidden(keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/health` is how anyone checks what is actually running. A demo mode that
    reported the real adapters would be worse than no reporting at all."""
    monkeypatch.setenv("DEMO_MODE", "1")
    assert "groq" not in factory.modes().values()


@pytest.mark.parametrize("value", ["false", "0", "no", "off", ""])
def test_demo_mode_stays_off_for_anything_but_a_yes(
    keys: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DEMO_MODE", value)
    assert factory.demo_mode() is False
    assert factory.modes()["llm"] == "groq"


def test_tone_override_silences_only_the_synthesizer(
    keys: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The combination worth rehearsing a demo on: the real transcriber and the real
    model still run, so everything that can go wrong still can — it just costs no
    Orpheus quota, which is capped at 100 requests a day and billed per clause."""
    monkeypatch.setenv("TTS_PROVIDER", "tone")
    assert factory.modes() == {"stt": "assemblyai", "llm": "groq", "tts": "tone"}
    _, mode = factory.build_tts()
    assert mode == "tone"


def test_default_tts_provider_is_the_real_one(keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_PROVIDER", "groq")
    assert factory.modes()["tts"] == "groq-orpheus"
