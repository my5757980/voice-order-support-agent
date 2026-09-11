---
id: 0010
title: Publish, Record And Submission Artifacts
stage: green
date: 2026-09-11
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: main
user: my5757980@gmail.com
command: (user request — redeploy on Replit, publish, then prepare every submission artifact including the video)
labels: ["replit", "publish", "barge-in", "playback", "streaming-partials", "confirmation", "reasoning-budget", "video", "slides", "submission"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon
  adr: null
  pr: null
files:
  - scripts/build.sh, scripts/serve.sh, scripts/start.sh (build and serve split for Replit publishing)
  - .replit (one port everywhere; deployment build/run steps; workflows committed)
  - backend/src/session/actor.py (playback model; floor held through tail playback; heard-only memory on the right message; no silent turns)
  - backend/src/adapters/tts/groq_orpheus.py (ordered clause emission; reply-relative offsets)
  - backend/src/adapters/tts/fake.py (reply-relative offsets)
  - frontend/src/worklets/playback.worklet.ts (30 s ring buffer; per-turn progress)
  - backend/src/adapters/stt/assemblyai.py (partials from every word, final or not; balanced turn detection)
  - backend/src/core/barge_in.py (one real word interrupts; backchannels and slight words do not)
  - backend/src/core/confirmation.py ("do you want to cancel…" arms the gate)
  - backend/src/adapters/llm/openai_compat.py (gpt-oss reasons at low effort by default)
  - specs/001-order-support-agent/plan.md (barge-in filter amended, with measurement)
  - SUBMISSION.md, README.md, assets/{demo.mp4,slides.pdf,cover.png}, assets/src/*
tests:
  - 279 passing (was 247)
  - production worklet probe: 8 s pushed / 4.06 s played before, 7.9 s after
  - barge-in with real AssemblyAI: 3.0 s -> 0.65 s locally, 1.14-1.36 s deployed
  - final recorded take against the published app: 0 errors, confirmation and cancellation correct
---

## Prompt

(Spanning several turns.) "2nd wala karo" (redeploy on Replit) — "to kahaa publish karo batao
na" — "ab project 100% ready hogaya hai na chalo ab submission ki sab cheezein ready karo video
bhi aur bhi jo jo hai phir submission shuru karte hain" — and "ho gaya" after each manual
Replit redeploy (automation was stopped by Replit's Cloudflare check).

## Response snapshot

Redeployed and published on Replit after fixing three deployment defects: start.sh never
rebuilt the frontend once dist/ existed; the workflow served port 5000 while the public URL
pointed at 8000; and the first publish failed its health check because installs and the Vite
build ran inside the run step. Published at voice-order-support-agent--my5757980.replit.app.

Recording the demo through the real capture path — synthesized shopper lines injected as the
microphone stream — exposed defects the suite could not, because its fake synthesizer paces
audio in realtime and its fake transcripts arrive complete: replies over four seconds lost
their start to a ring-buffer overrun (8 s pushed, 4.06 s played); clauses could play out of
order; barge-in was impossible while a finished reply was still playing; memory truncation
could shorten the previous turn's reply; "Do you want to cancel…" did not arm the confirmation
gate; gpt-oss at default reasoning effort produced empty and truncated replies on the
deployment (local .env had masked it); and partials were built from finalized words only,
so the agent stopped 3.0 s after being interrupted. All fixed with tests. AssemblyAI's own
documentation drove two of the fixes: the balanced turn-detection preset for customer
support, and `transcript` holding finalized words only.

Produced the submission artifacts: a 1:55 demo recorded from the published app with dead air
cut at timeline-derived points, a 10-slide PDF mapped to the four judging criteria, a 16:9
cover, and SUBMISSION.md leading with interruption fidelity.

## Outcome

- ✅ Impact: live, published app with working barge-in and full-length replies; T081–T085 done.
- 🧪 Tests: 279 passing; every fix has a regression test.
- 📁 Files: see front matter.
- 🔁 Next prompts: file on lablab.ai (T086, needs the user's login, enrolment and Discord);
  decide on Groq's paid tier if several judges may use the app at once.
- 🧠 Reflection: each recorded take found a defect the previous take hid. The fakes agreed with
  each other and disagreed with reality in exactly the places that mattered — timing, bursts,
  finality of words, and configuration that lived only in a local .env.

## Evaluation notes (flywheel)

- Failure modes observed: environment-only configuration (reasoning effort) invisible to local
  runs; realtime-paced fakes hiding every burst-delivery defect; a test that passed by marking
  the wrong message.
- Graders run and results: pytest 279/279; worklet overrun probe; barge-in probe against real
  STT; four recorded takes against the deployed app.
- Next experiment: replace the realtime fake synthesizer default with a burst mode, so the
  suite exercises the delivery pattern production actually has.
