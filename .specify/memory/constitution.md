<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.0 → 1.1.0
Bump rationale: MINOR — new section added ("External Constraints — Hackathon Compliance"). No
principle removed or redefined; no prohibition lifted. Added after scanning the official hackathon
page and lablab.ai Rule Book on 2026-09-09, which imposed constraints the v1.0.0 document did not
know about (permitted deployment platforms, mandatory submission artifacts, MIT licensing, the
four-criteria rubric, and the hard 8:00 PM PKT deadline).

Prior history:
  (unversioned template) → 1.0.0 — MAJOR, initial ratification of the governance baseline.

Modified principles:
  - [PRINCIPLE_1_NAME] (placeholder) → I. Streaming Everything, Buffer Nothing
  - [PRINCIPLE_2_NAME] (placeholder) → II. The Turn Is the Unit of Truth
  - [PRINCIPLE_3_NAME] (placeholder) → III. Barge-In Is a First-Class Path
  - [PRINCIPLE_4_NAME] (placeholder) → IV. Ports and Adapters, One Pinned Vendor
  - [PRINCIPLE_5_NAME] (placeholder) → V. Cancellation Over Completion
  - [PRINCIPLE_6_NAME] (placeholder) → VI. Every Turn Is Traceable
  - (new)                            → VII. Deterministic Core, Simulated Edges

Added sections:
  in 1.1.0:
  - External Constraints — Hackathon Compliance   (deployment platform, mandatory submission
    artifacts, MIT licensing, four-criteria rubric, hard deadline, precedence rule)
  in 1.0.0:
  - Architectural Principles          (replaces [SECTION_2_NAME])
  - Code Quality Standards            (replaces [SECTION_3_NAME])
  - Latency & Performance Requirements
  - Error Handling Philosophy
  - Security & Privacy Rules
  - Prohibitions — What We Will NEVER Do

Removed sections: none.

Downstream artifacts updated for 1.1.0:
  - ✅ specs/001-order-support-agent/plan.md   — deployment Fly.io → Replit; submission artifact
       table; judging-criteria mapping; build deadline pulled to Sep 27
  - ✅ specs/001-order-support-agent/tasks.md  — Phase 4 (T081–T086) added; T077 retargeted to
       Replit; T077b added for deployed-instance latency
  - ✅ specs/001-order-support-agent/spec.md   — Positioning section added (originality risk)

Templates requiring updates:
  - ✅ .specify/templates/plan-template.md   — Constitution Check gates made concrete
  - ✅ .specify/templates/spec-template.md   — reviewed, no change required (principle-neutral)
  - ✅ .specify/templates/tasks-template.md  — reviewed, no change required (principle-neutral)
  - ✅ .claude/commands/*.md                 — reviewed, agent-generic, no stale references
  - ⚠  README.md / docs/quickstart.md        — do not exist yet; MUST link this constitution when created

Deferred TODOs:
  - TODO(PROJECT_NAME): "Realtime Voice Agent" is a working name inferred from the repository
    (hackathone) and the stated goal. Rename via a PATCH amendment once the product name is set.

Vendor-verified facts (AssemblyAI docs, retrieved 2026-09-09) used as hard constraints below:
  endpoint wss://streaming.assemblyai.com/v3/ws; PCM16 mono 16 kHz; 50-1000 ms chunks;
  client messages Terminate / ForceEndpoint / KeepAlive / UpdateConfiguration; server messages
  Begin / Turn / Termination / Error; Turn fields turn_order, end_of_turn, turn_is_formatted,
  transcript, end_of_turn_confidence; tuning params format_turns, end_of_turn_confidence_threshold,
  min_turn_silence, max_turn_silence; Universal-3.5 Pro streaming advertises sub-300 ms latency with
  immutable transcripts. All other numbers in this document are OUR budgets, not vendor guarantees.
-->

# Realtime Voice Agent Constitution

## Core Principles

### I. Streaming Everything, Buffer Nothing (NON-NEGOTIABLE)

Audio, transcripts, tokens, and synthesized speech MUST move through the system as streams. No
stage may wait for a complete artifact when a partial one is actionable.

- Microphone audio MUST be framed at 50 ms, PCM16 signed little-endian, mono, 16 kHz, and written
  to the AssemblyAI WebSocket as it is captured. Never accumulate an utterance and send it at once.
- The LLM MUST be invoked with streaming enabled; the first token MUST be forwarded downstream
  without waiting for the completion.
- TTS MUST consume LLM output incrementally at clause or sentence boundaries and MUST emit audio
  frames as they are produced.
- Any component that cannot stream is disqualified from the critical path. It may only be used
  off-path (analytics, logging, post-call summarization).

**Rationale:** Perceived conversational quality is dominated by time-to-first-audio, not by total
processing time. Every buffer is latency the user hears as a pause.

### II. The Turn Is the Unit of Truth

Conversation state is modelled as an explicit, ordered sequence of turns derived from the
AssemblyAI `Turn` message. Turns are the only thing the rest of the system reasons about.

- `turn_order` from the STT provider is the canonical ordering key. We MUST NOT invent our own
  turn sequencing or re-order turns.
- A turn is *committed* only when `end_of_turn == true`. Partial turns are advisory: they may drive
  UI, barge-in decisions, and speculative LLM prefetch, but MUST NOT be written to conversation
  memory.
- The critical path MUST consume the unformatted `transcript` for speed. Formatted text
  (`turn_is_formatted == true`) is for display, transcripts, and logs only, and MUST NOT block
  LLM dispatch.
- `end_of_turn_confidence` MUST be threshold-gated and the threshold MUST be a configuration value,
  never a literal in business logic.
- Turn-detection tuning (`min_turn_silence`, `max_turn_silence`, `end_of_turn_confidence_threshold`,
  `format_turns`) is configuration. Changing it MUST NOT require a code change.

**Rationale:** A single, provider-anchored definition of "a turn" prevents the class of bugs where
memory, UI, and the LLM disagree about what the user actually said.

### III. Barge-In Is a First-Class Path (NON-NEGOTIABLE)

The user can speak at any moment, including mid-sentence while the agent is talking. Interruption
is a designed, tested code path — not an error path and not an afterthought.

- While the agent is speaking, incoming speech MUST be evaluated for interruption intent. Confirmed
  interruption MUST immediately: (a) stop TTS playback, (b) flush the un-played audio buffer,
  (c) cancel the in-flight LLM request, and (d) truncate the assistant message in memory to what was
  *actually heard by the user*, never to what was generated.
- Backchannels ("mhm", "yeah", "okay", "right") MUST NOT trigger interruption while the agent is
  speaking. A filter combining a minimum word count and a backchannel token set MUST be applied,
  with a grace window after the agent stops speaking.
- The backchannel set MUST be domain-configurable. Tokens that are semantically load-bearing in the
  active flow (e.g. a bare "yes" in a booking confirmation) MUST be removable from the filter.
- Every interruption MUST emit a structured event with the turn id, the barge-in latency, and
  whether it was suppressed as a backchannel.

**Rationale:** Memory that records words the user never heard poisons every subsequent turn. Agents
that cannot be interrupted are not conversations; agents that interrupt on "mhm" are unusable.

### IV. Ports and Adapters — One Pinned Vendor, Zero Pinned Assumptions

AssemblyAI Realtime STT is the only vendor this project commits to. Every other external
capability sits behind an interface owned by us.

- The core conversation engine MUST depend only on our own port interfaces: `SpeechRecognizer`,
  `LanguageModel`, `SpeechSynthesizer`, `ToolRegistry`, `ConversationMemory`.
- Vendor SDK types, exceptions, and payload shapes MUST NOT cross an adapter boundary into the core.
  Adapters translate to our domain types; leaking a provider type into core is a review blocker.
- LLM and TTS choices are deliberately undecided. Any code that makes them hard to swap — provider
  names in core modules, prompt formats coupled to one vendor's message schema, voice ids in
  business logic — is a violation regardless of whether it works.
- Swapping the LLM or TTS provider MUST require changing only an adapter and configuration. This is
  verified by maintaining at least two adapters (one real, one fake) per port at all times.

**Rationale:** We chose the harder STT path precisely to own the orchestration. That ownership is
worthless if we then hard-couple to an LLM or TTS vendor we have not yet evaluated.

### V. Cancellation Over Completion (NON-NEGOTIABLE)

Every operation downstream of user speech MUST be abortable, and abort MUST be the cheap path.

- Every LLM call, TTS call, and tool invocation MUST accept a cancellation token / abort signal
  scoped to the turn that caused it. Fire-and-forget async work is forbidden on the critical path.
- Cancellation MUST propagate transitively: cancelling a turn cancels its LLM stream, which cancels
  its TTS stream, which stops playback and releases the audio device buffer.
- A cancelled turn MUST leave no side effects in conversation memory beyond a truncation marker.
- Orphaned work is a defect, not noise. Any task still running more than 200 ms after its turn was
  cancelled MUST be logged at WARN with its turn id.

**Rationale:** In a barge-in world, most generated tokens are discarded. A system that can only run
work to completion will talk over its user.

### VI. Every Turn Is Traceable

Observability is part of the feature, not instrumentation added later.

- Every turn MUST carry a `turn_id` (our uuid) and `session_id` propagated through STT handling,
  LLM call, tool calls, TTS, and playback.
- Each pipeline hop MUST record a timed span. The seven mandatory spans are: `audio.capture`,
  `stt.turn`, `llm.ttft`, `llm.complete`, `tool.<name>`, `tts.ttfb`, `playback.start`.
- Logs MUST be structured (JSON), MUST include `session_id` and `turn_id`, and MUST NOT contain raw
  audio bytes.
- The following counters are mandatory and MUST be exported: turns completed, barge-ins,
  backchannel suppressions, STT reconnects, LLM errors by class, tool failures by name, and
  end-to-end latency histograms.
- "It felt slow" is not a bug report we accept. If a latency regression cannot be attributed to a
  span, the missing span is the first defect to fix.

**Rationale:** Real-time pipelines fail probabilistically and non-reproducibly. Without per-turn
traces, debugging degrades to guesswork.

### VII. Deterministic Core, Simulated Edges

The conversation engine MUST be testable without a microphone, a network, or a vendor account.

- Core logic (turn state machine, interruption decisions, memory management, tool dispatch) MUST be
  pure with respect to I/O and unit-testable in-process.
- Time MUST be injected. `sleep`, wall-clock reads, and timers in core logic are violations; tests
  MUST be able to advance time deterministically.
- STT behaviour MUST be reproducible in tests via recorded `Turn` / `Begin` / `Termination` /
  `Error` message fixtures replayed through a fake adapter. Barge-in, mid-turn disconnects, and
  low-confidence endpointing MUST each have a fixture.
- At least one contract test MUST validate our fixtures against the real AssemblyAI message schema,
  so our fakes cannot silently drift from the provider.
- No test on the critical path may depend on a live LLM or TTS call.

**Rationale:** A voice agent whose logic can only be exercised by speaking into a laptop will never
be refactored safely.

## Architectural Principles

The system is a pipeline of independently replaceable stages coordinated by a single orchestrator
that owns turn state.

1. **Single writer for conversation state.** Exactly one component (the Orchestrator) mutates turn
   state. Adapters emit events; they never mutate state directly.
2. **Event-driven seams.** Stages communicate over bounded async queues carrying typed events
   (`UserPartial`, `UserTurnCommitted`, `AgentTokens`, `AgentAudio`, `Interrupted`, `ToolRequested`,
   `ToolCompleted`). Queues MUST be bounded; unbounded queues hide latency as memory growth.
3. **Backpressure is explicit.** A full queue MUST cause a defined, logged degradation (drop oldest
   partials, never drop committed turns), never a silent block on the audio thread.
4. **The audio ingest loop is sacred.** It performs capture and socket write only. No transcription
   handling, no LLM work, no logging I/O, no lock acquisition that a slow consumer can hold.
5. **Memory is layered and bounded.** Working memory (recent turns, verbatim), summary memory
   (compacted older turns), and durable memory (facts/preferences) are separate stores with separate
   retention rules. Token budget per layer MUST be configured and enforced; the LLM context MUST NOT
   grow without bound across a long call.
6. **Tool calling is a contract, not a convention.** Every tool declares a JSON Schema for inputs and
   outputs. Arguments from the LLM MUST be schema-validated before execution. Side-effecting tools
   MUST be idempotent or accept an idempotency key derived from `turn_id`.
7. **Tools are latency-classified.** Every tool is declared `fast` (<300 ms, may block the reply) or
   `slow` (MUST be acknowledged with filler speech and resolved asynchronously). An unclassified
   tool MUST NOT be registered.
8. **Speculative execution is allowed, commitment is not.** The LLM MAY be dispatched on a
   high-confidence partial turn to hide latency, but its output MUST NOT reach TTS or memory until
   the turn is committed, and it MUST be cancelled if the user keeps speaking.
9. **Session lifecycle is explicit.** STT sessions MUST be opened with configured parameters, kept
   alive with `KeepAlive` during silence, ended with `Terminate`, and MUST support reconnection with
   conversation state preserved.
10. **Configuration over branching.** Model names, thresholds, timeouts, voices, and budgets live in
    typed, validated configuration loaded at startup. Environment-sniffing `if` statements in core
    logic are violations.

## Code Quality Standards

- **Typed, strictly.** Static typing is mandatory across the codebase with strict settings enabled.
  Untyped public functions and escape hatches (`Any`, `any`, `# type: ignore`, `as unknown`) require
  an inline comment justifying them; unjustified escapes fail review.
- **Async correctness is reviewed explicitly.** Every `await` on the critical path MUST have a
  timeout. Blocking calls inside async contexts are defects.
- **Smallest viable diff.** No opportunistic refactors bundled with feature work.
- **Functions do one thing.** Any function mixing I/O, state mutation, and decision logic MUST be
  decomposed before merge.
- **No magic numbers.** Every timing, threshold, and budget is a named constant or configuration
  key with a comment stating its unit.
- **Errors are typed.** No bare `except:` / `catch (e)` that swallows. Every caught error is either
  handled with a defined recovery, or re-raised as a domain error with context attached.
- **Lint, format, type-check gates run in CI and block merge.** Formatting is automated and never
  debated in review.
- **Tests accompany behaviour changes.** New critical-path behaviour without a test does not merge.
  The turn state machine and interruption logic MUST maintain the highest coverage in the codebase.
- **Public interfaces are documented with their failure modes and latency class**, not just their
  parameters.
- **Dependencies are justified.** Each new runtime dependency on the critical path requires a
  one-line rationale in the PR. Prefer the standard library.

## Latency & Performance Requirements

Latency is a correctness requirement. A functionally perfect response delivered late is a defect.

All figures are measured server-side per turn and MUST be exported as histograms. `p95` values are
the enforced gates; `hard fail` breaches page on-call and block release.

| Segment | Span | p50 | p95 | Hard fail |
|---------|------|-----|-----|-----------|
| Capture → first byte on STT socket | `audio.capture` | 20 ms | 50 ms | 100 ms |
| End of user speech → committed turn | `stt.turn` | 150 ms | 300 ms | 500 ms |
| Committed turn → LLM first token | `llm.ttft` | 250 ms | 450 ms | 800 ms |
| LLM first token → TTS first audio byte | `tts.ttfb` | 120 ms | 250 ms | 400 ms |
| Orchestrator overhead, all hops summed | (derived) | 10 ms | 30 ms | 50 ms |
| **End of user speech → first agent audio** | **(derived)** | **600 ms** | **1000 ms** | **1500 ms** |
| Interruption detected → agent audio silent | `playback.stop` | 50 ms | 100 ms | 200 ms |

Additional binding requirements:

- The end-to-end figure is measured, not computed by summing component p95s. Component gates and the
  end-to-end gate are enforced independently.
- Audio frames are 50 ms. Larger frames trade latency for throughput and are not permitted on the
  ingest path even though the provider accepts up to 1000 ms.
- The `stt.turn` budget tracks a provider capability (sub-300 ms advertised for Universal-3.5 Pro
  streaming). If the provider misses it, we raise it with the vendor; we do not silently relax our
  end-to-end gate.
- Memory footprint per active session MUST be bounded and asserted in a soak test. A 30-minute
  conversation MUST NOT show unbounded growth in queue depth, memory, or LLM context size.
- Performance tests run in CI against recorded fixtures. A PR that regresses any p95 by more than
  10% fails the build.
- Filler speech ("let me check that") MAY mask slow tool calls but MUST NOT be used to mask a
  latency regression in a `fast` path.

## Error Handling Philosophy

**The conversation must never go silent without explanation, and must never lie about what it did.**

1. **Classify before reacting.** Every error is exactly one of: `Transient` (retry), `Degraded`
   (fall back), `Fatal` (end the turn gracefully), or `Fatal-Session` (end the call gracefully).
   Unclassified errors are treated as `Fatal` for the turn.
2. **Retry only what is safe.** Retries with jittered exponential backoff are permitted for
   idempotent network operations. A turn that already produced audible speech MUST NOT be retried in
   a way that repeats speech to the user.
3. **Degrade loudly to the operator, gracefully to the user.** Every fallback emits a WARN with
   cause and turn id; the user hears a short, honest recovery line, never a stack trace, an error
   code, or silence.
4. **Timeouts everywhere, defaults nowhere.** Every external call has an explicit timeout derived
   from its latency budget. There is no "infinite wait" on the critical path.
5. **STT disconnects are expected, not exceptional.** The STT adapter MUST auto-reconnect with
   backoff, preserve conversation state across reconnects, and surface a `Degraded` state to the
   orchestrator. Audio captured during a reconnect gap is dropped and the gap is logged; it is never
   silently replayed into a later turn as if it were current speech.
6. **Circuit-break persistent failures.** After a configured number of consecutive failures a
   provider is opened out for a cooldown and the fallback path is used. Hammering a failing provider
   at conversational rates is prohibited.
7. **Tool failures are conversational events.** A failed tool returns a structured error to the LLM
   so the agent can respond honestly. Tool failures MUST NOT crash the turn, and the agent MUST NOT
   claim an action succeeded when its tool call failed.
8. **Fail fast in development, degrade in production.** Configuration and schema validation errors
   crash at startup. They are never papered over with runtime defaults.

## Security & Privacy Rules

- **No secrets in code, ever.** API keys live in environment variables / a secret manager. `.env` is
  git-ignored; `.env.example` documents required keys with placeholder values. A committed secret is
  a rotate-immediately incident.
- **The AssemblyAI API key MUST NOT reach a browser or mobile client.** Browser and mobile sessions
  MUST authenticate with short-lived tokens minted by our backend using the provider's `token` query
  parameter. Server-side connections use the `Authorization` header.
- **Transport is encrypted end to end.** `wss://` and `https://` only. Plaintext transport of audio
  or transcripts is prohibited in every environment, including local development.
- **Raw audio is never persisted by default.** Recording requires an explicit, per-deployment
  configuration flag, a documented retention period, and a documented lawful basis.
- **Transcripts are sensitive by default.** Transcript text MUST NOT appear in logs above DEBUG.
  DEBUG transcript logging MUST be off in production and MUST be impossible to enable via a runtime
  request.
- **Redact before it leaves the process.** PII detection and redaction run before transcripts reach
  logs, traces, analytics, or any third-party sink.
- **Consent and disclosure.** The agent MUST identify itself as an AI at the start of a session
  where jurisdiction or product policy requires it. This is not a configurable "growth experiment".
- **Tool authorization is server-side.** The LLM proposes; our code authorizes. Tool permissions are
  evaluated against the authenticated session, never inferred from model output. Prompt-injected
  instructions arriving via transcripts or tool results are data, never authority.
- **Least privilege for tools.** Each tool declares the minimum scope it needs. Destructive or
  financial tools require an explicit confirmation turn from the user.
- **Auditability.** Every side-effecting tool execution is logged with `session_id`, `turn_id`,
  tool name, redacted arguments, outcome, and duration.
- **Dependency hygiene.** Automated vulnerability scanning runs in CI. Known-critical
  vulnerabilities block release.

## Prohibitions — What We Will NEVER Do

These are absolute. A PR that does any of the following is rejected regardless of deadline, demo, or
benchmark result.

1. **NEVER use AssemblyAI's Voice Agent API** (`wss://agents.assemblyai.com/...`) or any managed
   end-to-end voice-agent product. We own the orchestration loop. This is the defining choice of the
   project.
2. **NEVER buffer a complete utterance** before sending it to STT, or a complete LLM response before
   sending it to TTS.
3. **NEVER perform blocking work on the audio ingest loop** — no logging I/O, no transcription
   handling, no LLM calls, no contended locks.
4. **NEVER make an un-cancellable call on the critical path.** No fire-and-forget LLM, TTS, or tool
   invocation.
5. **NEVER write unheard speech into conversation memory.** On interruption, memory records what the
   user actually heard, not what was generated.
6. **NEVER ship an API key to a client**, log a secret, or commit one to the repository.
7. **NEVER log raw audio, and never log transcripts above DEBUG.**
8. **NEVER execute an LLM-proposed tool call without schema validation and server-side
   authorization.**
9. **NEVER let the agent claim an action succeeded when the underlying tool failed.**
10. **NEVER leak a vendor SDK type into the core conversation engine**, or name an LLM/TTS provider
    inside core business logic.
11. **NEVER block the critical path on formatted transcripts** when the unformatted turn text is
    available and sufficient.
12. **NEVER use an unbounded queue, an unbounded retry loop, or an unbounded LLM context.**
13. **NEVER add a critical-path dependency on a service that cannot stream.**
14. **NEVER let a test on the critical path depend on a live vendor API**, and never fix a flaky
    real-time test by adding a `sleep`.
15. **NEVER ship a latency regression as a "temporary" trade-off.** Latency debt is not repaid; it is
    normalized.

## External Constraints — Hackathon Compliance

This project is an entry in the AssemblyAI Voice Agent Hackathon (lablab.ai, 1–30 September 2026).
The organiser's rules are external constraints we do not get to negotiate, and they outrank our own
engineering preferences wherever the two conflict. Verified against the hackathon page and the
lablab.ai Hackathon Rule Book on 2026-09-09.

- **Chosen path is Realtime Speech-to-Text, not the Voice Agent API.** The hackathon offers two
  paths; we deliberately took the harder one — real-time STT over WebSocket with our own
  orchestration, LLM, and TTS. Prohibition 1 already encodes this and remains correct.
- **Deployment platform MUST be Streamlit, Replit, or Vercel.** No other host is permitted for the
  submitted Application URL, whatever its technical merits. Choosing an unlisted platform risks
  exclusion.
- **Submission artifacts are deliverables, not paperwork.** Project title, short and long
  descriptions, technology and category tags, a 16:9 PNG/JPG cover image, an MP4 video, a PDF slide
  deck, a **public** GitHub repository, and a live Application URL are each mandatory. An incomplete
  submission may be scored down or excluded regardless of software quality.
- **The repository MUST be public and MIT-compliant.** A `LICENSE` file is a build artifact.
- **Submission deadline is 30 September 2026, 8:00 PM Pakistan Standard Time**, and it is a hard
  stop. The 6-hour manual-submission window requires prior organiser approval and a valid reason; it
  MUST NOT be planned around.
- **Judging is four equally weighted criteria**: Application of Technology, Presentation, Business
  Value, Originality. Two of the four are graded on communication artifacts rather than code.
  Engineering excellence alone cannot carry a submission, and this constitution's technical rigour
  MUST NOT be used as a reason to under-invest in the other two.
- **Originality is scored.** Where our differentiator overlaps with other public submissions, the
  honest response is to reposition on what is genuinely distinctive — never to overstate novelty in
  the description. Prohibition 9's spirit applies to our own claims about our own work.

**Precedence rule**: where a hackathon rule conflicts with a preference expressed elsewhere in this
constitution, the hackathon rule wins and the conflict MUST be recorded in the affected plan.
Where a hackathon rule would require violating a NON-NEGOTIABLE principle, escalate to the project
architect rather than silently choosing one.

## Governance

**Authority.** This constitution supersedes all other practices, conventions, and preferences in
this repository. Where a README, a comment, a habit, or an agent instruction conflicts with this
document, this document wins.

**Amendment procedure.**

1. Amendments are proposed as a PR modifying this file, with a rationale section stating the problem
   the current rule causes.
2. The PR MUST update the Sync Impact Report comment at the top of this file.
3. The PR MUST propagate the change to dependent artifacts in the same PR:
   `.specify/templates/plan-template.md`, `.specify/templates/spec-template.md`,
   `.specify/templates/tasks-template.md`, and any runtime guidance docs.
4. Amendments touching a NON-NEGOTIABLE principle require an accompanying ADR in `history/adr/`
   documenting the alternatives considered and the migration plan.
5. Amendments require explicit approval from the project architect. Silence is not consent.

**Versioning policy.** Semantic versioning applies to this document:

- **MAJOR** — a principle is removed or redefined in a backward-incompatible way; a prohibition is
  lifted; governance authority changes.
- **MINOR** — a new principle or section is added, or existing guidance is materially expanded.
- **PATCH** — clarifications, wording, typo fixes, and non-semantic refinements, including filling
  the deferred project name.

**Compliance review.**

- Every PR description MUST state which principles the change engages and how it complies.
- `/sp.plan` MUST run the Constitution Check gate before Phase 0 research and re-run it after Phase 1
  design. Violations MUST be recorded in the plan's Complexity Tracking table with a justification
  and the simpler alternative that was rejected — or the design MUST change.
- Unjustified complexity is rejected by default. The burden of proof is on the addition.
- Latency budgets and the prohibition list are enforced in CI, not by reviewer memory. Any rule here
  that proves unenforceable by automation MUST either become automated or be amended out.

**Runtime guidance.** Day-to-day agent and contributor workflow lives in `CLAUDE.md`. It implements
this constitution; it does not override it.

**Version**: 1.1.0 | **Ratified**: 2026-09-09 | **Last Amended**: 2026-09-09
