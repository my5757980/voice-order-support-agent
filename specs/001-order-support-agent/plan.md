# Implementation Plan: Voice Order Support Agent

**Branch**: `001-order-support-agent` | **Date**: 2026-09-09 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-order-support-agent/spec.md`
**Governed by**: `.specify/memory/constitution.md` v1.1.0

---

## Summary

> **⚠️ Superseded on 2026-09-10.** This document records the stack as *planned*. The
> language model and speech synthesis both changed during implementation — Claude requires
> a paid key, and the ElevenLabs free quota was exhausted. **What actually runs is
> AssemblyAI STT + Groq `openai/gpt-oss-120b` + Groq / Canopy Labs Orpheus TTS.**
> The evidence and reasoning are in [research.md](./research.md) § R9. The sections below
> are kept because the decision trail is worth having, not because they describe the
> current system.


Build a browser-based voice agent for e-commerce order support on an orchestration loop we own
end to end. Audio is captured in the browser as 16 kHz PCM16 in 50 ms frames, relayed through our
backend to AssemblyAI's v3 streaming WebSocket, committed into turns, reasoned over by Claude with
schema-validated tools against a seeded order backend, and spoken back through a streaming TTS
socket that can be silenced within 100 ms when the shopper interrupts.

The architecture is a single Python asyncio process per session with five replaceable adapters
behind ports we define. The design's load-bearing decision is **speculative LLM dispatch on
high-confidence partial turns**: without it, the serial chain of STT commit + LLM first token +
TTS first byte cannot fit the constitution's 1000 ms p95 budget. With it, LLM latency hides behind
the shopper's trailing silence and the perceived chain collapses to STT commit + TTS first byte.

Two budgets are at genuine risk and are tracked as measurement gates in week 1, not assumed:
TTS time-to-first-byte, and LLM time-to-first-token with adaptive thinking enabled.

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript 5.x (browser)
**Primary Dependencies**: FastAPI + uvicorn, `websockets`, `httpx`, `pydantic` v2,
`structlog`; Vite + AudioWorklet (no UI framework). **Actual providers: AssemblyAI STT,
Groq LLM, Groq Orpheus TTS** — two credentials, not three.
**Storage**: In-memory per session; SQLite for durable memory and seeded order fixtures (single
file, zero-ops, sufficient for the demo and swappable behind a repository port)
**Testing**: `pytest` + `pytest-asyncio`, recorded STT frame fixtures, `freezegun`-style injected
clock, Playwright for one browser smoke test
**Target Platform**: Linux container (single region), current Chrome/Edge/Safari/Firefox
**Project Type**: Web application (browser frontend + Python backend)
**Performance Goals**: End of shopper speech → first agent audio p50 600 ms / p95 1000 ms;
interruption → silence p95 100 ms; 200 concurrent sessions
**Constraints**: 50 ms audio frames, PCM16 mono 16 kHz; bounded queues; no vendor type in core;
every downstream call cancellable; API keys never reach the browser
**Scale/Scope**: 4 user stories, 68 functional requirements, 10 tools, ~3 weeks to Sep 30 deadline

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Source: `.specify/memory/constitution.md` v1.0.0. Marked after Phase 1 design; see
[Post-Design Constitution Re-Check](#post-design-constitution-re-check) for the full gate-by-gate
evaluation and [Complexity Tracking](#complexity-tracking) for the two items carrying risk.

- [x] **I. Streaming** — PASS. 50 ms frames, streaming LLM, clause-level TTS handoff.
- [x] **II. Turn truth** — PASS. Turn state derives from AssemblyAI `Turn`; only
      `end_of_turn == true` commits; critical path uses unformatted `transcript`.
- [x] **III. Barge-in** — PASS. AudioWorklet ring buffer + TTS `clear_buffer`; configurable
      backchannel filter.
- [x] **IV. Ports & adapters** — PASS. Five ports, two adapters each (real + fake).
- [x] **V. Cancellation** — PASS. One `asyncio.TaskGroup` per turn; cancel propagates structurally.
- [x] **VI. Traceability** — PASS. `session_id`/`turn_id` threaded; seven mandatory spans.
- [x] **VII. Deterministic core** — PASS. Injected clock, recorded frame fixtures, fake adapters.
- [x] **Latency budgets** — PASS **with two at-risk components** — see Complexity Tracking.
- [x] **Security & privacy** — PASS. Keys stay server-side; browser never touches a vendor socket.
- [x] **Prohibitions** — PASS. All 15 reviewed; none violated.

---

## 1. High-Level System Architecture

### Component and data flow

```text
BROWSER                          │ BACKEND (one asyncio task group per session)
                                 │
┌──────────────────────────┐     │  ┌────────────────────────────────────────────┐
│ mic → AudioWorklet       │     │  │ SessionActor                               │
│   capture @16kHz PCM16   │     │  │  owns: turn state, memory, all child tasks  │
│   50 ms frames           │─────┼─▶│                                            │
└──────────────────────────┘  WS │  │   ┌──────────────┐                         │
                            binary│  │   │ IngestLoop   │──▶ AssemblyAI v3 WS     │
┌──────────────────────────┐     │  │   │ (sacred)     │◀── Turn / Begin / Error  │
│ AudioWorklet playback    │◀────┼──│   └──────┬───────┘                         │
│   ring buffer            │  WS │  │          │ TurnEvent                        │
│   zero-on-interrupt      │binary│  │          ▼                                 │
└──────────────────────────┘     │  │   ┌──────────────┐                         │
                                 │  │   │ Orchestrator │  single writer of state  │
┌──────────────────────────┐     │  │   └──┬────┬──────┘                         │
│ transcript pane (text)   │◀────┼──│      │    │                                │
└──────────────────────────┘  WS │  │      │    └──▶ ToolRegistry ──▶ SQLite      │
                             json │  │      ▼         (schema-validated)          │
                                 │  │   Claude (stream) ──tokens──▶ ClauseSplitter │
                                 │  │                                   │          │
                                 │  │                                   ▼          │
                                 │  │                          ElevenLabs WS ──────┼─▶ audio out
                                 │  │                          (clear_buffer)      │
                                 │  └────────────────────────────────────────────┘
```

**Why audio is relayed through our backend rather than browser → AssemblyAI directly.**
AssemblyAI supports a `token` query parameter precisely so browsers can connect directly, and that
path saves one network hop. We reject it anyway, for two reasons that outrank ~20 ms:

1. **The browser is untrusted.** If the browser holds the STT socket, it decides what a committed
   turn is and reports it to us. Turn boundaries drive memory writes and tool calls; a client that
   can forge a `Turn` can forge "the customer confirmed the return". Constitution principle II makes
   turn state authoritative, so it must live server-side.
2. **Observability and single-writer.** The orchestrator cannot emit `stt.turn` spans or enforce
   backpressure on a stream it never sees.

The relay costs one hop inside our own datacenter. We keep it and spend the budget elsewhere.

### Barge-in, end to end

Barge-in is the tightest path in the system (100 ms p95) and crosses every layer, so it is designed
as one sequence rather than per-component behaviour:

```text
t+0ms    AssemblyAI emits a partial Turn while agent_speaking == True
t+2ms    BackchannelFilter evaluates: word count ≥ 2 AND not all-backchannel?
           └─ suppressed → drop, agent keeps talking, emit metric, DONE
t+3ms    Orchestrator declares INTERRUPTED
t+4ms    ── three things happen concurrently, none waits for another ──
         (a) send {"type":"clear_buffer"} to TTS socket  → stops upstream synthesis
         (b) send {"op":"flush"} to browser over control WS → AudioWorklet zeroes its
             ring buffer on the very next render quantum (~2.7 ms at 48 kHz)
         (c) turn_scope.cancel() → LLM stream, tool calls, TTS pump all raise
             CancelledError structurally
t+~35ms  Browser audio silent (dominated by network RTT, not by our code)
t+~40ms  Orchestrator truncates the assistant message in memory to the
         *spoken-and-heard* prefix, computed from frames actually released by the
         playback worklet — never from what the LLM generated
```

The critical detail is **(b) does not depend on (a)**. Telling ElevenLabs to stop producing audio
does nothing about the ~200 ms of audio already sitting in the browser's ring buffer. Only the
client-side flush makes the shopper experience silence. Many voice agents get this wrong and
measure "interrupt latency" at the wrong boundary.

### Turn commitment

```text
Turn(end_of_turn=false)  → advisory. Drives: UI transcript, barge-in evaluation,
                            speculative LLM dispatch. NEVER written to memory.
Turn(end_of_turn=true)   → committed. Written to working memory, ordered by turn_order.
                            Critical path reads unformatted `transcript`.
Turn(turn_is_formatted)  → display and logs only. Never blocks LLM dispatch.
```

---

## 2. Technology Stack Recommendation

The user asked for a recommendation rather than supplying a stack, so each choice below states what
it buys against the constitution and what would change it.

### Backend — Python 3.11+ with FastAPI and uvicorn

**Why.** Constitution principle V (cancellation over completion) is the hardest thing to retrofit,
and Python's structured concurrency gives it for free: `asyncio.TaskGroup` scoped to a turn means
cancelling the turn cancels the LLM stream, the tool calls, and the TTS pump as one operation, with
`CancelledError` propagating through `async with` cleanup. That is exactly principle V's
"cancellation MUST propagate transitively", enforced by the language rather than by discipline.
FastAPI gives native WebSocket endpoints for both the audio and control sockets, and all three
vendors have first-class Python clients.

**What would change it.** If the team is materially faster in TypeScript, Node with
`AbortController` is a legitimate second choice and nothing else in this plan changes. Do not split
the difference — one language for the backend.

### Frontend — TypeScript + Vite + AudioWorklet, no UI framework

**Why.** The frontend has exactly two hard jobs, and both are AudioWorklet jobs:

- **Capture**: `getUserMedia` → `AudioContext` → AudioWorklet producing 50 ms PCM16 mono frames at
  16 kHz, matching AssemblyAI's documented input format with no server-side resampling.
- **Playback**: a ring buffer inside an AudioWorklet that we can zero synchronously on the next
  render quantum. This is non-negotiable and is the reason not to use an `<audio>` element or
  MediaSource — neither can be silenced within 100 ms, because both buffer ahead in the browser's
  audio pipeline where we cannot reach.

A UI framework buys nothing here: the interface is a mic button, a transcript pane, and a status
row. Vite alone keeps the build under a second and the moving parts near zero.

### LLM — Claude Opus 5 (`claude-opus-5`), streaming, effort `low`

**Why this model.** It is the current default and the strongest reasoning available; tool-call
correctness matters more here than in most applications, because a wrong tool call in this domain
creates a real return or cancels a real order.

**Why adaptive thinking stays ON, at low effort.** This is the most consequential and least
obvious configuration decision in the plan, so the reasoning is recorded here in full:

- On Claude Opus 5, thinking is adaptive by default. Thinking costs time before the first visible
  token, which is a direct latency risk for a voice agent.
- The tempting fix is `thinking: {"type": "disabled"}`, which Opus 5 accepts at effort `high` or
  below. **We reject it.** Disabled thinking on Opus 5 has a documented failure mode where the model
  writes a tool call into its *visible text* instead of emitting a `tool_use` block: the turn
  succeeds, the tool never runs, and no error is raised. In this feature that failure mode
  translates directly into the agent saying "I've started your return" when no return exists —
  a violation of FR-030 and SC-011, the one class of error the spec treats as zero-tolerance.
- The supported way to buy latency back is `output_config: {"effort": "low"}`, which reduces
  thinking depth and produces terser, less preambled output — which the voice persona wants anyway
  (VID-003 caps replies at two sentences).

So: omit `thinking` (adaptive is the Opus 5 default), set `effort: "low"`, and measure. If TTFT
still misses budget after prompt caching and speculative dispatch, the escalation is a **model**
decision for the user to make — Claude Haiku 4.5 does no thinking unless asked and has a 200K
context window, far more than a voice session needs. That is a deliberate tradeoff to be chosen
explicitly, not a silent downgrade.

**Other LLM settings, and what each buys:**

| Setting | Value | Why |
|---|---|---|
| `max_tokens` | `~320` | Replies are capped at two sentences by VID-003; a deliberately short output is one of the few valid reasons to set this low |
| `strict: true` on every tool | on | Guarantees `tool_use.input` validates against the schema — satisfies FR-047 at the provider boundary, before our own validation |
| `cache_control` on system + tools | ephemeral | Render order is tools → system → messages, so the stable prefix (persona, policy, 10 tool schemas) caches and stops being re-processed on every turn. Directly cuts TTFT |
| streaming | always | Required by principle I; also required to abort mid-generation |
| Tool loop | **manual**, not the SDK Tool Runner | The Tool Runner owns the loop; we need to abort it mid-stream on barge-in and emit our own spans per hop. Dropping to a manual loop for control the runner does not expose is the documented reason to do so, and it also keeps us off a beta dependency |

Parallel tool calls must return **all** `tool_result` blocks in a single user message — splitting
them silently trains the model out of parallel calling, which would cost latency on the multi-tool
turns ("where is it and can I still change the address?").

**Optional lever, not baseline:** fast mode (`speed: "fast"`, beta `fast-mode-2026-02-01`, Opus 5
only) raises output tokens/sec up to 2.5×. It has separate rate limits and premium pricing, so it is
a week-3 measurement-driven option, not a week-1 assumption.

### TTS — ElevenLabs Flash v2.5 over the streaming input WebSocket

**Why.** The selection criterion is not voice quality, it is whether the provider gives us a
**server-side interrupt primitive**. ElevenLabs' `stream-input` WebSocket does: the server accepts
`text`, `flush`, and `close_connection`, and sends `audio`, `timestamps`, `clear_buffer`, and
`error`. `clear_buffer` is exactly the barge-in operation, and `timestamps` (character alignment)
is what lets us compute *what the shopper actually heard* for the memory truncation in principle III
— without alignment data that truncation is guesswork.

Configuration: `model_id="eleven_flash_v2_5"` (fastest), `optimize_streaming_latency=4`, PCM output
to skip client-side decode, and one persistent socket opened at session start so TCP and TLS
setup never lands on the critical path.

**The honest risk.** ElevenLabs documents **200–500 ms initial latency** and 100–300 ms per
subsequent chunk. Our constitution budgets `tts.ttfb` at p95 250 ms with a 400 ms hard fail. The
documented range straddles our hard-fail line. This is tracked in Complexity Tracking with a week-1
measurement gate and three named mitigations. **Cartesia Sonic** is the pre-identified alternative
if the gate fails; it is named here as a candidate to evaluate, not as a verified-faster claim.

### Simulated backend — SQLite behind repository interfaces

**Why.** The spec requires simulated data behind the contracts real systems will use (TC-008). A
single SQLite file seeded from a fixtures module gives deterministic tests (principle VII), real
query latency characteristics, zero operational cost, and one obvious seam to replace with a real
order service later.

---

## 3. Project Structure

### Documentation (this feature)

```text
specs/001-order-support-agent/
├── plan.md              # This file (/sp.plan output)
├── spec.md              # Feature specification
├── research.md          # Phase 0 output — decisions, rationale, alternatives
├── data-model.md        # Phase 1 output — entities and state machines
├── quickstart.md        # Phase 1 output — run it in 5 minutes
├── contracts/           # Phase 1 output
│   ├── tools.schema.json      # The 10 tool contracts
│   └── websocket-protocol.md  # Browser ↔ backend message protocol
├── checklists/
│   └── requirements.md  # Spec quality checklist (passing)
└── tasks.md             # Phase 2 output (/sp.tasks — NOT created here)
```

### Source code (repository root)

```text
backend/
├── src/
│   ├── core/                      # NO vendor imports allowed here — enforced by test
│   │   ├── turn_state.py          # Turn state machine (pure, injected clock)
│   │   ├── orchestrator.py        # Single writer of conversation state
│   │   ├── barge_in.py            # Backchannel filter + interruption decision
│   │   ├── clause_splitter.py     # Token stream → speakable clauses
│   │   ├── memory.py              # Working / summary / durable layers
│   │   ├── events.py              # Typed pipeline events
│   │   └── ports.py               # The five Protocol definitions
│   ├── adapters/
│   │   ├── stt/
│   │   │   ├── assemblyai.py      # v3 WebSocket adapter
│   │   │   └── fake.py            # Replays recorded frame fixtures
│   │   ├── llm/
│   │   │   ├── claude.py          # Streaming + manual tool loop
│   │   │   └── fake.py            # Scripted token streams
│   │   ├── tts/
│   │   │   ├── elevenlabs.py      # stream-input WS + clear_buffer
│   │   │   └── fake.py            # Emits silence frames with real timing
│   │   └── store/
│   │       ├── sqlite.py          # Seeded order/catalogue/policy repository
│   │       └── memory_store.py    # In-process, for tests
│   ├── tools/
│   │   ├── registry.py            # Schema validation + authz + idempotency
│   │   ├── definitions.py         # The 10 tools, latency-classified
│   │   └── handlers/              # One module per tool
│   ├── session/
│   │   ├── actor.py               # SessionActor — owns the TaskGroup
│   │   ├── ingest.py              # The sacred audio loop
│   │   └── lifecycle.py           # KeepAlive, Terminate, reconnect
│   ├── obs/
│   │   ├── spans.py               # The seven mandatory spans
│   │   ├── metrics.py             # Counters and histograms
│   │   └── redaction.py           # PII redaction before any sink
│   ├── config.py                  # Typed, validated, fail-fast at startup
│   └── app.py                     # FastAPI: WS endpoints + token minting + static
└── tests/
    ├── unit/                      # Core logic, no I/O
    ├── contract/                  # Fixtures vs. real vendor schemas
    ├── integration/               # Full pipeline with fake adapters
    └── fixtures/stt/              # Recorded Turn/Begin/Error frame sequences

frontend/
├── src/
│   ├── worklets/
│   │   ├── capture.worklet.ts     # 50 ms PCM16 @16 kHz framing
│   │   └── playback.worklet.ts    # Ring buffer, zero-on-flush
│   ├── transport.ts               # Audio WS + control WS
│   ├── ui.ts                      # Mic button, transcript pane, status
│   └── main.ts
└── tests/
```

**Structure Decision**: Web application (Option 2). The browser is not a thin client — it owns two
real-time audio worklets — so it gets a first-class `frontend/` tree rather than a static folder
inside the backend. `core/` is import-guarded by a test that fails if any vendor SDK is imported
below it, which is how principle IV stops being a good intention.

---

## 4. Key Modules and Responsibilities

| Module | Owns | Explicitly does NOT |
|---|---|---|
| **SessionActor** (`session/actor.py`) | The per-session `TaskGroup`, child task lifetimes, session-scoped cancellation | Make decisions about conversation content |
| **Orchestrator** (`core/orchestrator.py`) | The *only* writes to turn state and memory; dispatch decisions; interruption declaration | Touch sockets, vendor SDKs, or the clock directly |
| **IngestLoop** (`session/ingest.py`) | Read audio frames from the browser WS, write to the STT socket. Nothing else | Log to disk, handle transcripts, take locks, call the LLM |
| **TurnStateMachine** (`core/turn_state.py`) | Legal transitions: `IDLE → LISTENING → COMMITTED → THINKING → SPEAKING → (IDLE \| INTERRUPTED)` | Perform I/O; it is pure and clock-injected |
| **BargeInFilter** (`core/barge_in.py`) | Backchannel token set, minimum word count, grace window, confirmation-context override | Stop audio itself — it returns a decision |
| **SpeechRecognizer** adapter | AssemblyAI v3 socket, reconnect with backoff, translate `Turn`/`Begin`/`Termination`/`Error` into our domain events | Leak an AssemblyAI type past its own boundary |
| **LanguageModel** adapter | Streaming Claude calls, the manual tool loop, prompt-cache breakpoints, abort on cancel | Decide *whether* to dispatch — that is the orchestrator's call |
| **SpeechSynthesizer** adapter | Persistent TTS socket, clause submission, `clear_buffer`, character-alignment timestamps | Decide what to say |
| **ToolRegistry** (`tools/registry.py`) | Schema validation, server-side authorization, latency class, idempotency keys derived from `turn_id` | Trust any argument the model proposed |
| **ConversationMemory** (`core/memory.py`) | Working (verbatim committed turns) / summary (compacted, preserving order refs + actions) / durable (open cases, display name, last 3 summaries) | Serve as an authorization or order-state source — both are always re-read |

---

## 5. Real-Time Pipeline Design

### Partial vs. committed turns

| Signal | Drives | Forbidden from |
|---|---|---|
| `Turn(end_of_turn=false)` | UI transcript, barge-in evaluation, speculative dispatch | Memory, tool execution, committed decisions |
| `Turn(end_of_turn=true)` | Memory write, tool execution, real dispatch | — |
| `turn_is_formatted=true` | Display pane, logs, escalation transcript | Blocking the critical path |

### Speculative LLM dispatch — the design's central latency lever

Without speculation the chain is serial and does not fit:

```text
STT commit 300ms → LLM TTFT ~450ms → TTS TTFB ~250ms  = ~1000ms  (at budget, no headroom)
```

With speculation, the LLM runs during the shopper's trailing silence, so by the time the turn
commits the first tokens already exist:

```text
STT commit 300ms → [LLM already streaming] → TTS TTFB ~250ms = ~550-600ms  (hits p50)
```

**Dispatch rules** (all four must hold — this is a strict gate, because a speculative dispatch that
fires too eagerly burns tokens and, worse, can produce a stale answer):

1. `end_of_turn_confidence` ≥ `SPECULATIVE_THRESHOLD` (start at 0.7, configurable).
2. The partial transcript differs from the last dispatched partial by ≥ 3 characters.
3. No state-changing tool is a plausible outcome — speculation is **read-only**. A speculative turn
   may call `get_order`; it may never call `create_return`. Enforced in the ToolRegistry by a
   `speculative` flag on the call context, not by prompting.
4. At most one speculative call is in flight per turn; a new one cancels the previous.

**Commitment rules:**

- Speculative output goes to a **staging buffer**, never to TTS and never to memory.
- On `end_of_turn == true`, if the committed transcript matches the speculated one, the staged
  stream is promoted and TTS begins immediately. If it differs, the staged stream is discarded and
  cancelled, and a real dispatch runs.
- If the shopper keeps speaking, the speculative task is cancelled by the same `turn_scope` as
  everything else.

This is constitution Architectural Principle 8 ("speculative execution is allowed, commitment is
not") implemented literally.

### Interruption and backchannel filter

```python
# core/barge_in.py — pure decision, no I/O
def should_interrupt(text, agent_speaking, now, last_spoke_at, awaiting_confirmation) -> Decision:
    if awaiting_confirmation and is_affirmative_or_negative(text):
        return Decision.INTERRUPT        # FR-010: a "yes" answering a confirmation is a real answer
    if not agent_speaking and (now - last_spoke_at) > GRACE_WINDOW:
        return Decision.INTERRUPT        # not speaking, not in grace → normal turn
    if len(text.split()) < MIN_WORDS or all_backchannel(text):
        return Decision.SUPPRESS         # FR-009
    return Decision.INTERRUPT
```

`awaiting_confirmation` is checked **first** and deliberately. It is the collision the spec called
out (FR-010 vs FR-009): a bare "yes" is a backchannel during narration but a real answer during a
return confirmation. Ordering the confirmation check first is the whole fix, and it is a one-line
behaviour that deserves its own unit test.

`BACKCHANNELS` is configuration, and "yes"/"no" are **not** in the default set (a bare "yes" in a
booking-style flow is load-bearing) — matching the constitution's requirement that the set be
domain-configurable.

### Clause-level TTS handoff

Waiting for a full sentence before synthesizing adds the sentence's generation time to TTFB. The
`ClauseSplitter` emits on `,`, `;`, `—`, `.`, `?`, `!` and on a 12-word soft cap, so the first
speakable fragment reaches TTS typically within 2–3 tokens of the model starting. It never splits
inside a number, currency amount, or order reference — those must be spoken as one prosodic unit
(VID-007), so the splitter holds a small lookahead and suppresses a break inside a digit run.

### Holding phrases for slow tools

Tools are declared `fast` (< 300 ms, may block the reply) or `slow`. On dispatching a `slow` tool the
orchestrator starts a 400 ms timer; if the tool has not returned when it fires, a holding phrase is
sent to TTS immediately ("let me pull that up"). The phrase is **cancelled** if the tool returns
first, so a fast-returning slow tool never produces a pointless "one moment". Holding phrases are
drawn from a small rotating set so a multi-tool conversation does not repeat one string.

---

## 6. State Management Approach

**One writer, one place.** All conversation state lives inside the `SessionActor` and is mutated
only by the `Orchestrator`. Adapters emit events onto bounded queues; they never mutate. This is the
constitution's Architectural Principle 1 and it removes every lock from the design — there is no
shared mutable state across tasks, so there is nothing to contend on.

**Turn state machine** (`core/turn_state.py`), pure and clock-injected:

```text
IDLE ──speech──▶ LISTENING ──end_of_turn──▶ COMMITTED ──dispatch──▶ THINKING
                     │                                                  │
                     │◀────────── interrupt ──────────┐                 │ first audio
                     │                                │                 ▼
                     └──silence 30s──▶ CLOSING        └──────────── SPEAKING
                                                                        │
                                        INTERRUPTED ◀──barge-in─────────┘
                                             │
                                             └──truncate memory──▶ LISTENING
```

**Queues are bounded, and the degradation is defined per queue** — the constitution forbids
unbounded queues but also forbids silent blocking on the audio thread:

| Queue | Bound | On full |
|---|---|---|
| Audio frames (browser → STT) | 100 frames (5 s) | Drop **oldest**, increment `audio_frames_dropped`, WARN. Never block ingest |
| Partial turns | 1 (latest wins) | Replace — an old partial has no value |
| Committed turns | 8 | Block the *producer* and log; committed turns are never dropped |
| TTS clauses | 32 | Block the LLM consumer — natural backpressure on generation |

**Cancellation scopes.** Two nested scopes: `session_scope` (whole session) and `turn_scope`
(recreated per turn). Barge-in cancels `turn_scope` only. Because every child task is created inside
the turn's `TaskGroup`, cancellation is structural rather than something each call site must
remember — the language enforces principle V.

**Memory bounds.** Working memory holds the last 12 committed turns verbatim. Beyond that,
compaction summarizes older turns while preserving verbatim every order reference, every action
taken, and every reference number issued. Total prompt context is capped; a 30-minute session and a
2-minute session send comparably sized requests.

---

## 7. Error Handling & Fallback Strategy

Every error is classified into exactly one of the constitution's four classes before anything reacts
to it. Unclassified defaults to `Fatal` for the turn.

| Failure | Class | Response | Shopper hears |
|---|---|---|---|
| STT socket drops | `Degraded` | Reconnect with jittered backoff, preserve state, drop gap audio | "Sorry — I missed that, could you say it again?" (FR-056) |
| STT low confidence | `Transient` | Ask for repeat **once**, specifically; then change strategy | "I didn't catch the order number" (VID-013) |
| LLM 429 / 5xx | `Transient` | One retry with backoff, holding phrase covers it | Nothing — hidden by filler |
| LLM timeout > 3 s | `Degraded` | Abandon, apologise, offer escalation | "I'm having trouble with that — want me to get a person?" |
| TTS socket drops | `Degraded` | Reconnect; if it fails, fall back to on-screen text | Text appears; agent says nothing rather than lying |
| Read-only tool fails | `Transient` | One retry (FR-058), then escalate | "I can't reach the order system right now" |
| **State-changing tool fails** | `Fatal` (turn) | **Never retry blindly** — re-read state first (FR-059) | "That did **not** go through" — explicitly, per FR-030 |
| Tool schema validation fails | `Fatal` (turn) | Do not execute; return structured error to the model | Agent rephrases or escalates |
| Config invalid | `Fatal-Session` | **Crash at startup** — never a runtime default | Never starts |
| Browser mic revoked | `Degraded` | Text fallback (FR-060) | On-screen explanation |

**Circuit breakers.** After 3 consecutive failures a provider opens for a 30 s cooldown and the
fallback path runs. At conversational rates, retrying a dead provider is a denial-of-service against
ourselves.

**The rule that overrides all of the above:** the agent never claims success for an action that did
not succeed (FR-030 / SC-011), and never goes silent without explanation (FR-053). Every branch in
this table terminates in either speech or on-screen text — never in nothing.

---

## 8. Observability Plan

**The seven mandatory spans**, per the constitution, each tagged `session_id` + `turn_id`:

`audio.capture` · `stt.turn` · `llm.ttft` · `llm.complete` · `tool.<name>` · `tts.ttfb` ·
`playback.start`

Plus one this feature adds, because it is the number the spec's SC-006 is graded on and it cannot be
derived from the seven: `playback.stop` (interrupt → silence).

**Metrics** (Prometheus-style, exported from the start — not added in week 3):

| Metric | Type | Why it exists |
|---|---|---|
| `turn_latency_e2e_seconds` | histogram | The headline budget; measured, never summed from components |
| `barge_in_latency_seconds` | histogram | SC-006 |
| `backchannel_suppressed_total` | counter | SC-007 — and it catches an over-eager filter |
| `stt_reconnects_total` | counter | Silent degradation detector |
| `llm_errors_total{class}` | counter | Circuit-breaker input |
| `tool_failures_total{name}` | counter | Which simulated tool is flaky |
| `audio_frames_dropped_total` | counter | Backpressure actually firing |
| `session_outcome_total{resolved\|escalated\|abandoned}` | counter | SC-001 containment |
| `speculative_dispatch_total{promoted\|discarded}` | counter | Is speculation paying for itself? |

**Logging.** `structlog` JSON to stdout, always carrying `session_id` and `turn_id`. Raw audio bytes
are never logged. Transcript text is DEBUG-only and DEBUG is off in production, enforced by a config
assertion at startup rather than by convention. `obs/redaction.py` runs before any sink.

**The one dashboard that matters for the demo:** a live per-turn waterfall of the seven spans. When
someone says "it felt slow", the answer is a bar chart, not a theory. Per the constitution, a
latency regression that cannot be attributed to a span means the missing span is the first bug.

---

## 9. Deployment Strategy (hackathon demo)

**Shape:** one Docker image. Multi-stage build — Vite builds the frontend to static assets, FastAPI
serves them alongside the two WebSocket endpoints. One image, one process, one port. No nginx, no
separate frontend host, no CORS, no service mesh.

**Where: Replit — and this is a hard constraint, not a preference.** The lablab.ai Hackathon Rule
Book states: *"Demo Application Platform: Use Streamlit, Replit, or Vercel"*, and *"Failure to
adhere to submission guidelines may result in a lower score or exclusion from the hackathon."*
Fly.io — this plan's original choice — is **not on that list**, so it is out regardless of its
technical merits.

Of the three permitted platforms, only one fits this architecture:

| Platform | Verdict | Why |
|---|---|---|
| **Replit** | ✅ **Chosen** | Runs a persistent process, supports long-lived WebSocket servers, native Python/FastAPI. Our app is one process holding two inbound sockets and two outbound vendor sockets per session — this is the only permitted option that supports that shape |
| Vercel | ❌ | Serverless. No long-lived WebSocket server and no persistent per-session process. Could host the static frontend only, which would put the backend off-platform and make the submitted Application URL misleading |
| Streamlit | ❌ | Script-rerun execution model, hostile to raw WebSockets and AudioWorklet audio paths |

**Latency consequence, stated honestly**: we lose the region pinning that Fly.io would have given
us. We cannot place the process next to the vendor endpoints, so geographic round-trip time becomes
a variable we do not control. This makes the week-1 latency gates (T005/T006/T033) *more* important,
not less — they must be measured **on the deployed Replit instance**, not only locally, because a
budget that passes on localhost and fails in deployment is not a budget we have met.

**Config:** environment variables only, validated by a Pydantic settings model that **fails at
startup** on anything missing or malformed. `ASSEMBLYAI_API_KEY`, `ANTHROPIC_API_KEY`,
`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, plus tuning values. `.env.example` is committed with
placeholders; `.env` is git-ignored. No key is ever sent to the browser — the browser authenticates
to *us* with a short-lived session token and never opens a vendor socket.

**Scaling:** vertical for the demo. A session is a single asyncio task group with a bounded memory
footprint; one modest machine carries the demo comfortably, and the 200-concurrent target (NFR-009)
is a load-test exercise, not a demo-day requirement.

**Demo safety:** a `DEMO_MODE` flag seeds a known fixture customer with two orders in different
states (one shipped, one not) so the disambiguation flow and the cancel-refusal flow are both
demonstrable on cue. Live demos fail on unseeded data far more often than on bugs.

### Delivery plan to Sep 30 (~3 weeks from 2026-09-09)

| Week | Goal | Done when |
|---|---|---|
| **1** (Sep 9–15) | **Walking skeleton + latency truth.** Browser capture → backend → AssemblyAI → committed turn → hardcoded reply → TTS → playback. All seven spans emitting. | A full turn round-trips, and we have **measured** `stt.turn`, `llm.ttft`, `tts.ttfb` — the two at-risk gates are resolved with numbers, not estimates |
| **2** (Sep 16–22) | **The agent.** Claude with streaming + manual tool loop, 5 read-only tools, working memory, barge-in + backchannel filter, speculative dispatch. | US1 (order status) works end to end, interruption measurably under 200 ms |
| **3** (Sep 23–27) | **Safety and polish.** State-changing tools with the confirmation ritual, escalation, error table, holding phrases, demo seeding. | US2 + US3 work; the confirmation ritual cannot be bypassed |
| **3.5** (Sep 28–30) | **Submission assets.** Public GitHub repo + MIT LICENSE, deployed Replit URL, cover image (16:9), video (MP4), slides (PDF), title and descriptions, tags. | Every mandatory artifact exists and the submission is filed |
| **Sep 30, 8:00 PM PKT** | **Submission deadline — hard stop** | — |

**The build deadline is Sep 27, not Sep 30.** The submission artifacts are mandatory under the Rule
Book and take real time — a video presentation and slide deck are not a final-evening task. Treating
Sep 30 as a build day is the single most likely way to produce working software that scores badly or
is excluded outright. Manual submission is available for only 6 hours post-deadline, and only with
prior organizer approval and a valid reason — it is not a buffer to plan around.

**Cut list, in order, if week 3 compresses:** US4 (product/policy Q&A) → durable cross-session memory
→ summary-memory compaction (cap the session instead) → address changes. **Never cut:** the
confirmation ritual, the never-claim-false-success rule, or barge-in. Those three are what separate
a voice agent from a demo that should not touch real orders.

### Mandatory submission artifacts (lablab.ai Rule Book)

Verified against the hackathon page and the Rule Book on 2026-09-09. Every row is **mandatory**;
missing any of them risks a lower score or exclusion, independent of how good the software is.

| Artifact | Hard requirement | Owner task |
|---|---|---|
| Project title | Clear and descriptive | T081 |
| Short + long description | Character/word limits respected — "critical for evaluation" | T081 |
| Technology & category tags | Correct categorisation | T081 |
| Cover image | **PNG or JPG, 16:9** | T082 |
| Video presentation | **MP4 — mandatory** | T083 |
| Slide presentation | **PDF — mandatory** | T084 |
| Public GitHub repository | **Public**, MIT-compliant | T085 |
| Demo application platform | **Streamlit, Replit, or Vercel only** | T077 |
| Application URL | Live and interactive for judges | T077 |

Additional rules that bind this project:

- **Originality**: submissions must be original and MIT-compliant. A `LICENSE` file (MIT) is
  therefore a build artifact, not paperwork.
- **Team size**: 1–6 people.
- **Registration**: enrol on lablab.ai **and** join the lablab.ai Discord. AssemblyAI credits require
  signing up through the hackathon's own link with cookies accepted.
- **Ethics**: plagiarism or vote-gaming is immediate disqualification.

### Judging criteria and how this design addresses each

The four criteria are weighted equally in the published rubric. Building well is necessary but not
sufficient — two of the four are about communication and framing.

| Criterion | Where we stand | The gap to close |
|---|---|---|
| **Application of Technology** | Strong. We took the harder Realtime STT path and own the whole orchestration loop: turn commitment, speculative dispatch, barge-in, cancellation. | Demonstrate the depth explicitly — the measured latency waterfall is the evidence, and it belongs in the video |
| **Business value** | Strong. Post-purchase support is the highest-volume, lowest-value contact category in e-commerce, and the spec quantifies containment. | Lead the pitch with the deflection number, not the architecture |
| **Presentation** | **Not yet built.** No video, slides, or cover image exist. | Phase 3.5 — and it is graded as heavily as the code |
| **Originality** | **At risk — see below.** | Reposition the headline; see the note in spec.md § Positioning |

---

## Post-Design Constitution Re-Check

Re-evaluated after the design above. Gate-by-gate:

| Gate | Verdict | Evidence in this plan |
|---|---|---|
| **I. Streaming, buffer nothing** | **PASS** | 50 ms frames; streaming LLM; clause-level TTS handoff rather than sentence-level |
| **II. Turn is unit of truth** | **PASS** | Partial/committed table; `turn_order` canonical; unformatted transcript on the critical path; turn state server-side because the browser is untrusted |
| **III. Barge-in first-class** | **PASS** | Full end-to-end sequence designed; client-side flush independent of server `clear_buffer`; configurable backchannel set; confirmation-context override ordered first |
| **IV. Ports and adapters** | **PASS** | Five ports, real + fake adapter each; `core/` import-guarded by a test that fails on any vendor import |
| **V. Cancellation over completion** | **PASS** | Nested `TaskGroup` scopes; cancellation is structural, not per-call-site discipline |
| **VI. Every turn traceable** | **PASS** | Seven mandatory spans plus `playback.stop`; nine metrics; `session_id`/`turn_id` on every log line |
| **VII. Deterministic core** | **PASS** | Injected clock; recorded STT fixtures; fake adapters for all three vendors; no live vendor call on the critical-path tests |
| **Latency budgets** | **PASS, two components at risk** | Budgets stated in Technical Context; speculative dispatch is the mechanism that makes p50 reachable. `tts.ttfb` and `llm.ttft` carry measurement risk — see Complexity Tracking |
| **Security & privacy** | **PASS** | No key reaches the browser; browser never opens a vendor socket; recording off by default; transcripts DEBUG-only with a startup assertion; redaction before any sink |
| **Prohibitions (all 15)** | **PASS** | No Voice Agent API (own loop); no utterance buffering; ingest loop does nothing but capture+write; no un-cancellable call; memory truncated to heard audio via TTS alignment; no client key; no raw audio logged; schema + server-side authz on every tool; never claim false success; `core/` vendor-free; unformatted transcript on critical path; all queues bounded; no non-streaming critical-path dependency; fakes not live APIs in tests; no latency regressions accepted |

**No gate fails.** Two components carry measurement risk rather than design risk, tracked below.

## Complexity Tracking

> Filled because the Constitution Check flags two at-risk latency components. Neither is a design
> violation; both are vendor-capability uncertainties that must be resolved by measurement in week 1.

| Violation / Risk | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| **`tts.ttfb` may exceed its 400 ms hard fail.** ElevenLabs documents 200–500 ms initial latency; our budget is p95 250 ms / hard fail 400 ms. | A streaming TTS with a server-side `clear_buffer` interrupt primitive and character-alignment timestamps is required by principle III — alignment is what makes "truncate memory to what was heard" exact rather than guessed. | Non-streaming TTS rejected: violates principle I outright and cannot be interrupted. **Mitigations before escalating:** (1) persistent pre-warmed socket so connect/TLS is off the critical path, (2) `optimize_streaming_latency=4`, (3) PCM output to skip client decode, (4) clause-level submission so synthesis starts on the first fragment. **If p95 still exceeds 400 ms in week 1:** evaluate Cartesia Sonic, or amend the constitutional budget with measured evidence — per governance, an unenforceable rule must be automated or amended out, not quietly ignored. |
| **`llm.ttft` with adaptive thinking may exceed its 800 ms hard fail.** Opus 5 runs adaptive thinking by default, which precedes the first visible token. | Disabling thinking is the obvious fix and is **rejected on safety grounds**: with thinking disabled, Opus 5 can write a tool call into visible text instead of emitting a `tool_use` block — the call silently never runs. In this domain that means the agent says "I've started your return" when no return exists, violating FR-030/SC-011, the spec's zero-tolerance failure. | **Mitigations, in order:** (1) `effort: "low"`, (2) prompt caching on the stable tools+system prefix, (3) speculative dispatch, which hides TTFT behind trailing silence entirely and is the real fix. **If still over budget:** switching to Claude Haiku 4.5 (no thinking by default, 200K context — ample here) is a *user decision* to be made explicitly with measurements in hand, not a silent downgrade. Fast mode on Opus 5 is a second option at premium pricing. |

Two smaller decisions worth recording, neither a violation:

| Decision | Simpler alternative rejected because |
|---|---|
| Relay audio through our backend rather than browser → AssemblyAI direct (costs ~1 hop) | Direct connection would make the untrusted browser the authority on turn boundaries, which drive memory writes and tool calls. Principle II requires turn state be authoritative; a forgeable `Turn` means a forgeable confirmation |
| Manual tool loop instead of the SDK Tool Runner | The runner owns the loop; we must abort mid-stream on barge-in and emit per-hop spans. Needing control the runner does not expose is the documented reason to drop to a manual loop, and it keeps us off a beta dependency |
