"""A reply is sent to Orpheus in as few requests as the ear allows.

Groq's free tier allows this model ten requests a minute — a limit its response headers do
not report. One request per clause spent twelve of them in the first sixteen seconds of
the demo (a greeting, one answer, one interruption), and the voice cut out on the second
turn with ninety requests of the day's allowance still unused.

What must hold: the opening words still go out on their own so the voice starts quickly;
the rest of the reply is merged; words said before a slow tool call are not kept back
until it returns; an interruption drops what was held; and none of it moves an offset.
"""

from __future__ import annotations

import asyncio
import io
import json
import wave

import httpx

from src.adapters.tts.groq_orpheus import GroqSpeechSynthesizer


def _wav(seconds: float = 0.2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(b"\x01\x00" * int(24_000 * seconds))
    return buf.getvalue()


def _tts(**policy: float) -> tuple[GroqSpeechSynthesizer, list[str]]:
    """A synthesizer whose requests are recorded instead of sent."""
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content)["input"])
        return httpx.Response(200, content=_wav())

    tts = GroqSpeechSynthesizer(api_key="test", voice="hannah", **policy)  # type: ignore[arg-type]
    tts._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return tts, requests


async def _speak(tts: GroqSpeechSynthesizer, turn: str, clauses: list[str]) -> list:
    """Submit a reply the way the turn does — clause by clause, then drain."""
    frames = []

    async def pump() -> None:
        async for f in tts.audio():
            frames.append(f)

    task = asyncio.create_task(pump())
    for clause in clauses:
        await tts.submit(clause, turn)
    await tts.drain(turn_id=turn, timeout=5)
    await asyncio.sleep(0.05)
    task.cancel()
    return frames


ANSWER = [
    "I can look up your recent orders,",
    "track shipments,",
    "start returns,",
    "cancel or change pending orders.",
    "What would you like assistance with?",
]
GREETING = ["Hi,", "I'm an automated assistant for order support.", "What can I help you with?"]


async def test_the_opening_words_go_alone_and_the_rest_goes_together() -> None:
    tts, requests = _tts()
    await _speak(tts, "t1", ANSWER)
    assert requests == [ANSWER[0], " ".join(ANSWER[1:])]


async def test_an_opening_too_short_to_start_on_waits_for_the_next_clause() -> None:
    """ "Hi," on its own is a whole request that buys the shopper nothing."""
    tts, requests = _tts()
    await _speak(tts, "t1", GREETING)
    assert requests == [" ".join(GREETING[:2]), GREETING[2]]


async def test_the_demo_opening_fits_well_inside_ten_requests_a_minute() -> None:
    """Greeting, an answer, and the reply after an interruption: twelve requests before."""
    tts, requests = _tts()
    await _speak(tts, "greeting", GREETING)
    await _speak(tts, "answer", ANSWER)
    await _speak(tts, "after-interrupt", [
        "Your most recent order is the desk lamp,",
        "placed on the tenth of September,",
        "and it is still processing.",
        "Do you want details for that order?",
    ])
    assert len(requests) <= 6


async def test_merging_moves_no_offset() -> None:
    """The merged request starts exactly where the orchestrator's join puts its first
    clause: after the opening words and one space."""
    tts, _ = _tts()
    frames = await _speak(tts, "t1", ANSWER)
    starts = sorted({f.char_offset for f in frames})
    assert len(ANSWER[0]) + 1 in starts
    offsets = [f.char_offset for f in frames]
    assert offsets == sorted(offsets)
    assert max(offsets) < len(" ".join(ANSWER))


async def test_words_before_a_slow_tool_call_are_not_held_until_it_returns() -> None:
    """The model says something, then calls a tool that takes a while. What it already
    said must be spoken while the tool runs, not after — so held words are sent once the
    reply goes quiet, without waiting for the turn to drain."""
    tts, requests = _tts(hold_s=0.05)
    await tts.submit("Let me check that order for you.", "t1")
    await tts.submit("One moment.", "t1")
    await asyncio.sleep(0.2)  # the tool call; nobody has drained
    assert requests == ["Let me check that order for you.", "One moment."]


async def test_an_interruption_drops_the_words_still_held() -> None:
    tts, requests = _tts(hold_s=0.05)
    await tts.submit("Your order shipped on Tuesday,", "t1")
    await tts.submit("and it arrives", "t1")
    await asyncio.sleep(0.01)  # the opening words are on their way when the shopper cuts in
    await tts.clear_buffer()
    await asyncio.sleep(0.2)   # longer than the hold: nothing held may leak out afterwards
    await tts.drain(turn_id="t1", timeout=1)
    assert not any("and it arrives" in r for r in requests)


async def test_a_long_reply_is_not_held_in_one_request() -> None:
    tts, requests = _tts(later_chunk_words=8)
    await _speak(tts, "t1", [
        "Your order shipped on Tuesday,",
        "it cleared the Memphis hub this morning,",
        "it is out for delivery now,",
        "and it should reach you before six.",
    ])
    assert len(requests) >= 3
    assert all(len(r.split()) <= 16 for r in requests)
