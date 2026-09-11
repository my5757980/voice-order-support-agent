"""The five ports.

Constitution principle IV: the core depends only on these. Vendor SDK types, exceptions,
and payload shapes MUST NOT cross an adapter boundary into this package — enforced by
`tests/unit/test_core_purity.py`, not by good intentions.

AssemblyAI is the one pinned vendor; even so, nothing here names it. LLM and TTS
providers are deliberately swappable by configuration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from .events import AgentAudio, UserPartial, UserTurnCommitted


# -- Speech recognition ---------------------------------------------------


@runtime_checkable
class SpeechRecognizer(Protocol):
    """Streaming speech-to-text.

    Implementations own reconnection and translate provider messages into our events.
    Audio in is PCM16 mono 16 kHz in 50 ms frames; anything else is a caller bug.
    """

    async def connect(self, session_id: str) -> None: ...

    async def send_audio(self, frame: bytes) -> None: ...

    def events(self) -> AsyncIterator[UserPartial | UserTurnCommitted]:
        """Yields advisory partials and committed turns, in provider order."""
        ...

    async def keep_alive(self) -> None:
        """Called during silence so the session is not dropped for inactivity."""
        ...

    async def close(self) -> None: ...


# -- Language model -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ToolResult:
    id: str
    name: str
    ok: bool
    content: dict[str, object]
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class LlmChunk:
    """One increment of a streaming response.

    Exactly one of `text` or `tool_calls` is populated. `tool_calls` arrives complete —
    partial tool arguments are never surfaced, because a half-parsed argument must never
    reach a handler.
    """

    text: str | None = None
    tool_calls: Sequence[ToolCall] = field(default_factory=tuple)
    stop_reason: str | None = None


class ModelUnavailable(RuntimeError):
    """The model could not produce a reply for this turn.

    Raised by a `LanguageModel` adapter instead of a bare `RuntimeError` carrying a
    vendor's response body. `kind` is deliberately coarse — the distinction the shopper
    needs is "say it again in a moment" versus "this is not working right now"; the
    status code and the provider's message belong in metrics and logs.
    """

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"model {kind}: {detail}" if detail else f"model {kind}")
        self.kind = kind


@runtime_checkable
class LanguageModel(Protocol):
    """Streaming text generation with tool use.

    Implementations MUST abort cleanly on cancellation — in a barge-in world most
    generated tokens are discarded, so cancellation is the common path, not the
    exceptional one (constitution principle V).
    """

    def stream(
        self,
        *,
        messages: Sequence[dict[str, object]],
        speculative: bool = False,
    ) -> AsyncIterator[LlmChunk]: ...


# -- Speech synthesis -----------------------------------------------------


@runtime_checkable
class SpeechSynthesizer(Protocol):
    """Streaming text-to-speech with a server-side interrupt primitive.

    `clear_buffer` stops the provider producing more audio. It does NOT silence audio
    already buffered in the browser — that needs the client-side flush, and the two
    MUST be fired concurrently rather than in sequence.
    """

    async def connect(self, session_id: str) -> None:
        """Opened at session start so TLS setup never lands on the critical path."""
        ...

    async def submit(self, text: str, turn_id: str) -> None:
        """Submit a speakable clause. Called per clause, not per sentence."""
        ...

    async def flush(self) -> None: ...

    async def clear_buffer(self) -> None:
        """Server-side interrupt."""
        ...

    def audio(self) -> AsyncIterator[AgentAudio]:
        """Yields PCM frames carrying the character offset they begin at."""
        ...

    async def close(self) -> None: ...


# -- Tools ----------------------------------------------------------------


class LatencyClass(str, Enum):
    FAST = "fast"
    """< 300 ms. May block the reply."""
    SLOW = "slow"
    """Must be covered by a holding phrase."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, object]
    latency_class: LatencyClass
    state_changing: bool
    speculative_safe: bool
    requires_confirmation: bool = False


@runtime_checkable
class ToolRegistry(Protocol):
    """Validation, authorization, and dispatch.

    Authorization is applied server-side from the authenticated session and never from
    tool arguments: if a caller proposes a customer identifier it is ignored, not
    honoured.
    """

    def specs(self) -> Sequence[ToolSpec]: ...

    async def invoke(
        self,
        call: ToolCall,
        *,
        session_id: str,
        turn_id: str,
        speculative: bool = False,
    ) -> ToolResult:
        """Validate, authorize, then execute.

        A state-changing tool invoked with `speculative=True` MUST be refused before
        execution — speculation is read-only by construction.
        """
        ...


# -- Memory ---------------------------------------------------------------


@runtime_checkable
class ConversationMemory(Protocol):
    """Working, summary, and durable layers.

    Never an authorization source and never an order-state source: both are re-read
    from the store before any action that depends on them.
    """

    def append_committed(self, turn: UserTurnCommitted) -> None: ...

    def append_agent_turn(self, turn_id: str, text: str) -> None: ...

    def truncate_agent_turn(self, turn_id: str, heard_prefix_len: int) -> None:
        """On interruption, reduce the agent turn to what the shopper actually heard.

        Never to what was generated (constitution principle III).
        """
        ...

    def as_messages(self) -> Sequence[dict[str, object]]: ...
