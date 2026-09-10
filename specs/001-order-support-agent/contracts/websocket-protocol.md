# Contract: Browser ↔ Backend WebSocket Protocol

**Feature**: `001-order-support-agent` | **Date**: 2026-09-09

Two sockets, deliberately separate. Splitting them is not ceremony — it is what keeps the audio path
free of head-of-line blocking behind JSON control traffic, and it lets the barge-in flush travel on
an uncongested channel.

| Socket | Path | Payload | Direction |
|---|---|---|---|
| Audio | `/ws/audio?session=<token>` | Binary frames | Bidirectional |
| Control | `/ws/control?session=<token>` | JSON text | Bidirectional |

**Authentication**: `<token>` is a short-lived, session-scoped token minted by `POST /api/session`
against the shopper's authenticated store session. **No vendor API key is ever sent to the browser,
and the browser never opens a vendor socket** (constitution Security & Privacy; NFR-017). The token
is single-use, bound to one `session_id`, and expires in 60 seconds if unused.

---

## Audio socket

### Browser → backend

Raw binary frames. No envelope, no base64 — encoding overhead on the hottest path buys nothing.

| Property | Value |
|---|---|
| Format | PCM16 signed little-endian |
| Channels | 1 (mono) |
| Sample rate | 16 000 Hz |
| Frame size | 50 ms = 800 samples = **1600 bytes** |
| Cadence | One frame every 50 ms while the mic is live |

Frames map 1:1 onto what the backend writes to AssemblyAI, so no resampling occurs on either side.

### Backend → browser

Raw binary PCM16 frames of synthesized speech, same format. The playback worklet appends them to its
ring buffer.

**Backpressure**: the backend's inbound audio queue is bounded at 100 frames (5 s). On overflow the
**oldest** frame is dropped and `audio_frames_dropped_total` increments. The ingest loop never
blocks — a blocked ingest loop is a violation of the constitution's "sacred audio loop" rule.

---

## Control socket

### Backend → browser

| `type` | Payload | Meaning |
|---|---|---|
| `session.ready` | `{session_id}` | Sockets live; capture may begin |
| `transcript.partial` | `{text, turn_order}` | Advisory. Renders live text. **Never** authoritative |
| `transcript.committed` | `{text, text_formatted, turn_order, speaker}` | A committed turn |
| `agent.speaking` | `{turn_id}` | Playback started — `playback.start` boundary |
| `agent.done` | `{turn_id, status: "complete"｜"interrupted"}` | Turn finished |
| **`audio.flush`** | `{turn_id}` | **Zero the playback ring buffer immediately** |
| `display.reference` | `{kind, value, label}` | Tracking number / order ref / link — shown, never spoken (FR-020, VID-006) |
| `status` | `{state, message?}` | Reconnecting, degraded, closing |
| `error` | `{code, message}` | Shopper-safe text only — never a stack trace or vendor error |

### Browser → backend

| `type` | Payload | Meaning |
|---|---|---|
| `mic.state` | `{active: bool}` | Mic muted / unmuted / permission revoked |
| `text.input` | `{text}` | Text fallback (FR-060, NFR-013) — enters the pipeline as a committed turn |
| `session.end` | `{}` | Shopper ended the session |
| `playback.progress` | `{turn_id, frames_played}` | Feeds `heard_prefix_len` for truncation |

---

## The barge-in sequence

This is the protocol's reason for existing, so it is specified rather than left to implementation.

```text
Backend decides INTERRUPT
  │
  ├─▶ TTS socket:      {"type": "clear_buffer"}        stops upstream synthesis
  ├─▶ Control socket:  {"type": "audio.flush", ...}    stops what the shopper hears
  └─▶ turn_scope.cancel()                              stops LLM, tools, TTS pump
```

**These three fire concurrently and none waits for another.** The ordering matters and is a common
mistake worth stating plainly: `clear_buffer` stops the *provider* from producing more audio, but it
does nothing about the ~200 ms already sitting in the browser's ring buffer. Only `audio.flush`
makes the shopper experience silence. A design that sends `clear_buffer` and waits for
acknowledgement before flushing the client will measure a correct-looking interrupt latency and
still talk over its user.

**Budget**: `audio.flush` sent → ring buffer zeroed ≤ 100 ms p95, dominated by network RTT rather
than by our code. The worklet zeroes on its next render quantum (~2.7 ms at 48 kHz).

**Truncation**: on receiving the flush, the browser reports `playback.progress`; the backend
combines `frames_played` with TTS character-alignment timestamps to compute `heard_prefix_len`, and
truncates the agent turn in memory to exactly that. Memory records what was **heard**, never what
was generated (constitution principle III).

---

## Lifecycle and failure

| Event | Behaviour |
|---|---|
| Control socket drops | Session survives 10 s; browser reconnects with the same token; audio continues |
| Audio socket drops | Session enters `degraded`; on reconnect the shopper is asked to repeat (FR-056) |
| Both drop | Session terminates. Completed state-changing actions remain committed; unconfirmed actions do not occur |
| Backend STT reconnect | `status` sent with `state: "degraded"`; gap audio is dropped, never replayed as current speech (FR-057) |
| Idle 30 s | Two re-prompts (6 s, 18 s), then `status` closing and graceful termination (FR-006) |

## Invariants

1. Audio frames are exactly 1600 bytes. A short frame is a bug, not a partial read to accommodate.
2. `transcript.partial` never drives memory, tool execution, or any committed decision.
3. `audio.flush` is never coalesced, delayed, or batched behind other control messages.
4. No message on either socket ever carries a vendor API key, a raw vendor error, or a stack trace.
5. The browser is never trusted for turn boundaries — it reports playback progress and mic state,
   and nothing else that affects conversation state.
