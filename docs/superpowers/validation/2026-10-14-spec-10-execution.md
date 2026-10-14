# Spec 10 execution record

Branch `claude/magical-thompson-l28sua`, first built on `main` at `57abb2c`
and rebased onto `674a25b` (11's merge, #511), as one commit.
Spec: `docs/superpowers/specs/2026-10-09-roadmap-10-retrieval-query-planning-design.md`.
Plan: `docs/superpowers/plans/2026-10-14-roadmap-10.md`.

The originating request asked for Spec 10 to be implemented. All three slices
are in one PR. The plan gate, review and final gate are owed to the PR's Codex
review; the checklist leaves those boxes unticked until a round comes back
clean. No private store content was read or written: every fixture and eval
recipe is synthetic and uses the codebase's placeholder names.

## Rulings

Where the spec and the landed code disagreed, or the spec left a choice open,
the call made, why, and what it costs if wrong:

- **The sentinel already existed.** 02-S2 landed `routing.NO_LEGACY` (as a
  string constant, compared with `==`) and `NO_LEGACY_TASKS`, so 10 adds no
  sentinel and no baseline mechanism; the new route uses them, and the
  frozen baselines leave its task out through the derived set. Cost if
  wrong: none beyond the spec's wording.
- **History threads, not the shared pool.** The spec says
  `run_in_threadpool` for resolution; 09 resolves on `history_threads.run`
  because a `def` turn route blocks a shared-pool worker in `gather_sync`.
  The planner and the check follow 09. Cost if wrong: none — the call still
  never reads config on the loop (a test spies the thread).
- **One rerank, reapplied rather than re-asked.** The first retrieval still
  pays 09's rerank (so `E0` is exactly what 09 alone sends), and every merge
  reapplies that opinion to the merged order (`_reapplied`): graded scenes
  sort among the slots graded scenes hold, and a scene only a planned round
  found is ungraded and keeps its RRF slot. A planned turn therefore pays one
  rerank, as the spec bounds it. Cost if wrong: a planned scene is never
  graded; grading the final merge instead would need retrieval to defer its
  rerank on thin coverage.
- **A public expander for merges.** `history.merge` needs an expansion
  callback and none was public. `retrieval.expander` builds 09's own
  `_expander` lazily (catalog and eligible documents only, no second scoring
  pass), anchored on the union of the turn's and the plan's terms. Cost if
  wrong: one extra catalog read on the rare merge that selects a scene
  neither round expanded.
- **Round records are the merged evidence after each round.** 8.1 says each
  record carries "the scene keys and evidence ids" and section 14 says they
  match the merged evidence; consecutive records then say exactly what a
  round added, and the last record equals the returned selection. Cost if
  wrong: 12 would diff records rather than read a round's raw selection.
- **Forced planning still never plans on `error`.** The flow lets `force`
  pass the coverage gate; retrieval that failed outright is not planned on
  even when forced (nothing it found can be merged). `exhausted` may be
  forced, as the flow says.
- **Escalation stays off in the policy row.** The spec recommends
  `abstained`/`refused` to Primary; the repo rule is that no task escalates
  without its evidence (`evals/README.md`), and `test_operation_guard.py`
  checks statically that a call site passes `escalation=` exactly when its
  policy escalates, so the hop cannot be wired conditionally. `verdict_of`
  already maps an answer only an escalation hop gave to `escalated`
  (unit-tested on a hand-built `Decision`), so switching it on is the policy
  row, the `escalation=` thunk and its evidence. Cost if wrong: a non-answer
  is `unknown` (the spec's own pre-01d behaviour) until then.
- **No budget skip (open question 3).** The only maintained aggregate is the
  shell's all-time rollup; a budget is per period, and reading the period
  would be a ledger scan, which the spec rules out. `skipped:budget` is not
  emitted. Cost if wrong: an over-budget campaign can still spend a planning
  call, which budgets only warn about today.
- **The planner's request is not captured** (open question 6, as
  recommended); the check is captured through 01b. The plan and its trace
  ride the inspector row.
- **A declined repair plan is `repair_redundant`.** It adds nothing, which
  is that state's meaning; `unrepaired` stays for unusable, timed out or cut.
- **Templates.** `templates/history_plan/` as specified, and
  `templates/history_check/` for the check (the spec's name, after the
  route), beside 09's `history_rerank/`.
- **The regex guard gains a "viewed input" pin.** The planner's and the
  check's builders render prompts from text already taken through the view
  (09's window, 08's excerpts); they are pinned by name and fail if they ever
  read a scene themselves.
- **Offline planning evals run structural and lexical retrieval.** 09's
  synthetic vectors are recorded for 09's corpus; a planned question has no
  recorded direction, and inventing one would grade a vector, not a plan.
  Live arms use the real Embedding role. The recorded provider reports no
  tokens, so tokens per turn print `n/a`, never `0`.
- **Recordings live under `evals/recordings/history/`** beside 09's vector
  asset, as data the planning suite reads, not as registered decide cases
  (which `test_no_orphan_recordings` holds the top-level directory to). The
  sufficiency check's decide case is registered there as usual.

- **Planning runs on the narrator's step only (after the rebase onto 11).**
  11 retrieves from each NPC's own perspective. The spec keeps actor-scoped
  planning out of 10 (section 15), and the planner is built from the
  narrator's seeds, so `_gather` plans only when the step has no actor --
  the same rule 11 applies to the rerank -- and `plan.build_input` still
  refuses any perspective but the narrator's. Cost if wrong: an NPC's thin
  recall is not planned until a later change narrows the planner's input to
  what 11-C1 says that actor knows.
- **Codex review, round one** (all five findings fixed): the perspective is
  carried into the planner's input and reader, planner rows carry the run
  id, no planned or repair round starts past the deadline, cast-related
  threads come first under `MAX_THREADS`, and the eval arms aggregate the
  three money columns apart.
- **Codex review, rounds two to five** (all fixed): embed and retrieval
  counts include planned rounds; the merged coverage keeps the rerank status;
  the trace rides a frozen view; a failed check files its row; offline evals
  retrieve at depth 5; a retrieval that raises still traces `not_run:error`
  when planning was on; a record with no name is never offered by its id; a
  merge that comes back empty keeps the evidence it had; and the default gate
  takes an optional per-planned-turn cost bound (`--plan-cost-bound`), checked
  against each money column apart; and a failure past the free gate keeps
  the steps, plans and rounds that ran, and the evidence the last finished
  round left (round five).

## Verification

- `make check-py` (subset run during the work: history, routing, config,
  guards, inference baselines, context, evals — all green), the lint, mypy
  and eslint ratchets at baseline, `verify_templates.py` (545 checks),
  `make check-web`'s typecheck and the two touched frontend suites.
- `evals/run.py --history --plan` offline: the offline gate passes; the
  reported default gate is met on the recorded replies, which proves
  mechanics only. `history_plan` stays `off` until a live run, compared in
  conversation, justifies a default.
