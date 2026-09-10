"""Adapter selection.

The one place that decides real-vs-fake. Everything above this file talks to ports and
cannot tell the difference — which is the whole point of principle IV, and why the
project was buildable and testable before any API key existed.

Selection is per adapter, not global: an AssemblyAI key alone gets you real transcription
with scripted replies, which is a genuinely useful intermediate state while keys arrive
one at a time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PERSONA = Path(__file__).with_name("llm") / "persona.md"


def _key(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def load_persona() -> str:
    return PERSONA.read_text(encoding="utf-8") if PERSONA.is_file() else ""


def build_stt(**overrides: Any) -> tuple[Any | None, str]:
    """Returns (recognizer, mode). None means no transcription is available and the
    session runs on the text fallback, which is a documented capability (FR-060) rather
    than a degraded mode."""
    api_key = _key("ASSEMBLYAI_API_KEY")
    if api_key is None:
        return None, "text-only"

    from .stt.assemblyai import AssemblyAISpeechRecognizer

    return (
        AssemblyAISpeechRecognizer(
            api_key=api_key,
            sample_rate=int(os.environ.get("STT_SAMPLE_RATE", "16000")),
            speech_model=os.environ.get("STT_SPEECH_MODEL", "universal-3-5-pro"),
            end_of_turn_confidence_threshold=float(
                os.environ.get("STT_END_OF_TURN_CONFIDENCE", "0.4")
            ),
            min_turn_silence_ms=int(os.environ.get("STT_MIN_TURN_SILENCE_MS", "160")),
            max_turn_silence_ms=int(os.environ.get("STT_MAX_TURN_SILENCE_MS", "400")),
            **overrides,
        ),
        "assemblyai",
    )


def build_llm() -> tuple[Any, str]:
    """Any OpenAI-compatible provider — Groq, Gemini, OpenAI — behind one adapter.

    Which one is a `.env` line: `LLM_PROVIDER` plus the matching `<PROVIDER>_API_KEY`.
    """
    from .llm.openai_compat import OpenAICompatLanguageModel, configured_provider

    selected = configured_provider()
    if selected is None:
        from .llm.fake import FakeLanguageModel

        return FakeLanguageModel(), "scripted"

    provider, key = selected
    return (
        OpenAICompatLanguageModel(
            api_key=key,
            provider=provider,
            model=os.environ.get("LLM_MODEL") or None,
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "320")),
            system_prompt=load_persona(),
            reasoning_effort=os.environ.get("LLM_REASONING_EFFORT") or None,
        ),
        provider,
    )


def build_tts() -> tuple[Any, str]:
    """Groq Orpheus, or the tone generator when no key is present.

    The tone generator is not a broken state: it makes barge-in demonstrable before any
    key exists, which is what let the interruption path be built and tested first.
    """
    groq_key = _key("GROQ_API_KEY")
    if groq_key is None:
        from .tts.fake import FakeSpeechSynthesizer

        return FakeSpeechSynthesizer(), "tone"

    from .tts.groq_orpheus import GroqSpeechSynthesizer

    return (
        GroqSpeechSynthesizer(
            api_key=groq_key,
            voice=os.environ.get("TTS_VOICE", "hannah"),
        ),
        "groq-orpheus",
    )


def modes() -> dict[str, str]:
    """What each slot will use, without constructing anything. Surfaced on /api/health so
    a demo never has to guess whether it is running on real providers."""
    from .llm.openai_compat import configured_provider

    selected = configured_provider()
    return {
        "stt": "assemblyai" if _key("ASSEMBLYAI_API_KEY") else "text-only",
        "llm": selected[0] if selected else "scripted",
        "tts": "groq-orpheus" if _key("GROQ_API_KEY") else "tone",
    }
