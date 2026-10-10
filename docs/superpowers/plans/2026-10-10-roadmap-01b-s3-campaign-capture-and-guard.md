# 01b-S3: Campaign-level capture for the reconcile sweep, and the guard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The continuity reconcile sweep -- the one decide site with no scene
-- files one campaign-level prompt-log entry per pass into a campaign-decision
pool, readable through two campaign routes and the ledger's "What the sweep
asked" list; and a package-wide guard holds every `decide` call to the capture
helper, so a sixth site cannot skip it.

**Architecture:** `prompt_log.NO_SCENE = ""` names a campaign-level entry; its
pool (`operation == "decide"`, `scene == ""`) already counts apart in S2's
`_pool`. `common._record_campaign_prompt` is `_record_prompt`'s sibling that
proves the CAMPAIGN exists (`store.campaigns.read.read_campaign`) inside the
same non-blocking hold, asks `still`, writes, and bumps the revision after the
write. `decision_capture.capturing(cid, NO_SCENE, task, fence=...)` takes the
campaign path: no identity to read, a `fence` required, filed through the
sibling. `continuity._adjudicate` wraps its `decide` in a scope fenced on
`not stillborn()`. Two routes in `routes/scenes.py` beside the scene-scoped
prompt routes. The guard lives in `test_decision_capture.py` and reuses
`test_operation_guard.decide_calls` and `guard_markers.marker_reason`.

**Tech Stack:** Python 3.11, FastAPI, pytest; React + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01b-decision-capture-design.md` §3.3 (campaign fence and residual), §3.5, §3.6, §3.8 (campaign-level captures), §4 C1, §6 tests 1, 6, 7, 9 (campaign clauses), frontend 4, the guard.

## Global Constraints

- `capturing(cid, NO_SCENE, ...)` without a `fence` is a programming error:
  raise `ValueError` on entry (before any I/O), so a site cannot open an
  unfenced campaign scope.
- The campaign check and the write are one critical section under
  `campaign_lock_nowait`; contention, a gone campaign (`CampaignNotFound`),
  `StoreBusy` and `OSError` cost the capture silently; a write bumps the
  revision once, after it, in the hold; a skip bumps nothing.
- Scene-scoped prompt routes never reach a campaign entry (`read_entry(...,
  scene=sid)` and a path segment is never `""`); the campaign routes read only
  `scene == ""` rows. 404 for an unknown campaign.
- Guard: every `decide` operation call in the package passes `capture=` that is
  `<name>.hook(...)` in `routes/`, or, outside `routes/`, a bare name that is a
  parameter of its enclosing function; anything else needs
  `# capture-ok: <reason>` (a reason required; at most 2 markers). The guard
  fails on planted cases and passes on the tree.
- CLAUDE.md's "the scene-break, voice-drift and continuity decisions capture
  nothing" is rewritten to name the helper, the pool and the campaign-level
  entries; `test_docs_guard.py` must stay green.
- Reconcile's `_log_pass` stays counts-only.

## Review Focus

- Reconcile's scope must wrap only the `decide` call, inside the existing
  `try` that maps `LLMError`/`DecideRequestError` to a failed run, so the
  scope files and re-raises into that mapping.
- `stillborn` is `run.cancel_requested or run.forgotten`: a forgotten run
  (campaign deleted) files nothing, and the residual (store delete before
  `forget_subject`) is covered only by `read_campaign`.
- Retention: four reconcile passes at depth 3 leave three campaign decisions
  and every turn and scene decision; four scene-break checks leave three scene
  decisions and every campaign decision.

---

### Task 1: `NO_SCENE`, `_record_campaign_prompt`, campaign scope

**Files:** Modify `store/prompt_log.py` (`NO_SCENE`, `list_campaign_entries` or reuse `list_entries(cid, NO_SCENE)`), `routes/common.py`, `routes/decision_capture.py`; Test `tests/test_decision_capture.py`.

- [x] Failing tests: a campaign scope files one entry with `scene == ""` in its pool; without a fence it raises `ValueError`; a fence answering False files nothing and bumps nothing; a deleted campaign files nothing; contention files nothing and does not wait; retention (both clauses of test 7).
- [x] Implement.

### Task 2: the reconcile site and the two campaign routes

**Files:** Modify `routes/continuity.py` (`_adjudicate`), `routes/scenes.py` (two GET routes); Test `tests/test_decision_capture.py`.

- [x] Failing tests: a reconcile pass files exactly one `continuity-reconcile` entry at campaign level with `operation == "decide"` and the outcome last; a run marked `forgotten` files nothing; `GET /campaigns/{cid}/prompts` lists only `scene == ""` entries; `GET .../prompts/{eid}` reads one and 404s a scene entry; the scene-scoped detail route 404s a campaign entry.
- [x] Implement.

### Task 3: the package-wide guard

**Files:** Test `tests/test_decision_capture.py`.

- [x] Guard over `test_import_guard.SOURCES` with `test_operation_guard.decide_calls`; planted cases fail (no capture, a lambda, a local name in routes, a non-parameter name outside routes, a marker with no reason) and pass (`scope.hook()`, `s.hook(aid)`, a parameter pass-through outside routes, a reasoned marker); the tree passes; the marker count is at most 2.

### Task 4: frontend

**Files:** Modify `frontend/src/api/client.ts` (`listCampaignPrompts`, `getCampaignPrompt`), Create `frontend/src/components/continuity/SweepCaptures.tsx`, Modify `ContinuityReview.tsx`; Test `frontend/src/components/continuity/SweepCaptures.test.tsx`.

- [x] Failing vitest: the list is collapsed and reads nothing until opened; opened, it lists the sweep's entries by label and time; a row opens the entry read-only through `ContextBreakdown` ("outcome · not sent", no textbox).
- [x] Implement.

### Task 5: docs, verify, commit

- [x] CLAUDE.md sentence; full backend suite; vitest; tsc; ratchets.
- [x] Commit `01b-S3: the reconcile sweep captures at campaign level, and every decide is held to the helper`.

## Plan gate (substitute review, 2026-10-10)

No Codex CLI and no subagent tool: an adversarial self-review against the
spec and the code. Findings:

1. **Should-fix, folded.** The spec names the two campaign routes but not
   their module. They sit in `routes/scenes.py` beside the scene-scoped prompt
   routes they mirror (one place documents the prompt log's HTTP surface), and
   404 an unknown campaign as the scene routes 404 an unknown scene.
2. **Should-fix, folded.** An unfenced campaign scope would be the one capture
   with no ownership check but `read_campaign`, which a same-named recreate
   passes; the helper refuses one outright (`ValueError` on entry), so C1's
   "a campaign-level site passes its own fence" is enforced rather than hoped.
3. **Minor, folded.** The guard's "parameter of the enclosing function" is
   checked against the nearest enclosing `def`/`async def`/`lambda`'s
   arguments, positional, keyword-only and `*`/`**` alike; a module-level call
   outside `routes/` has no enclosing function and so needs a marker.
4. **Minor, noted.** Today no decide call lives outside `routes/`, so the
   pass-through branch is exercised only by planted cases; the guard also
   asserts it sees at least the five known sites, so a recogniser that stopped
   finding calls fails rather than passing vacuously.
5. **Minor, noted.** Campaign entries need no `repoint_scenes`/`forget_scene`
   handling: their `scene` is `""`, which no scene id equals, and they leave
   with the campaign directory.

## Review and final gate (substitute review, 2026-10-10)

An adversarial self-review of the slice diff, and of all three slices against
the spec (the "done -> actually done" question: does the change implement
01b, not merely is it clean), stood in for `/codex:review` and the final
`/codex:adversarial-review` (no Codex CLI, no subagent tool).

Traced, spec clause to code and test:

- C1: the helper is the one way a site captures; all five §3.6 sites use it
  (speaker, scene-break, voice drift, identity in S2; reconcile here), and the
  package-wide guard holds every `decide` call to `capture=<scope>.hook(...)`
  in `routes/` or a parameter pass-through elsewhere, with a capped, reasoned
  `# capture-ok:` marker (planted cases both ways; the tree passes with no
  marker). Stage and `at` on every record (S1); notes (`Scope.note`);
  `hop` would pass through unchanged (records are the outcome dict plus
  `part`). Campaign-level entries with `scene: ""`, read through the two
  campaign routes; every capture fenced (strict identity, or the site's fence,
  which a campaign scope is refused without).
- C2: no I/O in the hook; one filing per scope in a worker thread under the
  non-blocking lock; never on cancel, `Abandoned` or an abandoned review; a
  failure is one counts-only warning; no provider text (kind, status, code).
- §3.5 pools: three pools, each `depth()`; §6 test 7's three clauses.
- §3.7 diff 409 (S2); §3.8 frontend incl. "What the sweep asked" (frontend 4).
- §5: CLAUDE.md's "capture nothing" sentence rewritten to name the helper,
  the pool and the campaign-level entries.

Findings folded:

- **ISC004 / import order** in the new test and `continuity.py` imports, held
  at the ruff baseline.
- **The "What the sweep asked" list could show the wrong campaign's rows**
  after a campaign switch with the list open; the component is keyed on the
  campaign, and every answer is held with the campaign it was read for.

Not folded, stated:

- **The forget-window residual** (§3.3): between a campaign's store delete and
  `forget_subject`, only `read_campaign` covers a campaign-level capture, and
  a same-named recreate inside that window passes it. Accepted by the spec;
  documented on `_record_campaign_prompt`.
- **A reconcile pass that captures stamps the write token** even when its
  persists left the candidate cache as it was (§5 accepts this: a caller with
  an older token is owed a re-price, never a refusal).
- **01d's `hop`** is not stamped by anything yet; the capture would carry it
  through as it carries every outcome key.
- **The new marker family had no CONTRIBUTING row**: `test_docs_guard.py`
  failed on `capture-ok`; the guard table now names it.

Results: `test_decision_capture.py` 52 passed (21 new here, the guard's
planted cases included); docs guard 63 passed; ruff, mypy and eslint ratchets
all at baseline; `tsc -b` clean. Full backend suite (`-n 4`, run while vitest
ran beside it): 16415 passed, 5 skipped, 3 failed -- the root-only
`test_atomic.py::test_a_read_only_record_is_not_silently_replaced` (fails on
main too), the docs guard (fixed above), and
`test_routes.py::test_absorb_budget_exhaustion_cancels_the_slow_phase_and_keeps_the_rest`,
a timing test that passed in the S2 full run and three isolated re-runs.
Vitest: 4552 passed, 1 failed under the same load
(`CampaignView.render.test.tsx`'s render-count test, untouched by 01b), which
passes re-run alone.
