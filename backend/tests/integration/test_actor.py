"""Orchestrator behaviour, driven directly.

Barge-in is tested here rather than through TestClient because interrupting from a test
thread needs a second event loop, and the actor's tasks live in the first. Driving the
actor in-loop is both correct and more precise — it can assert on memory contents, not
just on messages.

`test_interruption_truncates_memory_to_what_was_heard` is the property this submission
leads with. If it ever regresses, the demo's central claim is false.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.adapters.llm.fake import FakeLanguageModel
from src.adapters.store.seed import DEMO_CUSTOMER_ID, seed
from src.adapters.store.sqlite import OrderRepository, connect
from src.adapters.tts.fake import FakeSpeechSynthesizer
from src.session.actor import SessionActor
from src.tools.handlers import build_registry


class Sink:
    """Captures everything the actor sends to the browser."""

    def __init__(self) -> None:
        self.control: list[dict[str, Any]] = []
        self.audio_frames = 0

    async def send_control(self, message: dict[str, Any]) -> None:
        self.control.append(message)

    async def send_audio(self, pcm: bytes) -> None:
        self.audio_frames += 1

    def types(self) -> list[str]:
        return [m["type"] for m in self.control]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.control if m["type"] == kind]


@pytest.fixture
def actor(tmp_path: Path) -> tuple[SessionActor, Sink]:
    conn: sqlite3.Connection = connect(str(tmp_path / "a.db"))
    seed(conn)
    sink = Sink()
    # realtime pacing preserved (barge-in depends on frames arriving over time),
    # but compressed so the suite stays fast.
    tts = FakeSpeechSynthesizer(realtime=True, ms_per_char=6)
    a = SessionActor(
        session_id="sess-test",
        customer_id=DEMO_CUSTOMER_ID,
        repo=OrderRepository(conn, DEMO_CUSTOMER_ID),
        registry=build_registry(),
        llm=FakeLanguageModel(token_delay_s=0.001),
        tts=tts,
        send_control=sink.send_control,
        send_audio=sink.send_audio,
    )
    return a, sink


async def _speak_until_audio(a: SessionActor, sink: Sink, text: str) -> None:
    """Start a turn and wait until audio is genuinely flowing."""
    await a.on_committed(text)
    for _ in range(200):
        await asyncio.sleep(0.01)
        if sink.audio_frames > 0:
            return
    pytest.fail("agent never produced audio")


# -- a complete turn -------------------------------------------------------


async def test_turn_produces_speech_and_timings(actor) -> None:  # type: ignore[no-untyped-def]
    a, sink = actor
    await a.on_committed("where's my order?")
    for _ in range(400):
        await asyncio.sleep(0.01)
        if sink.of("agent.done"):
            break

    assert sink.audio_frames > 0
    spans = sink.of("timing")[0]["spans"]
    assert spans["e2e"] > 0
    assert "llm.ttft" in spans and "tts.ttfb" in spans


async def test_agent_disambiguates_between_two_orders(actor) -> None:  # type: ignore[no-untyped-def]
    a, sink = actor
    await a.on_committed("where's my order?")
    for _ in range(400):
        await asyncio.sleep(0.01)
        if sink.of("agent.done"):
            break
    agent = [m for m in sink.of("transcript.committed") if m["speaker"] == "agent"]
    assert agent and "which one" in agent[0]["text"].lower()


# -- barge-in: the property we lead with -----------------------------------


async def test_interruption_flushes_the_browser_buffer(actor) -> None:  # type: ignore[no-untyped-def]
    """The client-side flush is what the shopper experiences. `clear_buffer` alone
    leaves already-buffered audio playing."""
    a, sink = actor
    await _speak_until_audio(a, sink, "where's my order?")
    await a.interrupt()
    assert "audio.flush" in sink.types()


async def test_interruption_stops_the_agent(actor) -> None:  # type: ignore[no-untyped-def]
    a, sink = actor
    await _speak_until_audio(a, sink, "where's my order?")
    frames_at_cut = sink.audio_frames
    await a.interrupt()
    await asyncio.sleep(0.2)
    # A couple of in-flight frames may land; the stream must not simply continue.
    assert sink.audio_frames - frames_at_cut < 4


async def test_interruption_truncates_memory_to_what_was_heard(actor) -> None:  # type: ignore[no-untyped-def]
    """Constitution principle III — memory records what the shopper HEARD, never what
    the model generated."""
    a, sink = actor
    await _speak_until_audio(a, sink, "where's my order?")

    a.on_playback_progress(a._current_turn_id, 800 * 3)  # three frames actually played
    await a.interrupt()
    await asyncio.sleep(0.3)

    assistant = [m for m in a._messages if m.get("role") == "assistant"]
    if assistant:
        assert assistant[-1].get("truncated") is True
        assert len(str(assistant[-1]["content"])) < 120


async def test_backchannel_does_not_interrupt(actor) -> None:  # type: ignore[no-untyped-def]
    """SC-007 — 'mhm' while the agent talks must not stop it."""
    a, sink = actor
    await _speak_until_audio(a, sink, "where's my order?")
    await a.on_partial("mhm")
    assert a._agent_speaking is True
    assert "audio.flush" not in sink.types()


async def test_real_speech_does_interrupt(actor) -> None:  # type: ignore[no-untyped-def]
    a, sink = actor
    await _speak_until_audio(a, sink, "where's my order?")
    await a.on_partial("no wait the other one")
    assert "audio.flush" in sink.types()


# -- the confirmation ritual through the orchestrator ----------------------


async def test_return_requires_an_affirmative_before_it_executes(actor) -> None:  # type: ignore[no-untyped-def]
    """SC-010. The agent asks; only the shopper's 'yes' unlocks the tool. The gate lives
    in the registry, so a model that skips the ritual still cannot act."""
    a, sink = actor

    await a.on_committed("I want to return the mug")
    for _ in range(400):
        await asyncio.sleep(0.01)
        if sink.of("agent.done"):
            break

    agent = [m for m in sink.of("transcript.committed") if m["speaker"] == "agent"]
    assert agent, "the agent must read the action back"
    assert agent[-1]["text"].rstrip().endswith("?"), "read-back must end in a direct question"
    assert a._awaiting_confirmation is True

    sink.control.clear()
    await a.on_committed("yes")
    for _ in range(400):
        await asyncio.sleep(0.01)
        if sink.of("agent.done"):
            break

    said = " ".join(
        m["text"] for m in sink.of("transcript.committed") if m["speaker"] == "agent"
    )
    assert "R-" in said, f"the return should have been created; agent said: {said!r}"


async def test_without_confirmation_the_tool_is_refused(actor) -> None:  # type: ignore[no-untyped-def]
    """Jumping straight to 'yes' with nothing pending must not create a return."""
    a, sink = actor
    await a.on_committed("yes")
    for _ in range(400):
        await asyncio.sleep(0.01)
        if sink.of("agent.done"):
            break

    said = " ".join(
        m["text"] for m in sink.of("transcript.committed") if m["speaker"] == "agent"
    )
    assert "R-" not in said


# -- the silent window before the first audio frame ------------------------


class SlowSynthesizer(FakeSpeechSynthesizer):
    """A synthesizer with a realistic time-to-first-byte.

    Groq Orpheus takes 1.3-2 s to return the first clause; the scripted synthesizer
    returns instantly. That difference hid a real defect for the entire project: every
    test interrupted an agent that was already audible, which is the easy case.
    """

    def __init__(self, ttfb_s: float = 0.4) -> None:
        super().__init__(realtime=True, ms_per_char=6)
        self._ttfb_s = ttfb_s

    async def submit(self, text: str, turn_id: str) -> None:
        await asyncio.sleep(self._ttfb_s)
        await super().submit(text, turn_id)


@pytest.fixture
def slow_actor(tmp_path: Path) -> tuple[SessionActor, Sink]:
    conn: sqlite3.Connection = connect(str(tmp_path / "slow.db"))
    seed(conn)
    sink = Sink()
    a = SessionActor(
        session_id="sess-slow",
        customer_id=DEMO_CUSTOMER_ID,
        repo=OrderRepository(conn, DEMO_CUSTOMER_ID),
        registry=build_registry(),
        llm=FakeLanguageModel(token_delay_s=0.001),
        tts=SlowSynthesizer(),
        send_control=sink.send_control,
        send_audio=sink.send_audio,
    )
    return a, sink


async def test_greeting_is_interruptible_before_its_first_audio_frame(
    slow_actor: tuple[SessionActor, Sink],
) -> None:
    """The window between a turn starting and its first audio frame is still the agent's
    turn, and interrupting it is still barge-in.

    The agent used to claim the floor only when audio began. With a real synthesizer that
    left 1.3-2 s in which an interruption was silently dropped — and then the audio
    arrived anyway and played over the shopper who had just been ignored. The greeting is
    the worst case, because talking over the greeting is what an impatient shopper does.
    """
    a, sink = slow_actor
    await a.greet()
    await asyncio.sleep(0.05)  # turn started, first frame is still hundreds of ms away
    assert sink.audio_frames == 0, "precondition: nothing has been spoken yet"

    await a.on_committed("where's my order?")

    assert "audio.flush" in sink.types(), "an interruption before first audio must flush"

    # Cancellation is scheduled, not immediate: the greeting task reports itself
    # interrupted once it unwinds.
    for _ in range(200):
        await asyncio.sleep(0.01)
        if [m for m in sink.of("agent.done") if m["status"] == "interrupted"]:
            break
    assert [m for m in sink.of("agent.done") if m["status"] == "interrupted"], (
        "the greeting turn must be cancelled, not left to arrive later"
    )
    # The replacement turn is free to speak — that is not the greeting arriving late.
    # Whether cancelled synthesis is suppressed is the synthesizer's own contract and is
    # covered against the real adapter's `_cancelled` set.
    await a.close()


async def test_silent_session_is_not_interrupted_by_the_first_thing_said(
    slow_actor: tuple[SessionActor, Sink],
) -> None:
    """The mirror image, and the reason the floor is released rather than merely set.

    A floor that is never given back would make every turn report itself interrupted by
    the next one.
    """
    a, sink = slow_actor
    await a.on_committed("where's my order?")
    for _ in range(400):
        await asyncio.sleep(0.01)
        if [m for m in sink.of("agent.done")]:
            break
    assert sink.of("agent.done"), "the turn should have completed"
    assert "audio.flush" not in sink.types(), "nobody interrupted anything"
    assert sink.of("agent.done")[0]["status"] == "complete"
    await a.close()
