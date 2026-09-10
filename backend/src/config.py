"""Typed, validated configuration.

Constitution Error Handling rule 8: configuration and schema validation errors crash at
startup. They are never papered over with runtime defaults — a missing API key must fail
loudly at boot, not produce a session that dies mid-conversation.

Every tuning value the constitution requires to be configuration lives here: turn
detection thresholds, barge-in parameters, latency budgets, model selection. None of
them may appear as a literal in business logic.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # -- Vendor credentials. Never sent to the browser. -------------------
    assemblyai_api_key: str = Field(min_length=1)
    anthropic_api_key: str = Field(min_length=1)
    elevenlabs_api_key: str = Field(min_length=1)
    elevenlabs_voice_id: str = Field(min_length=1)

    # -- Speech to text ---------------------------------------------------
    stt_speech_model: str = "universal-3-5-pro"
    stt_sample_rate: int = 16_000
    stt_frame_ms: int = 50
    stt_min_turn_silence_ms: int = 160
    stt_max_turn_silence_ms: int = 400
    stt_end_of_turn_confidence: float = Field(default=0.4, ge=0.0, le=1.0)
    stt_format_turns: bool = True

    # -- Language model ---------------------------------------------------
    llm_model: str = "claude-opus-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    llm_max_tokens: int = Field(default=320, ge=64, le=4096)
    llm_timeout_s: float = 3.0

    # -- Text to speech ---------------------------------------------------
    tts_model: str = "eleven_flash_v2_5"
    tts_optimize_streaming_latency: int = Field(default=4, ge=0, le=4)
    tts_timeout_s: float = 3.0

    # -- Barge-in ---------------------------------------------------------
    barge_in_min_words: int = Field(default=2, ge=1)
    barge_in_grace_seconds: float = Field(default=1.0, ge=0.0)

    # -- Speculative dispatch ---------------------------------------------
    speculative_enabled: bool = True
    speculative_threshold: float = Field(default=0.7, ge=0.0, le=1.0)

    # -- Conversation -----------------------------------------------------
    working_memory_turns: int = Field(default=12, ge=1)
    holding_phrase_delay_ms: int = Field(default=400, ge=0)
    order_lookback_days: int = Field(default=90, ge=1)

    # -- Runtime ----------------------------------------------------------
    environment: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"
    log_transcripts: bool = False
    """DEBUG-level transcript logging. MUST be false in prod — asserted below."""

    demo_mode: bool = True
    database_path: str = "./data/orders.db"

    @field_validator("stt_frame_ms")
    @classmethod
    def _frame_within_provider_range(cls, v: int) -> int:
        # AssemblyAI accepts 50-1000 ms. We pin to the bottom of the range because
        # every millisecond of framing is latency the shopper hears.
        if not 50 <= v <= 1000:
            raise ValueError("stt_frame_ms must be between 50 and 1000 (provider limit)")
        return v

    @field_validator("stt_max_turn_silence_ms")
    @classmethod
    def _max_silence_above_min(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        minimum = info.data.get("stt_min_turn_silence_ms")
        if minimum is not None and v <= minimum:
            raise ValueError("stt_max_turn_silence_ms must exceed stt_min_turn_silence_ms")
        return v

    @field_validator("log_transcripts")
    @classmethod
    def _no_transcript_logging_in_prod(cls, v: bool, info) -> bool:  # type: ignore[no-untyped-def]
        # Constitution Security & Privacy: DEBUG transcript logging MUST be off in
        # production and MUST be impossible to enable at runtime. Enforced here, at
        # boot, rather than trusted to deployment discipline.
        if v and info.data.get("environment") == "prod":
            raise ValueError("log_transcripts must be false when environment=prod")
        return v

    @property
    def frame_bytes(self) -> int:
        """Bytes per audio frame: PCM16 mono at the configured rate and duration."""
        samples = self.stt_sample_rate * self.stt_frame_ms // 1000
        return samples * 2


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate. Raises at import/boot time if anything is missing."""
    return Settings()  # type: ignore[call-arg]
