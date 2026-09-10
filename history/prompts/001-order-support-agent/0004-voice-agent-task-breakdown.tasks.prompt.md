---
id: 0004
title: Voice Agent Task Breakdown
stage: tasks
date: 2026-09-09
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: 001-order-support-agent (directory only; not a git repository)
user: my5757980@gmail.com
command: /sp.tasks
labels: ["voice-agent", "tasks", "planning", "latency-gates", "cut-list", "hackathon", "delivery"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-order-support-agent/tasks.md (created, 80 tasks, T001-T080)
  - history/prompts/001-order-support-agent/0004-voice-agent-task-breakdown.tasks.prompt.md (this file)
tests:
  - none executed (task list; no runnable code)
  - tasks.md validated by script — ID sequence, counts, [P] markers, critical-path flags
---

## Prompt

/sp.tasks

Using the Project Constitution (v1.0.0), the Product Specification (specs/001-order-support-agent/spec.md), and the Implementation Plan (specs/001-order-support-agent/plan.md), break the work into a clear, ordered list of implementation tasks.

Rules:
- Tasks should be small enough to complete in 1–4 hours
- Order them strictly by dependency (what must be done first)
- Group them into logical phases that match the delivery plan in the plan.md:
  - Phase 1: Walking skeleton + latency truth (Week 1)
  - Phase 2: The agent (US1 – order status) (Week 2)
  - Phase 3: Safety, state-changing actions & polish (US2 + US3) (Week 3)
- Each task must have:
  - Task ID (e.g. T-001)
  - Clear title
  - Short description
  - Acceptance Criteria (how we know it is done)
  - Estimated effort (S / M / L)
  - Critical-path flag (Yes/No) — mark the tasks that are required for a working demo

Also:
- Explicitly mark the tasks that resolve the two at-risk latency components (tts.ttfb and llm.ttft)
- Include the ordered cut-list items from the plan so we know what to drop if time runs out
- Do not create implementation code yet — only the task list

Be practical for a ~3 week timeline ending 30 September 2026.

## Response snapshot

Generated `specs/001-order-support-agent/tasks.md` — **80 tasks, T001–T080**, grouped into the three
weekly phases the user requested rather than the template's default one-phase-per-user-story layout.
The mapping is clean (Week 1 = setup + foundational, Week 2 = US1, Week 3 = US2/US3/polish), so both
structures are satisfied: the weekly headings are the user's, while the strict checklist line format
(`- [ ] T001 [P] [US1] description with file path`) is preserved so `/sp.analyze` and
`/sp.implement` can still parse it. Each task carries an indented detail block with
**What / Done when / Effort / Critical path**, which a bare checklist line cannot hold.

**The one sequencing decision worth defending**: the two at-risk latency spikes (T005 `tts.ttfb`,
T006 `llm.ttft`) were pulled to **day 1**, ahead of nearly all product work. They are standalone
scripts needing no skeleton, no orchestrator, and no browser. The plan had them landing at the end
of week 1; that is too late, because the entire Phase 2 architecture — particularly speculative
dispatch — is built on the assumption that those budgets hold. Discovering in week 2 that
`tts.ttfb` sits at 480 ms would invalidate design decisions already built upon. One day of
measurement up front is the cheapest insurance in the plan.

**Four ⚠️ tasks** resolve the at-risk components: T005 (TTS spike, matrix across
`optimize_streaming_latency`, PCM vs mp3, cold vs pre-warmed), T006 (LLM TTFT spike, matrix across
effort and cache warmth, with a non-zero `cache_read_input_tokens` assertion since a persistent zero
is itself the finding), T033 (end-to-end gate report — **week 2 does not start until it exists**),
and T054 (speculative dispatch, the mechanism that makes p50 reachable).

**Cut list** carried over from plan.md and ordered: US4 product/policy Q&A → durable cross-session
memory → summary compaction → `update_shipping_address` → resilience polish. Paired with an explicit
**never-cut** list of three items — the confirmation ritual plus never-claim-false-success
(T059+T061), barge-in including heard-prefix truncation (T049+T050+T051), and schema validation plus
server-side authorization (T037+T038). Those three are what separate a voice agent from a demo that
should not be pointed at real orders.

**Enforcement tasks that keep constitution principles from decaying into good intentions**: T013
(AST import guard failing if any vendor SDK is imported under `core/` — principle IV), T034 (the
UNIQUE constraint on `idempotency_key`, making duplicate prevention a *database* guarantee rather
than an application check), T037/T059 (state-changing tools structurally unreachable without a
confirmed `PendingAction`, enforced in the registry rather than by prompting), and T008 (the
`budgets.yaml` single source of truth — the drift fix flagged in three consecutive PHRs, now an
actual task rather than a standing suggestion).

Also included: a critical chain (longest dependency path, 61 hops), a blocking-relationship table, a
two-person parallelization split per week, and an incremental delivery ladder with T058 (US1
complete) named as the shippable MVP.

**Counts were verified by script, not tallied by hand** — and three of my initial summary figures
were wrong (critical-path 61→68, `[P]` 30→29, US1 tasks 5→4). Corrected in the file.

## Outcome

- ✅ Impact: Feature 001 has an executable, dependency-ordered 80-task breakdown with acceptance
  criteria per task, effort sizing, critical-path flags, and an ordered cut list. Ready for
  `/sp.implement`.
- 🧪 Tests: None executed — task list, no runnable code. Validation performed by script: 80 tasks;
  ID sequence T001–T080 with **no gaps and no duplicates** (diffed against a generated sequence);
  29 `[P]` markers; 68 critical-path / 12 non-critical; story labels US1:4, US2:4, US3:2, US4:1;
  phase boundaries confirmed at 33 / 25 / 22.
- 📁 Files: 1 created (`tasks.md`), 1 PHR.
- 🔁 Next prompts: `/sp.analyze` to cross-check spec ↔ plan ↔ tasks consistency before building, then
  `/sp.implement` starting with T005/T006 — the day-1 latency spikes — rather than T001. The two ADRs
  suggested in PHR-0003 (LLM thinking configuration, TTS provider selection) remain outstanding and
  are cheapest to write now, while the reasoning is fresh and before code depends on it.
- 🧠 Reflection: Reordering the latency spikes to day 1 was the single highest-value change this
  turn, and it came from asking "what does week 2 assume?" rather than from following the plan's
  own week-1 ordering. The plan is an input to the task list, not an authority over it — a task
  breakdown that only transcribes the plan adds nothing.

## Evaluation notes (flywheel)

- Failure modes observed: (1) **I stated summary counts before verifying them** — critical-path,
  `[P]`, and US1 figures were all wrong in the first write. Caught by scripting the counts rather
  than trusting the tally. The lesson generalises: any number in a generated artifact that was
  produced by counting should be verified against the artifact, because a confidently wrong count is
  worse than an omitted one. (2) `check-prerequisites.ps1` exits 128 in a non-git directory while
  succeeding — third script in this repo with the same git `$LASTEXITCODE` leak, now a fully
  established pattern (`create-new-feature.ps1`, `setup-plan.ps1`, `check-prerequisites.ps1`).
- Graders run and results (PASS/FAIL): Task ID sequence integrity PASS (T001–T080, zero gaps, zero
  duplicates). Checklist format compliance PASS (every task has checkbox, ID, description, file
  path). Required per-task fields PASS (What / Done when / Effort / Critical path on all 80). Effort
  bound PASS (no task exceeds L = 4 h). At-risk marking PASS (4 ⚠️ tasks). Cut list PASS (ordered,
  with a never-cut counterpart). Summary accuracy **FAIL then PASS** — three figures wrong on first
  write, corrected after scripted verification.
- Prompt variant (if applicable): n/a — first tasks run for this repository.
- Next experiment (smallest change to try): Have `/sp.analyze` assert that every FR and SC id in
  spec.md is referenced by at least one task's acceptance criteria. Right now coverage is asserted
  by my reading rather than by a check, and an unreferenced requirement is exactly the kind of gap
  that survives review and surfaces in week 3.
