---
id: 0002
title: Voice Order Support Specification
stage: spec
date: 2026-09-09
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: 001-order-support-agent (directory only; not a git repository, branch creation skipped)
user: my5757980@gmail.com
command: /sp.specify
labels: ["voice-agent", "e-commerce", "order-support", "spec", "barge-in", "tool-calling", "memory"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-order-support-agent/spec.md (created, ~850 lines)
  - specs/001-order-support-agent/checklists/requirements.md (created, all items passing)
  - history/prompts/001-order-support-agent/0002-voice-order-support-specification.spec.prompt.md (this file)
tests:
  - none (specification artifact; no executable code introduced)
  - validation greps run against spec.md — see Evaluation notes
---

## Prompt

Based on the Project Constitution we just created, write a complete Product Specification for the Voice Agent.

Project Idea:
[YAHAN APNA IDEA LIKHO - Agent kya karega, kis problem ko solve karega, target user kaun hai]

The specification must include:

1. Problem Statement
2. Target Users
3. Core User Flows (happy path + edge cases)
4. Functional Requirements (must have)
5. Non-Functional Requirements (latency, reliability, scalability)
6. Voice Interaction Design (how conversation should feel)
7. Tool / Function Calling capabilities
8. Memory & Context management
9. Success Metrics
10. Out of Scope (what we will not build)

Write it in a clear, structured, and unambiguous way so that a developer can implement it without asking questions.

## Response snapshot

**The prompt arrived with its Project Idea placeholder unfilled** — literally
`[YAHAN APNA IDEA LIKHO - Agent kya karega, kis problem ko solve karega, target user kaun hai]`.
Every requested section (problem, users, flows, tools, metrics) derives from what the agent actually
does, so proceeding on an invented domain would have produced a polished specification for the wrong
product. Blocked and asked rather than guessed.

Before asking, completed the work that did not depend on the answer: inspected
`create-new-feature.ps1` and confirmed it degrades gracefully without git (falls back to the
`.specify` marker, warns, skips branch creation, still scaffolds `specs/` and `history/prompts/`).

Three questions asked; answers received:

| Question | Answer |
|---|---|
| Agent domain | **E-commerce order support** |
| Voice surface | **Browser web app** |
| Backend | **Simulated, behind real tool interfaces** |

Scaffold created via `create-new-feature.ps1 -Number 1 -ShortName "order-support-agent"`. The script
exited 128 while still emitting valid JSON and creating every directory — the non-zero status is its
internal `git rev-parse` failing in a non-git directory and leaking `$LASTEXITCODE`, not a failure of
the script's work.

**Specification written** to `specs/001-order-support-agent/spec.md` (~850 lines), covering all ten
requested sections plus the template's mandatory structure:

- **Problem Statement** — framed on why *voice* specifically, not "a chatbot with a microphone":
  interruption, reference ("cancel that one"), and compound intent are what forms and IVR menus
  destroy.
- **Target Users** — primary shopper (three behavioural segments in a table), support ops manager,
  escalation-receiving human agent, plus explicit non-users.
- **Core User Flows** — 4 prioritised, independently testable user stories (P1 order status →
  P2 returns/cancellations → P3 escalation → P4 product/policy Q&A) with 23 acceptance scenarios.
- **Edge Cases** — 25+ across five groups: conversation dynamics, recognition/reference, data and
  policy, system failure, and adversarial (including prompt injection through order notes and
  product text).
- **Functional Requirements** — 68 numbered MUST/MUST NOT requirements in 11 groups.
- **Non-Functional** — latency table inherited from the constitution, plus reliability (99.5%),
  scalability (200 concurrent), accessibility, and security additions.
- **Voice Interaction Design** — 21 rules covering persona, response shape (≤2 sentences before
  yielding), turn-taking feel, repair language, and the confirmation ritual.
- **Tool Calling** — 10 tools in three latency/safety classes (5 fast read-only, 3 confirmed
  state-changing, 2 slow) with 8 binding tool requirements.
- **Memory & Context** — three layers (working / summary / durable) with 14 rules; durable memory is
  deliberately minimal and raw transcripts never cross into a later session.
- **Success Metrics** — 17 measurable, technology-agnostic criteria across effectiveness,
  conversation quality, safety/trust, and operations.
- **Out of Scope** — 21 explicit exclusions in four groups.
- Plus **Assumptions** (10 documented defaults), **Dependencies**, and a **Constitution Alignment**
  table mapping each principle to where the spec satisfies it.

**Quality checklist** written to `checklists/requirements.md` — all 16 items pass on the first
iteration, with automated grep evidence and manual review notes recorded in the file.

Notable design decisions inside the spec:

- **Zero `[NEEDS CLARIFICATION]` markers**, achieved by recording 10 documented defaults rather than
  deferring. The two most consequential are flagged in the checklist for a second look: inherited
  authentication, and ticket-based rather than live-transfer escalation.
- **Load-bearing "yes"** (FR-010) — the constitution requires backchannels not to interrupt, but a
  bare "yes" answering a return confirmation is a real answer. The spec resolves this collision
  explicitly instead of leaving it to implementation.
- **FR-030 / SC-011** — the agent must never report success for a failed action, promoted from a
  constitutional prohibition to both a requirement and a zero-tolerance success metric.
- **SC-010** — 100% of state-changing actions preceded by explicit confirmation, with any single
  violation named a release blocker.

## Outcome

- ✅ Impact: Feature 001 has an implementation-ready specification with 68 testable requirements,
  23 acceptance scenarios, and 17 measurable success criteria. Ready for `/sp.plan`.
- 🧪 Tests: None run — specification artifact, no executable code. Validation performed instead:
  zero `NEEDS CLARIFICATION` markers, zero unfilled template placeholders, zero vendor/stack
  leakage (grep for assemblyai|websocket|claude|gpt|gemini|openai|python|react|typescript|postgres|
  redis|api key|json schema → NONE). All 13 expected section headings present.
- 📁 Files: 3 created (spec, checklist, this PHR) plus the feature scaffold directories.
- 🔁 Next prompts: `/sp.plan` for feature 001 — the plan must resolve the two provider decisions the
  constitution deliberately deferred (LLM and TTS), each of which warrants its own ADR. `/sp.clarify`
  is optional here since no clarification markers remain; it is worth running only to pressure-test
  Assumptions 1 and 2.
- 🧠 Reflection: The single highest-value action this turn was refusing to invent the product. The
  cost of asking was one round trip; the cost of guessing would have been a 850-line specification
  for a product the user never wanted, with the error invisible until they read it. Everything
  independent of the answer — script inspection, git-fallback verification — was done before asking,
  so the round trip cost nothing but latency.

## Evaluation notes (flywheel)

- Failure modes observed: (1) User prompt template shipped with an unfilled placeholder — caught by
  reading the input rather than pattern-matching the surrounding instructions. (2)
  `create-new-feature.ps1` returns exit 128 in a non-git directory despite succeeding completely;
  the JSON output and created directories are the reliable success signal, not the exit code. Worth
  remembering for `/sp.plan`, which calls sibling scripts with the same git-fallback pattern.
- Graders run and results (PASS/FAIL): Clarification-marker check PASS (0). Placeholder check PASS.
  Vendor-leakage check PASS. Section completeness PASS (all 10 requested + mandatory template
  sections). Requirement testability PASS (all MUST/MUST NOT with observable outcomes; no "should",
  "appropriate", or "as needed"). Success-criteria technology-agnosticism PASS.
- Prompt variant (if applicable): n/a — first spec run for this repository.
- Next experiment (smallest change to try): The constitution's latency table is now duplicated in
  the spec's NFR section. Extracting both to a single machine-readable `budgets.yaml` referenced by
  each would remove the drift risk and make the numbers CI-enforceable — the same experiment
  proposed in PHR-0001, now with a second consumer justifying it.
