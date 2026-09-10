"""ElevenLabs streaming text-to-speech.

Chosen for two capabilities rather than for voice quality (research.md R4):

  `clear_buffer`  — a server-side interrupt primitive. Barge-in needs the provider to
                    stop producing, and a TTS without this cannot be interrupted at all.
  `timestamps`    — character alignment. This is what makes "truncate memory to what the
                    shopper actually heard" exact rather than an estimate from elapsed
                    time (principle III).

Known risk, tracked as a measurement gate (T005): the vendor documents 200-500 ms initial
latency against our 250 ms p95 / 400 ms hard-fail budget. Four mitigations are applied
here — persistent pre-warmed socket, `optimize_streaming_latency=4`, PCM output to skip
client-side decode, and clause-level submission so synthesis starts on the first fragment
rather than the first sentence.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from src.core.events import AgentAudio
from src.obs import metrics

ENDPOINT = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"


class ElevenLabsSpeechSynthesizer:
    """Implements the SpeechSynthesizer port."""

    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        optimize_streaming_latency: int = 4,
        output_format: str = "pcm_16000",
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._latency = optimize_streaming_latency
        # PCM rather than mp3: the browser plays it straight into the ring buffer with
        # no decode step, and decode is latency we cannot buy back.
        self._output_format = output_format

        self._ws: Any = None
        self._session_id = ""
        self._turn_id = ""
        self._queue: asyncio.Queue[AgentAudio | None] = asyncio.Queue()
        self._receiver: asyncio.Task[None] | None = None
        self._char_cursor = 0
        self._closing = False

    async def connect(self, session_id: str) -> None:
        """Open at session start.

        Pre-warming matters: TCP and TLS setup is 100+ ms, and doing it on the first
        clause would put it squarely inside the time-to-first-audio budget.
        """
        self._session_id = session_id
        self._closing = False
        url = ENDPOINT.format(voice_id=self._voice_id) + (
            f"?model_id={self._model_id}"
            f"&optimize_streaming_latency={self._latency}"
            f"&output_format={self._output_format}"
        )
        self._ws = await websockets.connect(
            url, additional_headers={"xi-api-key": self._api_key}, max_queue=64
        )
        # Handshake frame; voice settings tuned for conversational pace.
        await self._ws.send(
            json.dumps(
                {
                    "text": " ",
                    "voice_settings": {"stability": 0.4, "similarity_boost": 0.7, "speed": 1.05},
                }
            )
        )
        self._receiver = asyncio.create_task(self._receive_loop())

    async def submit(self, text: str, turn_id: str) -> None:
        """Send one speakable clause."""
        if self._ws is None:
            return
        self._turn_id = turn_id
        with contextlib.suppress(Exception):
            await self._ws.send(json.dumps({"text": text + " ", "try_trigger_generation": True}))

    async def flush(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"flush": True}))

    async def clear_buffer(self) -> None:
        """Server-side interrupt.

        This stops the provider producing more audio. It does NOT silence what the
        browser has already buffered — that needs the client-side `audio.flush`, and the
        two are fired concurrently rather than in sequence. Waiting for this to
        acknowledge before flushing the client is the classic mistake: it measures a
        correct-looking interrupt latency while the agent keeps talking.
        """
        if self._ws is None:
            return
        with contextlib.suppress(Exception):
            await self._ws.send(json.dumps({"type": "clear_buffer"}))
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._char_cursor = 0

    async def audio(self) -> AsyncIterator[AgentAudio]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def _receive_loop(self) -> None:
        try:
            assert self._ws is not None
            async for raw in self._ws:
                message = json.loads(raw) if isinstance(raw, str) else None
                if message is None:
                    continue

                if message.get("audio"):
                    pcm = base64.b64decode(message["audio"])
                    # Character alignment tells us where in the text this audio sits,
                    # which is what makes the heard-prefix computation exact.
                    alignment = message.get("normalizedAlignment") or message.get("alignment")
                    if alignment and alignment.get("chars"):
                        self._char_cursor += len(alignment["chars"])
                    await self._queue.put(
                        AgentAudio(
                            session_id=self._session_id,
                            turn_id=self._turn_id,
                            pcm=pcm,
                            char_offset=self._char_cursor,
                        )
                    )
                elif message.get("error"):
                    metrics.inc("llm_errors_total", {"class": "tts"})
                elif message.get("isFinal"):
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                metrics.inc("llm_errors_total", {"class": "tts_disconnect"})

    async def close(self) -> None:
        self._closing = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"text": ""}))  # close_connection
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._receiver is not None:
            self._receiver.cancel()
        await self._queue.put(None)
