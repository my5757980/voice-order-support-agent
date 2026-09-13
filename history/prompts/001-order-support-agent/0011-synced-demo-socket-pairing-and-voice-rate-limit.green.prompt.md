---
id: 0011
title: Synced Demo, Socket Pairing And Voice Rate Limit
stage: green
date: 2026-09-13
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: main
user: my5757980@gmail.com
command: (user request — fix the demo video's sync, record it live so it doubles as a test, then use a new Groq key and redeploy)
labels: ["video", "av-sync", "timecode", "websocket-pairing", "rate-limit", "orpheus", "replit", "submission"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon
  adr: null
  pr: null
files:
  - backend/src/app.py (control socket waits 30 s for the audio socket, woken by an event, and says so when it never arrives)
  - backend/src/adapters/tts/groq_orpheus.py (a reply goes out as its opening words plus one merged request, not one request per clause)
  - backend/tests/integration/test_app_e2e.py (pairing: a missing audio socket is reported; the window stays clear of the measured gap)
  - backend/tests/unit/test_tts_coalescing.py (new: merge, short opening, six-request demo opening, offsets, hold before a tool call, interruption, long reply)
  - backend/tests/unit/test_tts_ordering.py (pinned to one request per clause; the properties it guards are per request)
  - assets/demo.mp4 (re-recorded on the published app; picture and sound measured in sync)
  - SUBMISSION.md, README.md, assets/src/slides.html, assets/slides.pdf (3.0 s median; the ten-requests-a-minute voice cap)
tests:
  - 288 passing (was 279)
  - final take on the published app: 0 errors, all six turns spoken, barge-in 1.14 s, voice-to-voice 3.0 s median (max 3.7 s)
  - sync of the finished file: picture -31 ms and sound -66 ms from the page clock, -35 ms apart
  - socket-timing trace: audio socket 5.7 s behind the control socket under recording load
  - deployment key proven by counting: the new key's speech counter fell by the published app's greeting
---

## Prompt

(Spanning several turns.) "abyy to nahii videoo mayyy awaz kayy sath recording honii chiyaa thii
naa waz to haii hi nahii is mayyy" — "galat bhii haii videooo awaz phlayy hi chal rahii haiii
text dear pa a rahaa haii screen parrr ya achii live videoo record karooo project ki testing bhi
hojayy giii live record bhiiii" — "gsk_… ya new key try karoo is mayy abhii hogayy us purani wali
hata do delete kar do ya use karoo mujayy abhi chiya wait nahi kar sakta is lia ya try karooo" —
"kar diaaaaa" (Orpheus terms accepted) — "Replit update karke deployed par dobara shoot
(recommended)" — "ya 3noo bhi tum khud karooo jaldieeee" — "playwrite connected haiii".

## Response snapshot

The committed video's sound led its picture by about 0.8 s. Three measurement-based repairs
failed for one reason: Playwright's screencast writes its own timestamps and they drift — 1.3%
on one take, -3.15% in a test, with residuals up to 0.8 s — so no offset measured on one part of
a take held for another. Tab capture was tried as a single-stream alternative and abandoned:
headless Chromium captured the user's real screen instead of the tab, and its audio was hum.
Those recordings were deleted unpublished and the user was told.

What worked: the page paints `performance.now()` into a 24-pixel strip under every frame, the
build reads that clock back from each frame's own bytes and rebuilds the picture at 30 fps on
it, and the soundtrack is placed by locating the injected shopper lines, whose waveforms are
known. A straight-line fit corrects a 0.2% difference between the audio graph's clock and the
page's. The build checks its own output by reading the timecode back off the rebuilt picture.

Recording exposed two product defects. The control socket gave the audio socket five seconds
to arrive; under load it arrived 5.7 s late, and the session stayed connected with no
transcriber, greeting or error. And Groq's free tier allows Orpheus ten requests a minute —
a limit absent from the response headers, which report only the daily allowance — while the
adapter sent one request per clause: twelve in the first sixteen seconds of the demo, cutting
the voice on the second turn. Both fixed with tests: a 30-second event-driven pairing window
that reports failure, and replies sent as their opening words plus one merged request.

Swapped in the user's new Groq key locally and in Replit Secrets, pulled both fixes and
republished through the Replit workspace, proved the deployment uses the new key by its
request counter, and recorded the final take on the published app.

## Outcome

- ✅ Impact: a demo video whose sync is measured rather than asserted; a live app that no longer
  dies silently on a slow second socket or loses its voice to the per-minute cap.
- 🧪 Tests: 288 passing; both product fixes have regression tests.
- 📁 Files: see front matter.
- 🔁 Next prompts: file on lablab.ai (T086); rotate both Groq keys pasted into chat after judging.
- 🧠 Reflection: every proxy for sync measured something adjacent to sync. The fix was to make
  the recording carry the clock it would be judged against, and to check the output rather than
  the method.

## Evaluation notes (flywheel)

- Failure modes observed: a recorder whose timestamps drift; a verification that proved an audio
  track existed rather than what it contained; a rate limit the provider's headers do not report;
  a pairing timeout that failed without a message.
- Graders run and results: pytest 288/288; timecode read-back on the rebuilt picture (median
  -31 ms); clip correlation on the finished file (sound -35 ms against picture); deployed take
  with 0 errors.
- Next experiment: surface Groq's per-minute limits on /api/health from a startup probe, so the
  next hidden cap is visible before a demo rather than during one.
