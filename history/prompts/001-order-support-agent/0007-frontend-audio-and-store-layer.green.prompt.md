---
id: 0007
title: Frontend Audio And Store Layer
stage: green
date: 2026-09-09
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: 001-order-support-agent (directory only; not a git repository)
user: my5757980@gmail.com
command: (user request — build everything, professional UI/UX)
labels: ["frontend", "audioworklet", "barge-in", "ui-ux", "sqlite", "idempotency", "customer-scoping"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon
  adr: null
  pr: null
files:
  - frontend/package.json, tsconfig.json, vite.config.ts, index.html (T002)
  - frontend/src/worklets/capture.worklet.ts (T016)
  - frontend/src/worklets/playback.worklet.ts (T017)
  - frontend/src/worklets/worklet.d.ts
  - frontend/src/audio.ts, transport.ts, protocol.ts (T018)
  - frontend/src/ui.ts, main.ts, styles.css (T019)
  - backend/src/core/domain.py
  - backend/src/adapters/store/schema.sql (T034)
  - backend/src/adapters/store/seed.py (T035)
  - backend/src/adapters/store/sqlite.py (T036)
  - backend/tests/integration/test_store.py
tests:
  - 79 passing (was 61) — 18 new store integration tests
  - frontend `tsc --noEmit` clean under strict + noUncheckedIndexedAccess + exactOptionalPropertyTypes
  - live verification: seeded DB, cross-customer isolation, per-item eligibility, partial refs
---

## Prompt

bariiiii bariiiiii sab shuruuu karooo or project best uiiiux ayy sath bana lagayyy professonal project haii

## Response snapshot

Built the entire frontend and the simulated store layer. **Eight more tasks complete** (T002, T016,
T017, T018, T019, T034, T035, T036), **17 of 87 total**, **79 tests passing**.

**The frontend's hardest file is `playback.worklet.ts`,** and it is the reason the plan refused an
`<audio>` element. A ring buffer we own can be zeroed on the next render quantum — 128 samples,
8 ms at 16 kHz. An `<audio>` element or MediaSource buffers ahead inside the browser's audio
pipeline where we cannot reach, so neither can be silenced inside the 100 ms barge-in budget. The
worklet also reports how many samples were *actually played*, which is the number that makes
`heard_prefix_len` exact rather than an estimate from elapsed time.

`capture.worklet.ts` emits exactly 800-sample / 1600-byte frames at 16 kHz — precisely AssemblyAI's
input format, so no resampling happens anywhere in the pipeline. It clamps before scaling to Int16,
because out-of-range float samples wrap catastrophically rather than clipping.

One AudioContext at 16 kHz drives both directions. Echo cancellation is on deliberately: the agent's
voice leaves the same speakers the microphone is listening to, and without AEC the agent interrupts
itself.

**In `main.ts`, the `audio.flush` handler awaits nothing before flushing.** That is the whole point —
the server-side `clear_buffer` stops the provider *producing* audio, but what the shopper is hearing
right now already sits in the local ring buffer. A design that waited for acknowledgement would
measure a correct-looking interrupt latency and still talk over the user.

**UI/UX** — an instrument, not a chat toy. Dark-first but fully theme-aware (tokens on bare `:root`,
media query and `[data-theme]` only ever *redefine* tokens). Conversation on the left, a Signal panel
on the right carrying the live latency waterfall with **budget markers**, so a bar becomes a verdict
rather than decoration. Three deliberate choices tied to the spec:

- A partial transcript renders dashed and italic and never looks settled — partials never reach
  memory, and the interface must not imply otherwise.
- Interruption is *visible*: a flash divider, and the agent's bubble marked truncated with the note
  "memory keeps only what you heard". Since we now lead on barge-in fidelity, the system's most
  distinctive behaviour should be legible on screen, not buried in logs.
- Tracking numbers and order references render as cards in the Signal panel rather than being spoken
  (FR-020, VID-006).

**Store layer.** The load-bearing lines are two:

1. `OrderRepository` is constructed *with* a customer id rather than accepting one per call — there
   is no signature that lets a caller pass someone else's. `OrderNotFound` deliberately does not
   distinguish "does not exist" from "belongs to another account", so the agent can never confirm an
   order exists elsewhere.
2. `UNIQUE(idempotency_key)` in the schema makes duplicate-return prevention a **database**
   guarantee, so FR-029 holds even if the calling logic above it is wrong.

Fixtures cover the awkward branches on purpose: a split shipment, an order with one returnable item
and one past its window, a shipped order that cannot be cancelled, and an unshipped order with no
tracking at all. Those are the branches that break live.

Verified by running it: cross-customer read → `OrderNotFound`; `ORD-4302` shows one `eligible` and
one `window_closed` line item; partial ref `4471` resolves; `can_cancel` is True for processing and
False for shipped.

## Outcome

- ✅ Impact: A complete, typechecked browser client with both AudioWorklets, and a seeded store with
  enforced customer scoping and DB-level idempotency. Everything still runs with zero API keys.
- 🧪 Tests: **79 passing** (61 → 79; 18 new store tests). Frontend `tsc --noEmit` clean under strict
  mode with `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`.
- 📁 Files: 14 frontend, 5 backend, 1 test file.
- 🔁 Next prompts: T037/T038 (ToolRegistry + the 10 tool definitions with `strict: true`) and T039
  (the five read-only handlers) — still no keys needed. After that the backend needs T020–T023
  (FastAPI sockets, session actor) to make the frontend actually connect to something.
- 🧠 Reflection: The UI work paid for itself beyond appearance. Rendering partials as visibly
  unsettled, and interruption as a visible truncation, forced the interface to tell the truth about
  what the system does with memory — which is exactly the property we now lead the submission with.
  A prettier chat bubble would have hidden it.

## Evaluation notes (flywheel)

- Failure modes observed: none functional. One process note: `pytest -q` under this configuration
  suppresses the summary line, and my first two attempts to extract per-file counts produced empty
  output. Rather than reporting a guessed number I switched to a form that prints the total — 79 —
  and reported only that. Counting is the thing I got wrong in PHR-0004; not repeating it mattered
  more than having a per-file table.
- Graders run and results (PASS/FAIL): Full suite PASS (79/79). Frontend strict typecheck PASS.
  Cross-customer isolation PASS. Missing-vs-forbidden indistinguishability PASS. DB idempotency
  constraint PASS (raises `IntegrityError` on duplicate key). Per-line-item eligibility PASS.
  Split-shipment preservation PASS. Unshipped-order-has-no-tracking PASS.
- Prompt variant (if applicable): n/a.
- Next experiment (smallest change to try): The playback worklet's flush latency is currently
  argued from first principles (128-sample quantum = 8 ms) rather than measured. A small harness
  that times `flush()` → first silent sample in a real browser would turn the barge-in claim into
  evidence — and that measurement is exactly what the demo video should show.
