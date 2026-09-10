---
id: 0006
title: Core Scaffold And Pure Logic
stage: green
date: 2026-09-09
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: 001-order-support-agent (directory only; not a git repository)
user: my5757980@gmail.com
command: (user request — build the project now, API keys later)
labels: ["implementation", "scaffold", "core", "turn-state", "barge-in", "tests", "no-keys-required"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon
  adr: null
  pr: null
files:
  - backend/pyproject.toml (T001)
  - backend/conftest.py (runs from a clean clone without an editable install)
  - backend/src/config.py + .env.example (T003)
  - backend/src/core/clock.py (T011)
  - backend/src/core/events.py (T010)
  - backend/src/core/ports.py (T009)
  - backend/src/core/turn_state.py (T012)
  - backend/src/core/barge_in.py (T049)
  - backend/src/obs/redaction.py (T004)
  - backend/tests/unit/test_core_purity.py (T013)
  - backend/tests/unit/test_turn_state.py, test_barge_in.py, test_redaction.py, test_clock.py
  - specs/001-order-support-agent/spec.md (positioning: recommendation → DECIDED)
  - specs/001-order-support-agent/tasks.md (9 tasks marked complete)
tests:
  - 61 unit tests, all passing (pytest)
  - test_barge_in.py 18 · test_turn_state.py 23 · test_redaction.py 10 · test_clock.py 6 · test_core_purity.py 4
  - purity guard verified against a planted `import anthropic` — failed as designed, passed after restore
---

## Prompt

nahiii to keysss to bad mayyy mil hii jayyy giii jaha sayyy laynii hogiii phlayy code project to ready karoo naaaa

(Preceding turn established: positioning = lead with barge-in fidelity; API keys = none yet.)

## Response snapshot

Built the entire portion of the system that needs **no API keys** — which, by design, is the part
the constitution cares most about. Principle VII requires the conversation engine be testable
without a microphone, a network, or a vendor account, so "no keys yet" is not a blocker for the core
at all; it is the exact scope the architecture was drawn to make buildable first.

**Nine tasks completed** (T001, T003, T004, T009, T010, T011, T012, T013, T049) with **61 passing
unit tests**.

Modules:

- **`core/clock.py`** (T011) — injected time. `FakeClock.sleep()` deliberately does *not* advance
  the clock; a test that expects time to pass must call `advance()`. That turns the passage of time
  into an assertion instead of a race against a real scheduler.
- **`core/events.py`** (T010) — frozen dataclasses, every one carrying `session_id` + `turn_id` so
  principle VI holds structurally rather than by remembering to log the ids. `AgentAudio` carries
  `char_offset` from TTS alignment, which is what later makes `heard_prefix_len` exact.
- **`core/ports.py`** (T009) — the five Protocols. No vendor name appears anywhere, including
  AssemblyAI's.
- **`core/turn_state.py`** (T012) — explicit transition table; anything absent is illegal by
  construction, so adding a state forces deciding its transitions. Includes the silence ladder
  (6 s / 18 s / 30 s) with the property that a stage fires once — re-prompts escalate rather than
  repeat — and that mid-response silence never triggers a re-prompt, which would have the agent
  interrupt itself.
- **`core/barge_in.py`** (T049) — the FR-009/FR-010 collision resolved by ordering: the
  `awaiting_confirmation` check runs *first*, so a bare "yes" is a backchannel during narration and
  a real answer during "shall I start that return?". `is_confirmation_answer` is deliberately
  narrow (≤3 tokens): a long sentence merely containing "yes" is a new request, and treating it as
  consent would be precisely the failure the confirmation ritual exists to prevent. "yes"/"no" are
  absent from the default backchannel set.
- **`obs/redaction.py`** (T004) — runs before any sink. Tested both directions: PII is removed, and
  ordinary speech survives intact, because a log nobody can read is not a safe log.
- **`config.py`** (T003) — Pydantic settings, frozen, fail-fast. `log_transcripts=true` with
  `environment=prod` raises at boot rather than being trusted to deployment discipline.

**The test worth calling out is `test_core_purity.py` (T013).** It walks the AST of every module
under `core/` and fails on any vendor SDK, network, or storage import — and additionally forbids
`time`/`asyncio` everywhere except `clock.py`, the designated seam. Principle IV stops being a good
intention at this point. **Verified live**: planted `import anthropic` into `barge_in.py`, ran the
guard, it failed with `barge_in.py imports 'anthropic'`; restored the file, it passed again. A guard
that has never been seen to fail is not evidence of anything.

Also added `backend/conftest.py` so `pytest` runs from a clean clone with no editable install —
useful on day one and in CI before dependency caching.

Spec positioning updated from *Recommendation* to **DECIDED**: lead with interruption fidelity.

## Outcome

- ✅ Impact: The deterministic core of the agent exists and is fully tested with zero vendor
  dependencies. Everything from here either wires adapters to it or measures it.
- 🧪 Tests: **61 passing.** test_barge_in 18, test_turn_state 23, test_redaction 10, test_clock 6,
  test_core_purity 4. The purity guard was additionally proven to fail on a planted violation.
- 📁 Files: 19 created under `backend/` plus `.env.example`; 2 spec artifacts updated.
- 🔁 Next prompts: T002 (frontend Vite scaffold + the two AudioWorklets — also needs no keys, and
  the playback ring buffer is the hardest single piece of the barge-in budget), then T034–T036
  (SQLite schema, seed fixtures, repositories — no keys), then T037/T038 (ToolRegistry + the 10 tool
  definitions — no keys). The spikes T005/T006 unblock the moment keys arrive.
- 🧠 Reflection: "No keys yet" turned out to constrain almost nothing. The constitution's principle
  VII — deterministic core, simulated edges — meant the highest-value, hardest-to-retrofit logic
  (turn state, interruption, redaction, config discipline) was buildable and testable on day one.
  An architecture that had let vendor calls leak into the conversation engine would have been
  blocked here.

## Evaluation notes (flywheel)

- Failure modes observed: none in this turn — every module landed passing on first run. Worth noting
  the near-miss: `clock.py` legitimately needs `time` and `asyncio`, which the purity guard would
  otherwise forbid. Rather than weakening the rule globally, the guard exempts exactly one file by
  name. A blanket exemption would have quietly permitted `time.monotonic()` anywhere in core, which
  is the specific thing principle VII exists to prevent.
- Graders run and results (PASS/FAIL): Full unit suite PASS (61/61). Purity guard positive control
  PASS (rejects planted vendor import). Purity guard negative control PASS (clean tree passes).
  Silence-ladder escalation PASS (each stage fires once). Confirmation-override ordering PASS
  (the FR-009/FR-010 collision test). Redaction over-redaction check PASS (ordinary speech intact).
- Prompt variant (if applicable): n/a.
- Next experiment (smallest change to try): Add a positive-control assertion to every future guard
  test, the way `test_guard_actually_catches_a_violation` does here. A guard that cannot be observed
  failing is indistinguishable from a guard that does nothing, and this project now has several
  more planned (timeout-wrapping lint, single-writer check, no-vendor-in-core).
