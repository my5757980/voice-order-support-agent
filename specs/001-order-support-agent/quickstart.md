# Quickstart: Voice Order Support Agent

**Feature**: `001-order-support-agent` | **Date**: 2026-09-09

Get a voice turn round-tripping locally, then verify the constitution's gates hold.

---

## Prerequisites

- Python 3.11+
- Node 20+
- API keys: AssemblyAI, Anthropic, ElevenLabs
- A browser with microphone permission (Chrome or Edge recommended for AudioWorklet stability)

## 1. Configure

```bash
cp .env.example .env
```

Fill in:

```bash
ASSEMBLYAI_API_KEY=...
ANTHROPIC_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...

# Tuning — every one of these is configuration, never a literal in code
STT_SPEECH_MODEL=universal-3-5-pro
STT_MIN_TURN_SILENCE_MS=160
STT_MAX_TURN_SILENCE_MS=400
STT_END_OF_TURN_CONFIDENCE=0.4
SPECULATIVE_THRESHOLD=0.7
BARGE_IN_MIN_WORDS=2
BARGE_IN_GRACE_SECONDS=1.0
LLM_MODEL=claude-opus-5
LLM_EFFORT=low
LLM_MAX_TOKENS=320
HOLDING_PHRASE_DELAY_MS=400
DEMO_MODE=true
```

`.env` is git-ignored. Configuration is validated at startup and the process **fails to boot** on
anything missing or malformed — never a silent runtime default.

## 2. Install and seed

```bash
cd backend && pip install -e ".[dev]" && python -m src.adapters.store.seed
cd ../frontend && npm install
```

Seeding creates a fixture customer with two orders in deliberately different states — one shipped,
one not — so order disambiguation and the cancel-refusal path are both demonstrable on cue.

## 3. Run

```bash
# terminal 1
cd backend && uvicorn src.app:app --reload --port 8000
# terminal 2
cd frontend && npm run dev
```

Open `http://localhost:5173`, grant microphone access, and say **"where's my order?"**

---

## What a correct first turn looks like

```text
[you]    "where's my order?"
[agent]  "You have two — the headphones from the 3rd and the desk lamp from
          the 7th. Which one?"
[you]    "the lamp"
[agent]  "It's out for delivery, arriving today."
```

If the agent asks for an order number, order identification (FR-015/FR-016) is broken — it should
never ask for a number when the account has recent orders.

## Verify the gates

These are the checks that matter; run them before trusting anything else.

**Latency** — the two at-risk gates from the plan:

```bash
curl -s localhost:8000/metrics | grep -E 'turn_latency_e2e|tts_ttfb|llm_ttft|stt_turn'
```

| Span | p95 budget | Hard fail |
|---|---|---|
| `stt.turn` | 300 ms | 500 ms |
| `llm.ttft` | 450 ms | 800 ms |
| `tts.ttfb` | 250 ms | **400 ms — the known risk** |
| end-to-end | 1000 ms | 1500 ms |

**Barge-in** — interrupt the agent mid-sentence:

```bash
curl -s localhost:8000/metrics | grep barge_in_latency
```

Must be under 200 ms. Then say "mhm" while it talks — `backchannel_suppressed_total` should
increment and the agent must **not** stop.

**The confirmation ritual** — the one gate that is a release blocker:

```text
[you]    "I want to return the headphones"
[agent]  "That's the wireless headphones from your order on the 3rd —
          shall I start that return?"
[you]    "yes"
[agent]  "Done. Your return reference is R-4471..."
```

The agent must **never** create the return before an explicit affirmative. Note that "yes" here is a
real answer, not a backchannel — that override is the `pending_confirmation` check, and it is the
single most likely thing to regress.

**Prompt caching** is working if this is non-zero after the second turn:

```bash
curl -s localhost:8000/metrics | grep cache_read_input_tokens
```

A persistent zero means a silent invalidator — usually a timestamp in the system prompt or an
unsorted tool list.

## Tests

```bash
cd backend
pytest tests/unit          # pure core logic, no I/O, injected clock
pytest tests/contract      # our STT fixtures vs. the real vendor schema
pytest tests/integration   # full pipeline with fake adapters — no live vendor calls
pytest --cov=src/core      # core must stay the highest-covered package
```

The import guard is the one that keeps principle IV honest:

```bash
pytest tests/unit/test_core_purity.py   # fails if any vendor SDK is imported under core/
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| Agent talks over you | `audio.flush` not reaching the browser, or playback is an `<audio>` element rather than an AudioWorklet ring buffer |
| Agent stops on "mhm" | `BARGE_IN_MIN_WORDS` too low, or the backchannel set is missing tokens |
| Agent ignores "yes" during confirmation | `pending_confirmation` not consulted **first** in the barge-in decision |
| Long silence before speech | Check `llm.ttft` — likely thinking latency; confirm `LLM_EFFORT=low` and that caching is hitting |
| Choppy audio | Frames not exactly 1600 bytes, or capture running on the main thread instead of a worklet |
| Crash at startup | Working as designed — a config error must fail fast, never default silently |
