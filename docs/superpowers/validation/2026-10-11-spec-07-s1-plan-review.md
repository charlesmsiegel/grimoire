# Spec 07-S1 plan review record

**Scope:** S1 plan before implementation.
**Date:** 2026-10-11.
**Current verdict:** `approve` - fourth actual Codex plan gate. Human plan review
and execution-method choice are pending; implementation has not started.

## First actual Codex plan gate

**Reviewed commit:** `fe99f0230911a7f7eb33169f7e533b139c556a47`.
**Thread:** `01a12ca5-59b9-74f0-bc01-3b03c2839628`.
**Verdict:** `needs-attention`.

Block the PLAN gate until collateral reclassification copies have a defined failure path.

### Handle identity refusals in post-move reclassification sweeps

**Severity:** medium.

The central copy guard also reaches overlay.rewrite_owner_refs/rewrite_ref_fields through update_entity. These sweeps run after campaign_entity has moved the source and rewritten its ledgers. An inherited referring group with unresolved Members would raise RuntimeError-based GroupIdentityConflict, which the existing reclassify._sweep catch list excludes. The plan specifies neither collateral-copy preflight nor post-move handling/tests. Following the existing control flow would therefore return 409 after a landed move, leave references partly rewritten, and miss the success-only campaign revision stamp.

**Recommendation:** Add a route regression where reclassifying another record triggers an inherited group's owner/ref rewrite with unresolved Members. Either preflight those collateral copies before moving, or explicitly handle GroupIdentityConflict in the best-effort post-move sweep while preserving the landed move's response and revision stamp.

**Verification:** The current `_sweep` catches OSError, ValueError and
EntityNotFound after the move. Both collateral owner/ref rewrites call overlay
update_entity, so the new RuntimeError-based copy refusal would indeed escape.

**Correction:** Task 3 explicitly includes GroupIdentityConflict in the existing
best-effort post-move collateral handling, preserves normal landed-move response
and revision stamping, leaves blocked referring copies unchanged and allows
independent sweep legs. Route tests cover Owners and dormant-ref-field paths.
Strict original-record preflight and journal relocation begin/complete remain
outside those legs; their safety fences cannot be swallowed by this change.

**Second actual plan gate:** pending. No implementation has started.


## Second actual Codex plan gate

**Reviewed commit:** `c19c118913de3b73c10a937880176d1b7e79606b`.
**Thread:** `01a12cac-fc35-7351-98e3-37e699d3f2bf`.
**Verdict:** `needs-attention`.

The collateral-sweep correction closes the prior blocker, but failed relocation completion still lacks revision-stamp handling.

### Stamp landed moves when relocation completion fails

**Severity:** high.

Task 4 explicitly allows completion failure after the record moves, while Task 3 keeps completion outside best-effort catches. With the supplied exception flow, an atomic completion-write error would propagate as non-2xx after the move. The middleware stamps only 2xx, and no compensating revision bump is planned. This leaves the pre-move token usable for advance/fork confirmation against changed state. This conclusion follows from retaining the supplied exception behavior.

**Recommendation:** Specify failure-path revision stamping after durable lifecycle changes or a landed move, while preserving suspension. Add an injected completion-write failure regression asserting the landed destination, suspended offer, and changed campaign revision; cover affected campaigns in World-driven moves too.

**Verification:** Campaign write middleware stamps only successful responses.
Relocation completion can fail after the move, outside best-effort sweep catches.
Existing World follow error handling stamps only its own later loop, so an
introduced earlier failure also needs explicit affected-campaign stamping.

**Correction:** Task 4 tracks attempted lifecycle effects and bumps every possibly
changed held campaign on failure before release, including a landed move followed
by failed completion, partial durable suspension and copy-removal retirement.
World-driven failures include affected dependents even before the follow loop.
Strict preflight-only refusals do not stamp. Tests assert destination/suspension,
changed revision and refusal of the old revision for confirmation. Existing
best-effort revision writer/crash limits are preserved.

**Third actual plan gate:** pending. No implementation has started.


## Third actual Codex plan gate

**Reviewed commit:** `b989c27b90ccd7baa7cda73be8ac1e2b9d81d3af`.
**Thread:** `01a12cb1-be86-7bb3-977f-5d40a831138c`.
**Verdict:** `needs-attention`.

Block the plan gate: the new membership Undo path can change Members on an error without invalidating the campaign revision.

### Cover post-write Undo failures in the revision contract

**Severity:** high.

The failure contract covers reclassification and copy removal but omits group_members Undo/Redo. Existing undo.undo restores the record at undo.py:503, then calls fallible journal.append and mark_undone at lines 522–523. If either fails, the restored Members remain durable while the error response receives no middleware stamp. A pre-Undo campaign revision can therefore still pass revision.require against changed campaign state.

**Recommendation:** For the new group_members writer, bump the revision inside undo.undo's existing campaign hold before propagating any journaling failure after restoration lands. Add injected append and mark_undone failures asserting changed Members, an error response, a changed revision, and rejection of the old token.

**Verification:** Existing undo.undo writes the restored record before journal
append/mark_undone. Either fallible journal step can leave restored Members
behind while a non-2xx response skips middleware revision stamping.

**Correction:** Task 4 extends the same failure contract to S1 group PUT/copy
and group_members Undo/Redo. Inside the existing campaign hold, stamp after
possible restoration/publication effects before propagating later errors.
Parameterized append/mark_undone and Undo/Redo tests check durable restored
Members, error response, changed revision, old-token refusal and truthful partial
journal state. A post-publication PUT/base failure is covered too; preflight-only
refusals and the existing multi-file crash limits remain unchanged.

**Fourth actual plan gate:** pending. No implementation has started.


## Fourth actual Codex plan gate - approved

**Reviewed commit:** `51f4cc9500f737d3a72b6eb86504fbb3b9c807da`.
**Thread:** `01a12cb7-3bf7-79d2-8355-308cceed7f64`.
**Verdict:** `approve`.

Approve the 07-S1 plan gate at 51f4cc9500f737d3a72b6eb86504fbb3b9c807da. No grounded material blockers remain in the supplied S1 plan; implementation is unreviewed.

No findings. Complete the human plan review and choose Native or Subagent-driven
before implementation, as required by the writing-plans skill.

**Document checks:** git diff --check; roadmap_slices.py check (128 slices, 282
edges); five task headings, balanced fences and valid Python assertion/signature
blocks; referenced paths exist or are declared new files. These are planning
checks, not validation of the unimplemented feature.
