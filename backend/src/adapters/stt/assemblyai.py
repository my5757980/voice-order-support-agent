"""AssemblyAI v3 streaming speech-to-text.

The one pinned vendor, and the reason this project exists in its current shape: we took
the Realtime STT path rather than the managed Voice Agent API, so turn-taking is ours to
own. Everything AssemblyAI-shaped stops at this file — no provider type crosses into
`core/` (principle IV, enforced by tests/unit/test_core_purity.py).

Verified contract (docs, 2026-09-09):
    endpoint  wss://streaming.assemblyai.com/v3/ws
    auth      Authorization header server-side (the `token` query param is for browsers,
              which never connect here — see research.md R2)
    audio     PCM16 signed little-endian, mono, 16 kHz, 50-1000 ms chunks
    client →  Terminate, ForceEndpoint, KeepAlive, UpdateConfiguration
    server →  Begin, Turn, Termination, Error
    Turn      turn_order, end_of_turn, turn_is_formatted, transcript,
              end_of_turn_confidence
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import websockets

from src.core.events import UserPartial, UserTurnCommitted
from src.obs import metrics

ENDPOINT = "wss://streaming.assemblyai.com/v3/ws"
KEEPALIVE_INTERVAL_S = 20.0
MAX_BACKOFF_S = 8.0


class AssemblyAISpeechRecognizer:
    """Implements the SpeechRecognizer port."""

    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int = 16_000,
        speech_model: str = "universal-3-5-pro",
        format_turns: bool = True,
        end_of_turn_confidence_threshold: float = 0.4,
        min_turn_silence_ms: int = 160,
        max_turn_silence_ms: int = 400,
    ) -> None:
        self._api_key = api_key
        # Every one of these is configuration, never a literal in business logic
        # (principle II) — and `UpdateConfiguration` makes them live-tunable.
        self._params = {
            "sample_rate": sample_rate,
            "speech_model": speech_model,
            "format_turns": str(format_turns).lower(),
            "end_of_turn_confidence_threshold": end_of_turn_confidence_threshold,
            "min_turn_silence": min_turn_silence_ms,
            "max_turn_silence": max_turn_silence_ms,
        }
        self._ws: Any = None
        self._session_id = ""
        self._events: asyncio.Queue[UserPartial | UserTurnCommitted | None] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._closing = False
        self._degraded = False

    @property
    def degraded(self) -> bool:
        """True while reconnecting. Surfaced to the orchestrator as `Degraded`."""
        return self._degraded

    # -- lifecycle ---------------------------------------------------------

    async def connect(self, session_id: str) -> None:
        self._session_id = session_id
        self._closing = False
        await self._open()
        self._tasks = [
            asyncio.create_task(self._receive_loop()),
            asyncio.create_task(self._keepalive_loop()),
        ]

    async def _open(self) -> None:
        url = f"{ENDPOINT}?{urlencode(self._params)}"
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": self._api_key},
            max_queue=64,
        )
        self._degraded = False

    async def send_audio(self, frame: bytes) -> None:
        """Write one 50 ms PCM16 frame.

        Dropped rather than buffered when the socket is down: a queue that grows during
        a reconnect converts a network problem into a latency problem, and the audio is
        stale by the time it would be sent anyway.
        """
        if self._ws is None or self._degraded:
            metrics.inc("audio_frames_dropped_total")
            return
        try:
            await self._ws.send(frame)
        except Exception:
            metrics.inc("audio_frames_dropped_total")
            await self._schedule_reconnect()

    async def keep_alive(self) -> None:
        if self._ws is not None and not self._degraded:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "KeepAlive"}))

    async def force_endpoint(self) -> None:
        """Manually commit the current turn. Held in reserve for short utterances where
        silence-based endpointing is slow ("yes"); not used by default."""
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "ForceEndpoint"}))

    async def update_configuration(self, **changes: object) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "UpdateConfiguration", **changes}))

    async def close(self) -> None:
        self._closing = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "Terminate"}))
            with contextlib.suppress(Exception):
                await self._ws.close()
        for task in self._tasks:
            task.cancel()
        await self._events.put(None)

    # -- receive -----------------------------------------------------------

    async def events(self) -> AsyncIterator[UserPartial | UserTurnCommitted]:
        while True:
            item = await self._events.get()
            if item is None:
                return
            yield item

    async def _receive_loop(self) -> None:
        while not self._closing:
            try:
                assert self._ws is not None
                async for raw in self._ws:
                    if isinstance(raw, bytes):
                        continue
                    self._dispatch(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._closing:
                    return
                # Disconnects are expected, not exceptional (constitution error rule 5).
                await self._schedule_reconnect()

    def _dispatch(self, message: dict[str, object]) -> None:
        match message.get("type"):
            case "Turn":
                self._on_turn(message)
            case "Begin":
                pass
            case "Termination":
                self._closing = True
            case "Error":
                metrics.inc("llm_errors_total", {"class": "stt"})

    def _on_turn(self, message: dict[str, object]) -> None:
        transcript = str(message.get("transcript", "")).strip()
        if not transcript:
            return

        turn_order = int(message.get("turn_order", 0))
        confidence = float(message.get("end_of_turn_confidence", 0.0))
        formatted = bool(message.get("turn_is_formatted", False))

        if message.get("end_of_turn"):
            self._events.put_nowait(
                UserTurnCommitted(
                    session_id=self._session_id,
                    turn_id="",  # assigned by the orchestrator, which owns turn identity
                    text=transcript,
                    turn_order=turn_order,
                    end_of_turn_confidence=confidence,
                    # Formatted text is for display and logs only. It must never block
                    # dispatch, so the critical path reads `text` above.
                    text_formatted=transcript if formatted else None,
                )
            )
        else:
            self._events.put_nowait(
                UserPartial(
                    session_id=self._session_id,
                    turn_id="",
                    text=transcript,
                    turn_order=turn_order,
                    end_of_turn_confidence=confidence,
                )
            )

    # -- reconnection ------------------------------------------------------

    async def _schedule_reconnect(self) -> None:
        if self._closing or self._degraded:
            return
        self._degraded = True
        metrics.inc("stt_reconnects_total")

        backoff = 0.25
        while not self._closing:
            await asyncio.sleep(backoff + random.uniform(0, backoff / 2))  # jittered
            try:
                await self._open()
                # Audio captured during the gap is gone. It is never replayed as if it
                # were current speech (FR-057) — the orchestrator asks the shopper to
                # repeat instead.
                return
            except Exception:
                backoff = min(MAX_BACKOFF_S, backoff * 2)

    async def _keepalive_loop(self) -> None:
        while not self._closing:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
            await self.keep_alive()
