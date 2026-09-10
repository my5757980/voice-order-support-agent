---
id: 0005
title: Hackathon Requirements Compliance Audit
stage: misc
date: 2026-09-09
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: 001-order-support-agent (directory only; not a git repository)
user: my5757980@gmail.com
command: (user request — Playwright scan of hackathon rules + audit all 4 SDD artifacts)
labels: ["hackathon", "compliance", "playwright", "requirements-audit", "originality", "replit", "submission"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon
  adr: null
  pr: null
files:
  - .specify/memory/constitution.md (v1.0.0 → v1.1.0; External Constraints section added)
  - specs/001-order-support-agent/spec.md (Positioning section added — originality risk)
  - specs/001-order-support-agent/plan.md (deployment Fly.io → Replit; submission artifacts; judging map; deadline)
  - specs/001-order-support-agent/research.md (R8 deployment decision corrected)
  - specs/001-order-support-agent/tasks.md (Phase 4 T081–T086 added; T077 retargeted; T077b added)
  - history/prompts/001-order-support-agent/0005-hackathon-requirements-compliance-audit.misc.prompt.md (this file)
tests:
  - none executed (documentation audit)
  - verified by grep: zero stale Fly.io references outside correction notes; task count 87; constitution v1.1.0
---

## Prompt

https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon  tumharayy pass mcp playwrite connected haii use karoo is link par jaoo ya is hackathone ka detail requirement haii full scan karo line by line word by word dekhoo 2 thayy option maynay 2 wala choose kiaa dekhoo sab 4 roo specs sahii requiremeint mutabiq banayaa phir implement shuruu karnaaaa ok karoo ab check

## Response snapshot

Scanned the hackathon page and the lablab.ai Hackathon Rule Book via Playwright MCP (the
`/hackathon-guidelines` path 404s; the real rules live at `/hackathon-rules`, found by extracting
footer hrefs). Audited all four SDD artifacts against what was found.

**Path choice confirmed correct.** The hackathon offers two paths; the user chose #2, Realtime
Speech-to-Text with own orchestration. Constitution prohibition 1 ("NEVER use the Voice Agent API")
matches exactly. No change needed there — and it is the less crowded path, since most public
submissions use the managed Voice Agent API.

**Three P0 violations found and fixed:**

1. **Deployment was non-compliant.** The plan specified Fly.io. The Rule Book permits only
   *"Streamlit, Replit, or Vercel"* and warns that non-adherence "may result in a lower score or
   exclusion". Corrected to **Replit** across plan.md, research.md R8, and tasks.md T077. Replit is
   the only permitted platform that runs a persistent process capable of holding long-lived
   WebSocket servers — Vercel is serverless (no WS server) and Streamlit's rerun model is hostile to
   raw WebSockets and AudioWorklet. Added T077b to re-measure latency **on the deployed instance**,
   because Replit gives no region pinning and the original latency argument depended on it.

2. **Every mandatory submission artifact was missing from the task list.** The Rule Book mandates a
   project title, short + long descriptions within limits, technology/category tags, a **16:9
   PNG/JPG** cover image, an **MP4** video, a **PDF** slide deck, a **public** GitHub repository, and
   a live Application URL. tasks.md had none of these. Added **Phase 4 (T081–T086)**, all critical
   path, plus MIT `LICENSE` as a build artifact ("submissions must be original and MIT-compliant")
   and a full-history secret scan before the repo goes public — this project holds three vendor keys
   and history is not rewritable once judges have the link.

3. **The deadline was misread.** Submission closes **Sep 30, 8:00 PM Pakistan Standard Time**, and
   the video and slides must exist by then. The plan treated Sep 30 as a demo day. Build deadline
   pulled to **Sep 27**, with Sep 28–30 as a dedicated submission phase. The 6-hour manual-submission
   window needs prior organiser approval and is explicitly not a buffer to plan around.

**The most consequential finding is strategic, not procedural — originality.** Judging is four
equally weighted criteria: Application of Technology, Presentation, Business Value, **Originality**.
Reviewing the ~36 public submissions showed the spec's headline differentiator — *an agent that
refuses to act without confirmation and never claims false success* — is independently the headline
of at least four other entries: **Voice Action Gate** ("cannot execute an irreversible action unless
every argument can be traced back to words the user actually said"), **The claim intake agent that
refuses to guess**, **Saakshi — Consent You Can Prove**, and **FarmVoice**. Being fourth to a good
idea scores badly on Originality even with a stronger implementation.

What *is* rare: **barge-in fidelity**. Only one public submission mentions interruption at all,
because almost everyone took the managed API that hands them turn-taking. Our spec already requires
something nearly nobody implements — memory truncated to the words the shopper *actually heard*,
computed from TTS character alignment cross-referenced with frames the playback buffer released.
Added a **Positioning** section to spec.md recommending we keep the product unchanged but lead with
interruption fidelity instead of action-safety. This changes the title, the video's opening, and the
slide order — **and no code**.

Also noted, not acted on: the chosen path advertises *"Multilingual speech recognition"*, while the
spec scopes v1 as English-only (Assumption 3, Out of Scope 16). Flagged as an optional
Application-of-Technology lever, deliberately not adopted — it is scope creep with 18 days left.

Constitution bumped **1.0.0 → 1.1.0** (MINOR — new section, no principle changed) adding *External
Constraints — Hackathon Compliance*, including a precedence rule: hackathon rules outrank our own
preferences, but a rule that would require violating a NON-NEGOTIABLE principle escalates to the
architect rather than being silently resolved.

## Outcome

- ✅ Impact: All four artifacts now match the actual, verified hackathon requirements. Three
  compliance defects fixed, any one of which could have cost the entry. Task count 80 → 87.
- 🧪 Tests: None executed — documentation audit. Verified by grep: constitution reports v1.1.0; zero
  stale Fly.io references outside explicit correction notes; 87 tasks with 75 on the critical path;
  Replit referenced in both plan.md and tasks.md.
- 📁 Files: 5 modified, 1 PHR created.
- 🔁 Next prompts: begin implementation at **T005/T006** (the day-1 latency spikes) — but they need
  AssemblyAI, Anthropic, and ElevenLabs API keys, which is the actual gate on starting. T001–T004
  (scaffold, config, logging) need no keys and can start immediately.
- 🧠 Reflection: Every one of the three P0 defects came from an external document nobody had read —
  not from flawed reasoning. The plan's Fly.io choice was well-argued and correct on the technical
  merits, and still wrong, because the merits were not the binding constraint. Reading the rules
  before optimising against them would have cost ten minutes on day one.

## Evaluation notes (flywheel)

- Failure modes observed: (1) **I planned a deployment target without reading the competition rules**
  — the single largest miss across four turns of otherwise careful work, and it survived a full
  Constitution Check because the gate list only asked whether the design met *our* constitution. The
  generalisable lesson: a compliance gate is only as good as the inputs it was written against, and
  external requirements need to be gathered *before* the first design decision, not audited after.
  (2) `lablab.ai/hackathon-guidelines` 404s while being the linked label; the real path is
  `/hackathon-rules`, recovered by extracting hrefs rather than guessing URLs.
- Graders run and results (PASS/FAIL): Path-choice conformance PASS (Realtime STT, prohibition 1
  intact). Deployment-platform conformance **FAIL → fixed**. Submission-artifact coverage **FAIL →
  fixed** (0/9 → 9/9 mapped to tasks). Deadline accuracy **FAIL → fixed**. Licensing requirement
  **FAIL → fixed** (T085). Originality **AT RISK → repositioning recommended, user decision
  pending**. Constitution version coherence PASS.
- Prompt variant (if applicable): n/a.
- Next experiment (smallest change to try): Add an "External requirements" gate to
  `plan-template.md`'s Constitution Check — one line asking whether competition rules, platform
  constraints, or client contracts have been read and cited. This failure class is invisible to a
  gate list that only checks internal consistency, and it is cheap to prevent.
