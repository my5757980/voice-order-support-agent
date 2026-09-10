"""Synthetic speech.

Produces audible PCM16 at 16 kHz so barge-in is demonstrable before any TTS key exists —
you cannot test interrupting an agent that makes no sound. The tone is a soft, speech-band
sine with an envelope, deliberately quiet: it should be obviously synthetic, never mistaken
for the real voice in a demo recording.

Crucially it emits frames **at realtime pace**. A fake that dumped a second of audio
instantly would hide every ordering bug in the barge-in path, which is the one path this
fake exists to exercise.
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import AsyncIterator

from src.core.events import AgentAudio

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 800          # 50 ms, matching the capture format
MS_PER_CHAR = 55             # roughly conversational pace
AMPLITUDE = 0.16             # quiet on purpose


def _frame(phase: float, freq: float, n: int = FRAME_SAMPLES) -> tuple[bytes, float]:
    samples = []
    step = 2 * math.pi * freq / SAMPLE_RATE
    for i in range(n):
        # Gentle envelope so frame boundaries do not click.
        env = 0.5 - 0.5 * math.cos(2 * math.pi * i / n)
        samples.append(int(AMPLITUDE * env * math.sin(phase) * 32767))
        phase += step
    return struct.pack(f"<{n}h", *samples), phase


class FakeSpeechSynthesizer:
    """Implements the SpeechSynthesizer port."""

    def __init__(self, *, realtime: bool = True, ms_per_char: int = MS_PER_CHAR) -> None:
        self._queue: asyncio.Queue[AgentAudio | None] = asyncio.Queue()
        self._realtime = realtime
        self._ms_per_char = ms_per_char
        # Cancellation is per TURN, not global. A single shared flag meant that a new
        # turn calling submit() un-cancelled the previous turn, whose producer then
        # resumed pushing frames into the same queue — two producers, a queue that never
        # drained, and a turn that hung until its timeout. Recording cancelled turn ids
        # makes "turn A was interrupted" a fact that stays true.
        self._cancelled: set[str] = set()
        self._active_turn = ""
        self._session_id = ""
        self.submitted: list[str] = []

    async def connect(self, session_id: str) -> None:
        self._session_id = session_id

    async def submit(self, text: str, turn_id: str) -> None:
        """Synthesize one clause. Character offsets are carried on every frame, which is
        what lets the orchestrator compute exactly how much was heard."""
        self.submitted.append(text)
        self._active_turn = turn_id

        frames = max(1, round(len(text) * self._ms_per_char / 50))
        phase = 0.0
        # Vary pitch per clause so consecutive clauses are audibly distinct.
        freq = 210 + (len(text) % 5) * 18

        for i in range(frames):
            if turn_id in self._cancelled:
                # This turn was interrupted mid-clause. Stop producing for it — and
                # stay stopped, however many later turns begin.
                return
            pcm, phase = _frame(phase, freq)
            char_offset = round(len(text) * i / frames)
            await self._queue.put(
                AgentAudio(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    pcm=pcm,
                    char_offset=char_offset,
                )
            )
            if self._realtime:
                await asyncio.sleep(0.05)

    async def drain(self, *, turn_id: str = "", timeout: float = 20.0) -> None:
        """Same contract as the real synthesizers, so tests exercise the real path."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if turn_id and turn_id in self._cancelled:
                return
            if self._queue.empty():
                await asyncio.sleep(0.05)
                if self._queue.empty():
                    return
            await asyncio.sleep(0.05)

    async def flush(self) -> None:
        return None

    async def clear_buffer(self) -> None:
        """Server-side interrupt. Stops production; it does NOT silence what the browser
        has already buffered — that is the client-side flush, fired independently."""
        if self._active_turn:
            self._cancelled.add(self._active_turn)
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def audio(self) -> AsyncIterator[AgentAudio]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        await self._queue.put(None)
