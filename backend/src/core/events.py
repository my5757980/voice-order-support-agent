"""Typed pipeline events.

Constitution Architectural Principle 2: stages communicate over bounded queues carrying
typed events. Adapters emit these; only the Orchestrator acts on them.

Every event carries `session_id` and `turn_id` so principle VI (every turn traceable)
holds structurally rather than by remembering to log the ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Speaker(str, Enum):
    SHOPPER = "shopper"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class Correlated:
    """Base for everything that flows through the pipeline."""

    session_id: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class UserPartial(Correlated):
    """An advisory, uncommitted transcript.

    MUST NOT reach conversation memory (principle II). Drives the live transcript pane,
    barge-in evaluation, and speculative dispatch only.
    """

    text: str
    turn_order: int
    end_of_turn_confidence: float


@dataclass(frozen=True, slots=True)
class UserTurnCommitted(Correlated):
    """A committed turn — `end_of_turn == true` from the STT provider.

    `text` is the unformatted transcript and is what the critical path consumes.
    `text_formatted` is for display and logs and MUST NOT block dispatch.
    """

    text: str
    turn_order: int
    end_of_turn_confidence: float
    text_formatted: str | None = None


@dataclass(frozen=True, slots=True)
class AgentTokens(Correlated):
    """A fragment of generated text on its way to synthesis."""

    text: str
    is_final: bool = False


@dataclass(frozen=True, slots=True)
class AgentAudio(Correlated):
    """PCM16 mono 16 kHz frame of synthesized speech."""

    pcm: bytes
    char_offset: int
    """Character offset in the agent's text that this frame starts at, from TTS
    alignment. This is what makes `heard_prefix_len` exact on interruption rather
    than an estimate."""


@dataclass(frozen=True, slots=True)
class Interrupted(Correlated):
    """A confirmed barge-in. Emitted after the filter decided, not before."""

    heard_prefix_len: int
    barge_in_latency_s: float


@dataclass(frozen=True, slots=True)
class BackchannelSuppressed(Correlated):
    """A partial that looked like an interruption but was an acknowledgement.

    Emitted so an over-eager filter is visible in metrics rather than silently
    swallowing real interruptions (SC-007).
    """

    text: str


@dataclass(frozen=True, slots=True)
class ToolRequested(Correlated):
    name: str
    arguments: dict[str, object] = field(default_factory=dict)
    speculative: bool = False


@dataclass(frozen=True, slots=True)
class ToolCompleted(Correlated):
    name: str
    ok: bool
    result: dict[str, object] = field(default_factory=dict)
    error_code: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class SessionStatus(Correlated):
    state: str
    message: str | None = None
