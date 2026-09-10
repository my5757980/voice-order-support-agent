# Voice Order Support Agent

**A voice agent that remembers only what you actually heard.**

Built for the [AssemblyAI Voice Agent Hackathon](https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon)
on the **Realtime Speech-to-Text** path — AssemblyAI's streaming WebSocket with our own
orchestration, LLM and text-to-speech. Not the managed Voice Agent API.

---

## The idea

Interrupt most voice agents and one of two things happens: they talk over you, or they
stop but keep the words they were saying in their memory. The second failure is quieter
and worse — every following turn is reasoning about a sentence you never heard.

This agent computes what you *actually heard*, from text-to-speech character alignment
cross-referenced against the audio frames the browser's playback buffer genuinely
released, and truncates its own memory to exactly that.

That property is only available because we took the harder path. An agent built on a
managed voice API is handed turn-taking and never sees the seam.

The domain is e-commerce post-purchase support — "where is my order?", returns,
cancellations. The highest-volume, lowest-value contact category in retail, and one the
business can already answer instantly from data it owns.

## What it does

- Identifies your order without asking for a number, and disambiguates by item and date
- Answers status and tracking, and says "it hasn't shipped" rather than inventing a carrier
- Starts returns and cancels orders — **only after reading the action back and hearing you agree**
- Escalates to a human with full context, immediately, whenever asked
- Can be interrupted mid-sentence, and knows the difference between "no wait —" and "mhm"

## What makes it different

| | |
|---|---|
| **Interruption fidelity** | Memory keeps the heard prefix, not the generated text |
| **Backchannels don't interrupt** | "mhm" while it talks is ignored; "yes" answering a confirmation is not |
| **Actions are structurally gated** | `create_return` is unreachable without a confirmed read-back — enforced in the tool registry, not asked for in a prompt |
| **Never claims false success** | A failed tool produces "that did **not** go through", never "done" |
| **Duplicate-proof** | Idempotency is a database `UNIQUE` constraint, so it holds even if the calling logic is wrong |

## Architecture

```
BROWSER                        │  BACKEND (one asyncio task group per session)
                               │
  mic → AudioWorklet ──────────┼──▶ ingest loop ──▶ AssemblyAI v3 WebSocket
     50 ms PCM16 @ 16 kHz      │                         │ Turn events
                               │                         ▼
  playback AudioWorklet ◀──────┼─── Orchestrator ──▶ ToolRegistry ──▶ SQLite
     ring buffer,              │         │              (4 gates)
     zeroed on interrupt       │         ▼
                               │      Claude (streaming) ──▶ clause splitter ──▶ ElevenLabs
```

Three design decisions worth knowing:

**Audio is relayed through our backend**, not browser→AssemblyAI direct, even though the
vendor supports the latter. Committed turns authorise memory writes and tool calls,
including "the customer confirmed the return" — an untrusted browser must not be the
authority on what was committed.

**Barge-in fires three things concurrently**: the TTS `clear_buffer`, a client-side
`audio.flush`, and cancellation of the turn's task group. The server call stops the
*provider*; only the client flush silences the ~200 ms already buffered in the browser.
Waiting for one before the other measures a correct-looking interrupt latency while the
agent keeps talking.

**Adaptive thinking stays on** in Claude despite the latency cost. With thinking
disabled, Opus 5 can write a tool call into visible text instead of emitting a `tool_use`
block — the turn succeeds, the tool never runs, nothing raises. Here that means the agent
saying "I've started your return" when no return exists. `effort: "low"` buys the latency
back safely.

## Run it

No API keys required — the whole pipeline runs on scripted LLM and tone-based TTS
against the real tool registry and real seeded data.

```bash
# backend
cd backend && pip install -e ".[dev]" && python -m src.adapters.store.seed
uvicorn src.app:app --port 8000

# frontend, in another terminal
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173>, then type or say **"where's my order?"**

With keys, copy `.env.example` to `.env` and fill in `ASSEMBLYAI_API_KEY`,
`ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`. Each adapter switches
independently — an AssemblyAI key alone gives real transcription with scripted replies.
`GET /api/health` reports which mode each slot is in.

## Tests

```bash
cd backend && pytest        # 159 tests, no network, no vendor account
```

Two are worth reading:

- `test_core_purity.py` walks the AST of every module under `core/` and fails on any
  vendor SDK, network or storage import. It has a positive control that proves it can
  fail — a guard never seen failing is indistinguishable from no guard.
- `test_actor.py::test_interruption_truncates_memory_to_what_was_heard` is the property
  this project leads with. If it regresses, the central claim is false.

## Stack

Python 3.11+ / FastAPI · TypeScript / Vite / AudioWorklet · SQLite ·
AssemblyAI Universal-3.5 Realtime STT · Claude Opus 5 · ElevenLabs Flash v2.5

## Project documents

Built spec-first. The reasoning behind every decision is in the repository:

- [`.specify/memory/constitution.md`](.specify/memory/constitution.md) — the
  non-negotiable engineering principles
- [`specs/001-order-support-agent/spec.md`](specs/001-order-support-agent/spec.md) —
  68 requirements, 23 acceptance scenarios
- [`specs/001-order-support-agent/plan.md`](specs/001-order-support-agent/plan.md) —
  architecture and the two at-risk latency budgets
- [`specs/001-order-support-agent/research.md`](specs/001-order-support-agent/research.md) —
  vendor claims marked `[vendor]`, our budgets marked `[ours]`

## Licence

MIT — see [LICENSE](LICENSE).
