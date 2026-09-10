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
    ) -> None:
        if voice not in VOICES:
            raise ValueError(f"voice must be one of {VOICES}")
        self._voice = voice
        self._model = model
        self._queue: asyncio.Queue[AgentAudio | None] = asyncio.Queue()
        self._session_id = ""
        # Cancellation is per TURN, not global. A single shared flag meant that a new
        # turn calling submit() un-cancelled the previous turn, whose producer then
        # resumed pushing frames into the same queue — two producers, a queue that never
        # drained, and a turn that hung until its timeout. Recording cancelled turn ids
        # makes "turn A was interrupted" a fact that stays true.
        self._cancelled: set[str] = set()
        self._active_turn = ""
        self._tasks: set[asyncio.Task[None]] = set()
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
        self._active_turn = turn_id

        task = asyncio.create_task(self._synthesize(clause, turn_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _synthesize(self, clause: str, turn_id: str) -> None:
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
                return
        except (httpx.TimeoutException, httpx.HTTPError):
            metrics.inc("llm_errors_total", {"class": "tts_timeout"})
            return

        if turn_id in self._cancelled:
            # The shopper interrupted while this clause was being synthesized. It is
            # already paid for, but it must not be spoken.
            return

        pcm = _resample_24k_to_16k(_pcm_from_wav(response.content))
        total_frames = max(1, len(pcm) // (FRAME_SAMPLES * 2))

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
            char_offset = round(len(clause) * index / total_frames)
            await self._queue.put(
                AgentAudio(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    pcm=frame,
                    char_offset=char_offset,
                )
            )

    async def drain(self, *, turn_id: str = "", timeout: float = 20.0) -> None:
        """Wait until every submitted clause has been synthesized and emitted.

        Synthesis is fire-and-forget per clause, so the turn needs a way to know the
        audio is genuinely out before it declares itself finished.
        """
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
        """No-op: each clause is synthesized as a complete request, so there is never a
        partial generation waiting to be pushed out."""
        return None

    async def clear_buffer(self) -> None:
        """Stop emitting.

        There is no server-side interrupt on a REST endpoint, so in-flight synthesis is
        abandoned rather than cancelled upstream. The client-side `audio.flush` is what
        the shopper actually experiences, and it is unaffected.
        """
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
        for task in list(self._tasks):
            task.cancel()
        await self._queue.put(None)
        await self._client.aclose()
