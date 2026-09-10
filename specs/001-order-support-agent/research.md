# Phase 0 Research: Voice Order Support Agent

**Feature**: `001-order-support-agent` | **Date**: 2026-09-09
**Purpose**: Resolve every unknown in the plan's Technical Context before design.

All vendor facts below were retrieved from live documentation during planning, not recalled.
Numbers marked **[vendor]** are documented provider claims; numbers marked **[ours]** are budgets
we chose. The distinction matters: we hold ourselves to ours and measure theirs.

> **Read R9 first.** R3 and R4 were superseded during implementation: Claude needs a paid
> key and the ElevenLabs free quota was exhausted. The system runs on **AssemblyAI STT +
> Groq LLM + Groq Orpheus TTS**. R3 and R4 are kept as the decision trail — including the
> reasoning that still applies to any model with adaptive thinking — but they are not a
> description of the current stack.

---

## R1. AssemblyAI v3 streaming contract

**Decision**: Use `wss://streaming.assemblyai.com/v3/ws` with `speech_model=universal-3-5-pro`,
16 kHz PCM16 mono audio in 50 ms frames, from the backend using the `Authorization` header.

**Verified contract** **[vendor]**:

| Aspect | Value |
|---|---|
| Endpoint | `wss://streaming.assemblyai.com/v3/ws` |
| Query params | `sample_rate`, `speech_model`, `token` (browser only) |
| Auth | `Authorization` header server-side; `token` query param for browser/mobile |
| Audio | PCM16 signed little-endian, mono, 16 kHz, 50–1000 ms chunks |
| Client messages | `Terminate`, `ForceEndpoint`, `KeepAlive`, `UpdateConfiguration` |
| Server messages | `Begin`, `Turn`, `Termination`, `Error` |
| `Turn` fields | `turn_order`, `end_of_turn`, `turn_is_formatted`, `transcript`, `end_of_turn_confidence` |
| Tuning params | `format_turns`, `end_of_turn_confidence_threshold`, `min_turn_silence`, `max_turn_silence` |
| Latency claim | Sub-300 ms with formatted, immutable transcripts (Universal-3.5 Pro) |

**Rationale**: Frame size is set to the bottom of the supported range (50 ms, not 1000 ms) because
every millisecond of framing is added latency the shopper hears. `UpdateConfiguration` means turn
tuning is a live operation, satisfying the constitution's requirement that these be configuration
rather than code.

**Alternatives considered**: Browser-direct connection using the `token` parameter — rejected, see
R2. Larger frames for fewer syscalls — rejected, trades the one resource we cannot buy back.

**Design consequence**: `ForceEndpoint` gives us a manual turn-commit lever. Useful later if
endpointing proves slow on short utterances ("yes"), but not used in v1 — endpointing tuning is
tried first.

---

## R2. Where the STT socket lives — browser or backend

**Decision**: The browser streams audio to **our** backend over a WebSocket; the backend owns the
AssemblyAI socket.

**Rationale**: AssemblyAI explicitly supports browser-direct connections via short-lived tokens, and
that path saves one network hop. We reject it on two grounds that outrank ~20 ms:

1. **Turn boundaries are security-relevant.** Committed turns drive memory writes and tool
   execution, including "the customer confirmed the return". If the browser owns the STT socket, an
   untrusted client decides what was committed. Constitution principle II makes turn state
   authoritative — so it must be produced somewhere we trust.
2. **Observability.** `stt.turn` spans and queue backpressure cannot be measured on a stream the
   backend never sees.

**Alternatives considered**: Browser-direct with server-side re-verification — rejected as strictly
more complexity than just holding the socket ourselves.

**Cost accepted**: one intra-datacenter hop, budgeted at ≤ 20 ms inside the 30 ms orchestration
allowance **[ours]**.

---

## R3. LLM selection and configuration

**Decision**: Claude Opus 5 (`claude-opus-5`), streaming, `output_config: {"effort": "low"}`,
adaptive thinking left **on** (the model's default), `max_tokens ≈ 320`, `strict: true` on every
tool, prompt caching on the tools+system prefix, driven by a **manual** tool loop.

**Rationale — the thinking decision is the important one.** Thinking precedes the first visible
token, so it is a direct latency cost. The obvious optimisation is to disable it, which Opus 5
accepts at effort `high` or below. We reject that, because with thinking disabled Opus 5 has a
documented failure mode where it writes a tool call into **visible text** instead of emitting a
`tool_use` block: the turn succeeds, the tool never runs, and nothing raises. In this domain that
failure surfaces as *"I've started your return"* when no return exists — precisely the
zero-tolerance failure named in FR-030 and SC-011. A latency optimisation that can silently
fabricate a completed action is not a tradeoff we are willing to price.

The supported latency lever is `effort: "low"`, which reduces thinking depth and yields terser, less
preambled output — which the two-sentence voice persona (VID-003) wants regardless.

**Configuration rationale**:

- `max_tokens ≈ 320` — replies are capped at two sentences; deliberately short output is a valid
  reason to set this low.
- `strict: true` — guarantees `tool_use.input` validates against the schema at the provider
  boundary, ahead of our own validation. Requires `additionalProperties: false` + `required`.
- **Prompt caching** — render order is tools → system → messages, so the stable prefix (persona,
  policy text, 10 tool schemas) caches. Verify with `usage.cache_read_input_tokens`; a persistent
  zero means a silent invalidator (a timestamp in the system prompt, an unsorted tool list).
- **Manual tool loop, not the SDK Tool Runner** — the runner owns the loop; we must abort mid-stream
  on barge-in and emit our own per-hop spans. Needing control the runner does not expose is the
  documented reason to drop to a manual loop, and it avoids a beta dependency on the critical path.
- **Parallel tool results must be returned in a single user message** — splitting them across
  messages silently trains the model out of parallel calls, which costs latency on compound turns.

**Alternatives considered**:

| Option | Why not (now) |
|---|---|
| `thinking: {"type": "disabled"}` | Silent tool-call-in-text failure mode; unacceptable in a domain with state-changing actions |
| Claude Haiku 4.5 | Does no thinking by default and would likely be fastest; 200K context is ample. Held as the **explicit escalation** if measured TTFT misses budget — a user decision with numbers in hand, not a silent downgrade |
| Fast mode (`speed: "fast"`) | Up to 2.5× output tokens/sec on Opus 5, but a research preview with separate rate limits and premium pricing. Week-3 option, not a week-1 assumption |
| SDK Tool Runner | Owns the loop we need to abort |

**Open measurement**: `llm.ttft` against an 800 ms hard fail. Resolved in week 1.

---

## R4. TTS selection

**Decision**: ElevenLabs Flash v2.5 (`eleven_flash_v2_5`) over the streaming-input WebSocket at
`wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input`, with
`optimize_streaming_latency=4`, PCM output, and one persistent socket per session.

**Verified contract** **[vendor]**:

| Aspect | Value |
|---|---|
| Client → server | `text`, `timestamps`, `flush`, `close_connection` |
| Server → client | `audio` (binary), `timestamps`, `clear_buffer`, `error` |
| Initial latency | **200–500 ms** |
| Per-chunk latency | 100–300 ms |
| Fastest model | `eleven_flash_v2_5` |

**Rationale**: The selection criterion is not voice quality — it is whether the provider exposes a
**server-side interrupt primitive** and **character alignment**. ElevenLabs gives both:

- `clear_buffer` is literally the barge-in operation on the server side.
- `timestamps` (character alignment) is what makes "truncate memory to what the shopper actually
  heard" (constitution principle III) *exact*. Without alignment, that truncation is an estimate,
  and an estimate here means memory that disagrees with the shopper's experience.

**The honest risk**: documented 200–500 ms initial latency straddles our 400 ms hard fail **[ours]**
and exceeds our 250 ms p95 target. This is the plan's primary measurement gate.

**Mitigations, applied before escalating**: persistent pre-warmed socket (connect + TLS off the
critical path), `optimize_streaming_latency=4`, PCM output to skip client-side decode, clause-level
submission so synthesis begins on the first fragment rather than the first sentence.

**Alternatives considered**: Cartesia Sonic — pre-identified fallback, named as a candidate to
benchmark, **not** asserted as faster (we have not verified its numbers). Non-streaming TTS —
rejected outright: violates constitution principle I and cannot be interrupted.

**Escalation path if the gate fails**: benchmark Cartesia; if no provider hits 400 ms, amend the
constitutional budget with measured evidence. Per governance, a rule that proves unenforceable must
be automated or amended out — not quietly ignored.

---

## R5. Backend language and concurrency model

**Decision**: Python 3.11+ with FastAPI/uvicorn, one `asyncio.TaskGroup` per session and a nested
one per turn.

**Rationale**: The constitution's hardest principle to retrofit is V (cancellation over completion).
Structured concurrency provides it as a language property: cancelling the turn's task group cancels
the LLM stream, tool calls, and TTS pump together, with `CancelledError` propagating through
`async with` cleanup. That is "cancellation MUST propagate transitively" enforced by the runtime
rather than by every call site remembering. FastAPI provides native WebSocket endpoints for both
sockets, and all three vendors ship first-class Python clients.

**Alternatives considered**: Node/TypeScript with `AbortController` — a legitimate second choice;
`AbortSignal` propagation is manual per call site rather than structural. Chosen against only
because structured cancellation is the single riskiest requirement. Go — best raw latency, but the
vendor SDK story and iteration speed are worse for a 3-week build.

**Reversal condition**: if the team is materially faster in TypeScript, switch — nothing else in the
plan changes.

---

## R6. Browser audio capture and playback

**Decision**: AudioWorklet for both directions. Capture produces 50 ms PCM16 mono @ 16 kHz frames;
playback is a ring buffer that can be zeroed synchronously.

**Rationale**: Playback is the constraint that decides this. The barge-in budget is 100 ms p95, and
by the time we decide to interrupt, ~200 ms of synthesized audio is already buffered client-side.
An `<audio>` element or MediaSource buffers ahead inside the browser's audio pipeline where we
cannot reach, so neither can be silenced fast enough. An AudioWorklet ring buffer can be zeroed on
the next render quantum (~2.7 ms at 48 kHz).

This is also why the server-side `clear_buffer` alone is insufficient: it stops *production*, not
*playback*. Both are required, and they must fire independently rather than in sequence.

Capture at 16 kHz directly matches AssemblyAI's input format, avoiding a resample step on either
side.

**Alternatives considered**: `MediaRecorder` — produces encoded chunks at coarse intervals, wrong
format and wrong granularity. `ScriptProcessorNode` — deprecated and runs on the main thread, where
UI work would jitter frame timing.

---

## R7. Simulated backend

**Decision**: SQLite, seeded from a fixtures module, behind repository interfaces identical to those
a real order service would implement.

**Rationale**: Satisfies spec TC-008 (simulated tools expose the contract real ones will) and
constitution principle VII (deterministic core). Real query semantics and real (small) latency, zero
operational cost, one obvious replacement seam. Seeded fixtures also make every acceptance scenario
reproducible, including the awkward ones — split shipments, expired return windows, an order that
shipped between two turns.

**Alternatives considered**: In-memory dicts — rejected, hides the repository seam and makes the
later swap to a real service a rewrite rather than an adapter change. A real order API — out of
scope by the user's explicit decision.

---

## R8. Deployment target

**Decision (revised 2026-09-09)**: One process serving static assets + two WebSocket endpoints,
deployed on **Replit**.

**This decision was corrected after reading the lablab.ai Hackathon Rule Book.** The original choice
was Fly.io in region `iad`, picked for region pinning next to the vendor endpoints. That is not
available to us: the Rule Book states *"Demo Application Platform: Use Streamlit, Replit, or
Vercel"*, and warns that failing to adhere may mean a lower score or exclusion. Fly.io is not on the
list, so its technical merits are irrelevant.

**Rationale**: Of the three permitted platforms, only Replit runs a persistent process capable of
holding long-lived WebSocket servers, which is the entire workload — two inbound sockets and two
outbound vendor sockets per session. A single process also removes CORS and reverse-proxy config.

**Alternatives considered (within the permitted set)**: **Vercel** — serverless, no long-lived
WebSocket server; could host the static frontend only, which would put the backend off-platform and
make the submitted Application URL misleading. **Streamlit** — script-rerun execution model, hostile
to raw WebSockets and AudioWorklet audio paths. Both rejected on capability, not preference.

**Cost accepted, stated plainly**: we lose region pinning. Geographic round-trip time to the three
providers becomes a variable we do not control, which makes the latency gates *more* important —
they must be re-measured on the deployed instance (T077b), not only on localhost.

---

## Resolved unknowns

| Unknown from Technical Context | Resolution |
|---|---|
| Backend language/framework | Python 3.11+ / FastAPI (R5) |
| Frontend approach | TypeScript + Vite + AudioWorklet, no framework (R6) |
| LLM provider and settings | Claude Opus 5, effort `low`, thinking on, manual tool loop (R3) |
| TTS provider | ElevenLabs Flash v2.5 via stream-input WS (R4) |
| Storage | SQLite behind repository ports (R7) |
| STT socket ownership | Backend, not browser (R2) |
| Deployment | Replit — mandated by the Rule Book's permitted-platform list (R8) |

**No `NEEDS CLARIFICATION` markers remain.**

## Open measurement gates (week 1)

These are not unresolved decisions — they are decisions whose *viability* must be confirmed with
numbers before week 2 builds on them.

| Gate | Budget **[ours]** | Vendor claim **[vendor]** | If it fails |
|---|---|---|---|
| `tts.ttfb` | p95 250 ms, hard fail 400 ms | 200–500 ms initial | Benchmark Cartesia Sonic; else amend budget with evidence |
| `llm.ttft` | p95 450 ms, hard fail 800 ms | not published | Prompt caching → speculative dispatch → escalate model choice to the user |
| `stt.turn` | p95 300 ms, hard fail 500 ms | sub-300 ms claimed | Tune `min_turn_silence` / `end_of_turn_confidence_threshold`; raise with vendor |
| Barge-in end-to-end | p95 100 ms, hard fail 200 ms | n/a — ours to build | Profile the three concurrent paths; the client flush is the one that matters |

---

## R9. Measured gate results (2026-09-10)

Both at-risk budgets from plan.md Complexity Tracking are now closed with numbers rather
than estimates, and the provider line-up changed twice on the way. Recording what
actually happened, including the parts that did not go to plan.

### What the stack became, and why

| Slot | Planned | Actual | Why it changed |
|---|---|---|---|
| STT | AssemblyAI v3 | **AssemblyAI v3** | Unchanged. The one pinned vendor. |
| LLM | Claude Opus 5 | **Groq `openai/gpt-oss-120b`** | Claude requires a paid key. Groq is free and its gateway supports tool calling — which every tool in this project depends on. |
| TTS | ElevenLabs Flash v2.5 | **Groq / Canopy Labs Orpheus** | The ElevenLabs account had 1 of 10,000 free credits left. Orpheus runs on the Groq key already held. |

**AssemblyAI's own LLM Gateway was evaluated first and rejected on measurement**, not
preference: on the free tier exactly 1 of 34 models is reachable (`qwen3.5-4b-32k-fast`),
it returns `"does not support tools"`, and its TTFT measured 4,922 ms. Either fact alone
disqualifies it — the agent cannot look up an order without tools, and five seconds of
silence is not a conversation.

The swap cost **one adapter file**. `openai_compat.py` speaks Groq, Gemini, OpenAI and the
LLM Gateway from one implementation; the provider is a `.env` line. That is constitution
principle IV earning its keep on the day it was tested.

### Measured, from Pakistan

| Gate | Budget **[ours]** | Measured p50 | Measured p95 | Verdict |
|---|---|---|---|---|
| `llm.ttft` | 450 ms / hard fail 800 ms | 1,500 ms | — | **over, locally** |
| `tts.ttfb` | 250 ms / hard fail 400 ms | 610 ms | 687 ms (min **125 ms**) | **over, locally** |
| end-to-end | 1,000 ms / hard fail 1,500 ms | 2,891 ms | — | **over, locally** |

### The measurement is not valid where it was taken

A bare authenticated round trip to these US-hosted endpoints, carrying no inference at
all, measures **929 ms p50** from here (min 437 ms). AssemblyAI measures 1,078 ms.

So roughly 900 ms of every figure above is geography, and the models' own contributions
are far smaller — the TTS minimum of **125 ms** is the clearest evidence, since that is
what a fast network leaves behind.

**These numbers therefore neither pass nor fail the budgets.** They were taken half a
world from the inference, and the deployment target is Replit in the US, where that hop
does not exist. The gate is genuinely closed only by **T077b**, which re-measures from
the deployed instance. Reporting the local figures as a pass would be dishonest; treating
them as a fail would be equally wrong.

**No budget has been amended.** Amending one requires the governance procedure in the
constitution and measured evidence from the environment the budget applies to.

### Capability lost with the TTS swap, stated plainly

Orpheus is REST, not a socket, so two things R4 chose ElevenLabs for are gone:

- **No `clear_buffer`.** Barge-in latency is unaffected — the client-side `audio.flush`
  was always the half the listener experiences — but we can no longer stop the provider
  mid-clause. That costs credits, not milliseconds.
- **No character alignment.** `heard_prefix_len` becomes clause-proportional rather than
  character-exact. Still bounded by audio genuinely played, at coarser resolution.

Recoverable with a fresh ElevenLabs account; the adapter is still in the tree.

---

## R10. T077b — measured on the deployed instance (2026-09-10)

Deployed to Replit (US) at `…pike.replit.dev`, both keys set as Replit Secrets,
`/api/health` reporting `{stt: assemblyai, llm: groq, tts: groq-orpheus}`. This is the
measurement R9 said was the only one that could actually close the gates.

### The result

| Span | Budget p95 / hard fail **[ours]** | Local (Pakistan) | **Deployed (US)** | Verdict |
|---|---|---|---|---|
| `llm.ttft` | 450 / 800 ms | 1,500 ms | **1,063 ms** | over hard fail |
| `tool` | — | 0 ms | **11 ms** | fine |
| `tts.ttfb` | 250 / 400 ms | 2,437 ms | 1,723 ms → **~660 ms corrected** | over hard fail |
| end-to-end | 1,000 / 1,500 ms | 2,891 ms | **2,609 ms** | over hard fail |

### R9's central claim was wrong, and this is the correction

R9 argued that roughly 900 ms of the local figures was Pakistan→US network round trip,
and implied the deployed numbers would therefore land near budget. **They did not.**
End-to-end improved from 2,891 ms to 2,609 ms — about 280 ms, not 900.

The reasoning error is worth naming: the spans are recorded **server-side**, so a client's
distance from the server was never inside them. What the deployment removed was the
*server's* distance from Groq, which is real but much smaller than the client RTT I had
measured and then wrongly attributed to the same figures. The 929 ms RTT number was
correct; using it to predict these spans was not.

**The models are genuinely slower than the budgets.** That is the honest finding.

### A measurement defect found in the same run

`tts.ttfb` was being recorded from **turn start** rather than from the LLM's first token,
which is the interval the constitution's budget actually names. It therefore included the
whole language-model latency and reported ~1,723 ms where the synthesis interval was
around 660 ms — roughly three times the thing being judged.

Fixed (`TurnTimings.mark_since`). Every prior `tts.ttfb` figure in this document, R9
included, was inflated the same way. The corrected value still exceeds the 400 ms hard
fail, so the conclusion does not change — but the number that decision rests on is now
the right number.

### Where this leaves the budgets

**No budget is amended here.** The constitution requires that through governance, with
evidence, and the choice belongs to the project architect. The options, with what each
actually costs:

1. **Implement speculative dispatch (T054).** This is the designed mitigation and it is
   still unbuilt. It dispatches the model on a high-confidence partial turn, so the
   1,063 ms of `llm.ttft` runs during the shopper's trailing silence instead of after it.
   It cannot help `tts.ttfb`, and it is the only option that improves what the shopper
   perceives without changing a provider.
2. **Change the speech provider.** ~660 ms against a 400 ms hard fail is the largest
   single gap. A streaming TTS would also restore `clear_buffer` and character alignment,
   both of which were lost with Orpheus.
3. **Amend the budgets to what free-tier providers can deliver**, with these measurements
   as the evidence, and say so plainly rather than reporting a pass that is not one.

Doing none of these and quoting the budgets as met is the one option the constitution
rules out.
