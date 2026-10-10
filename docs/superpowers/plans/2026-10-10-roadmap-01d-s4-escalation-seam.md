# 01d-S4: The escalation seam in `routes/` — Implementation Plan

Review gates: not run (speed mode); the Codex gates are owed.

**Goal:** the one door a decide call site resolves its escalation hop
through, `routes.common.escalation_inference(task, cid) ->
(UsableInference | None, sentence)`, and the two guards that hold call sites
to it. Delivers 01d-C2b (full: the seam over `_soft_resolved`, the role's
own preset, the base route's `requires`, the route-named sentence) and
01d-C1 (full: the static half of "a call site and its policy agree").
No call site uses the seam and `TASK_POLICY` stays empty, so nothing
escalates in production; each task's switching change (02, 10, 11, 12) adds
its own call.

## Design

1. **The seam** (`routes/common.py`). `escalation_inference(task, cid="")`
   reads `routing.policy(task)`; a task whose `escalate_to` is not a role in
   `ESCALATION_ROLES` (no escalation, or `CALLER`, which has no role to
   resolve) is a `ValueError` -- a caller bug the routing guard also catches
   statically. Otherwise it resolves `inference.resolve(task, cid,
   operation="decide", role=policy.escalate_to)  # routing-ok:` and hands the
   thunk to the existing `_soft_resolved`, so a refusal is `(None,
   sentence)`, never a 409 and never a third soft pattern. The call site
   builds the `inference.Escalator` as
   `lambda: run_in_threadpool(lambda: common.escalation_inference("<task>", cid))`.
2. **What a `role=` resolution runs with.** `resolve(..., role=)` has no
   route, so `cascade.preset_for(None, selection)` already gives the role's
   OWN preset (scope `connection`) and never the decide route's -- nothing to
   build, only a test to hold it. Its `missing` covers only the operation's
   needs, so the seam holds the hop to the BASE route's: a new pure
   `store.inference.resolve.escalation_refusal(resolved, task)` --
   `unusable` first (the missing-key sentence, naming the role), then
   `_missing(primary, _needs(route, "decide"))` over the base route; a known
   gap is 409 `incapable` with `escalation_text`'s sentence, which names the
   base route (`routing.label_for`), the role and the model ("The Scene
   breaks route escalates to the Primary role (m on P), which cannot ... —
   choose another Primary model."). Not `incapable_text`: its remedy offers
   "pin this route", which would not move the hop.
3. **Skips reach the capture** (S3 handoff; checklist erratum).
   `decision_capture.Scope.note_escalations(part, decision)` notes one row
   per `Decision.escalations` entry -- index, trigger, margin, outcome,
   `served`, and `detail` cut at its first `": "`, so a failed hop's provider
   text (`"rate_limit: <provider words>"`, `"unreadable: <detail>"`) is never
   persisted. Call sites call it after `decide` returns; none does yet.
4. **Routing guard** (`test_routing_guard.py`). `escalation_inference` is a
   seam: only ever called (a bare reference fails, as for
   `require_inference`), its first argument a literal task a route claims
   whose policy escalates to a ROLE, its second argument `cid` and a
   decorated handler passing it mounted under `/campaigns/{cid}`, its task
   literal counted in the task inventory, one definition.
   `RESOLVER_CALL_CAP` 5 → 6 with the reason. Planted violations for each.
5. **Operation guard** (`test_operation_guard.py`). `decide_problems` gains
   `policy_of` (default `routing.policy`): an `operations.decide` call
   passes `escalation=` exactly when its task's policy escalates. Planted
   both ways under a synthetic policy.
6. **Docs.** CLAUDE.md's escalation sentence (landed in S3) names the seam
   and the guard; CONTRIBUTING's guard row gains the seam.

## Open questions

§11 Q5 adopted: speaker escalation stays off. Q3 (show escalation in
Settings) stays open: no task escalates, so there is nothing to show.

## Files

- `backend/src/grimoire/store/inference/resolve.py`: `escalation_refusal`,
  `escalation_text`.
- `backend/src/grimoire/routes/common.py`: `escalation_inference`.
- `backend/src/grimoire/routes/decision_capture.py`: `Scope.note_escalations`.
- `backend/tests/test_escalation_seam.py` (new).
- `backend/tests/test_routing_guard.py`, `backend/tests/test_operation_guard.py`.
- `CLAUDE.md`, `CONTRIBUTING.md`.

## Tests (`test_escalation_seam.py`)

- A missing key on the escalation role: `(None, sentence)`; through `decide`
  every candidate is `skipped` with that sentence and no hop call is made.
- A hop primary known to lack the base route's `requires` (a synthetic
  route requirement patched in): `(None, sentence)` with `kind` `incapable`,
  naming the base route; a primary that can neither generate nor decide is
  refused the same way, naming the route.
- The role's own preset is sent: a route preset and a different Primary
  preset; the hop target's `preset_id` is the Primary's.
- A usable resolution drives a real hop end to end through `decide`.
- A task whose policy does not escalate (or escalates to `CALLER`) is a
  `ValueError`.
- `note_escalations` keeps only the detail before `": "`, and notes nothing
  with capture off.
