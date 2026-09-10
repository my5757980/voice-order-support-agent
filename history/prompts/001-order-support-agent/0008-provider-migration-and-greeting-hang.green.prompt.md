---
id: 0008
title: Provider Migration And Greeting Hang
stage: green
date: 2026-09-10
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: 001-order-support-agent (directory only; not a git repository)
user: ms5373268@gmail.com
command: (user request — obtain API keys, then finish the project)
labels: ["groq", "provider-migration", "deadlock", "event-loop", "barge-in", "latency-gates", "ports-and-adapters"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon
  adr: null
  pr: null
files:
  - backend/src/adapters/llm/openai_compat.py (new — Groq/Gemini/OpenAI/LLM-Gateway in one adapter)
  - backend/src/adapters/tts/groq_orpheus.py (new — REST TTS, WAV 24kHz → 16kHz resample)
  - backend/src/adapters/factory.py (per-slot real-vs-fake selection)
  - backend/src/adapters/llm/persona.md (disclosure moved out of first turn; tools-before-questions)
  - backend/src/session/actor.py (greeting is a tracked turn; drain replaces fixed sleep)
  - backend/src/adapters/tts/fake.py, groq_orpheus.py (per-turn cancellation)
  - backend/src/app.py (session tasks owned by the control socket)
  - backend/tests/integration/test_app_e2e.py (greeting acknowledged; interrupt regression added)
  - specs/001-order-support-agent/research.md (R9 — measured gate results)
tests:
  - 160 passing (was 159; +1 greeting-interrupt regression)
  - live end-to-end verified against AssemblyAI + Groq LLM + Groq Orpheus TTS
---

## Prompt

(Spanning several turns.) Obtain the API keys from the platform; ElevenLabs is not needed
if Groq covers it; then finish the project — "ab jo bachaa haii project 100% ready karo".

## Response snapshot

Two distinct pieces of work: getting the stack onto real providers, and then fixing a
deadlock that the change exposed.

### Provider migration — the plan's LLM and TTS both had to change

**AssemblyAI STT** connected on the first try (browser sign-up, $150 hackathon credits).
Key moved from the dashboard to `.env` **via the OS clipboard**, so it never entered the
transcript — verified by masked prefix/suffix only.

**Claude was not available** (paid key). Before recommending an alternative I evaluated
AssemblyAI's own LLM Gateway, since it would have needed no new credential — and
**rejected it on measurement**: on the free tier exactly 1 of 34 models is reachable, it
returns `"does not support tools"`, and TTFT measured **4,922 ms**. Either fact alone
disqualifies it; every tool in this project depends on tool calling.

Settled on **Groq `openai/gpt-oss-120b`**, chosen by measurement over four candidates —
it was the only fast one that actually emitted `tool_calls`. Wrote one
`openai_compat.py` adapter covering Groq, Gemini, OpenAI and the LLM Gateway; the
provider is a `.env` line. **The Claude→Groq swap cost one file and touched no core
code** — principle IV earning its keep on the day it was tested.

**ElevenLabs turned out to have 1 of 10,000 free credits left**, which the code surfaced
as a clean `quota_exceeded` rather than a mystery. Switched to **Groq Orpheus** on the
key already held. That required accepting model terms — a legal agreement I opened but
**declined to accept on the user's behalf**; they did it themselves.

Orpheus is REST-only and returns WAV 24 kHz, so the adapter walks the RIFF chunk list
(rather than assuming a 44-byte header) and does a 3:2 decimation to the pipeline's
16 kHz, averaging pairs as a cheap anti-alias. The capability loss versus ElevenLabs —
no `clear_buffer`, no character alignment — is written into the adapter docstring and
research.md R9 rather than glossed.

### The deadlock — and why it was a real bug, not a test artifact

Adding a spoken greeting hung the suite: 13 tests in, then nothing, for 420 s. Unit,
store, tools and actor suites all passed; only `test_app_e2e.py` stalled.

I reasoned about it twice and was wrong twice, so I stopped guessing and wrote a
20-line reproduction with progress markers and a `faulthandler` watchdog. The trace was
unambiguous:

```
[2] agent.speaking   ← greeting audio starts
[4] audio.flush      ← text.input INTERRUPTS the greeting
[7] state            ← thinking… then nothing
```

**Three defects, in order of severity:**

1. **The greeting was an untracked task.** `asyncio.create_task(actor.greet())` was never
   stored in `_turn_task`, so `interrupt()` had nothing to cancel. The greeting was the
   one moment in the session the agent **could not be talked over** — which is precisely
   the moment an impatient shopper tries. Straight violation of principles III and V.

2. **`_cleared` was a global flag on a per-turn concern.** A new turn's `submit()` called
   `_cleared.clear()`, un-cancelling the *previous* turn, whose producer then resumed
   pushing into the same queue. Two producers, a queue that never drained, `drain()`
   hanging to its 20 s ceiling. Replaced with a set of cancelled turn ids, so "turn A was
   interrupted" stays true however many turns follow.

3. **Session tasks were spawned from whichever socket handler ran.** The greeting started
   in the audio socket's context, the shopper's turn in the control socket's. Under
   TestClient each websocket gets its own event loop, so two loops awaited one
   `asyncio.Queue` — futures resolved on a loop that is never notified, and the waiter is
   never woken. Moved all session task ownership to the control socket, which extends
   Architectural Principle 1's single-writer rule from *state* to *tasks*.

Defect 1 was a genuine product bug that the test harness happened to surface. Defect 3
was harness-triggered but a real fragility.

### Two test failures that were mine, not the product's

After the fix, two e2e tests failed asserting on `agent_turns[0]` — which is now the
greeting. The behaviour was correct; the assertions were stale. Fixed by adding an
explicit `_settle()` that steps past the greeting, and added
`test_shopper_can_interrupt_the_opening_greeting` as a regression for defect 1.

### Measured gates, honestly reported

Recorded in research.md R9. `llm.ttft` 1,500 ms, `tts.ttfb` p50 610 ms (min **125 ms**),
e2e 2,891 ms — all over budget locally. But a bare authenticated round trip to these
US-hosted endpoints, carrying no inference at all, measures **929 ms p50** from Pakistan.

So these numbers **neither pass nor fail**. They were taken half a world from the
inference, and the deployment target is US. R9 says so explicitly, and **no budget was
amended** — that needs the governance procedure and evidence from the environment the
budget applies to.

## Outcome

- ✅ Impact: The whole pipeline runs on real providers — AssemblyAI STT, Groq LLM, Groq
  Orpheus TTS — with a live turn calling a tool and answering in persona. Three real
  defects fixed, one of which made the greeting uninterruptible.
- 🧪 Tests: **160 passing** (159 → 160). Live end-to-end verified: greeting
  `tts.ttfb 1031 ms`; turn `llm.ttft 1500 · tool 0 · tts.ttfb 2437 · e2e 2891 ms`, agent
  reply *"I see three recent orders: a desk lamp placed yesterday, headphones shipped on
  the fourth, and a mug delivered on the twenty‑first. Which one would you like the
  status for?"* — tool called, natural dates, two sentences, one question.
- 📁 Files: 2 adapters created, 6 modified, research.md R9 added. 50/87 tasks.
- 🔁 Next prompts: T077 (Replit deploy) then T077b — the deployed re-measurement is the
  only thing that actually closes the latency gates. Then Phase 4 submission artifacts,
  which are mandatory and unstarted.
- 🧠 Reflection: The persona change is worth remembering. Telling the model to "say once,
  in your first turn, that you are an automated assistant" made it spend the entire first
  turn on the disclosure and *ask* which order instead of looking one up. Moving the
  disclosure into a greeting the orchestrator speaks — and adding an explicit
  "look things up before asking" rule — fixed it. Instructions that compete for the same
  turn will be resolved by the model, not by intent.

## Evaluation notes (flywheel)

- Failure modes observed: **I reasoned about the deadlock twice and was wrong twice**,
  burning two cycles on plausible-but-untested theories (cross-portal sends, queue item
  loss) before writing a reproduction. The reproduction took four minutes and gave an
  unambiguous answer. Lesson with teeth: when a concurrency bug survives one round of
  reasoning, stop reasoning and instrument — the marginal guess is worth less than the
  marginal print statement. Also: a stale `LLM_MODEL=claude-opus-5` in `.env` silently
  overrode the Groq default, and the failure surfaced as a confusing 404 for a model on
  the wrong provider; defaults that live in two places will disagree.
- Graders run and results (PASS/FAIL): Full suite PASS (160/160). Live end-to-end PASS
  (tool called, persona respected, spans emitted). Greeting-interruptible regression
  PASS. Per-turn cancellation PASS (no `_cleared` references remain — asserted in the
  edit script). Key handling PASS (AssemblyAI key never entered the transcript; Groq key
  did, because the user pasted it — flagged for rotation).
- Prompt variant (if applicable): persona v2 — disclosure moved to a spoken greeting,
  "look things up before asking" section added.
- Next experiment (smallest change to try): T008's `budgets.yaml` is now overdue in a way
  it was not before. R9 has measured numbers that belong next to the budgets they are
  compared against, and the local-versus-deployed distinction needs somewhere to live
  that is not prose. Extract it before T077b, so the deployed re-measurement writes into
  a structure rather than another paragraph.
