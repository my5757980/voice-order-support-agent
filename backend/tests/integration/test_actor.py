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
    the model generated.

    Cut while the model is still generating, so the reply has not reached memory. The
    heard part of the clauses already spoken must be recorded — the shopper heard those
    words. This test used to pass by marking the tool-call request as "truncated", which
    was the last assistant message at the time: the property was never actually held.
    """
    a, sink = actor
    await a.on_committed("where's my order?")
    for _ in range(300):
        await asyncio.sleep(0.01)
        if sink.audio_frames >= 6:
            break
    turn = a._play_turn
    a.on_playback_progress(turn, 800 * 5)  # five 50 ms frames actually played
    await a.interrupt()
    await asyncio.sleep(0.3)

    last = a._messages[-1]
    assert last["role"] == "assistant" and last.get("truncated") is True
    assert "tool_calls" not in last, "the tool-call request is not what the shopper heard"
    assert last["content"], "the shopper heard words; memory must hold them"
    spoken = " ".join(a._spoken)
    assert spoken.startswith(last["content"])
    assert len(last["content"]) < len(spoken)
    await a.close()


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


# -- a model that does not answer ------------------------------------------


class RateLimitedModel(FakeLanguageModel):
    """Refuses the way Groq's free tier does at 8,000 tokens a minute."""

    def stream(self, **_: Any):  # type: ignore[override]
        from src.core.ports import ModelUnavailable

        async def refuse():  # type: ignore[no-untyped-def]
            raise ModelUnavailable("rate_limited", "tokens per minute (TPM): Limit 8000")
            yield  # pragma: no cover — makes this an async generator

        return refuse()


@pytest.fixture
def limited_actor(tmp_path: Path) -> tuple[SessionActor, Sink]:
    conn: sqlite3.Connection = connect(str(tmp_path / "limited.db"))
    seed(conn)
    sink = Sink()
    a = SessionActor(
        session_id="sess-limited",
        customer_id=DEMO_CUSTOMER_ID,
        repo=OrderRepository(conn, DEMO_CUSTOMER_ID),
        registry=build_registry(),
        llm=RateLimitedModel(),
        tts=FakeSpeechSynthesizer(realtime=False),
        send_control=sink.send_control,
        send_audio=sink.send_audio,
    )
    return a, sink


async def _until_done(sink: Sink) -> None:
    for _ in range(300):
        await asyncio.sleep(0.01)
        if sink.of("agent.done"):
            return
    pytest.fail("the turn never ended")


async def test_a_refused_model_still_ends_the_turn(
    limited_actor: tuple[SessionActor, Sink],
) -> None:
    """Regression: the 429 escaped the task group unhandled. No `agent.done` was ever
    sent, so the browser sat in "thinking" for the rest of the session."""
    a, sink = limited_actor
    await a.on_committed("what was the total on that order?")
    await _until_done(sink)
    assert sink.of("agent.done")[0]["status"] == "failed"
    await a.close()


async def test_the_shopper_is_told_in_words_that_name_no_vendor(
    limited_actor: tuple[SessionActor, Sink],
) -> None:
    a, sink = limited_actor
    await a.on_committed("what was the total on that order?")
    await _until_done(sink)
    (error,) = sink.of("error")
    assert error["code"] == "model_rate_limited"
    text = error["message"].lower()
    assert "again" in text, "it must say what the shopper can do"
    for leak in ("groq", "429", "tpm", "token", "limit 8000"):
        assert leak not in text, leak
    await a.close()


async def test_the_error_arrives_before_the_turn_ends(
    limited_actor: tuple[SessionActor, Sink],
) -> None:
    """`agent.done` is terminal for a turn; a consumer reading up to it must already
    have everything it needs to know about the turn."""
    a, sink = limited_actor
    await a.on_committed("where's my order?")
    await _until_done(sink)
    kinds = sink.types()
    assert kinds.index("error") < kinds.index("agent.done")
    await a.close()


async def test_the_session_survives_to_answer_the_next_turn(
    limited_actor: tuple[SessionActor, Sink],
) -> None:
    """A failed turn is one turn, not a dead session."""
    a, sink = limited_actor
    await a.on_committed("where's my order?")
    await _until_done(sink)
    a._llm = FakeLanguageModel(token_delay_s=0.001)  # the limit window rolls over
    await a.on_committed("where's my order?")
    for _ in range(300):
        await asyncio.sleep(0.01)
        if len(sink.of("agent.done")) >= 2:
            break
    assert [m["status"] for m in sink.of("agent.done")] == ["failed", "complete"]
    await a.close()



# -- the tail: synthesis finished, the shopper still hearing it ----------------------


@pytest.fixture
def burst_actor(tmp_path: Path) -> tuple[SessionActor, Sink]:
    """A synthesizer that returns every frame at once, as Groq Orpheus does.

    The realtime fake used everywhere else paces frames at playback speed, so a turn
    never finished while its audio was still playing. Orpheus finishes seconds early —
    and that gap is where barge-in used to be impossible.
    """
    conn: sqlite3.Connection = connect(str(tmp_path / "burst.db"))
    seed(conn)
    sink = Sink()
    a = SessionActor(
        session_id="sess-burst",
        customer_id=DEMO_CUSTOMER_ID,
        repo=OrderRepository(conn, DEMO_CUSTOMER_ID),
        registry=build_registry(),
        llm=FakeLanguageModel(token_delay_s=0.001),
        tts=FakeSpeechSynthesizer(realtime=False),
        send_control=sink.send_control,
        send_audio=sink.send_audio,
    )
    return a, sink


async def _until_done(sink: Sink, n: int = 1) -> None:
    for _ in range(500):
        await asyncio.sleep(0.01)
        if len(sink.of("agent.done")) >= n:
            return
    pytest.fail("the turn never finished")


async def test_a_reply_still_playing_can_be_interrupted(burst_actor) -> None:  # type: ignore[no-untyped-def]
    """Regression from the deployed app: `agent.done` arrived 0.5 s after the reply's
    first audio and the audio played for six seconds. A shopper talking over that tail
    got no flush and no stop — the server believed the agent had finished."""
    a, sink = burst_actor
    await a.on_committed("where's my order?")
    await _until_done(sink)
    assert a._audio_playing, "precondition: the turn is over, the audio is not"

    await a.on_committed("sorry, the headphones one")
    assert "audio.flush" in sink.types()


async def test_the_reply_still_playing_is_cut_to_what_was_heard(burst_actor) -> None:  # type: ignore[no-untyped-def]
    a, sink = burst_actor
    await a.on_committed("where's my order?")
    await _until_done(sink)
    reply = a._reply
    assert reply is not None
    full = str(reply["content"])

    a.on_playback_progress(a._play_turn, 16_000)  # one second of it actually played
    await a.interrupt()

    assert reply.get("truncated") is True
    assert full.startswith(str(reply["content"]))
    assert 0 < len(str(reply["content"])) < len(full)


async def test_the_floor_is_released_when_the_audio_ends(burst_actor) -> None:  # type: ignore[no-untyped-def]
    """The mirror image: once the browser has played everything, the next turn is a
    new turn, not an interruption."""
    a, sink = burst_actor
    await a.on_committed("where's my order?")
    await _until_done(sink)
    a._playback_end = 0.0  # the audio has finished playing
    flushes = sink.types().count("audio.flush")

    await a.on_committed("thanks")
    assert sink.types().count("audio.flush") == flushes


async def test_a_turn_cut_before_it_spoke_leaves_the_previous_reply_alone(
    burst_actor,  # type: ignore[no-untyped-def]
) -> None:
    """The old truncation shortened "the last assistant message". Cut before the new
    turn produced any audio, that was the previous reply — which had been heard in full."""
    a, sink = burst_actor
    await a.on_committed("where's my order?")
    await _until_done(sink)
    previous = dict(a._reply or {})
    a._playback_end = 0.0

    a._llm = SlowToStartModel()
    await a.on_committed("and the mug?")
    await asyncio.sleep(0.05)  # in flight, nothing spoken yet
    await a.interrupt()

    assert a._reply is not None and a._reply.get("content") == previous.get("content")
    assert not a._reply.get("truncated")


class SlowToStartModel(FakeLanguageModel):
    """Holds its first token long enough for a test to interrupt before any audio."""

    def stream(self, **kwargs: Any):  # type: ignore[override]
        inner = super().stream(**kwargs)

        async def slow():  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.5)
            async for chunk in inner:
                yield chunk

        return slow()



# -- dead air -------------------------------------------------------------------------


class SpeechlessModel(FakeLanguageModel):
    """Ends the turn without a single word, as gpt-oss did on the deployed app when its
    reasoning spent the whole token budget."""

    def stream(self, **_: Any):  # type: ignore[override]
        from src.core.ports import LlmChunk

        async def nothing():  # type: ignore[no-untyped-def]
            yield LlmChunk(stop_reason="length")

        return nothing()


async def test_a_turn_with_nothing_to_say_still_says_something(burst_actor) -> None:  # type: ignore[no-untyped-def]
    a, sink = burst_actor
    a._llm = SpeechlessModel()
    await a.on_committed("yes, go ahead")
    await _until_done(sink)

    said = [m["text"] for m in sink.of("transcript.committed") if m["speaker"] == "agent"]
    assert said, "the shopper heard nothing at all"
    assert "again" in said[-1].lower(), "it must tell the shopper what to do"
    assert sink.audio_frames > 0
