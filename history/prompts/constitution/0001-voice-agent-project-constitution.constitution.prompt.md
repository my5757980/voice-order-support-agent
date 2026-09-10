---
id: 0001
title: Voice Agent Project Constitution
stage: constitution
date: 2026-09-09
surface: agent
model: claude-opus-5
feature: none
branch: none (not a git repository)
user: my5757980@gmail.com
command: /sp.constitution
labels: ["voice-agent", "assemblyai", "realtime-stt", "constitution", "latency", "architecture", "barge-in"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - .specify/memory/constitution.md (created from template, v1.0.0)
  - .specify/templates/plan-template.md (Constitution Check gates made concrete)
  - history/prompts/constitution/0001-voice-agent-project-constitution.constitution.prompt.md (this file)
tests:
  - none (documentation artifact; no executable code introduced)
---

## Prompt

You are a senior software architect specializing in real-time voice agents.

We are building a production-grade Voice Agent using AssemblyAI's Realtime Speech-to-Text API (the harder path). We will NOT use AssemblyAI's Voice Agent API.

Tech constraints:
- Speech-to-Text: AssemblyAI Realtime STT (WebSocket)
- LLM: We will decide later (Claude / GPT / Gemini)
- Text-to-Speech: We will decide later
- Architecture must be modular, observable, and low-latency
- Must support interruptions, turn-taking, tool calling, and conversation memory

Create a clear "Project Constitution" document that includes:

1. Core Principles (non-negotiable rules)
2. Architectural Principles
3. Code Quality Standards
4. Latency & Performance Requirements
5. Error Handling Philosophy
6. Security & Privacy Rules
7. What we will NEVER do in this project

Be strict and opinionated. This constitution will guide all future decisions.

## Response snapshot

Ratified `.specify/memory/constitution.md` at **v1.0.0** (initial ratification — the file was an
unfilled template with zero defined principles).

Before writing, the AssemblyAI v3 streaming contract was verified against live vendor documentation
via the context7 MCP server rather than assumed from memory, per the Authoritative Source Mandate.
Verified facts encoded as hard constraints: endpoint `wss://streaming.assemblyai.com/v3/ws`; PCM16
mono 16 kHz audio in 50–1000 ms chunks; client messages `Terminate` / `ForceEndpoint` / `KeepAlive` /
`UpdateConfiguration`; server messages `Begin` / `Turn` / `Termination` / `Error`; `Turn` fields
`turn_order`, `end_of_turn`, `turn_is_formatted`, `transcript`, `end_of_turn_confidence`; tuning
params `format_turns`, `end_of_turn_confidence_threshold`, `min_turn_silence`, `max_turn_silence`;
Universal-3.5 Pro streaming advertises sub-300 ms latency with immutable transcripts. Docs also
confirmed two design-shaping details: voice agents should consume the *unformatted* utterance for
speed (formatted finals are for display), and barge-in requires backchannel filtering with a
minimum word count plus a grace window after the agent stops speaking.

Structure delivered — all seven requested sections plus Governance:

- **Core Principles** (7, two more than the template's slots): I. Streaming Everything, Buffer
  Nothing (NON-NEGOTIABLE); II. The Turn Is the Unit of Truth; III. Barge-In Is a First-Class Path
  (NON-NEGOTIABLE); IV. Ports and Adapters — One Pinned Vendor, Zero Pinned Assumptions;
  V. Cancellation Over Completion (NON-NEGOTIABLE); VI. Every Turn Is Traceable; VII. Deterministic
  Core, Simulated Edges.
- **Architectural Principles** — 10 rules: single writer for conversation state, bounded event
  queues with explicit backpressure, a sacred audio ingest loop, layered/bounded memory,
  schema-validated tool contracts with idempotency keys, `fast`/`slow` tool latency classification,
  speculative-but-uncommitted LLM dispatch, explicit STT session lifecycle, configuration over
  branching.
- **Code Quality Standards** — strict typing, mandatory timeouts on every critical-path `await`,
  no magic numbers, typed errors, CI-blocking gates.
- **Latency & Performance Requirements** — a p50/p95/hard-fail table per span. Headline gate:
  end of user speech → first agent audio, p50 600 ms / p95 1000 ms / hard fail 1500 ms; barge-in to
  silence p95 100 ms. End-to-end is explicitly *measured*, not summed from component p95s.
- **Error Handling Philosophy** — four-way error classification (`Transient` / `Degraded` / `Fatal` /
  `Fatal-Session`), no retry that repeats already-audible speech, expected STT reconnects with
  state preserved, circuit breakers, tool failures as conversational events.
- **Security & Privacy Rules** — API key never reaches a client (short-lived `token` for
  browser/mobile), no raw audio persisted by default, transcripts never logged above DEBUG,
  server-side tool authorization, prompt-injected text treated as data and never authority.
- **Prohibitions** — 15 absolute NEVERs, headed by "NEVER use AssemblyAI's Voice Agent API", which
  is recorded as the defining choice of the project.
- **Governance** — amendment procedure (NON-NEGOTIABLE changes require an ADR), semver policy, and
  compliance review binding `/sp.plan` to the Constitution Check gate.

Consistency propagation: `plan-template.md`'s placeholder `[Gates determined based on constitution
file]` was replaced with a concrete 10-item gate checklist so the constitution is enforced at
planning time rather than by reviewer memory. `spec-template.md`, `tasks-template.md`, and
`.claude/commands/*.md` were reviewed and needed no change (principle-neutral, agent-generic).

Deferred: `TODO(PROJECT_NAME)` — "Realtime Voice Agent" is a working name inferred from the repo
directory and stated goal; renaming is a PATCH amendment.

## Outcome

- ✅ Impact: Project now has a binding v1.0.0 governance baseline covering all seven requested areas,
  with vendor-verified protocol constraints and numeric latency gates. Planning is now gated on it.
- 🧪 Tests: None run — documentation artifact, no executable code. Validation performed instead:
  zero unresolved bracket/placeholder tokens (`grep -nE '\[[A-Z_]{3,}\]|\{\{[A-Z_]+\}\}'` → NONE),
  version line matches the Sync Impact Report, dates in ISO `YYYY-MM-DD` form.
- 📁 Files: 2 modified/created under `.specify/`, plus this PHR.
- 🔁 Next prompts: `/sp.adr` for the two pending architectural decisions (LLM provider selection,
  TTS provider selection); then `/sp.specify` for the first vertical slice — a single end-to-end
  turn: mic → AssemblyAI v3 WebSocket → committed turn → stub LLM → stub TTS → playback, with the
  seven mandatory spans wired from day one.
- 🧠 Reflection: The strongest constraints came from the vendor docs, not from generic voice-agent
  intuition — specifically "use unformatted transcript on the critical path" and the backchannel
  suppression pattern. Checking docs first changed the content of two principles. Latency numbers
  are deliberately split into independently-enforced component gates and a separately-measured
  end-to-end gate, because summing component p95s would have produced an arithmetically dishonest
  budget (300 + 450 + 250 > 1000).

## Evaluation notes (flywheel)

- Failure modes observed: A Bash heredoc write of the full constitution failed with
  "unexpected EOF while looking for matching quote" despite a quoted delimiter; the template file
  was left intact (verified 55 lines, unmodified) and the write was redone with the Write tool.
  Lesson: for large markdown containing backticks, pipes, and apostrophes, prefer Write over a
  shell heredoc.
- Graders run and results (PASS/FAIL): Placeholder-token check PASS. Version/report coherence PASS.
  ISO date format PASS. Heading hierarchy preserved (`##` sections, `###` principles) PASS.
  Declarative MUST/NEVER language instead of vague "should" PASS.
- Prompt variant (if applicable): n/a — first constitution run for this repository.
- Next experiment (smallest change to try): Encode the latency table as a machine-readable
  `budgets.yaml` so CI can assert the p95 gates directly, instead of the numbers living only in
  prose where they cannot be enforced.
