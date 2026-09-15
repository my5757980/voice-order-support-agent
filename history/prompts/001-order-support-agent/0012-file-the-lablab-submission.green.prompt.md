---
id: 0012
title: File The Lablab Submission
stage: green
date: 2026-09-15
surface: agent
model: claude-opus-5
feature: 001-order-support-agent
branch: main
user: my5757980@gmail.com
command: (user request — open the lablab.ai form and file the submission; LinkedIn post and reply drafted first)
labels: ["submission", "lablab", "linkedin", "t086"]
links:
  spec: specs/001-order-support-agent/spec.md
  ticket: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon/voice-order-support-agent/voice-order-support-agent-interrupt-it-any-time
  adr: null
  pr: null
files:
  - SUBMISSION.md (288 tests; link to the submitted project page)
  - specs/001-order-support-agent/tasks.md (T086 done)
tests:
  - lablab.ai confirmation: "You have successfully submitted your project for the AssemblyAI - Voice Agent Hackathon event!"
  - team page shows "Edit Submission", no longer "draft in progress"
  - public project page carries the title, the 1:50 video with the cover as its poster, the slides PDF, the repository, the live app, the tags and the long description
  - live app answers a logged-out request: HTTP 200, /api/health ok
---

## Prompt

(Spanning several turns.) "video kis location may hai" — "mujhe ye video with professional
text linkedin par post karna hai … professional english text de do" — a LinkedIn comment
received on the post, with "is ko professional kya jawab banta hai batao" — "chalo ab jaldi
form open karo or submission shuru karo" — "playwright connected hai open karo submission
shuru karo" — "kar dia" (signed in to lablab.ai) — "haaa" (approval to press Submit).

## Response snapshot

Drafted the LinkedIn post and a reply to a technical comment, keeping every claim to what was
measured and not overstating the author's prior experience.

For the submission, the Playwright MCP server would not reconnect, so the browser profile the
user had signed in with was opened directly and driven over its debugging port. The user
signed in themselves; nothing touched their credentials or the Cloudflare check.

lablab.ai requires a team before it will accept a submission, so a closed solo team was
created. The three-step form was filled from SUBMISSION.md, with the long description cut to
fit its 2,000-character limit and the test count corrected to 288. The technology picker does
not offer AssemblyAI and refuses custom tags, so Groq, OpenAI (for gpt-oss) and Replit were
chosen and AssemblyAI is named in the title and both descriptions. The user reviewed a summary
and approved.

The first press returned HTTP 200 but only saved a draft: the button had read "Retry" because
an earlier step save had failed, and on reload the draft held none of the text. Refilling all
three steps while recording the form's server calls brought the button back to "Submit"; the
cover, dropped in the refill, was restored before pressing it. lablab.ai confirmed the
submission, and the public project page was checked field by field.

## Outcome

- ✅ Impact: T086 done — the project is submitted, ahead of the Sep 30 deadline.
- 🧪 Tests: confirmation text, team page state, public project page contents, logged-out app check.
- 📁 Files: see front matter.
- 🔁 Next prompts: join the lablab.ai Discord if not already a member (the task lists it as
  required, and it was not checked from here); rotate both Groq keys once judging ends.
- 🧠 Reflection: a 200 from a form is not a submission. What settled it was the page the
  organiser shows afterwards — "Edit Submission" instead of "draft in progress".

## Evaluation notes (flywheel)

- Failure modes observed: a status code standing in for an outcome; a stale "Retry" label
  hiding a failed step save; a cover image lost silently on refill; a locator matching a hidden
  cookie-consent button of the same name.
- Graders run and results: organiser confirmation, team page, public project page.
- Next experiment: when automating a multi-step form, read back the server's copy of each step
  before moving on, rather than trusting the client's fields.
