---
description: "Task list for Voice Order Support Agent implementation"
---

# Tasks: Voice Order Support Agent

**Input**: Design documents from `/specs/001-order-support-agent/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅
**Deadline**: 30 September 2026 (planning date 2026-09-09 → ~3 working weeks)

**Tests**: Included. The constitution makes test-backed behaviour non-negotiable for the turn state
machine, interruption logic, and tool contracts, so test tasks are first-class here rather than
optional.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: `[US1]`–`[US4]` maps to the user stories in spec.md. Setup, foundational, and polish
  tasks carry no story label.
- Every task carries a detail block: **What** / **Done when** (acceptance criteria) / **Effort** /
  **Critical path**.

**Effort**: `S` = 1–2 h · `M` = 2–3 h · `L` = 3–4 h. Nothing exceeds 4 h; anything that would has
already been split.

**Critical path** = required for a working demo on 30 Sep. `No` means valuable but droppable —
see the [Cut List](#cut-list-ordered).

**⚠️ = resolves one of the two at-risk latency components** identified in plan.md Complexity
Tracking (`tts.ttfb`, `llm.ttft`).

> **Note on ordering**: the phases below follow the weekly delivery plan in plan.md, as requested,
> rather than the one-phase-per-user-story default. The mapping is clean: Week 1 = setup +
> foundational, Week 2 = US1, Week 3 = US2/US3/polish. Task IDs use `T001` (no hyphen) to stay
> parseable by `/sp.analyze` and `/sp.implement`.

---

## Phase 1: Walking Skeleton + Latency Truth (Week 1 — Sep 9–15)

**Goal**: One full turn round-trips through real infrastructure, every span is measured, and the two
at-risk latency budgets are resolved with numbers instead of estimates.

**Checkpoint criteria**: a hardcoded reply travels mic → STT → orchestrator → TTS → speaker, and
`/metrics` reports real p50/p95 for all seven spans.

### 1A. Setup

- [x] T001 [P] Scaffold backend Python project in `backend/pyproject.toml` with src layout and dev dependencies
  - **What**: Python 3.11+ project, `src/` package layout, deps: `fastapi`, `uvicorn[standard]`, `websockets`, `anthropic`, `pydantic>=2`, `structlog`, `aiosqlite`; dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`.
  - **Done when**: `pip install -e ".[dev]"` succeeds; `pytest` collects 0 tests without error; `ruff check` and `mypy src` both pass on an empty package.
  - **Effort**: S · **Critical path**: Yes

- [x] T002 [P] Scaffold frontend Vite + TypeScript project in `frontend/package.json`
  - **What**: Vite + TS, no UI framework. Strict `tsconfig`. Dev server proxying `/ws` and `/api` to `localhost:8000`.
  - **Done when**: `npm run dev` serves a blank page; `npm run build` emits static assets; `tsc --noEmit` passes.
  - **Effort**: S · **Critical path**: Yes

- [x] T003 Typed fail-fast configuration in `backend/src/config.py` and `.env.example`
  - **What**: Pydantic settings model covering every key and tuning value in quickstart.md. Validation runs at import; the process refuses to boot on missing or malformed values.
  - **Done when**: booting with an absent `ANTHROPIC_API_KEY` exits non-zero with a message naming the field; no runtime default silently substitutes for a missing value; `.env.example` lists every key with placeholders and is committed while `.env` is git-ignored.
  - **Effort**: S · **Critical path**: Yes

- [x] T004 [P] Structured JSON logging and redaction skeleton in `backend/src/obs/redaction.py`
  - **What**: `structlog` emitting JSON to stdout, binding `session_id`/`turn_id`. Redaction hook that every sink passes through. Startup assertion that DEBUG transcript logging is off when not in dev.
  - **Done when**: every log line is valid JSON carrying both ids; a unit test proves an email address and a card-shaped number are redacted before reaching the sink; booting in production config with DEBUG transcripts enabled fails at startup.
  - **Effort**: M · **Critical path**: Yes

### 1B. ⚠️ Latency spikes — run these FIRST

> These are standalone scripts. They need **no** skeleton, no orchestrator, and no browser. Running
> them on day 1 rather than at the end of week 1 is the whole point: both budgets are at risk, and
> week 2's architecture is built on the assumption that they hold. Measure before you build on them.

- [x] T005 ⚠️ [P] TTS latency spike in `backend/scripts/spike_tts_latency.py`
  - **What**: Standalone script opening a persistent ElevenLabs `stream-input` socket, submitting a short clause, and timing submission → first audio byte over ≥50 trials. Compare `optimize_streaming_latency` 0 vs 4, PCM vs mp3, cold vs pre-warmed socket.
  - **Done when**: p50/p95/p99 reported per configuration; the result is written into research.md R4 as measured fact; a go/no-go is recorded against the **250 ms p95 / 400 ms hard fail** budget. If it fails, T007 is triggered.
  - **Effort**: M · **Critical path**: Yes

- [x] T006 ⚠️ [P] LLM TTFT spike in `backend/scripts/spike_llm_ttft.py`
  - **What**: Standalone script measuring `claude-opus-5` time-to-first-text-token over ≥50 trials with a representative system prompt and the 10 tool schemas attached. Matrix: `effort` low vs medium; prompt cache cold vs warm.
  - **Done when**: p50/p95 reported per cell; `usage.cache_read_input_tokens` confirmed non-zero on warm runs (a persistent zero means a silent cache invalidator and is itself the finding); go/no-go recorded against the **450 ms p95 / 800 ms hard fail** budget.
  - **Effort**: M · **Critical path**: Yes

- [x] T007 Latency contingency decision record in `specs/001-order-support-agent/research.md`
  - **What**: Only if T005 or T006 misses budget. Benchmark Cartesia Sonic against the same harness (TTS), and/or measure `claude-haiku-4-5` on the same matrix (LLM). Present measured options to the user; do not switch provider or model unilaterally.
  - **Done when**: either both gates pass and this task closes as not-needed, or a decision is recorded with numbers and the user has chosen. Amending a constitutional budget requires the governance procedure, not a quiet edit.
  - **Effort**: M · **Critical path**: Yes (conditional)

- [ ] T008 [P] Single-source latency budgets in `backend/src/obs/budgets.yaml`
  - **What**: Extract every p50/p95/hard-fail figure from constitution, spec NFRs, and plan into one file, each row tagged `source: vendor|ours`. Loader plus a CI assertion comparing measured percentiles against it.
  - **Done when**: the three documents reference this file rather than restating numbers; a deliberately-tightened budget makes CI fail. **This is the drift fix flagged in PHR-0001/0002/0003** — it stops being a suggestion here.
  - **Effort**: M · **Critical path**: No

### 1C. Foundational core (no vendor imports)

- [x] T009 [P] Port protocols in `backend/src/core/ports.py`
  - **What**: `Protocol` definitions for `SpeechRecognizer`, `LanguageModel`, `SpeechSynthesizer`, `ToolRegistry`, `ConversationMemory`, expressed only in domain types.
  - **Done when**: `mypy` passes; no import from `anthropic`, `elevenlabs`, or any vendor package appears in the file.
  - **Effort**: S · **Critical path**: Yes

- [x] T010 [P] Typed pipeline events in `backend/src/core/events.py`
  - **What**: `UserPartial`, `UserTurnCommitted`, `AgentTokens`, `AgentAudio`, `Interrupted`, `ToolRequested`, `ToolCompleted`, each carrying `session_id` and `turn_id`.
  - **Done when**: all events are frozen dataclasses; a test asserts every event type carries both correlation ids.
  - **Effort**: S · **Critical path**: Yes

- [x] T011 [P] Injectable clock in `backend/src/core/clock.py` with a controllable test fake
  - **What**: `Clock` protocol with `now()` and `sleep()`; real implementation plus a `FakeClock` that advances on command.
  - **Done when**: a test advances time deterministically without wall-clock waiting; grep proves no direct `time.time()` or `asyncio.sleep` call exists under `core/`.
  - **Effort**: S · **Critical path**: Yes

- [x] T012 Turn state machine in `backend/src/core/turn_state.py`
  - **What**: Pure implementation of the state machine in data-model.md — `IDLE → LISTENING → COMMITTED → THINKING → SPEAKING → (IDLE | INTERRUPTED)` plus silence transitions.
  - **Done when**: every legal transition is unit-tested; every illegal transition raises; the module performs no I/O and takes its clock by injection.
  - **Effort**: M · **Critical path**: Yes

- [x] T013 Core purity import guard in `backend/tests/unit/test_core_purity.py`
  - **What**: Walk the AST of every module under `core/` and fail if any vendor SDK or I/O library is imported.
  - **Done when**: the test passes today and demonstrably fails when a stub `import anthropic` is added to a core module. **This is what makes constitution principle IV enforceable rather than aspirational.**
  - **Effort**: S · **Critical path**: Yes

### 1D. Observability (from day one, not week three)

- [x] T014 Span emission in `backend/src/obs/spans.py`
  - **What**: The seven mandatory spans plus `playback.stop`, as async context managers auto-binding `session_id`/`turn_id`.
  - **Done when**: a fake turn emits all eight spans in order; each records a duration; a missing correlation id raises rather than logging blank.
  - **Effort**: M · **Critical path**: Yes

- [x] T015 Metrics registry and `/metrics` endpoint in `backend/src/obs/metrics.py`
  - **What**: The nine counters/histograms named in plan.md § 8, Prometheus exposition format.
  - **Done when**: `curl localhost:8000/metrics` returns all nine series; histograms expose p50/p95/p99 buckets.
  - **Effort**: M · **Critical path**: Yes

### 1E. Browser audio

- [x] T016 [P] Capture worklet in `frontend/src/worklets/capture.worklet.ts`
  - **What**: `getUserMedia` → AudioContext → AudioWorklet emitting exactly 1600-byte PCM16 mono frames at 16 kHz every 50 ms.
  - **Done when**: a 10-second capture yields exactly 200 frames of exactly 1600 bytes; frame cadence jitter stays under 5 ms; no audio work runs on the main thread.
  - **Effort**: L · **Critical path**: Yes

- [x] T017 [P] Playback worklet with zeroable ring buffer in `frontend/src/worklets/playback.worklet.ts`
  - **What**: Ring buffer consuming PCM16 frames, with a `flush` message that zeroes it on the next render quantum.
  - **Done when**: a test plays 2 s of tone, sends `flush`, and confirms silence within one render quantum (~2.7 ms at 48 kHz). **Explicitly not an `<audio>` element** — the whole barge-in budget depends on this.
  - **Effort**: L · **Critical path**: Yes

- [x] T018 Frontend transport in `frontend/src/transport.ts`
  - **What**: Both sockets per `contracts/websocket-protocol.md` — binary audio socket, JSON control socket, reconnect handling.
  - **Done when**: every message type in the contract round-trips against a stub backend; `audio.flush` is never queued behind other control messages.
  - **Effort**: M · **Critical path**: Yes

- [x] T019 [P] Minimal UI in `frontend/src/ui.ts`
  - **What**: Mic button, live transcript pane, status row, and an on-screen reference area for `display.reference` payloads. Keyboard-operable, screen-reader labelled.
  - **Done when**: partial transcripts render live and are visually distinct from committed ones; tracking numbers appear on screen rather than being spoken.
  - **Effort**: M · **Critical path**: Yes

### 1F. Backend sockets and session

- [x] T020 FastAPI app with both WebSocket endpoints in `backend/src/app.py`
  - **What**: `/ws/audio` (binary) and `/ws/control` (JSON), plus static asset serving for the built frontend.
  - **Done when**: both accept connections, reject a missing/expired token, and a browser client completes a handshake to `session.ready`.
  - **Effort**: M · **Critical path**: Yes

- [x] T021 Session token minting in `backend/src/app.py` — `POST /api/session`
  - **What**: Mint a single-use, 60-second, session-scoped token bound to one `session_id`.
  - **Done when**: a token works once and is rejected on reuse and after expiry; a test asserts **no vendor API key appears in any HTTP response body or any WebSocket frame** sent to the browser.
  - **Effort**: M · **Critical path**: Yes

- [x] T022 SessionActor with nested task scopes in `backend/src/session/actor.py`
  - **What**: `asyncio.TaskGroup` per session, nested `turn_scope` per turn. Bounded queues with the per-queue overflow policy from plan.md § 6.
  - **Done when**: cancelling `turn_scope` demonstrably cancels a child LLM stub, tool stub, and TTS stub together; a test asserts no task survives more than 200 ms past cancellation.
  - **Effort**: L · **Critical path**: Yes

- [x] T023 Ingest loop in `backend/src/session/ingest.py`
  - **What**: The sacred loop — read audio frames from the browser socket, write to the STT socket. Nothing else.
  - **Done when**: a code review checklist item confirms the loop body contains no logging I/O, no transcript handling, no LLM call, and no lock; a full inbound queue drops the **oldest** frame and increments the counter rather than blocking.
  - **Effort**: M · **Critical path**: Yes

### 1G. STT adapter

- [x] T024 AssemblyAI v3 adapter in `backend/src/adapters/stt/assemblyai.py`
  - **What**: Connect to `wss://streaming.assemblyai.com/v3/ws` with configured params, stream PCM16, parse `Begin`/`Turn`/`Termination`/`Error` into domain events. `KeepAlive` during silence, `Terminate` on close.
  - **Done when**: a live session produces partial and committed turns; `turn_order` is preserved; no AssemblyAI type escapes the adapter boundary (verified by T013).
  - **Effort**: L · **Critical path**: Yes

- [x] T025 Record STT fixtures into `backend/tests/fixtures/stt/`
  - **What**: Capture real frame sequences for: a clean turn, a barge-in, a mid-turn disconnect, a low-confidence endpoint, and a split/ambiguous utterance.
  - **Done when**: five fixture files exist as raw provider JSON, each with a README line describing what it reproduces.
  - **Effort**: M · **Critical path**: Yes

- [x] T026 Fake STT adapter in `backend/src/adapters/stt/fake.py`
  - **What**: Replay fixtures through the same port with realistic inter-message timing driven by the injected clock.
  - **Done when**: integration tests run the full pipeline with zero network calls; replay timing is deterministic under `FakeClock`.
  - **Effort**: M · **Critical path**: Yes

- [x] T027 [P] STT contract test in `backend/tests/contract/test_stt_schema.py`
  - **What**: Validate the recorded fixtures against the real AssemblyAI message schema so our fakes cannot silently drift from the provider.
  - **Done when**: the test fails if a fixture omits a required field or uses a stale field name.
  - **Effort**: S · **Critical path**: No

- [x] T028 STT reconnection in `backend/src/session/lifecycle.py`
  - **What**: Jittered backoff reconnect preserving conversation state; gap audio dropped and logged; `Degraded` status surfaced.
  - **Done when**: the disconnect fixture drives a reconnect without ending the session; a test asserts gap audio is **never** replayed into a later turn as current speech.
  - **Effort**: M · **Critical path**: Yes

### 1H. TTS adapter and skeleton wiring

- [x] T029 ElevenLabs adapter in `backend/src/adapters/tts/elevenlabs.py`
  - **What**: Persistent `stream-input` socket opened at session start (pre-warmed), clause submission, `flush`, `close_connection`; audio frames onto the outbound queue.
  - **Done when**: text submitted produces PCM frames reaching the browser; socket setup happens at session start and never on the critical path.
  - **Effort**: L · **Critical path**: Yes

- [x] T030 TTS interrupt and alignment capture in `backend/src/adapters/tts/elevenlabs.py`
  - **What**: `clear_buffer` on interrupt; capture character-alignment `timestamps` and expose them for `heard_prefix_len` computation.
  - **Done when**: `clear_buffer` halts synthesis; alignment data is retained per clause and maps character offsets to audio frame offsets.
  - **Effort**: M · **Critical path**: Yes

- [x] T031 [P] Fake TTS adapter in `backend/src/adapters/tts/fake.py`
  - **What**: Emit silence frames with realistic timing and synthetic alignment data.
  - **Done when**: barge-in and truncation tests run with no network call and deterministic timing.
  - **Effort**: S · **Critical path**: No

- [x] T032 Wire the walking skeleton in `backend/src/session/actor.py`
  - **What**: mic → STT → committed turn → **hardcoded reply string** → TTS → playback. No LLM, no tools yet.
  - **Done when**: speaking into the browser produces spoken output end to end, and all eight spans emit for that turn.
  - **Effort**: M · **Critical path**: Yes

- [x] T033 ⚠️ End-to-end latency measurement and Phase 1 gate report
  - **What**: Run ≥30 turns through the skeleton; report measured p50/p95 for `stt.turn`, `tts.ttfb`, and end-to-end against `budgets.yaml`.
  - **Done when**: a written gate report exists stating pass/fail per budget. **Week 2 does not start until this report exists** — it is the difference between building on measurement and building on assumption.
  - **Effort**: M · **Critical path**: Yes

---

## Phase 2: The Agent — US1 Order Status (Week 2 — Sep 16–22)

**Goal**: US1 works end to end — the shopper asks where their order is and gets a real, tool-grounded
answer, with interruption working.

**Independent test**: sign in as the fixture customer with two orders, ask "where's my order?",
confirm disambiguation by item and date, then "what about the other one?" resolves without
re-identification.

### 2A. Simulated store

- [x] T034 [P] SQLite schema in `backend/src/adapters/store/schema.sql`
  - **What**: Tables for every domain entity in data-model.md, including the **UNIQUE constraint on `ReturnRequest.idempotency_key`**.
  - **Done when**: schema applies cleanly; a test proves two inserts with the same idempotency key raise a constraint violation rather than creating two rows.
  - **Effort**: M · **Critical path**: Yes

- [x] T035 [P] Seed fixtures in `backend/src/adapters/store/seed.py`
  - **What**: One fixture customer with two orders in deliberately different states (one shipped with tracking, one not yet shipped), a split-shipment order, an expired-return-window item, and an already-returned item.
  - **Done when**: `python -m src.adapters.store.seed` is idempotent and produces data exercising every US1/US2 acceptance scenario including the awkward ones.
  - **Effort**: M · **Critical path**: Yes

- [x] T036 Repositories in `backend/src/adapters/store/sqlite.py`
  - **What**: Order, product, and policy repositories. **Every query takes `customer_id` from the authenticated session, never from an argument.**
  - **Done when**: a test proves a query for another customer's order returns not-found even when that order id is passed explicitly; return eligibility is evaluated per line item, not per order.
  - **Effort**: L · **Critical path**: Yes

### 2B. Tool layer

- [x] T037 ToolRegistry in `backend/src/tools/registry.py`
  - **What**: Schema validation, server-side authorization, latency classification, idempotency keys derived from `turn_id`, and the `speculative` call-context flag.
  - **Done when**: an unclassified tool cannot register; invalid arguments never reach a handler; a `state_changing` tool invoked in speculative context is refused before execution.
  - **Effort**: L · **Critical path**: Yes

- [x] T038 Tool definitions in `backend/src/tools/definitions.py`
  - **What**: Load `contracts/tools.schema.json` and emit Anthropic tool definitions with `strict: true`, `additionalProperties: false`, and explicit `required`.
  - **Done when**: all 10 tools export; a test asserts **no schema exposes `customer_id`**; provider-side strict validation is confirmed active.
  - **Effort**: M · **Critical path**: Yes

- [x] T039 [P] [US1] Read-only tool handlers in `backend/src/tools/handlers/`
  - **What**: `list_recent_orders`, `get_order`, `get_shipment_tracking`, `get_product_info`, `get_policy`.
  - **Done when**: each returns within its 300 ms fast budget against seeded data; `get_shipment_tracking` returns `not_yet_shipped` as a first-class result rather than an error.
  - **Effort**: L · **Critical path**: Yes

- [x] T040 [P] Tool audit trail in `backend/src/tools/registry.py`
  - **What**: Write a `ToolAuditEntry` for every invocation with redacted arguments, outcome, duration, and `actor: agent`.
  - **Done when**: every tool call produces exactly one audit row; arguments are redacted before write.
  - **Effort**: S · **Critical path**: No

### 2C. LLM adapter

- [x] T041 Claude adapter with manual tool loop in `backend/src/adapters/llm/claude.py`
  - **What**: `async with client.messages.stream(...)`, `model=claude-opus-5`, `output_config={"effort": "low"}`, thinking left at the adaptive default, `max_tokens≈320`. Manual `while stop_reason == "tool_use"` loop. **All parallel `tool_result` blocks returned in a single user message.**
  - **Done when**: a multi-tool turn completes; tokens stream incrementally; a test asserts parallel results are never split across messages.
  - **Effort**: L · **Critical path**: Yes

- [x] T042 Prompt assembly and cache breakpoints in `backend/src/adapters/llm/prompt.py`
  - **What**: Render order tools → system → messages, with `cache_control` on the stable prefix. Merchant-supplied text wrapped in a delimited block the system prompt declares non-authoritative.
  - **Done when**: `usage.cache_read_input_tokens` is non-zero from the second turn onward; a test injects instruction-shaped text into a product description and asserts it does not alter behaviour.
  - **Effort**: M · **Critical path**: Yes

- [x] T043 System prompt and persona in `backend/src/adapters/llm/persona.md`
  - **What**: Encode the Voice Interaction Design rules — two-sentence cap, answer-first, no markdown or emoji, no spelled-out URLs, AI disclosure, one question per turn.
  - **Done when**: sampled responses respect the two-sentence cap; no response contains markdown, emoji, or a spoken URL.
  - **Effort**: M · **Critical path**: Yes

- [x] T044 Mid-stream cancellation in `backend/src/adapters/llm/claude.py`
  - **What**: Abort the streaming request on `turn_scope` cancellation and release the connection.
  - **Done when**: cancelling mid-generation stops token consumption within 200 ms and leaks no connection across 100 cancelled turns.
  - **Effort**: M · **Critical path**: Yes

- [x] T045 [P] Fake LLM adapter in `backend/src/adapters/llm/fake.py`
  - **What**: Scripted token streams and scripted tool-call sequences, driven by the injected clock.
  - **Done when**: every US1 integration test runs with no live LLM call.
  - **Effort**: M · **Critical path**: Yes

### 2D. Orchestrator and memory

- [x] T046 Working memory in `backend/src/core/memory.py`
  - **What**: Deque of the last 12 committed turns, verbatim. Partial turns are structurally excluded.
  - **Done when**: a test asserts an uncommitted partial never enters memory under any path.
  - **Effort**: M · **Critical path**: Yes

- [x] T047 EntityContext and reference resolution in `backend/src/core/memory.py`
  - **What**: Insertion-ordered orders discussed, last order/line-item refs, corrections map.
  - **Done when**: "the other one" resolves to the second-most-recent order; a correction supersedes the prior value and the superseded value is never reused; an ambiguous referent on a state-changing intent returns `AMBIGUOUS` rather than a guess.
  - **Effort**: L · **Critical path**: Yes

- [ ] T048 Orchestrator dispatch loop in `backend/src/core/orchestrator.py`
  - **What**: The single writer. Consumes events, drives the turn state machine, dispatches to LLM and tools, owns interruption declaration.
  - **Done when**: a grep-backed test confirms no other module mutates turn state or memory.
  - **Effort**: L · **Critical path**: Yes

### 2E. Barge-in

- [x] T049 Backchannel filter in `backend/src/core/barge_in.py`
  - **What**: Pure decision function. **`awaiting_confirmation` is checked first**, then grace window, then word count and backchannel set. Configurable token set with "yes"/"no" excluded by default.
  - **Done when**: unit tests cover — "mhm" during agent speech suppresses; "yes" during a pending confirmation **interrupts**; a 3-word utterance interrupts; the grace window expires correctly. The confirmation-first ordering has its own named test, since it is the FR-009/FR-010 collision and the most likely thing to regress.
  - **Effort**: M · **Critical path**: Yes

- [ ] T050 Barge-in execution path in `backend/src/core/orchestrator.py`
  - **What**: On INTERRUPT, fire concurrently and independently: TTS `clear_buffer`, control-socket `audio.flush`, `turn_scope.cancel()`.
  - **Done when**: a test proves the client flush is **not** awaiting the `clear_buffer` response; measured interrupt-to-silence is under 200 ms.
  - **Effort**: L · **Critical path**: Yes

- [ ] T051 Heard-prefix truncation in `backend/src/core/memory.py`
  - **What**: Combine `playback.progress` frame counts with TTS character alignment to compute `heard_prefix_len`; truncate the agent turn in memory to exactly that.
  - **Done when**: interrupting mid-sentence stores only the spoken-and-heard prefix; a test asserts the stored text is never the full generated text. **This is the constitution's principle III made real.**
  - **Effort**: L · **Critical path**: Yes

- [ ] T052 [P] Barge-in latency instrumentation in `backend/src/obs/spans.py`
  - **What**: `playback.stop` span plus `barge_in_latency_seconds` and `backchannel_suppressed_total`.
  - **Done when**: both metrics populate under load; p95 is visible on `/metrics`.
  - **Effort**: S · **Critical path**: Yes

### 2F. Streaming output and speculation

- [x] T053 Clause splitter in `backend/src/core/clause_splitter.py`
  - **What**: Emit speakable fragments on `,;—.?!` and a 12-word soft cap, with lookahead suppressing breaks inside digit runs, currency amounts, and order references.
  - **Done when**: "your total is forty two dollars fifty" is never split mid-amount; the first fragment reaches TTS within 2–3 tokens of generation starting.
  - **Effort**: M · **Critical path**: Yes

- [ ] T054 ⚠️ Speculative dispatch in `backend/src/core/orchestrator.py`
  - **What**: The four gates from plan.md § 5 — confidence threshold, 3-char delta, **read-only only**, one in flight. Output lands in a staging buffer, never TTS or memory.
  - **Done when**: a speculative call never reaches TTS before commitment; a speculative `create_return` is refused by the registry; a diverging committed transcript discards and cancels the staged stream. **This is the mechanism that makes the p50 budget reachable.**
  - **Effort**: L · **Critical path**: Yes

- [ ] T055 Speculation promotion and metrics in `backend/src/core/orchestrator.py`
  - **What**: On commit, promote a matching staged stream to TTS immediately; otherwise discard and re-dispatch. Emit `speculative_dispatch_total{promoted|discarded}`.
  - **Done when**: measured end-to-end p50 improves versus speculation disabled, and the improvement is quantified in the gate report. If it does not pay for itself, that is a finding worth recording rather than hiding.
  - **Effort**: M · **Critical path**: Yes

### 2G. US1 delivery

- [x] T056 [US1] Order identification and disambiguation in `backend/src/core/orchestrator.py`
  - **What**: Single open order answers directly; multiple orders disambiguate by item name and date in **one** question.
  - **Done when**: acceptance scenarios 1–3 of US1 pass; the agent never asks for an order number when recent orders exist.
  - **Effort**: M · **Critical path**: Yes

- [ ] T057 [P] [US1] On-screen references in `backend/src/core/orchestrator.py` and `frontend/src/ui.ts`
  - **What**: Emit `display.reference` for tracking numbers, order refs, and links instead of speaking them.
  - **Done when**: no tracking number or URL is ever spoken; each appears on screen with a label.
  - **Effort**: S · **Critical path**: Yes

- [ ] T058 [US1] US1 acceptance test suite in `backend/tests/integration/test_us1_order_status.py`
  - **What**: All six US1 acceptance scenarios driven through fake adapters.
  - **Done when**: all six pass with no live vendor call, including the no-recent-orders and not-yet-shipped branches.
  - **Effort**: L · **Critical path**: Yes

---

## Phase 3: Safety, State-Changing Actions & Polish (Week 3 — Sep 23–27)

**Goal**: The agent can act on orders without ever acting unconfirmed or lying about the outcome, and
can hand off to a human with context.

### 3A. US2 — returns, cancellations, address changes

- [ ] T059 [US2] Confirmation ritual in `backend/src/core/orchestrator.py`
  - **What**: `PendingAction` created before any state-changing tool; read-back names item, order, and effect and ends in a direct yes/no; no other content bundled into that turn.
  - **Done when**: a state-changing tool is **structurally unreachable** without a confirmed `PendingAction` — enforced in the registry, not by prompting. Any non-affirmative response is treated as refusal.
  - **Effort**: L · **Critical path**: Yes

- [ ] T060 [P] [US2] State-changing tool handlers in `backend/src/tools/handlers/`
  - **What**: `create_return`, `cancel_order`, `update_shipping_address` with server-side window and shipment checks and turn-derived idempotency keys.
  - **Done when**: a return outside its window is refused with the closing date; cancelling a shipped order offers a return instead; a repeated call returns `deduplicated: true` without creating a second record.
  - **Effort**: L · **Critical path**: Yes

- [ ] T061 [US2] Never-claim-false-success enforcement in `backend/src/core/orchestrator.py`
  - **What**: Tool failures are returned to the model as structured errors, and the response for a failed action must state it did **not** happen.
  - **Done when**: with the backend forced to fail, the agent's reply asserts failure and offers escalation. **A single violation is a release blocker (SC-011).**
  - **Effort**: M · **Critical path**: Yes

- [ ] T062 [US2] US2 acceptance test suite in `backend/tests/integration/test_us2_returns.py`
  - **What**: All eight US2 acceptance scenarios, including the failed-backend branch and the duplicate-request branch.
  - **Done when**: all eight pass; a test explicitly proves no return is created when confirmation is withheld.
  - **Effort**: L · **Critical path**: Yes

### 3B. US3 — escalation

- [ ] T063 [P] [US3] Escalation tool and ticket in `backend/src/tools/handlers/escalate_to_human.py`
  - **What**: Build the ticket with summary, order refs, actions taken, and a server-attached transcript pointer.
  - **Done when**: the ticket contains everything a human needs; the transcript pointer is attached server-side, never passed by the model.
  - **Effort**: M · **Critical path**: Yes

- [ ] T064 [US3] Escalation triggers in `backend/src/core/orchestrator.py`
  - **What**: Immediate on explicit request; offered after two consecutive failures on the same request; automatic when no tool covers the request.
  - **Done when**: "let me talk to a person" escalates in the same turn with no argument or retry; US3 acceptance scenarios 1–5 pass.
  - **Effort**: M · **Critical path**: Yes

### 3C. Error handling and resilience

- [ ] T065 Error classification in `backend/src/core/errors.py`
  - **What**: The four classes — `Transient`, `Degraded`, `Fatal`, `Fatal-Session` — with the response mapping from plan.md § 7. Unclassified defaults to `Fatal` for the turn.
  - **Done when**: every row of the plan's error table has a test; a state-changing failure is **never** blindly retried.
  - **Effort**: L · **Critical path**: Yes

- [ ] T066 [P] Timeouts on every external call in `backend/src/adapters/`
  - **What**: Explicit timeout derived from each call's latency budget. No unbounded await on the critical path.
  - **Done when**: a lint-style test asserts every `await` on a vendor client is timeout-wrapped.
  - **Effort**: M · **Critical path**: Yes

- [ ] T067 [P] Circuit breakers in `backend/src/adapters/`
  - **What**: Open after 3 consecutive failures, 30 s cooldown, fallback path engaged.
  - **Done when**: a failing provider stops being called during cooldown; a metric exposes breaker state.
  - **Effort**: M · **Critical path**: No

- [ ] T068 [P] Text fallback and mic-revocation handling in `frontend/src/ui.ts`
  - **What**: `text.input` path into the pipeline; graceful explanation when mic permission is denied or revoked mid-session.
  - **Done when**: every voice capability is reachable by text; revoking permission mid-session shows an explanation rather than silence.
  - **Effort**: M · **Critical path**: No

- [ ] T069 Silence handling and session close in `backend/src/session/lifecycle.py`
  - **What**: Re-prompts at 6 s and 18 s, graceful close at 30 s with a stated reason; closing turn summarises actions taken.
  - **Done when**: re-prompts escalate in specificity rather than repeating; the session never dies silently.
  - **Effort**: M · **Critical path**: Yes

### 3D. Conversational polish

- [ ] T070 Holding phrases in `backend/src/core/orchestrator.py`
  - **What**: 400 ms timer on `slow` tools; phrase cancelled if the tool returns first; rotating phrase set.
  - **Done when**: a fast-returning slow tool produces **no** holding phrase; a genuinely slow tool is covered before perceptible silence; consecutive slow calls do not repeat the same phrase.
  - **Effort**: M · **Critical path**: Yes

- [ ] T071 [P] Repair language in `backend/src/adapters/llm/persona.md`
  - **What**: Specific rather than generic repair prompts; strategy change on second failure; refusals always paired with an alternative.
  - **Done when**: low-confidence input produces "I didn't catch the order number", not "I didn't understand"; the third attempt offers escalation instead of asking again.
  - **Effort**: S · **Critical path**: No

- [ ] T072 [P] Payment-credential guard in `backend/src/core/orchestrator.py`
  - **What**: Detect card-shaped or credential-shaped speech, interrupt, and redirect to a secure channel.
  - **Done when**: a spoken card number is never stored, logged, or passed to a tool; the agent interrupts rather than completing the capture.
  - **Effort**: M · **Critical path**: No

### 3E. Deferred scope (cut candidates — see Cut List)

- [ ] T073 [P] [US4] Product and policy Q&A in `backend/src/core/orchestrator.py`
  - **What**: Answer from retrieved product and policy records only; honest "I don't have that" otherwise; one-sentence decline for out-of-domain questions.
  - **Done when**: US4 acceptance scenarios 1–4 pass; no invented product attribute appears in sampled answers.
  - **Effort**: M · **Critical path**: **No — cut candidate #1**

- [ ] T074 [P] Durable cross-session memory in `backend/src/core/memory.py`
  - **What**: Open case refs, display name, last three session summaries. Proactive reference to an open case in the opening turn.
  - **Done when**: a returning shopper with an open return hears it referenced; raw transcripts never enter durable memory; deletion on request works.
  - **Effort**: L · **Critical path**: **No — cut candidate #2**

- [ ] T075 Summary-memory compaction in `backend/src/core/memory.py`
  - **What**: Compact beyond 12 working turns, preserving order refs, actions, and reference numbers verbatim.
  - **Done when**: a 30-minute session sends a request comparable in size to a 2-minute one. If cut, cap session length instead.
  - **Effort**: L · **Critical path**: **No — cut candidate #3**

### 3F. Deployment and demo

- [x] T076 [P] Multi-stage Dockerfile in `Dockerfile`
  - **What**: Vite build → Python runtime serving static assets plus both WebSocket endpoints. One image, one process, one port.
  - **Done when**: `docker run -p 8000:8000` serves a fully working app with no separate frontend host and no CORS configuration.
  - **Effort**: M · **Critical path**: Yes

- [ ] T077 Replit deployment in `.replit` and `replit.nix`
  - **What**: Deploy the FastAPI process to Replit as a persistent (Reserved VM / Autoscale) deployment. Secrets as Replit Secrets, never committed. **Replit is mandated** — the Rule Book permits only Streamlit, Replit, or Vercel, and Replit is the only one of the three that supports a long-lived WebSocket server.
  - **Done when**: the public Replit URL completes a full voice turn from a cold browser; both WebSocket endpoints work over TLS; no secret appears in the repo or in any client payload.
  - **Effort**: L · **Critical path**: Yes

- [ ] T077b ⚠️ Re-measure latency **on the deployed instance** in `backend/scripts/spike_*.py`
  - **What**: Re-run the T005/T006/T033 measurements from the Replit deployment rather than localhost.
  - **Done when**: deployed p50/p95 are recorded against `budgets.yaml`. **Replit gives no region pinning**, so geographic RTT is now an uncontrolled variable — a budget that passes locally and fails deployed has not been met. If deployed latency misses badly, tune buffer sizes and holding-phrase thresholds rather than pretending the local numbers count.
  - **Effort**: M · **Critical path**: Yes

- [ ] T078 `DEMO_MODE` seeding and reset in `backend/src/adapters/store/seed.py`
  - **What**: One-command reset to a known fixture state exercising disambiguation, a cancel refusal, and a successful return.
  - **Done when**: the demo can be reset between runs in under 5 seconds. Live demos fail on unseeded data far more often than on bugs.
  - **Effort**: S · **Critical path**: Yes

- [ ] T079 Demo script and rehearsal in `specs/001-order-support-agent/demo-script.md`
  - **What**: A scripted run covering order status → disambiguation → interruption → return with confirmation → escalation, with expected agent responses and a fallback if a provider degrades live.
  - **Done when**: rehearsed end to end at least twice on the deployed URL.
  - **Effort**: M · **Critical path**: Yes

- [ ] T080 Quickstart validation in `specs/001-order-support-agent/quickstart.md`
  - **What**: Follow quickstart.md from a clean clone and correct anything that does not work as written.
  - **Done when**: a person who has not seen the project reaches a working voice turn using only the document.
  - **Effort**: M · **Critical path**: No

---

## Phase 4: Hackathon Submission (Sep 28–30 — hard stop 8:00 PM PKT)

**Goal**: Every mandatory artifact in the lablab.ai Rule Book exists and the submission is filed
before the deadline.

**Why this is its own phase**: two of the four judging criteria (Presentation, Originality) are
graded on artifacts that do not exist yet, and they carry the same weight as Application of
Technology. The Rule Book is explicit: *"Failure to adhere to submission guidelines may result in a
lower score or exclusion from the hackathon."* Working software with a missing MP4 scores worse than
weaker software with a complete submission. **Every task here is critical path.**

- [ ] T081 Project title, descriptions, and tags in `SUBMISSION.md`
  - **What**: Clear descriptive title; short and long descriptions within the platform's character/word limits; correct technology and category tags (AssemblyAI Realtime STT, Anthropic Claude, ElevenLabs, voice agent, e-commerce).
  - **Done when**: all fields drafted and within limits. The long description leads with the business problem (containment rate), not the architecture — "Business value" is a separate scored criterion and the description is where it is judged.
  - **Effort**: M · **Critical path**: Yes

- [ ] T082 [P] Cover image at `assets/cover.png`
  - **What**: PNG or JPG, **16:9 aspect ratio** — both are hard format requirements.
  - **Done when**: file is 16:9, legible as a thumbnail, and names the product and the one-line value proposition.
  - **Effort**: S · **Critical path**: Yes

- [ ] T083 Video presentation at `assets/demo.mp4`
  - **What**: **MP4, mandatory.** Recorded from the deployed Replit URL, not localhost. Must show: a real interruption mid-sentence, the confirmation ritual before a return, and the live latency waterfall.
  - **Done when**: the video shows the agent being interrupted and recovering correctly, and states the measured p50/p95. The barge-in moment is the single most persuasive 10 seconds available — it is hard to fake and most submissions cannot show it.
  - **Effort**: L · **Critical path**: Yes

- [ ] T084 [P] Slide presentation at `assets/slides.pdf`
  - **What**: **PDF, mandatory.** Structure against the four judging criteria: problem and business value → why the harder Realtime STT path → architecture and measured latency → what makes it original.
  - **Done when**: exported as PDF; every slide maps to a scored criterion; no slide is architecture-for-its-own-sake.
  - **Effort**: M · **Critical path**: Yes

- [ ] T085 Public GitHub repository with MIT `LICENSE`
  - **What**: `git init`, MIT LICENSE file, README with setup instructions and an architecture diagram, `.gitignore` excluding `.env`. Push public.
  - **Done when**: repo is **public**; a clean clone runs via quickstart.md; **a secret scan of the full history returns nothing** — this project holds three vendor API keys and history is not rewritable after judges have the link.
  - **Effort**: M · **Critical path**: Yes

- [ ] T086 File the submission on lablab.ai
  - **What**: Enrol on lablab.ai and join the Discord (both required to participate). Attach every artifact, verify the Application URL loads for a logged-out visitor.
  - **Done when**: submission filed **before Sep 30, 8:00 PM PKT**. Manual submission exists for only 6 hours afterwards, requires prior organizer approval and a valid reason, and is not a buffer to plan around.
  - **Effort**: S · **Critical path**: Yes

---

## Cut List (ordered)

Drop from the bottom up if week 3 compresses. Taken directly from plan.md.

| Order | Task(s) | What is lost | Mitigation |
|---|---|---|---|
| 1st to cut | T073 — US4 product/policy Q&A | Least differentiated capability; closest to what a text FAQ already does | Agent escalates these questions instead |
| 2nd | T074 — durable cross-session memory | Returning shoppers are not greeted with their open case | Each session starts fresh; nothing breaks |
| 3rd | T075 — summary compaction | Very long sessions grow context | Cap session length instead; demo sessions are short |
| 4th | `update_shipping_address` within T060 | One of three state-changing actions | Returns and cancellations still demonstrate the confirmation ritual |
| 5th | T067, T068, T071, T072, T080 | Resilience and polish | Acceptable for a demo, **not** for real traffic |

**Never cut, at any cost** — these three are what separate a voice agent from a demo that should not
be pointed at real orders:

1. **T059 + T061** — the confirmation ritual and the never-claim-false-success rule.
2. **T049 + T050 + T051** — barge-in, including the memory truncation to what was heard.
3. **T037 + T038** — schema validation and server-side authorization on every tool.
4. **All of Phase 4 (T081–T086)** — the mandatory submission artifacts. These are not cuttable in a
   different sense from the other three: cutting them does not weaken the product, it forfeits the
   entry. Two of the four judging criteria are graded entirely on artifacts in this phase, and the
   Rule Book allows exclusion for an incomplete submission. **Protect Phase 4 by cutting features,
   never the reverse** — that is what the cut list above exists for.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Week 1)** blocks everything. Within it, **1B latency spikes (T005–T007) have no
  dependencies and should run on day 1** — they are standalone scripts, and both Phase 2 and the
  architecture depend on their outcome.
- **Phase 2 (Week 2)** depends on the Phase 1 gate report (T033).
- **Phase 3 (Week 3)** depends on the orchestrator and tool layer from Phase 2.

### Critical chain (longest dependency path)

```text
T001 → T003 → T009/T010/T011 → T012 → T022 → T023 → T024 → T032 → T033
     → T037 → T041 → T048 → T050 → T054 → T056 → T059 → T060 → T077
```

Anything not on this chain has slack. T005–T008, T013, T027, T031, T040 and every `[P]` task can
absorb delay without moving the demo date.

### Key blocking relationships

| Task | Blocked by | Why |
|---|---|---|
| T032 (skeleton) | T024, T029, T022 | Needs both adapters and the session actor |
| T033 (gate report) | T032, T014, T015 | Cannot measure without spans and metrics |
| T041 (Claude adapter) | T037, T038 | Tools must exist before the loop can call them |
| T050 (barge-in path) | T017, T030, T022 | Needs client flush, `clear_buffer`, and cancel scopes |
| T051 (truncation) | T030, T017 | Needs alignment data and playback progress |
| T054 (speculation) | T041, T048, T037 | Needs the LLM loop and the registry's speculative flag |
| T059 (confirmation) | T037, T048 | Enforced in the registry, not in the prompt |
| T060 (state-changing) | T034, T059 | Needs the unique constraint and the ritual |

### Parallel opportunities

Where two people are available:

- **Week 1**: one on backend (T001, T003, T009–T015, T020–T024), one on frontend (T002, T016–T019).
  T005/T006 are standalone and can be run by either on day 1.
- **Week 2**: one on store + tools (T034–T040), one on LLM + orchestrator (T041–T048). They meet
  at T056.
- **Week 3**: one on US2 (T059–T062), one on error handling and deployment (T065–T069, T076–T078).

All tasks marked `[P]` touch distinct files and have no incomplete dependency.

---

## Implementation Strategy

**MVP scope**: Phase 1 + Phase 2 = US1 order status, working end to end with barge-in. That alone is
demonstrable and delivers real value. If everything after T058 slipped, there would still be a
credible product.

**Incremental delivery**:

1. **T033** — the skeleton speaks, and the two at-risk budgets have numbers. First real checkpoint.
2. **T058** — US1 complete. Shippable as an MVP.
3. **T062** — US2 complete. The agent can act, safely.
4. **T064** — US3 complete. Safe for real customers.
5. **T079** — demo rehearsed.

**The one sequencing decision worth defending**: T005 and T006 come before almost everything else,
even though they build no product. Both budgets they measure are at risk, and the entire Phase 2
architecture — particularly speculative dispatch — assumes their outcome. Discovering in week 2 that
`tts.ttfb` sits at 480 ms would invalidate design decisions already built on. One day of measurement
up front is the cheapest insurance in this plan.

---

## Summary

| Metric | Value |
|---|---|
| Total tasks | 87 |
| Phase 1 (Week 1, Sep 9–15) | 33 |
| Phase 2 (Week 2, Sep 16–22 — US1) | 25 |
| Phase 3 (Week 3, Sep 23–27 — US2/US3/polish) | 23 |
| Phase 4 (Sep 28–30 — submission) | 6 |
| Critical path (`Yes`) | 75 |
| Non-critical (`No`) | 12, of which 3 are explicit cut candidates (T073–T075) |
| ⚠️ At-risk latency tasks | 5 (T005, T006, T033, T054, T077b) |
| Parallelizable `[P]` | 31 |
| Tasks per story | US1: 4 · US2: 4 · US3: 2 · US4: 1 (the rest are infrastructure and submission) |

Counts verified against the file by script rather than tallied by hand.

**Revision note (2026-09-09)**: this list was revised after scanning the official hackathon page and
lablab.ai Rule Book. Changes: deployment target corrected Fly.io → **Replit** (Fly.io is not a
permitted platform); **Phase 4 added** with the six mandatory submission artifacts, which were
missing entirely; T077b added to re-measure latency on the deployed instance since Replit gives no
region pinning; build deadline pulled from Sep 30 to **Sep 27**, because the submission artifacts
are mandatory and take real time.
