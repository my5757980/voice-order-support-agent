"""FastAPI application: two WebSocket endpoints, token minting, metrics, static assets.

One process, one port. No CORS, no reverse proxy, no second deploy target — the
constraint that made this shape right (Replit permits only a single persistent process)
also made it the simplest thing that works.

`DEMO_MODE` runs the whole pipeline on fake LLM and TTS adapters against the real tool
registry and the real seeded store. The four tool gates, the confirmation ritual and
barge-in are all genuinely exercised without a single API key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from src.adapters import factory
from src.adapters.store.seed import DEMO_CUSTOMER_ID, seed
from src.adapters.store.sqlite import OrderRepository, connect
from src.obs import metrics
from src.session.actor import SessionActor
from src.session.tokens import TokenStore
from src.tools.handlers import build_registry


def _load_env_file() -> None:
    """Populate os.environ from a local .env, without overriding what is already set.

    quickstart.md tells you to copy .env.example to .env and run uvicorn. Nothing was
    reading that file, so the keys were silently ignored and the app fell back to the
    scripted adapters — working, but not what anyone following the instructions expected,
    and with no error to explain it.

    Real environment variables win. On Replit the keys arrive as Secrets, and a stray
    .env in the image must never shadow them.
    """
    for candidate in (Path(".env"), Path("../.env"), Path(__file__).resolve().parents[2] / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if value and key not in os.environ:
                os.environ[key] = value
        return


_load_env_file()

if factory.demo_mode():
    # A mode that quietly replaces every provider with a scripted stand-in is the one
    # configuration that can look completely healthy while proving nothing. `.env.example`
    # shipped it switched on, so this is not hypothetical.
    print(
        "WARNING: DEMO_MODE is on - speech, model and synthesis are all scripted. "
        "Set DEMO_MODE=false for a real conversation.",
        flush=True,
    )

DB_PATH = os.environ.get("DATABASE_PATH", "./data/orders.db")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="Voice Order Support Agent", version="0.1.0")
tokens = TokenStore()

_conn = connect(DB_PATH)
seed(_conn)  # idempotent; guarantees the demo fixtures exist on every boot
_registry = build_registry()

# Live actors, keyed by session. Bounded by the number of open sockets.
_sessions: dict[str, SessionActor] = {}
_recognizers: dict[str, Any] = {}
_pending: dict[str, dict[str, Any]] = {}


async def _pump_transcripts(session_id: str, recognizer: Any, actor: SessionActor) -> None:
    """Forward transcripts into the orchestrator.

    Partials drive the live pane and the barge-in decision; only a committed turn
    reaches memory (principle II).
    """
    with contextlib.suppress(Exception):
        async for event in recognizer.events():
            if getattr(event, "__class__", None).__name__ == "UserTurnCommitted":
                await actor.on_committed(event.text)
            else:
                # Pass the provider's end-of-turn confidence through — it is what
                # decides whether the model can be started before the shopper stops.
                await actor.on_partial_scored(
                    event.text, getattr(event, "end_of_turn_confidence", 0.0)
                )


@app.post("/api/session")
async def create_session() -> dict[str, str]:
    """Mint a short-lived, session-scoped token.

    This is the only credential the browser ever receives. No vendor API key appears in
    any response body or any WebSocket frame (NFR-017).
    """
    token, session_id = tokens.mint(DEMO_CUSTOMER_ID)
    return {"token": token, "session_id": session_id}


@app.get("/api/health")
async def health() -> dict[str, object]:
    # Surfacing the adapter modes means a demo never has to guess whether it is running
    # on real providers or on the scripted fallbacks.
    return {
        "ok": True,
        "adapters": factory.modes(),
        "tools": len(_registry.registered()),
        "sessions": len(_sessions),
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    return metrics.render()


@app.get("/api/metrics")
async def metrics_snapshot() -> dict[str, object]:
    return {"percentiles": metrics.snapshot()}


# -- control socket --------------------------------------------------------


@app.websocket("/ws/control")
async def control_socket(websocket: WebSocket) -> None:
    token = websocket.query_params.get("session", "")
    claim = tokens.claim(token)
    if claim is None:
        await websocket.close(code=4401, reason="invalid or expired session token")
        return
    session_id, customer_id = claim
    await websocket.accept()

    async def send_control(message: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await websocket.send_text(json.dumps(message))

    _pending.setdefault(session_id, {})["control"] = send_control
    await _try_start(session_id, customer_id)
    await send_control({"type": "session.ready", "session_id": session_id})

    actor = await _await_actor(session_id)
    if actor is not None:
        await _start_session_tasks(session_id, actor, send_control)

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_client_message(session_id, json.loads(raw))
    except (WebSocketDisconnect, json.JSONDecodeError, RuntimeError):
        pass
    finally:
        await _teardown(session_id)


# -- audio socket ----------------------------------------------------------


@app.websocket("/ws/audio")
async def audio_socket(websocket: WebSocket) -> None:
    token = websocket.query_params.get("session", "")
    claim = tokens.claim(token)
    if claim is None:
        await websocket.close(code=4401, reason="invalid or expired session token")
        return
    session_id, customer_id = claim
    await websocket.accept()

    async def send_audio(pcm: bytes) -> None:
        with contextlib.suppress(Exception):
            await websocket.send_bytes(pcm)

    _pending.setdefault(session_id, {})["audio"] = send_audio
    await _try_start(session_id, customer_id)

    try:
        while True:
            frame = await websocket.receive_bytes()
            metrics.inc("audio_frames_received_total")
            recognizer = _recognizers.get(session_id)
            if recognizer is not None:
                # The sacred loop: read a frame, write a frame. Nothing else happens
                # here — no logging I/O, no transcript handling, no lock.
                await recognizer.send_audio(frame)
            # With no recognizer there is nothing to transcribe with, so the frame is
            # counted and dropped rather than buffered. A queue that grows with no
            # consumer is exactly the failure the bounded-queue rule exists to prevent.
    except (WebSocketDisconnect, RuntimeError):
        pass


# -- session lifecycle -----------------------------------------------------


async def _try_start(session_id: str, customer_id: str) -> None:
    """Start the actor once both sockets have arrived."""
    parts = _pending.get(session_id, {})
    if session_id in _sessions or "control" not in parts or "audio" not in parts:
        return

    tts, _tts_mode = factory.build_tts()
    await tts.connect(session_id)
    llm, _llm_mode = factory.build_llm()

    actor = SessionActor(
        session_id=session_id,
        customer_id=customer_id,
        repo=OrderRepository(_conn, customer_id),
        registry=_registry,
        llm=llm,
        tts=tts,
        send_control=parts["control"],
        send_audio=parts["audio"],
    )
    _sessions[session_id] = actor




async def _await_actor(session_id: str, timeout_s: float = 5.0) -> SessionActor | None:
    """Wait for both sockets to arrive so the actor exists."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        actor = _sessions.get(session_id)
        if actor is not None:
            return actor
        await asyncio.sleep(0.02)
    return None


async def _start_session_tasks(session_id: str, actor: SessionActor, send_control: Any) -> None:
    """Attach speech recognition and speak the greeting.

    Deliberately called from the control socket rather than from `_try_start`. Every
    task that mutates turn state now starts in one place, which extends Architectural
    Principle 1's single-writer rule from state to tasks. It also removes a real
    fragility: tasks spawned from two different socket handlers can end up awaiting the
    same queue from two event loops, and the waiter is then never woken.
    """
    recognizer, mode = factory.build_stt()
    if recognizer is not None:
        await recognizer.connect(session_id)
        _recognizers[session_id] = recognizer
        asyncio.create_task(_pump_transcripts(session_id, recognizer, actor))
    await send_control({"type": "status", "state": "ready", "message": f"speech: {mode}"})

    # The AI disclosure belongs in the opening turn, not in the model's first reply
    # (VID-019, FR-004). greet() returns once the turn is started, not once it is spoken.
    await actor.greet()

async def _handle_client_message(session_id: str, message: dict[str, Any]) -> None:
    actor = _sessions.get(session_id)
    if actor is None:
        return

    match message.get("type"):
        case "text.input":
            # Text fallback enters the pipeline as a committed turn — every voice
            # capability must be reachable without a microphone (FR-060, NFR-013).
            await actor.on_committed(str(message.get("text", "")))
        case "playback.progress":
            actor.on_playback_progress(
                str(message.get("turn_id", "")), int(message.get("frames_played", 0))
            )
        case "mic.state":
            pass
        case "session.end":
            await _teardown(session_id)


async def _teardown(session_id: str) -> None:
    actor = _sessions.pop(session_id, None)
    _pending.pop(session_id, None)
    recognizer = _recognizers.pop(session_id, None)
    if recognizer is not None:
        with contextlib.suppress(Exception):
            await recognizer.close()
    if actor is not None:
        metrics.inc("session_outcome_total", {"outcome": "resolved"})
        await actor.close()


# -- static assets ---------------------------------------------------------

if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:

    @app.get("/", response_class=HTMLResponse)
    async def dev_hint() -> str:
        return (
            "<h1>Voice Order Support — backend running</h1>"
            "<p>The frontend is not built yet. Run <code>npm run dev</code> in "
            "<code>frontend/</code> and open http://localhost:5173, or "
            "<code>npm run build</code> to have it served from here.</p>"
            "<p><a href='/api/health'>/api/health</a> · <a href='/metrics'>/metrics</a></p>"
        )
