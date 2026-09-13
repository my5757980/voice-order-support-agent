"""Groq / Canopy Labs Orpheus text-to-speech.

Selected after ElevenLabs turned out to have an exhausted free quota (1 credit of 10,000
remaining). This runs on the Groq key the project already holds, so it costs nothing
extra and adds no new credential.

**It is an honest downgrade, and the trade is worth naming.** research.md R4 chose
ElevenLabs for two capabilities this endpoint does not have:

  `clear_buffer`  — a server-side interrupt. Orpheus is REST, not a socket, so there is
                    nothing to tell "stop generating". We stop *emitting* instead, and
                    the client-side `audio.flush` still silences the shopper's speakers
                    within one render quantum. Since that flush was always the half that
                    the listener actually experiences, barge-in latency is unaffected;
                    what we lose is the ability to stop the provider mid-clause, which
                    costs credits rather than milliseconds.

  `timestamps`    — character alignment. Without it, `heard_prefix_len` becomes
                    clause-proportional rather than character-exact: we know the clause
                    text and its audio duration, so frames-played maps onto characters
                    linearly. The truncation is still bounded by what was genuinely
                    played, just to clause resolution instead of character resolution.

Measured (2026-09-09, from Pakistan): p50 610 ms, p95 687 ms, **min 125 ms** per clause.
The spread is network, not model — see T077b, which re-measures from the deployed
instance where the trans-Pacific hop is gone.

Output is WAV 24 kHz mono 16-bit; the pipeline runs at 16 kHz, so this adapter
downsamples before framing.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from collections.abc import AsyncIterator

import httpx

from src.core.events import AgentAudio
from src.obs import metrics

ENDPOINT = "https://api.groq.com/openai/v1/audio/speech"
MODEL = "canopylabs/orpheus-v1-english"
VOICES = ("autumn", "diana", "hannah", "austin", "daniel", "troy")

SOURCE_RATE = 24_000   # what Orpheus returns
TARGET_RATE = 16_000   # what the rest of the pipeline speaks
FRAME_SAMPLES = 800    # 50 ms at 16 kHz — matches the capture format exactly


_FAILURE_MEMORY = 32
"""Turns to remember a synthesis failure for. Bounded because an unbounded map keyed by
turn id is a slow leak on a long session."""

# How a reply is cut into requests.
#
# Groq's free tier allows this model ten requests a minute, and the limit is not in the
# response headers — they report the daily allowance, which looked healthy the whole time.
# Sending every clause as its own request is the natural shape for latency, and it spent
# twelve requests in the first sixteen seconds of the demo: a greeting, one answer, and an
# interruption. The voice cut out on the second turn while ninety requests a day remained.
#
# So a reply goes out in as few requests as the ear allows. The opening words go alone,
# because they are what the shopper is waiting to hear and a short request comes back
# fastest. Everything after is merged and sent when the reply ends — or after a short
# hold, so words spoken before a slow tool call are not kept back until the tool returns.
FIRST_CHUNK_WORDS = 4
"""The first request of a reply waits for at least this many words. "Hi," alone costs a
request and buys nothing."""

LATER_CHUNK_WORDS = 40
"""Merged text is sent early once it reaches this size, so one request never holds a
paragraph."""

HOLD_S = 0.35
"""The longest merged text waits for more. A fast model finishes a whole reply inside it;
a model paused on a tool call does not keep the words it already said waiting."""


def _classify(status: int) -> str:
    """Provider status to a cause the shopper can be told about.

    Deliberately coarse. The distinction that matters to someone waiting is between
    "try again shortly" and "this is not going to work right now"; the exact status code
    belongs in metrics, never in anything spoken or shown.
    """
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "unauthorized"
    return "unavailable"


def _pcm_from_wav(data: bytes) -> bytes:
    """Extract the PCM payload from a RIFF/WAVE container.

    Walks the chunk list rather than assuming a 44-byte header: encoders are free to
    insert LIST or fact chunks, and a fixed offset would slice into audio.
    """
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data  # already raw
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        if chunk_id == b"data":
            return data[pos + 8 : pos + 8 + size]
        pos += 8 + size + (size & 1)  # chunks are word-aligned
    return data[44:]


def _resample_24k_to_16k(pcm: bytes) -> bytes:
    """24 kHz → 16 kHz, a clean 3:2 decimation.

    Every three input samples become two output samples, each an average of the pair
    that straddles it. Averaging is a crude low-pass, but it is the cheap way to avoid
    the aliasing that plain sample-dropping would fold into the speech band — and this
    runs on every clause, so cost matters.
    """
    count = len(pcm) // 2
    if count < 3:
        return pcm
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])

    out: list[int] = []
    for i in range(0, count - 2, 3):
        a, b, c = samples[i], samples[i + 1], samples[i + 2]
        out.append((a + b) // 2)
        out.append((b + c) // 2)
    return struct.pack(f"<{len(out)}h", *out)


class GroqSpeechSynthesizer:
    """Implements the SpeechSynthesizer port."""

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "hannah",
        model: str = MODEL,
        timeout_s: float = 15.0,
        first_chunk_words: int = FIRST_CHUNK_WORDS,
        later_chunk_words: int = LATER_CHUNK_WORDS,
        hold_s: float = HOLD_S,
    ) -> None:
        if voice not in VOICES:
            raise ValueError(f"voice must be one of {VOICES}")
        self._voice = voice
        self._model = model
        self._first_words = first_chunk_words
        self._later_words = later_chunk_words
        self._hold_s = hold_s
        # Clauses of the active turn not yet sent, whether its first request has gone,
        # and the timer that sends held words if the reply goes quiet.
        self._pending: list[str] = []
        self._first_sent = False
        self._release: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[AgentAudio | None] = asyncio.Queue()
        self._session_id = ""
        # Cancellation is per TURN, not global. A single shared flag meant that a new
        # turn calling submit() un-cancelled the previous turn, whose producer then
        # resumed pushing frames into the same queue — two producers, a queue that never
        # drained, and a turn that hung until its timeout. Recording cancelled turn ids
        # makes "turn A was interrupted" a fact that stays true.
        self._cancelled: set[str] = set()
        self._failures: dict[str, str] = {}
        self._active_turn = ""
        self._tasks: set[asyncio.Task[None]] = set()
        # Emission order within a turn: each clause waits for the one submitted before it.
        self._emit_chain: dict[str, asyncio.Event] = {}
        # Where the next clause starts within the reply, as the orchestrator joins it.
        self._turn_chars = 0
        self.submitted: list[str] = []

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
                "user-agent": "voice-order-support-agent/0.1",
            },
        )

    async def connect(self, session_id: str) -> None:
        """No socket to open. The HTTP client is created eagerly so the first clause does
        not pay for a cold TLS handshake."""
        self._session_id = session_id

    async def submit(self, text: str, turn_id: str) -> None:
        clause = text.strip()
        if not clause:
            return
        self.submitted.append(clause)
        if turn_id != self._active_turn:
            self._emit_chain.clear()
            self._turn_chars = 0
            self._pending = []
            self._first_sent = False
            self._cancel_release()
        self._active_turn = turn_id

        self._pending.append(clause)
        words = sum(len(c.split()) for c in self._pending)
        if words >= (self._later_words if self._first_sent else self._first_words):
            self._dispatch(turn_id)
        elif self._release is None:
            self._release = asyncio.create_task(self._release_after(turn_id))

    def _dispatch(self, turn_id: str) -> None:
        """Send the held clauses of this turn as one request."""
        self._cancel_release()
        text, self._pending = " ".join(self._pending), []
        if not text or turn_id in self._cancelled:
            return
        self._first_sent = True

        # Offsets are relative to the whole reply: each request starts after the text
        # before it and the single space the orchestrator joins clauses with — which is
        # also how held clauses are joined here, so merging moves no offset. Offsets that
        # restarted at zero every request could not say how much had been heard.
        base = self._turn_chars
        self._turn_chars += len(text) + 1

        # Synthesis runs in parallel — that is the latency win — but emission is strictly
        # in submission order. Requests used to be emitted in whatever order they
        # returned, and a short one returns first: the agent could say its second clause
        # before its first.
        previous = self._emit_chain.get(turn_id)
        emitted = asyncio.Event()
        self._emit_chain[turn_id] = emitted

        task = asyncio.create_task(self._synthesize(text, turn_id, base, previous, emitted))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _release_after(self, turn_id: str) -> None:
        """Send held words once the reply has gone quiet for a moment."""
        try:
            await asyncio.sleep(self._hold_s)
        except asyncio.CancelledError:
            return
        self._release = None
        if turn_id == self._active_turn:
            self._dispatch(turn_id)

    def _cancel_release(self) -> None:
        if self._release is not None:
            self._release.cancel()
            self._release = None

    async def _synthesize(
        self,
        clause: str,
        turn_id: str,
        base: int = 0,
        previous: asyncio.Event | None = None,
        emitted: asyncio.Event | None = None,
    ) -> None:
        try:
            await self._synthesize_clause(clause, turn_id, base, previous)
        finally:
            # Always release the next clause — on failure and on cancellation too — or one
            # bad clause would silence the rest of the reply.
            if emitted is not None:
                emitted.set()

    async def _synthesize_clause(
        self, clause: str, turn_id: str, base: int, previous: asyncio.Event | None
    ) -> None:
        try:
            response = await self._client.post(
                ENDPOINT,
                json={
                    "model": self._model,
                    "voice": self._voice,
                    "input": clause,
                    "response_format": "wav",
                },
            )
            if response.status_code != 200:
                metrics.inc("llm_errors_total", {"class": f"tts_http_{response.status_code}"})
                self._record_failure(turn_id, _classify(response.status_code))
                return
        except (httpx.TimeoutException, httpx.HTTPError):
            metrics.inc("llm_errors_total", {"class": "tts_timeout"})
            self._record_failure(turn_id, "timeout")
            return

        if turn_id in self._cancelled:
            # The shopper interrupted while this clause was being synthesized. It is
            # already paid for, but it must not be spoken.
            return

        pcm = _resample_24k_to_16k(_pcm_from_wav(response.content))
        total_frames = max(1, len(pcm) // (FRAME_SAMPLES * 2))

        if previous is not None:
            await previous.wait()
        if turn_id in self._cancelled:
            return

        for index in range(total_frames):
            if turn_id in self._cancelled:
                return
            start = index * FRAME_SAMPLES * 2
            frame = pcm[start : start + FRAME_SAMPLES * 2]
            if len(frame) < FRAME_SAMPLES * 2:
                frame = frame.ljust(FRAME_SAMPLES * 2, b"\x00")

            # No character alignment from this provider, so the offset is interpolated
            # across the clause. Clause-resolution rather than character-resolution —
            # still bounded by audio genuinely played, which is the property that matters.
            char_offset = base + round(len(clause) * index / total_frames)
            await self._queue.put(
                AgentAudio(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    pcm=frame,
                    char_offset=char_offset,
                )
            )

    def _record_failure(self, turn_id: str, kind: str) -> None:
        """Remember that a turn produced no audio, so the turn can say so.

        Counting the failure in metrics tells an operator. It tells the shopper nothing,
        and the shopper is the one sitting in silence watching a reply they cannot hear.
        The first failure of a turn is the honest one to report — later clauses of an
        already-failing turn add nothing.
        """
        self._failures.setdefault(turn_id, kind)
        while len(self._failures) > _FAILURE_MEMORY:
            self._failures.pop(next(iter(self._failures)))

    def synthesis_error(self, turn_id: str) -> str | None:
        """Why this turn produced no audio, read once and forgotten.

        Popped rather than read so a turn is reported exactly once, and so the map stays
        bounded by turns in flight rather than by session length.
        """
        return self._failures.pop(turn_id, None)

    async def drain(self, *, turn_id: str = "", timeout: float = 20.0) -> None:
        """Wait until every submitted clause has been synthesized and emitted.

        Synthesis is fire-and-forget per request, so the turn needs a way to know the
        audio is genuinely out before it declares itself finished. It is also the end of
        the reply, which is the moment to send whatever is still held.
        """
        if self._pending and (not turn_id or turn_id == self._active_turn):
            self._dispatch(self._active_turn)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if turn_id and turn_id in self._cancelled:
                return
            if not self._tasks and self._queue.empty():
                # One more beat so the pump can forward the final frame it just took.
                await asyncio.sleep(0.05)
                if not self._tasks and self._queue.empty():
                    return
            await asyncio.sleep(0.05)

    async def flush(self) -> None:
        """No-op: each request is synthesized whole, so there is never a partial
        generation waiting to be pushed out. Held text is sent by `drain`, which the turn
        calls when the reply is complete."""
        return None

    async def clear_buffer(self) -> None:
        """Stop emitting.

        There is no server-side interrupt on a REST endpoint, so in-flight synthesis is
        abandoned rather than cancelled upstream. The client-side `audio.flush` is what
        the shopper actually experiences, and it is unaffected. Words held back for
        merging are dropped: they were never going to be heard.
        """
        self._cancel_release()
        self._pending = []
        if self._active_turn:
            self._cancelled.add(self._active_turn)
        for task in list(self._tasks):
            task.cancel()
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()

    async def audio(self) -> AsyncIterator[AgentAudio]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self._cancel_release()
        for task in list(self._tasks):
            task.cancel()
        await self._queue.put(None)
        await self._client.aclose()
