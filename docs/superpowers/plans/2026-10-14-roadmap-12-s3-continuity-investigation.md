# Roadmap 12-S3: E2, continuity investigation (the pilot)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** A reader investigates one live continuity finding; at most one
proposal is written on it, with the run's trace.
**Architecture:** `POST /campaigns/{cid}/continuity/candidates/{candidate_id}/investigate`
(202, `@computes_only`, `def`) reserves a `background` run, kind
`continuity-investigate`, adopted by a start for the same finding and refused
409 `busy` to another (`reserve_campaign_background(labels=, match=)` over a new
`single_match` in `start_or_existing`). The run investigates and calls
`reconcile.persist_investigation`.
**Spec:** sections 6.2, 8.2, 9.2, 9.4, 12, 14 "Write posture" / "Runtime".

## Decisions taken against the spec

- Candidate records carry no per-record `generation` or `basis`: those are the
  cache file's, and the persist writes the file with both unchanged.
- A proposal citing any scene outside the run's `inspected` set is
  `uncertain` whole (`final._proposal`), beyond `reconcile.rebuild`'s own
  dropping of unknown evidence: the spec's "downgraded to uncertain,
  mechanically".
- The seed is the sweep's own candidate block (`continuity_reconcile/item.j2`)
  and criteria (`continuity_reconcile/question.j2`), named by letter.
- The UI is the finding detail's sidebar "Investigate" (or "Ask for a second
  opinion" when a proposal stands, open question 4), drawn only while the
  switch is on, and the trace beside the proposal it justifies.

## Tasks

- [x] `investigation` route (`requires=("tools",)`, `NO_LEGACY`) with `investigation-continuity`.
- [x] `reconcile.rebuild`, `reconcile.persist_investigation`, `candidates.investigation_trace`.
- [x] `runs` adoption predicate; the start route and its refusals; the `investigation_continuity` switch (config, `PUT /config`, Settings).
- [x] CLAUDE.md detached runs, docs guard, routing tests; `test_investigation_routes.py` and `test_investigation_persist.py`.
