---
id: 0003
title: Voice Agent Implementation Plan
stage: plan
date: 2026-09-09
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: 001-order-support-agent (directory only; not a git repository)
user: my5757980@gmail.com
command: /sp.plan
labels: ["voice-agent", "architecture", "assemblyai", "claude-opus-5", "elevenlabs", "latency", "barge-in", "asyncio"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-order-support-agent/plan.md (created, ~500 lines)
  - specs/001-order-support-agent/research.md (created, Phase 0, 8 decisions)
  - specs/001-order-support-agent/data-model.md (created, Phase 1)
  - specs/001-order-support-agent/contracts/tools.schema.json (created, 10 tools, validated)
  - specs/001-order-support-agent/contracts/websocket-protocol.md (created)
  - specs/001-order-support-agent/quickstart.md (created)
  - CLAUDE.md (appended Active Technologies section — script no-op'd, done manually)
  - history/prompts/001-order-support-agent/0003-voice-agent-implementation-plan.plan.prompt.md (this file)
tests:
  - none executed (planning artifacts; no runnable code yet)
  - tools.schema.json validated as JSON; asserted all 10 tools latency-classified and zero customer_id leakage
---

## Prompt

/sp.plan

Using the Project Constitution (v1.0.0) and the Product Specification for the Voice Order Support Agent (specs/001-order-support-agent/spec.md), create a complete technical implementation plan.

Context already decided:
- STT: AssemblyAI Realtime Speech-to-Text (WebSocket only) — harder path
- Surface: Browser web app
- Tools: Simulated backend with real interfaces (schema-validated)
- We own the full orchestration (no AssemblyAI Voice Agent API)

The plan must include:

1. High-level System Architecture
   - Components and clear data flow (Audio → STT → Orchestrator → LLM → Tools → TTS → Audio)
   - How barge-in, turn commitment, and cancellation work end-to-end

2. Technology Stack Recommendation
   - Backend language & framework
   - Frontend (browser audio capture + playback)
   - LLM choice (with justification)
   - TTS choice (with justification)
   - Why each choice fits the constitution (streaming, latency, cancellation, ports & adapters)

3. Project / Folder Structure

4. Key Modules and Responsibilities
   - Orchestrator
   - SpeechRecognizer adapter (AssemblyAI)
   - LanguageModel adapter
   - SpeechSynthesizer adapter
   - ToolRegistry + simulated tools
   - ConversationMemory (working / summary / durable)
   - Session & turn state machine

5. Real-time Pipeline Design
   - Exact handling of partial vs committed turns
   - Speculative LLM dispatch rules
   - Interruption + backchannel filter
   - Holding phrases for slow tools

6. State Management Approach

7. Error Handling & Fallback Strategy (aligned with constitution)

8. Observability Plan
   - Mandatory spans, metrics, structured logging

9. Deployment Strategy (for hackathon demo)

10. Constitution Check
    - Go through every gate in the plan template and mark PASS / FAIL / N-A
    - Any FAIL must be justified or the design changed

Be concrete and practical. Prefer simple, reliable solutions that can be built in the remaining time before the Sep 30 deadline. Do not over-engineer.

## Response snapshot

Ran the full `/sp.plan` workflow: setup script → context load → Constitution Check → Phase 0
research → Phase 1 design & contracts → agent context update → post-design re-check.

**Vendor facts were verified, not recalled.** Loaded the `claude-api` skill (triggered by the LLM
model-choice request) and queried ElevenLabs docs through context7. Two verified facts changed the
design rather than merely decorating it:

1. **ElevenLabs documents 200–500 ms initial TTS latency** against our constitution's `tts.ttfb`
   p95 of 250 ms / hard fail 400 ms. The documented range straddles our hard-fail line. Recorded as
   an explicit at-risk gate with four mitigations and a named fallback (Cartesia Sonic, listed as a
   candidate to benchmark — deliberately *not* asserted as faster, since we did not verify it).
2. **Disabling thinking on Claude Opus 5 has a documented failure mode** where the model writes a
   tool call into visible text instead of emitting a `tool_use` block — the call silently never
   runs. In this domain that is the agent saying "I've started your return" when no return exists:
   exactly FR-030 / SC-011, the spec's one zero-tolerance failure. So thinking stays ON with
   `effort: "low"` as the supported latency lever. This is the plan's least obvious and most
   consequential configuration decision, and it was driven entirely by reading the docs.

**Stack recommended** (the user delegated the choice rather than supplying one): Python 3.11+ /
FastAPI backend — chosen because `asyncio.TaskGroup` makes constitution principle V (transitive
cancellation) a *language* property rather than per-call-site discipline; TypeScript + Vite +
AudioWorklet frontend — chosen because playback must be a ring buffer we can zero synchronously,
which an `<audio>` element cannot do within the 100 ms barge-in budget; Claude Opus 5 streaming with
a manual tool loop, `strict: true`, and prompt caching on the tools+system prefix; ElevenLabs Flash
v2.5 selected for its `clear_buffer` interrupt primitive and character-alignment timestamps rather
than for voice quality; SQLite behind repository ports.

**The design's central technical argument** is speculative LLM dispatch. The serial chain
(STT 300 + LLM TTFT 450 + TTS TTFB 250) consumes the entire 1000 ms p95 with zero headroom.
Dispatching the LLM on a high-confidence partial turn hides TTFT behind the shopper's trailing
silence, collapsing the perceived chain to ~550–600 ms and making p50 reachable. Four strict gates
govern it, the important one being that speculation is **read-only** — a speculative turn may call
`get_order` but may never call `create_return`, enforced in the ToolRegistry by a call-context flag
rather than by prompting.

**Two findings that correct common voice-agent mistakes**, both written into the contracts:

- Barge-in requires the TTS `clear_buffer` **and** a client-side `audio.flush`, fired concurrently
  with neither waiting on the other. `clear_buffer` stops the provider producing audio but does
  nothing about the ~200 ms already in the browser's ring buffer. A design that awaits
  acknowledgement before flushing measures a correct-looking interrupt latency and still talks over
  the user.
- Audio is relayed through our backend rather than browser→AssemblyAI direct, despite the vendor
  supporting the latter via short-lived tokens. Costs one hop; buys the property that an untrusted
  browser cannot forge a committed turn — and committed turns authorize memory writes and tool
  calls, including "the customer confirmed the return".

**Artifacts**: `plan.md` (all 10 requested sections + template structure + delivery plan to Sep 30
with an ordered cut list), `research.md` (8 decisions, each with rationale/alternatives, and a
`[vendor]` vs `[ours]` marker distinguishing provider claims from our budgets), `data-model.md`
(domain vs conversation entities, two state machines, enforcement table), `contracts/` (10 tool
schemas + WebSocket protocol), `quickstart.md`.

**Constitution Check: no gate fails.** Two components carry *measurement* risk rather than design
risk (`tts.ttfb`, `llm.ttft`), both recorded in Complexity Tracking with mitigations and escalation
paths, and both scheduled as week-1 gates so week 2 does not build on unverified assumptions.

**One tooling defect found**: `update-agent-context.ps1` reported "✓ Updated existing Claude Code
context file" but inserted nothing — `CLAUDE.md` lacks the marker sections it looks for. Verified
the file was intact (210 lines, content unchanged), then appended the Active Technologies section
manually to fulfil the workflow step.

## Outcome

- ✅ Impact: Feature 001 has an implementation-ready plan with a verified stack, a defensible
  latency strategy, contracts for 10 tools and both WebSockets, and a 3-week delivery plan with an
  ordered cut list. Ready for `/sp.tasks`.
- 🧪 Tests: None executed — planning artifacts, no runnable code. Validation performed instead:
  zero unresolved template placeholders in plan.md; zero `NEEDS CLARIFICATION` markers across all
  artifacts (the 3 grep hits are statements asserting absence); `tools.schema.json` parses as valid
  JSON with 10 tools, every one latency-classified, and `customer_id` absent from every input schema
  — the property that keeps FR-003/TC-003 (server-side customer scoping) from decaying into
  convention.
- 📁 Files: 6 created under `specs/001-order-support-agent/`, 1 appended (`CLAUDE.md`), 1 PHR.
- 🔁 Next prompts: `/sp.tasks` to decompose into dependency-ordered tasks. Before that, `/sp.adr` is
  worth running for two decisions that clear the significance bar independently: the LLM
  configuration (thinking-on-at-low-effort, justified on safety not latency) and the TTS selection
  (chosen for its interrupt primitive, carrying a known budget risk).
- 🧠 Reflection: Loading `claude-api` and querying the TTS docs before writing changed two decisions
  and surfaced one budget conflict that would otherwise have been discovered in week 2, after the
  architecture had been built on it. The pattern that keeps paying: read the vendor contract first,
  then design; and mark which numbers are theirs versus ours, so a missed budget prompts the right
  conversation instead of a silent relaxation.

## Evaluation notes (flywheel)

- Failure modes observed: (1) `update-agent-context.ps1` reports success while performing no
  insertion when the target file lacks its expected markers — a silent no-op, caught only by
  grepping for the content afterward rather than trusting the ✓. (2) `setup-plan.ps1` exits 128 in a
  non-git directory while succeeding completely — same git-`$LASTEXITCODE` leak as
  `create-new-feature.ps1` in PHR-0002, now confirmed as a pattern across these scripts: the JSON
  output and created files are the reliable success signal, never the exit code.
- Graders run and results (PASS/FAIL): Placeholder check PASS. Clarification-marker check PASS.
  Tool schema JSON validity PASS. Latency-class completeness PASS (10/10). Customer-scoping leakage
  check PASS (0 schemas expose `customer_id`). Constitution gate coverage PASS (all 10 gates
  evaluated with evidence; 2 risks tracked in Complexity Tracking rather than waved through).
- Prompt variant (if applicable): n/a — first plan run for this repository.
- Next experiment (smallest change to try): The latency table now appears in three places —
  constitution, spec NFRs, and plan Technical Context — with the plan additionally splitting
  `[vendor]` from `[ours]`. Three copies is two too many. Extract to `budgets.yaml` with a
  provenance field per row and have CI assert measured p95s against it; that single file would then
  be the thing the week-1 measurement gate updates, and the drift risk disappears. This is the third
  consecutive PHR proposing it, which is itself the signal that it should be a task in `/sp.tasks`
  rather than a standing suggestion.
