---
id: 0009
title: Full Flow Test And Five Defects
stage: green
date: 2026-09-11
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: main
user: my5757980@gmail.com
command: (user request — run a full 10-15 prompt conversation, listen to it, record the demo video)
labels: ["end-to-end", "audio", "barge-in", "confirmation-ritual", "on-screen-references", "rate-limits", "demo-mode", "video"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon
  adr: null
  pr: null
files:
  - frontend/src/audio.ts (playback and capture start separately; playback first)
  - frontend/src/main.ts (playback before sockets; no optimistic duplicate turn)
  - frontend/scripts/build-worklets.mjs (new — worklets transpiled to public/worklets/*.js)
  - frontend/index.html (inline favicon)
  - backend/src/session/actor.py (floor held from turn start; synthesis/model failures reported; references shown; confirmation from two signals)
  - backend/src/core/confirmation.py (new — seeks_confirmation, is_affirmative)
  - backend/src/core/references.py (new — identifiers from tool results by shape)
  - backend/src/core/clause_splitter.py (invisible characters stripped)
  - backend/src/core/ports.py (ModelUnavailable with retry_after)
  - backend/src/adapters/llm/openai_compat.py (typed failures; one short rate-limit retry)
  - backend/src/adapters/tts/groq_orpheus.py (per-turn synthesis failure recorded)
  - backend/src/adapters/factory.py (DEMO_MODE implemented; TTS_PROVIDER=tone)
  - backend/src/adapters/llm/persona.md (check eligibility before offering an action)
  - backend/src/app.py (.env loaded; demo-mode boot warning)
  - backend/tests/conftest.py (suite forced onto scripted adapters)
  - .env.example (DEMO_MODE=false)
  - specs/001-order-support-agent/tasks.md (T081 tags corrected to the real stack)
tests:
  - 247 passing (was 177 at the start of this work)
  - 13-prompt shopper flow driven twice against the real model and tools
  - production build verified: FastAPI serving dist/, 99/99 frames rendered, peak RMS 0.25
---

## Prompt

(Spanning several turns.) "mujayy awaz bhii nahii a rahii bikul bhiiii" — then: "ab aik bar
full test karoo ... aik flow chunooo 10 15 prompt walaa chaloo mayy dekhoo awaz sunoo ...
shuru sayyy kara start" — then: "isiii testing mayy video bhii sath record kar layynaa
submision mayyy chiyaaaa".

## Response snapshot

No-audio had three independent causes: the production build shipped raw TypeScript as the
AudioWorklet modules (deployed build mute in both directions), playback was coupled to
microphone permission, and the greeting raced playback setup. Fixed and verified against
the real deployed shape.

A 13-prompt conversation (status, tracking, return, cancellation, address change) was then
driven end to end against the real model and tools, with video and agent audio recorded
and muxed. It reported zero errors and was wrong in five ways: the tracking number the
agent claimed to put on screen was never sent; the confirmation gate armed only on two
literal phrases the model never used, and "Yes, go ahead." was not recognised as consent;
the agent offered a return for a `not_returnable` item, took consent, then refused;
a reply ended in several hundred zero-width spaces; and a Groq 429 killed a turn silently,
leaving the UI on "thinking" forever. All five fixed with regression tests.

Measured: every available Groq chat model is capped at 8,000 tokens/minute on the free
tier; the base prompt is 1,711 tokens, so the bucket sustains about one request per
fifteen seconds. Also found and fixed: DEMO_MODE was documented but never implemented, and
`.env.example` shipped it as `true` — implementing it would have silently faked the whole
product on any deploy filled from the example.

## Outcome

- ✅ Impact: audio works in dev and production builds; confirmation ritual, on-screen
  references and failure reporting now behave as specified; 5 commits pushed.
- 🧪 Tests: 247 passing; full flow re-run confirms the fixes.
- 📁 Files: see front matter.
- 🔁 Next prompts: decide on Groq tier for the demo and judging; redeploy Replit (the
  deployed build predates the worklet fix and is mute); record the real-voice video.
- 🧠 Reflection: every test in the suite interrupted an agent that was already audible and
  typed consent without punctuation. The defects lived exactly where the scripted adapters
  differ from real ones — time-to-first-byte, phrasing, punctuation, rate limits.

## Evaluation notes (flywheel)

- Failure modes observed: a clean run ("zero errors") hid four behavioural defects; only
  reading the transcript found them.
- Graders run and results: pytest 247/247; ruff clean on touched files.
- Next experiment: drive the voice path (not text) with a fake-microphone WAV to exercise
  real AssemblyAI endpointing and barge-in in an automated run.
