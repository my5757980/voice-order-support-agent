# Submission — AssemblyAI Voice Agent Hackathon

Everything the lablab.ai submission form asks for, drafted in one place. Numbers below are
measured on the deployed app, not estimated; where a number depends on where the listener is,
that is said.

---

## Project title

**Voice Order Support Agent — interrupt it any time**

## Short description

A voice agent for "where is my order?", built on AssemblyAI Realtime STT with its own
orchestration. Talk over it and it stops at once — and remembers only the words you actually heard.

## Long description

**The problem.** Post-purchase support is the highest-volume, lowest-value contact in e-commerce,
and its dominant question — *where is my order?* — is one the business can already answer from
data it owns. Shoppers still wait in a queue for a human, or fight an IVR menu, or type order
numbers into a chatbot that collapses on the first follow-up. The need is conversational: short,
connected questions with pronouns, corrections and changes of mind, answered immediately. That
is a voice problem — and most voice agents make it worse, because they cannot be interrupted.

**What it does.** The shopper opens the page and talks. The agent looks their orders up before
asking anything, answers in two sentences, and puts tracking numbers and order references on
screen instead of reading them aloud. It can start a return, cancel an order that has not
shipped, or change an address — and for anything that changes the world it checks first that
the action is possible, reads it back, and waits for an explicit yes.

**What makes it different: it can be interrupted, properly.** We took the Realtime STT path, not
the managed Voice Agent API, so we own turn-taking — and the part we cared about is the one the
managed path cannot give you. When the shopper talks over the agent:

- the browser's own playback buffer is zeroed on the next audio render quantum, independently of
  the server stopping synthesis — the only way already-buffered speech actually stops;
- barge-in fires on the first real word of a streaming partial, not on the end of the sentence;
  backchannels like "mhm" and "okay" are filtered out and never stop it;
- memory records **only what the shopper heard** — computed from the audio frames the browser
  actually played, mapped onto the reply at clause resolution — never what the model generated.
  The next answer builds on what was heard.

**Built to be trusted with actions.** State-changing tools are gated in the tool registry, not
in the prompt: an unknown tool, invalid arguments, a speculative turn, or a missing confirmation
each stop a call before it runs. Returns are idempotent through a database constraint. The agent
never claims success a tool did not report. Speculative dispatch starts the model during the
shopper's trailing silence, and the registry makes a speculative turn structurally read-only.

**Measured, and shown.** Every turn draws a latency waterfall on screen — first token, tool, first
audio, end to end. On the recorded run against the deployed app: the agent stopped talking
1.14 s after the shopper started speaking, and answered 3.0 s (median) after the
shopper finished. Honest caveat: on Groq's free tier the model is capped at 8,000 tokens a
minute and the voice at ten requests a minute, so the live demo serves one conversation at a
time.

**Stack.** AssemblyAI Universal-Streaming (v3 WebSocket, balanced turn detection) · Groq
`openai/gpt-oss-120b` with a manual tool loop · Groq / Canopy Labs Orpheus TTS · FastAPI +
asyncio, one task group per turn · TypeScript + AudioWorklet capture and ring-buffer playback ·
SQLite behind repository ports. Vendor SDKs live only in adapters; the core imports none, and a
test enforces it. 288 tests.

## Technology tags

AssemblyAI · Universal-Streaming · Realtime Speech-to-Text · Groq · gpt-oss-120b · Orpheus TTS ·
FastAPI · WebSockets · AudioWorklet · Python · TypeScript

## Category tags

Voice AI · Voice Agent · Customer Support · E-commerce · Conversational AI

---

## Links

| Field | Value |
|---|---|
| Submitted project page | https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon/voice-order-support-agent/voice-order-support-agent-interrupt-it-any-time |
| Application URL | https://voice-order-support-agent--my5757980.replit.app |
| Repository (public, MIT) | https://github.com/my5757980/voice-order-support-agent |
| Video | `assets/demo.mp4` |
| Slides | `assets/slides.pdf` |
| Cover image | `assets/cover.png` (16:9) |

## About the demo video

Recorded from the deployed Replit app. The shopper's voice is synthesized so the demo is
reproducible; it enters through the real microphone path into AssemblyAI streaming STT, and the
barge-in happens for real. Pauses between turns — waiting out the free-tier token limit — are
shortened in the edit. Nothing inside a turn is cut.
