# Roadmap 12-S5: E3, history question

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** A reader asks the campaign's history a question and gets an answer
citing only scenes the run read, held on a draft run and never stored.
**Architecture:** `POST /campaigns/{cid}/history/question` (202,
`@computes_only`) through `runs.run_draft`, which gains `pass_run=True` so the
loop runs under the draft's own run id. The ledger's context column offers
"Ask the history" while `investigation_question` is on; the panel uses
`api.askHistory` (`draftRun`).
**Spec:** sections 6.3, 8.2, 9.3, 12-S5.

- [x] The `investigation-question` task; `answer` validation against `inspected` (none left is `insufficient_evidence`).
- [x] The route's refusals before reserving (off, empty or long question, incapable, unpriceable); a repeated attempt adopted and not re-run.
- [x] The switch, the ledger panel and its tests; CLAUDE.md (+1 handler, +1 draft).
