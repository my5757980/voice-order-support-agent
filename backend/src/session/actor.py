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
from src.core.confirmation import is_affirmative, seeks_confirmation
from src.core.ports import ModelUnavailable, ToolCall
from src.core.references import extract as extract_references
from src.core.speculation import SpeculationConfig, Verdict, promotable, should_dispatch
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


def _whole_words(text: str, n: int) -> str:
    """The first `n` characters of `text`, backed off to a word boundary.

    Heard offsets interpolate within a clause, so they can land mid-word. Memory keeps
    whole words only, and errs toward having heard less.
    """
    if n >= len(text):
        return text.rstrip()
    cut = text[: max(n, 0)]
    if n > 0 and not text[n].isspace():
        cut = cut.rsplit(" ", 1)[0] if " " in cut else ""
    return cut.rstrip()


_SAMPLE_RATE = 16_000
"""The playback format, PCM16 mono — what the browser's worklet renders."""

_NETWORK_LAG_S = 0.1
"""How far the browser's playback trails the server's send: roughly one network hop.
Used only where the browser has not yet reported what it played."""

_PLAYBACK_MARGIN_S = 0.25
"""Audio counts as still playing this long past the modelled end. A barge-in that lands
a little late flushes an already-empty buffer, which is harmless; one dismissed because
the model ended early lets the agent talk over the shopper, which is the failure."""


_MODEL_ERROR_TEXT: dict[str, str] = {
    "rate_limited": (
        "Sorry — I couldn't get to that just now. Please say it again in a few seconds."
    ),
    "timeout": "Sorry — that took too long. Please say it again.",
    "unauthorized": "Sorry — I can't answer right now. Please try again shortly.",
    "unavailable": "Sorry — I can't answer right now. Please try again shortly.",
}
"""What the shopper is told when a turn produced no reply at all. About what they can
do next, never about which provider failed or why (NFR-014)."""


def _leaves(group: BaseException) -> list[BaseException]:
    """Flatten nested exception groups — a TaskGroup nests them."""
    if isinstance(group, BaseExceptionGroup):
        return [leaf for sub in group.exceptions for leaf in _leaves(sub)]
    return [group]


_SPEECH_ERROR_TEXT: dict[str, str] = {
    "rate_limited": "Voice output has hit its rate limit — the reply is written above.",
    "unauthorized": "Voice output is unavailable — the reply is written above.",
    "unavailable": "Voice output is unavailable right now — the reply is written above.",
    "timeout": "Voice output timed out — the reply is written above.",
}
"""What the shopper is told when a reply exists but could not be spoken. One line each,
about what they can do, never about which provider failed or why."""


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
        speculation: SpeculationConfig | None = None,
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
        self._spec_cfg = speculation or SpeculationConfig()

        self._sm = TurnStateMachine(self._clock)
        self._messages: list[dict[str, Any]] = []
        self._turn_task: asyncio.Task[None] | None = None
        self._timings: TurnTimings | None = None
        self._agent_speaking = False
        self._turn_in_flight = False
        self._last_spoke_at = self._clock.now()
        self._awaiting_confirmation = False
        # Set when the registry refuses a state-changing call for want of
        # confirmation — a fact the system observed, not a guess about wording.
        self._confirmation_refused = False
        self._current_turn_id = ""
        self._interrupt_started: float | None = None
        self._interrupted_turn = ""

        # The browser's playback, modelled here. Orpheus returns each clause whole and the
        # pump forwards it at once, so synthesis finishes seconds before the shopper stops
        # hearing the reply. The floor, barge-in and "what was heard" all have to follow
        # the audio the shopper is hearing, not the audio the server finished sending.
        self._playback_end = 0.0
        self._play_turn = ""
        self._play_frames: list[tuple[float, float, int]] = []  # (start, seconds, char offset)
        self._progress_samples = 0  # the browser's own count, for the playing turn
        self._reply: dict[str, Any] | None = None
        self._reply_turn = ""
        # The clauses handed to the synthesizer for the turn being generated, before the
        # reply as a whole reaches memory. Shared with the running turn, not copied.
        self._spoken: list[str] = []
        self._spoken_turn = ""

        # Speculation state. The staged text is the whole point: it exists so a
        # speculative answer can be held back until the turn commits, and discarded
        # without ever being spoken or remembered if the shopper said something else.
        self._spec_task: asyncio.Task[None] | None = None
        self._spec_text: str | None = None      # partial the speculation was built on
        self._spec_result: str | None = None    # staged answer, not yet spoken
        self._spec_started: float | None = None

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
        greeting = {"role": "assistant", "content": GREETING}
        self._messages.append(greeting)
        self._reply, self._reply_turn = greeting, turn_id
        await self._send_control(
            {"type": "transcript.committed", "text": GREETING, "turn_order": 0, "speaker": "agent"}
        )
        self._turn_in_flight = True
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

    @property
    def _agent_has_floor(self) -> bool:
        """The agent is speaking, or has started a turn and is about to.

        `_agent_speaking` only becomes true on the first audio frame. With a real
        synthesizer that is 1.3-2 s after the turn starts — measured, not guessed — and
        for that entire window an interruption used to be dropped on the floor. Then the
        audio arrived anyway and the agent talked over the shopper it had just ignored.
        The greeting is the worst case, because talking over the greeting is exactly what
        an impatient shopper does.

        Holding the floor starts when the turn starts. `_turn_in_flight` is cleared in
        `_finish_turn`, which runs inside the turn task, so it closes the window between
        a turn finishing and its task being marked done — where the task check alone
        would report a floor nobody holds.
        """
        if self._audio_playing:
            return True
        if self._turn_task is None or self._turn_task.done():
            return False
        return self._turn_in_flight

    @property
    def _audio_playing(self) -> bool:
        """The browser is still playing the agent's audio.

        With a synthesizer that returns each clause whole, the turn task finished — and
        gave up the floor — while seconds of reply were still queued in the browser. A
        shopper talking over that tail was not a barge-in as far as the server knew: no
        flush, no stop, and the agent kept talking over them. Measured on the deployed
        app: the reply's `agent.done` arrived 0.5 s after its first audio, and the audio
        played for six seconds.
        """
        return time.monotonic() < self._playback_end + _PLAYBACK_MARGIN_S

    # -- inbound -----------------------------------------------------------

    async def on_partial(self, text: str) -> None:
        """An advisory transcript. Never reaches memory (principle II) — it only drives
        the live pane and the barge-in decision."""
        await self._send_control({"type": "transcript.partial", "text": text, "turn_order": 0})

        if not self._agent_has_floor:
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

    async def on_partial_scored(self, text: str, confidence: float) -> None:
        """A partial that carries the provider's end-of-turn confidence.

        Same advisory contract as `on_partial` — nothing here reaches memory — plus the
        one thing confidence makes possible: starting the model early.
        """
        await self.on_partial(text)

        verdict = should_dispatch(
            text,
            confidence=confidence,
            last_dispatched=self._spec_text,
            awaiting_confirmation=self._awaiting_confirmation,
            agent_speaking=self._agent_has_floor,
            config=self._spec_cfg,
        )
        if verdict is not Verdict.DISPATCH:
            return

        self._cancel_speculation()
        self._spec_text = text.strip()
        self._spec_started = time.monotonic()
        self._spec_task = asyncio.create_task(self._speculate(text.strip()))

    async def _speculate(self, text: str) -> None:
        """Run the model against a partial turn, into a staging buffer.

        Read-only by construction: the ToolRegistry refuses any state-changing tool while
        `speculative=True`, so this cannot create a return however the model reasons.
        """
        staged: list[str] = []
        messages = [*self._messages, {"role": "user", "content": text}]
        try:
            for _ in range(2):  # one tool hop is enough to answer a lookup
                calls: list[ToolCall] = []
                async for chunk in self._llm.stream(messages=messages, speculative=True):
                    if chunk.text:
                        staged.append(chunk.text)
                    if chunk.tool_calls:
                        calls.extend(chunk.tool_calls)
                if not calls:
                    break
                results = await asyncio.gather(
                    *[self._invoke(c, self._current_turn_id, speculative=True) for c in calls]
                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls
                        ],
                    }
                )
                for call, result in zip(calls, results, strict=True):
                    messages.append({"role": "tool", "tool_use_id": call.id, "content": result})
            self._spec_result = "".join(staged).strip() or None
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed speculation is a non-event. The real turn runs normally.
            self._spec_result = None

    def _cancel_speculation(self) -> None:
        if self._spec_task is not None and not self._spec_task.done():
            self._spec_task.cancel()
        self._spec_task = None
        self._spec_result = None
        self._spec_started = None

    async def on_committed(self, text: str) -> None:
        """A committed turn. This is the first point anything enters memory."""
        if self._agent_has_floor:
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
        if self._awaiting_confirmation and is_affirmative(text):
            self._registry.confirm(turn_id)
        self._awaiting_confirmation = False

        # Promote a speculation only if the shopper actually said what it answered.
        # Anything else is an answer to a question they did not ask, and it is discarded
        # rather than spoken (Architectural Principle 8: speculation is allowed,
        # commitment is not).
        staged = self._spec_result if promotable(text, self._spec_text) else None
        if staged is not None:
            metrics.inc("speculative_dispatch_total", {"outcome": "promoted"})
            if self._spec_started is not None and self._timings is not None:
                saved = (time.monotonic() - self._spec_started) * 1000
                self._timings.record("llm.speculation_saved_ms", saved)
        elif self._spec_text is not None:
            metrics.inc("speculative_dispatch_total", {"outcome": "discarded"})
        self._spec_text = None
        promoted = staged
        self._cancel_speculation()

        # One task group per turn. Cancelling it cancels everything below.
        self._confirmation_refused = False
        self._turn_in_flight = True
        self._turn_task = asyncio.create_task(self._run_turn(turn_id, promoted=promoted))

    async def interrupt(self) -> None:
        """Barge-in.

        Three things happen concurrently and none waits for another:
          (a) tell TTS to stop producing
          (b) tell the browser to zero its ring buffer
          (c) cancel the turn

        (b) is the one the shopper experiences. (a) alone leaves ~200 ms of audio already
        buffered in the browser still playing.
        """
        if not self._agent_has_floor:
            return
        self._interrupt_started = time.monotonic()
        # The turn being cut is the one the shopper is hearing, if any audio is playing;
        # otherwise the one in flight, which may not have said anything yet.
        target = self._play_turn if self._audio_playing else self._current_turn_id
        self._interrupted_turn = target
        # Whether the whole reply had been synthesized decides what "heard everything sent"
        # means below, so it is read before the task is cancelled.
        synthesis_done = self._turn_task is None or self._turn_task.done()

        await asyncio.gather(
            self._tts.clear_buffer(),
            self._send_control({"type": "audio.flush", "turn_id": self._interrupted_turn}),
            return_exceptions=True,
        )
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()

        self._agent_speaking = False
        self._turn_in_flight = False
        self._last_spoke_at = self._clock.now()
        if self._sm.can(Trigger.BARGE_IN):
            self._sm.fire(Trigger.BARGE_IN)

        # Memory records what was HEARD, not what was generated (principle III).
        self._truncate_heard(target, synthesis_done=synthesis_done)
        self._playback_end = 0.0  # the browser has just zeroed its buffer

        if self._sm.can(Trigger.TRUNCATED):
            self._sm.fire(Trigger.TRUNCATED)
        await self._emit_state()

    def on_playback_progress(self, turn_id: str, frames_played: int) -> None:
        """Frames actually released by the browser's playback worklet.

        Combined with the character offsets carried on each audio frame, this is what
        makes the truncation exact rather than an estimate from elapsed time.
        """
        if turn_id == self._play_turn:
            self._progress_samples = max(self._progress_samples, frames_played)

        # The flush confirmation is for the turn that was interrupted, which on the
        # commit path is no longer the current turn by the time it arrives.
        if self._interrupt_started is not None and turn_id == self._interrupted_turn:
            ms = (time.monotonic() - self._interrupt_started) * 1000
            metrics.observe("barge_in_latency_seconds", ms / 1000)
            if self._timings:
                self._timings.record("playback.stop", ms)
            self._interrupt_started = None

    # -- the turn ----------------------------------------------------------

    async def _run_turn(self, turn_id: str, *, promoted: str | None = None) -> None:
        timings = self._timings
        assert timings is not None
        splitter = ClauseSplitter()
        spoken: list[str] = []
        self._spoken, self._spoken_turn = spoken, turn_id

        try:
            if self._sm.can(Trigger.DISPATCHED):
                self._sm.fire(Trigger.DISPATCHED)
            await self._emit_state()

            async with asyncio.TaskGroup() as group:
                group.create_task(self._pump_audio(turn_id, timings))

                if promoted is not None:
                    # The answer already exists — generated while the shopper was still
                    # speaking. Skip straight to synthesis; this is the entire saving.
                    timings.mark_from_turn_start("llm.ttft")
                    for clause in splitter.feed(promoted):
                        spoken.append(clause)
                        await self._tts.submit(clause, turn_id)

                for hop in range(0 if promoted is not None else MAX_TOOL_HOPS):
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
                    reply = {"role": "assistant", "content": full}
                    self._messages.append(reply)
                    self._reply, self._reply_turn = reply, turn_id
                    await self._send_control(
                        {
                            "type": "transcript.committed",
                            "text": full,
                            "turn_order": 0,
                            "speaker": "agent",
                        }
                    )
                    # A pending confirmation, from either signal: the registry actually
                    # refused a state-changing call, or the reply reads as a request to
                    # authorise one. The first covers a model that reaches for the tool
                    # and is stopped; the second a well-behaved model that asks first,
                    # where nothing was refused and there is no fact to read. Two
                    # hard-coded phrases covered neither reliably (see core/confirmation).
                    self._awaiting_confirmation = self._confirmation_refused or (
                        seeks_confirmation(full)
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
        except* Exception as group:
            # Anything else used to escape the task group unhandled: "Task exception was
            # never retrieved" in the log, no `agent.done`, no word to the shopper, and a
            # UI left saying "thinking" forever. The first time it happened was a Groq
            # 429 at turn five of a rehearsal. A turn that cannot be answered must still
            # end, and must say so.
            await self._fail_turn(timings, group)

    async def _fail_turn(self, timings: TurnTimings, group: BaseExceptionGroup) -> None:
        kind = next(
            (e.kind for e in _leaves(group) if isinstance(e, ModelUnavailable)),
            "unavailable",
        )
        metrics.inc("turn_failures_total", {"kind": kind})
        await self._send_control(
            {"type": "error", "code": f"model_{kind}", "message": _MODEL_ERROR_TEXT[kind]}
        )
        await self._finish_turn(timings, status="failed")

    async def _invoke(
        self, call: ToolCall, turn_id: str, *, speculative: bool = False
    ) -> dict[str, Any]:
        started = time.monotonic()
        ctx = ToolContext(
            session_id=self.session_id,
            turn_id=turn_id,
            repo=self._repo,
            speculative=speculative,
        )

        # A slow tool owes the shopper a holding phrase before they hear silence.
        holding: asyncio.Task[None] | None = None
        if not speculative and self._registry.is_slow(call.name):
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

        if result.error_code == "confirmation_required":
            self._confirmation_refused = True

        content = dict(result.content)
        # A speculative turn may never reach the shopper — putting its findings on screen
        # would be exactly that, and would also leak the answer to a question they have
        # not finished asking (Architectural Principle 8).
        if not speculative and result.ok:
            await self._show_references(content)
        return content

    async def _show_references(self, content: dict[str, Any]) -> None:
        """Put identifiers on screen, because the agent is about to say it did.

        The persona forbids reading a tracking number aloud and tells the agent to say it
        has been put on screen instead. Nothing was putting it there, so the one thing
        the shopper needed was the one thing they never got — while the agent claimed
        otherwise. The panel is filled before the reply is spoken, so it is already there
        when the sentence pointing at it arrives.
        """
        for kind, label, value in extract_references(content):
            await self._send_control(
                {"type": "display.reference", "kind": kind, "label": label, "value": value}
            )
            metrics.inc("display_references_total", {"kind": kind})

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
                # From the LLM's first token, which is what the budget names. On a turn
                # with no LLM (the greeting) there is no such mark, and mark_since falls
                # back to turn start — correct, since submission is the turn start there.
                timings.mark_since("tts.ttfb", "llm.ttft")
                first = False
                self._agent_speaking = True
                if self._sm.can(Trigger.FIRST_AUDIO):
                    self._sm.fire(Trigger.FIRST_AUDIO)
                await self._send_control({"type": "agent.speaking", "turn_id": turn_id})
                await self._emit_state()
            self._model_playback(frame)
            await self._send_audio(frame.pcm)

    def _model_playback(self, frame: Any) -> None:
        """Advance the model of the browser's playback by one frame being sent.

        A frame plays when the one before it finishes, or on arrival if the buffer ran
        dry — so the model follows underruns between clauses as well as bursts.
        """
        now = time.monotonic()
        if frame.turn_id != self._play_turn:
            self._play_turn = frame.turn_id
            self._play_frames = []
            self._progress_samples = 0
        start = max(self._playback_end, now)
        seconds = len(frame.pcm) / 2 / _SAMPLE_RATE
        self._play_frames.append((start, seconds, int(getattr(frame, "char_offset", 0))))
        self._playback_end = start + seconds

    async def _report_synthesis_failure(self, turn_id: str) -> None:
        """Say out loud that the agent could not say anything.

        A turn whose synthesis failed still completes: the reply is written, the tools
        ran, memory is correct — there is simply no audio. Left unreported that reads as
        a broken app, because the one thing the shopper is waiting for never arrives and
        nothing on screen admits it. The text is deliberately about the shopper's
        situation and never about the provider (NFR: no vendor error text reaches a
        shopper).
        """
        probe = getattr(self._tts, "synthesis_error", None)
        if probe is None:
            return
        kind = probe(turn_id)
        if kind is None:
            return
        await self._send_control(
            {"type": "error", "code": f"speech_{kind}", "message": _SPEECH_ERROR_TEXT[kind]}
        )

    async def _finish_turn(self, timings: TurnTimings, *, status: str) -> None:
        self._agent_speaking = False
        self._turn_in_flight = False
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
        # Before `agent.done`: that message is terminal for a turn, so anything a
        # consumer needs to know about the turn has to precede it.
        await self._report_synthesis_failure(timings.turn_id)
        await self._send_control(
            {"type": "agent.done", "turn_id": timings.turn_id, "status": status}
        )

    def _heard_chars(self, now: float) -> int | None:
        """Characters of the playing reply the shopper has heard; None if every frame
        sent so far has been heard.

        The browser's own count wins when it has reported one; the playback model,
        lagged by a network hop, stands in before the first report. Either way the
        answer is the reply-relative offset of the first frame not yet played, so memory
        is never ahead of the audio.
        """
        if self._progress_samples > 0:
            heard_s = self._progress_samples / _SAMPLE_RATE
        else:
            t = now - _NETWORK_LAG_S
            heard_s = sum(min(max(t - s, 0.0), d) for s, d, _ in self._play_frames)
        elapsed = 0.0
        for _, seconds, offset in self._play_frames:
            if elapsed + seconds > heard_s:
                return offset
            elapsed += seconds
        return None

    def _truncate_heard(self, target: str, *, synthesis_done: bool) -> None:
        """Make memory hold what the shopper heard of the interrupted turn — no more, and
        nothing about any other turn.

        If the reply is already in memory (it finished generating and was still playing
        out), it is shortened to what was heard. If it is not — the cut came while the
        model was still generating — the heard part of the clauses already spoken is
        recorded as a truncated reply: the shopper heard those words, so the model has
        to know they were said.

        The old version shortened "the last assistant message". When the cut came during
        generation that was the tool-call request, or the previous turn's reply, heard in
        full — and the words the shopper had just heard were never remembered at all.
        """
        if target != self._play_turn:
            return  # nothing of the interrupted turn had reached the speakers
        in_memory = self._reply if self._reply_turn == target else None
        if in_memory is not None:
            text = str(in_memory.get("content", ""))
        elif self._spoken_turn == target and self._spoken:
            text = " ".join(self._spoken)
        else:
            return

        heard = self._heard_chars(time.monotonic())
        if heard is None:  # every frame sent so far was heard
            if synthesis_done:
                heard = len(text)
            else:
                heard = self._play_frames[-1][2] if self._play_frames else 0
        cut = _whole_words(text, heard)

        if in_memory is not None:
            if len(cut) < len(text.rstrip()):
                in_memory["content"] = cut
                in_memory["truncated"] = True
        elif cut:
            self._messages.append({"role": "assistant", "content": cut, "truncated": True})

    async def _emit_state(self) -> None:
        await self._send_control({"type": "state", "state": self._sm.state.value})

    async def close(self) -> None:
        self._cancel_speculation()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        await self._tts.close()


class _TurnComplete(Exception):
    """Internal signal used to exit the turn's task group cleanly once generation ends.

    A TaskGroup only exits when every child finishes, and the audio pump is an infinite
    consumer — so completion is signalled rather than waited for.
    """
