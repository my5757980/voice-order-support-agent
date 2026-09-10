"""SessionActor — the single writer of conversation state.

Constitution Architectural Principle 1: exactly one component mutates turn state.
Adapters emit; the actor decides. That removes every lock from the design, because there
is no shared mutable state across tasks.

Cancellation is structural rather than remembered. Each turn runs inside its own
`asyncio.TaskGroup`; cancelling the turn cancels the LLM stream, the tool calls, and the
TTS pump together, with `CancelledError` propagating through `async with` cleanup. That
is principle V enforced by the language instead of by every call site being careful.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.barge_in import BargeInConfig, Decision, decide
from src.core.clause_splitter import ClauseSplitter
from src.core.clock import Clock, SystemClock
from src.core.ports import ToolCall
from src.core.turn_state import Trigger, TurnState, TurnStateMachine
from src.obs import metrics
from src.obs.spans import TurnTimings
from src.tools.registry import ToolContext, ToolRegistry

SendControl = Callable[[dict[str, Any]], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]

MAX_TOOL_HOPS = 4
"""Bounded: an unbounded tool loop is an unbounded retry loop by another name."""

GREETING = "Hi, I'm an automated assistant for order support. What can I help you with?"

MAX_SPEECH_DRAIN_S = 20.0
"""Ceiling on waiting for synthesis. Bounded so a hung provider ends the turn rather
than holding the session open indefinitely."""


class SessionActor:
    def __init__(
        self,
        *,
        session_id: str,
        customer_id: str,
        repo: Any,
        registry: ToolRegistry,
        llm: Any,
        tts: Any,
        send_control: SendControl,
        send_audio: SendAudio,
        clock: Clock | None = None,
        barge_in: BargeInConfig | None = None,
    ) -> None:
        self.session_id = session_id
        self.customer_id = customer_id
        self._repo = repo
        self._registry = registry
        self._llm = llm
        self._tts = tts
        self._send_control = send_control
        self._send_audio = send_audio
        self._clock = clock or SystemClock()
        self._barge_cfg = barge_in or BargeInConfig()

        self._sm = TurnStateMachine(self._clock)
        self._messages: list[dict[str, Any]] = []
        self._turn_task: asyncio.Task[None] | None = None
        self._timings: TurnTimings | None = None
        self._agent_speaking = False
        self._last_spoke_at = self._clock.now()
        self._awaiting_confirmation = False
        self._current_turn_id = ""
        self._spoken_chars = 0
        self._interrupt_started: float | None = None

    async def greet(self) -> None:
        """The opening turn: AI disclosure and an open invitation, in one sentence.

        Spoken before the shopper says anything (VID-019, FR-004). Putting it here rather
        than asking the model to do it in its first reply matters — the model used to
        spend that entire turn on the disclosure and ask which order instead of looking
        one up.

        Returns as soon as the turn is *started*. The speaking itself runs as a tracked
        turn task so `interrupt()` and `close()` can cancel it like any other — an
        untracked greeting cannot be interrupted, and the greeting is exactly when an
        impatient shopper talks over the agent.
        """
        turn_id = uuid.uuid4().hex[:12]
        self._current_turn_id = turn_id
        self._timings = TurnTimings(session_id=self.session_id, turn_id=turn_id)
        self._messages.append({"role": "assistant", "content": GREETING})
        await self._send_control(
            {"type": "transcript.committed", "text": GREETING, "turn_order": 0, "speaker": "agent"}
        )
        self._turn_task = asyncio.create_task(self._run_greeting(turn_id))

    async def _run_greeting(self, turn_id: str) -> None:
        timings = self._timings
        assert timings is not None
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._pump_audio(turn_id, timings))
                await self._tts.submit(GREETING, turn_id)
                drain = getattr(self._tts, "drain", None)
                if drain is not None:
                    await drain(turn_id=turn_id, timeout=MAX_SPEECH_DRAIN_S)
                raise _TurnComplete
        except* _TurnComplete:
            await self._finish_turn(timings, status="complete")
        except* asyncio.CancelledError:
            await self._finish_turn(timings, status="interrupted")

    # -- inbound -----------------------------------------------------------

    async def on_partial(self, text: str) -> None:
        """An advisory transcript. Never reaches memory (principle II) — it only drives
        the live pane and the barge-in decision."""
        await self._send_control({"type": "transcript.partial", "text": text, "turn_order": 0})

        if not self._agent_speaking:
            return

        verdict = decide(
            text,
            agent_speaking=True,
            awaiting_confirmation=self._awaiting_confirmation,
            now=self._clock.now(),
            last_spoke_at=self._last_spoke_at,
            config=self._barge_cfg,
        )
        if verdict is Decision.SUPPRESS:
            metrics.inc("backchannel_suppressed_total")
            return
        await self.interrupt()

    async def on_committed(self, text: str) -> None:
        """A committed turn. This is the first point anything enters memory."""
        if self._agent_speaking:
            await self.interrupt()

        turn_id = uuid.uuid4().hex[:12]
        self._current_turn_id = turn_id
        self._timings = TurnTimings(session_id=self.session_id, turn_id=turn_id)

        if self._sm.state is TurnState.IDLE:
            self._sm.fire(Trigger.SPEECH_STARTED)
        if self._sm.can(Trigger.TURN_COMMITTED):
            self._sm.fire(Trigger.TURN_COMMITTED)

        self._messages.append({"role": "user", "content": text})
        await self._send_control(
            {"type": "transcript.committed", "text": text, "turn_order": 0, "speaker": "shopper"}
        )

        # A bare affirmative while a confirmation is pending is the shopper consenting.
        # Recording it in the registry is what unlocks the state-changing tool — the
        # gate lives there, not in the prompt.
        if self._awaiting_confirmation and _is_affirmative(text):
            self._registry.confirm(turn_id)
        self._awaiting_confirmation = False

        # One task group per turn. Cancelling it cancels everything below.
        self._turn_task = asyncio.create_task(self._run_turn(turn_id))

    async def interrupt(self) -> None:
        """Barge-in.

        Three things happen concurrently and none waits for another:
          (a) tell TTS to stop producing
          (b) tell the browser to zero its ring buffer
          (c) cancel the turn

        (b) is the one the shopper experiences. (a) alone leaves ~200 ms of audio already
        buffered in the browser still playing.
        """
        if not self._agent_speaking:
            return
        self._interrupt_started = time.monotonic()

        await asyncio.gather(
            self._tts.clear_buffer(),
            self._send_control({"type": "audio.flush", "turn_id": self._current_turn_id}),
            return_exceptions=True,
        )
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()

        self._agent_speaking = False
        self._last_spoke_at = self._clock.now()
        if self._sm.can(Trigger.BARGE_IN):
            self._sm.fire(Trigger.BARGE_IN)

        # Memory records what was HEARD, not what was generated (principle III).
        self._truncate_agent_turn()

        if self._sm.can(Trigger.TRUNCATED):
            self._sm.fire(Trigger.TRUNCATED)
        await self._emit_state()

    def on_playback_progress(self, turn_id: str, frames_played: int) -> None:
        """Frames actually released by the browser's playback worklet.

        Combined with the character offsets carried on each audio frame, this is what
        makes the truncation exact rather than an estimate from elapsed time.
        """
        if turn_id != self._current_turn_id:
            return
        self._spoken_chars = max(self._spoken_chars, frames_played // 800 * 3)

        if self._interrupt_started is not None:
            ms = (time.monotonic() - self._interrupt_started) * 1000
            metrics.observe("barge_in_latency_seconds", ms / 1000)
            if self._timings:
                self._timings.record("playback.stop", ms)
            self._interrupt_started = None

    # -- the turn ----------------------------------------------------------

    async def _run_turn(self, turn_id: str) -> None:
        timings = self._timings
        assert timings is not None
        splitter = ClauseSplitter()
        spoken: list[str] = []

        try:
            if self._sm.can(Trigger.DISPATCHED):
                self._sm.fire(Trigger.DISPATCHED)
            await self._emit_state()

            async with asyncio.TaskGroup() as group:
                group.create_task(self._pump_audio(turn_id, timings))

                for hop in range(MAX_TOOL_HOPS):
                    tool_calls: list[ToolCall] = []
                    first_token = True

                    async for chunk in self._llm.stream(messages=self._messages):
                        if chunk.text:
                            if first_token:
                                timings.mark_from_turn_start("llm.ttft")
                                first_token = False
                            for clause in splitter.feed(chunk.text):
                                spoken.append(clause)
                                await self._tts.submit(clause, turn_id)
                        if chunk.tool_calls:
                            tool_calls.extend(chunk.tool_calls)

                    if not tool_calls:
                        break

                    # Parallel results go back in ONE message: splitting them trains the
                    # model out of parallel calling, which costs latency on compound turns.
                    results = await asyncio.gather(
                        *[self._invoke(call, turn_id) for call in tool_calls]
                    )
                    # Record the request as well as the results. Providers reject a tool
                    # result that does not reference the assistant turn that asked for it.
                    self._messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {"id": c.id, "name": c.name, "arguments": c.arguments}
                                for c in tool_calls
                            ],
                        }
                    )
                    for call, result in zip(tool_calls, results, strict=True):
                        self._messages.append(
                            {"role": "tool", "tool_use_id": call.id, "content": result}
                        )
                    if hop == MAX_TOOL_HOPS - 1:
                        break

                if (tail := splitter.flush()) is not None:
                    spoken.append(tail)
                    await self._tts.submit(tail, turn_id)

                timings.mark_from_turn_start("llm.complete")

                full = " ".join(spoken).strip()
                if full:
                    self._messages.append({"role": "assistant", "content": full})
                    await self._send_control(
                        {
                            "type": "transcript.committed",
                            "text": full,
                            "turn_order": 0,
                            "speaker": "agent",
                        }
                    )
                    # A read-back ending in a question is a pending confirmation; the next
                    # affirmative unlocks the tool.
                    self._awaiting_confirmation = full.rstrip().endswith("?") and (
                        "shall i" in full.lower() or "go ahead" in full.lower()
                    )

                # Wait for synthesis to actually finish rather than for a fixed delay.
                # A fixed sleep was tuned against the fake synthesizer, which emits
                # instantly; a real one takes hundreds of milliseconds, so the turn used
                # to end before any audio arrived.
                drain = getattr(self._tts, "drain", None)
                if drain is not None:
                    await drain(turn_id=turn_id, timeout=MAX_SPEECH_DRAIN_S)
                else:
                    await asyncio.sleep(0.25)

                # The audio pump is an infinite consumer, so the group will not exit on
                # its own. Signal completion, which cancels the pump and unwinds cleanly.
                raise _TurnComplete

        except* _TurnComplete:
            await self._finish_turn(timings, status="complete")
        except* asyncio.CancelledError:
            # Interrupted. Nothing further is spoken and no memory is written beyond the
            # truncation already applied.
            await self._finish_turn(timings, status="interrupted")

    async def _invoke(self, call: ToolCall, turn_id: str) -> dict[str, Any]:
        started = time.monotonic()
        ctx = ToolContext(
            session_id=self.session_id, turn_id=turn_id, repo=self._repo, speculative=False
        )

        # A slow tool owes the shopper a holding phrase before they hear silence.
        holding: asyncio.Task[None] | None = None
        if self._registry.is_slow(call.name):
            holding = asyncio.create_task(self._holding_phrase(turn_id))

        try:
            result = await self._registry.invoke(call, ctx)
        finally:
            if holding is not None:
                holding.cancel()  # cancelled if the tool beat the timer

        if self._timings:
            self._timings.record("tool", (time.monotonic() - started) * 1000)
        if not result.ok:
            metrics.inc("tool_failures_total", {"name": call.name})
        return dict(result.content)

    async def _holding_phrase(self, turn_id: str) -> None:
        """Fires only if the tool has not returned in time. Cancelled otherwise, so a
        fast-returning slow tool never produces a pointless 'one moment'."""
        await asyncio.sleep(0.4)
        await self._tts.submit("Let me pull that up.", turn_id)

    async def _pump_audio(self, turn_id: str, timings: TurnTimings) -> None:
        first = True
        async for frame in self._tts.audio():
            if frame.turn_id != turn_id:
                continue
            if first:
                timings.mark_from_turn_start("tts.ttfb")
                first = False
                self._agent_speaking = True
                if self._sm.can(Trigger.FIRST_AUDIO):
                    self._sm.fire(Trigger.FIRST_AUDIO)
                await self._send_control({"type": "agent.speaking", "turn_id": turn_id})
                await self._emit_state()
            await self._send_audio(frame.pcm)

    async def _finish_turn(self, timings: TurnTimings, *, status: str) -> None:
        self._agent_speaking = False
        self._last_spoke_at = self._clock.now()
        if self._sm.can(Trigger.RESPONSE_COMPLETE):
            self._sm.fire(Trigger.RESPONSE_COMPLETE)

        spans = timings.finish()
        metrics.observe("turn_latency_e2e_seconds", spans["e2e"] / 1000)
        # Timings first, then completion. `agent.done` is the terminal message for a
        # turn, so anything a consumer needs about that turn must precede it.
        await self._send_control(
            {"type": "timing", "turn_id": timings.turn_id, "spans": spans}
        )
        await self._emit_state()
        await self._send_control(
            {"type": "agent.done", "turn_id": timings.turn_id, "status": status}
        )

    def _truncate_agent_turn(self) -> None:
        for msg in reversed(self._messages):
            if msg.get("role") == "assistant":
                text = str(msg.get("content", ""))
                heard = text[: self._spoken_chars] if self._spoken_chars else ""
                msg["content"] = heard.rstrip()
                msg["truncated"] = True
                break
        self._spoken_chars = 0

    async def _emit_state(self) -> None:
        await self._send_control({"type": "state", "state": self._sm.state.value})

    async def close(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        await self._tts.close()


class _TurnComplete(Exception):
    """Internal signal used to exit the turn's task group cleanly once generation ends.

    A TaskGroup only exits when every child finishes, and the audio pump is an infinite
    consumer — so completion is signalled rather than waited for.
    """


def _is_affirmative(text: str) -> bool:
    tokens = text.lower().strip(" .!?,").split()
    return bool(tokens) and len(tokens) <= 3 and tokens[0] in {
        "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "go", "do",
    }
