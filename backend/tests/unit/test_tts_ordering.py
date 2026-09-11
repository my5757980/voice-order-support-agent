"""Clauses are spoken in the order they were written, whatever order they come back in.

Each clause is its own request to Orpheus, and they run in parallel for latency. They
used to be emitted in completion order — and a short clause completes first, so the
agent could say its second clause before its first. Offsets used to restart at zero for
every clause, so nothing could say how far into a reply the shopper had got.
"""

from __future__ import annotations

import asyncio
import io
import json
import wave

import httpx

from src.adapters.tts.groq_orpheus import GroqSpeechSynthesizer


def _wav(value: int, seconds: float) -> bytes:
    """A constant-valued 24 kHz mono clip, so every frame says which clause it came from."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        n = int(24_000 * seconds)
        w.writeframes(value.to_bytes(2, "little", signed=True) * n)
    return buf.getvalue()


def _tts(delays: dict[str, float], values: dict[str, int]) -> GroqSpeechSynthesizer:
    async def handler(request: httpx.Request) -> httpx.Response:
        clause = json.loads(request.content)["input"]
        await asyncio.sleep(delays[clause])
        return httpx.Response(200, content=_wav(values[clause], 0.3))

    tts = GroqSpeechSynthesizer(api_key="test", voice="hannah")
    tts._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return tts


async def _collect(tts: GroqSpeechSynthesizer, turn: str) -> list:
    frames = []

    async def pump() -> None:
        async for f in tts.audio():
            frames.append(f)

    task = asyncio.create_task(pump())
    await tts.drain(turn_id=turn, timeout=5)
    await asyncio.sleep(0.05)
    task.cancel()
    return frames


def _clause_of(frame) -> int:  # type: ignore[no-untyped-def]
    return int.from_bytes(frame.pcm[:2], "little", signed=True)


async def test_a_slow_first_clause_is_still_spoken_first() -> None:
    first, second = "Your headphones are in Memphis,", "arriving Monday."
    tts = _tts(delays={first: 0.25, second: 0.0}, values={first: 1000, second: 2000})
    await tts.submit(first, "t1")
    await tts.submit(second, "t1")
    frames = await _collect(tts, "t1")

    order = [_clause_of(f) for f in frames]
    assert order, "nothing was emitted"
    assert order == sorted(order), "the second clause was emitted before the first"
    assert set(order) == {1000, 2000}


async def test_offsets_run_across_the_whole_reply() -> None:
    """The second clause starts where the orchestrator's join puts it: after the first
    clause and one space. Not back at zero."""
    first, second = "It shipped Tuesday,", "and arrives Monday."
    tts = _tts(delays={first: 0.0, second: 0.0}, values={first: 1000, second: 2000})
    await tts.submit(first, "t1")
    await tts.submit(second, "t1")
    frames = await _collect(tts, "t1")

    second_frames = [f for f in frames if _clause_of(f) == 2000]
    assert second_frames[0].char_offset == len(first) + 1
    offsets = [f.char_offset for f in frames]
    assert offsets == sorted(offsets)
    assert max(offsets) < len(first) + 1 + len(second)


async def test_a_failed_clause_does_not_silence_the_rest() -> None:
    """Each clause waits for the one before it. A clause whose request fails must still
    release the next, or one bad request would leave the rest of the reply unsaid."""
    first, second = "This one fails,", "this one is spoken."

    async def handler(request: httpx.Request) -> httpx.Response:
        clause = json.loads(request.content)["input"]
        if clause == first:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, content=_wav(2000, 0.3))

    tts = GroqSpeechSynthesizer(api_key="test", voice="hannah")
    tts._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await tts.submit(first, "t1")
    await tts.submit(second, "t1")
    frames = await _collect(tts, "t1")

    assert frames and all(_clause_of(f) == 2000 for f in frames)
    assert tts.synthesis_error("t1") == "unavailable"
