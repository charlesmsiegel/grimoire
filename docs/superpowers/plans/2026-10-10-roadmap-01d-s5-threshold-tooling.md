# 01d-S5: Threshold tooling in evals — Implementation Plan

Review gates: not run (speed mode); the Codex gates are owed.

**Goal:** the eval-side half of 01d-C3 (full): `evals/run.py
--escalation-sweep`, `--escalation POLICY_JSON`, `evals/runner.escalator`
and the `evals/README.md` "Decision escalation" section with spec §6.3's
procedure and bar. It enables no task: `TASK_POLICY` stays empty, and a
task's thresholds and cases land with its switching change.

## Design

1. **The offline sweep** (`evals/escalation.py`, `sweep`). For each
   `--case` (a decide case on a decide route), every native recording is
   read through its adapter's `decision_result` (the production mapping,
   as `runner.native_output` reads it), each item stamped `backend:
   native` and `served = (kind, "", model)` with the recording's adapter
   KIND (`NATIVE_KINDS`: `openai` → `openai_compatible`), then for each
   threshold of `GRID` (0.05 to 0.5) `decisions.triggers` is asked with
   `margins={kind: threshold}`. **It counts through `triggers`, never a
   bare `margin < m`** (checklist erratum 01d-S2, `MASS_TIE`), so a partial
   report of exactly 0.6 does not escalate at 0.20. The deciding question,
   triggers and answer filter come from `--escalation` when given, else
   the task's own policy question (else the case's first question) with
   every trigger. A case with no native recording says so (a structured
   reply reports no margin). Golden over a fixed recording: a new
   `decide-continuity-identity.native-margins` (OpenAI choice
   distributions, the compliant verdicts, must-pass), so the golden shows
   thresholds moving and the boundary.
2. **The policy override** (`escalation.parse`). A JSON object of the
   escalation fields only (`escalate_to`, `escalate_on`, `question`,
   `margins` as `{kind: m}`, `escalate_max`, `escalate_answers`,
   `reads_declines`), refused (`PolicyError`, argparse exit 2) for a task
   off a decide route and on every `test_task_policy.py` rule a JSON value
   can break: a role in `ESCALATION_ROLES` (never `CALLER`: an eval has no
   resolver) other than the route's default; known triggers and a
   question; margins per native kind in `(0, MAX_MARGIN]`, required beside
   `low_margin`; an answer filter only beside `low_margin`;
   `1 <= escalate_max <= 8`; and `decisions.triggers((), ...)` dry-run.
   The pinned-question and per-key answer checks stay the test's (they
   read store constants per task); `decide` refuses an item lacking the
   question before any meter anyway. `runner.overriding(task, policy)`
   patches `routing.TASK_POLICY` around the call and restores it.
3. **A live run under a policy.** `--escalation` needs `--live` (or the
   sweep) and `--case`, refuses a forced `--decide-backend` and
   `--record`, and joins the config's axes (compacted), so `--out` both
   runs and `--compare` reads them as two configs. A decide case is then
   answered by `inference.decide(..., escalation=runner.escalator(...))`
   under `overriding` -- the app's own hop -- rather than `run_stages`;
   its hop rows carry `hop: escalation`, which 01a's aggregate, call
   records and `item_records` already split out.
4. **`runner.escalator(task, policy, resolved, *, real_home)`.**
   *Deviation from spec §6.3 (and the S5 acceptance wording), decided
   here:* the escalation role is resolved in the REAL store, before any
   isolate (`runner.escalation_roles`, through
   `store.inference.resolve.resolve(..., role=)` and
   `resolve.escalation_refusal`, never `routes.common`), because the
   throwaway home holds no settings and a role resolved there resolves to
   nothing -- the same reason `resolve_connections` already reads each
   case's settings there. What the spec's tripwire protects is the hop's
   ledger row, so the THUNK, awaited inside the case's isolate, refuses
   (`(None, ISOLATE_ERROR)`: every trigger skipped, nothing sent) when the
   active home is the real one. A role that cannot serve the hop refuses
   the run up front with the seam's sentence (exit 1).

## Open questions

§11 Q1 (raw margins for folded options) adopted: the sweep reads raw
`decisions.margin`. Q5: no speaker escalation, nothing enabled.

## Files

- `evals/escalation.py` (new), `evals/runner.py`, `evals/run.py`,
  `evals/cases.py`, `evals/recordings/decide-continuity-identity.native-margins.json`,
  `evals/README.md`.
- `backend/tests/test_eval_escalation.py` (new), `backend/tests/test_evals.py`.

## Tests

The sweep's golden output (and the CLI's, which prints the same); the
filter narrowing it; a case with no native recording; the grid and kinds;
`--escalation` refused off a decide route (parse and CLI) and per rule;
flag refusals; `overriding` restores; `escalator` hands its resolution over
inside an isolate and returns `ISOLATE_ERROR` in the real home;
`escalation_roles` refuses a keyless role with the seam's sentence; a live
decide case under a policy files one `hop: escalation` row and marks its
item escalated, and without one escalates nothing.
