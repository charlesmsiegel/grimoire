# Test consolidation ledger — test-suite acceleration, phases D and E

Spec: `docs/superpowers/specs/2026-10-08-test-suite-acceleration-design.md`
(§6, §7). Plan: `docs/superpowers/plans/2026-10-08-test-suite-acceleration.md`.
Source snapshot: production code of `a219190`, unchanged by these phases.

Every candidate the spec named is accounted for below, merged or kept, with
the reason. A merge was made only where the old tests built the **same state**
and ran the **same action** (byte-identical arguments, every check read at the
same point), and it keeps every old assertion as a named field of one
comparison, so a failure still reports every field that went wrong rather than
the first.

## Evidence, and how it was gathered

- **Arcs.** For each group, the old tests and then the new one were run three
  times under `--cov-context=test`; for each run the arcs of the group's
  contexts were unioned, and the three runs intersected (the stable set, spec
  finding B1). `lost` is what the old stable set had and the new one does not.
- **Mutation probes.** Each merged assertion was attacked by one edit to the
  production code it guards, applied in a separate git worktree and reverted
  after, never committed. A probe is *caught* when the merged test goes red.
- **Seconds** come from the measurement campaign in
  `2026-10-08-test-performance-report.md`, not from these targeted runs.

| Group | Old → new | Verdict | Arcs lost | Mutants caught |
|---|---|---|---|---|
| C1 continuity reconcile, network failure | 3 → 1 | merged | 4 (see note) | 7 / 7 |
| C2 continuity reconcile, undecodable reply | 2 → 1 | merged | 3 (see note) | 8 / 8 |
| C3 reroll model attribution | 2 → 1 | merged | 0 | 3 / 3 |
| C4 director note and its accounting | 2 → 1 | merged | 0 | 6 / 7 (one equivalent) |
| C5 empty campaign's optional sections | 4 → 1 | merged | 0 | 3 / 3 |
| C6 inference baseline, per-state builds | — | kept | — | — |
| C7 absorb non-list sections | 75 cases → 5 | batched | 0 (92 gained) | 3 / 3 |
| C8 commitment title normalisation | — | kept | — | — |
| C9 sheet / card rejection tables | — | kept | — | — |

C1 and C2 count a probe against both merged tests once per test.

**The arcs C1 and C2 "lost"** are in `main._scheduled_backup`,
`config.backup_enabled` and `config.read_config`'s first-reader race branch.
Each is reached by the scheduled-backup check that every app's lifespan starts
on a worker thread, racing the test, and that shutdown may cancel before it
runs. With three app start-ups per run the old group gave that race three
chances; the merged test gives it one. The test's own action never reaches
those lines, and the suite's stable arc set (the report) keeps them.

## The machine-generated inventory (spec §6, steps 1–3)

Beyond the spec's own candidates, every test module was walked as an AST and
its test functions grouped by (fixture parameters, the first four calls the test
makes to the app, as method and path skeleton -- `PUT
/api/campaigns/{}/scenes/{}/chronicle`); parametrized tests are already one
function and were left out. Each group's members were joined to their seconds in
a whole-suite phase profile (four workers, head `08fb85d`, route memo on), and
ranked by the most a merge could save -- every execution but the slowest.

225 groups of two or more tests share fixtures and leading calls. Across all of
them that upper bound is **77 s of the profile's 1121 node-seconds** (about 7 %,
or roughly 20 s of wall time across four workers), and it is an upper bound only:
the top groups (27 group-play turns, 23 chronicle saves, 25 ledger reads) post the
same route with different payloads, casts and stored state to test different
outcomes -- spec class D, separate scenarios, not one action checked several
ways. Measured against what phase F's route memo removed (the per-app route
analysis that had been most of every route test), further consolidation buys
little and costs the per-test diagnostics the spec's safeguards protect, so it
stops here. The inventory script is a one-off and is not committed; its method
is the paragraph above.

## Merged

### C1 — `test_continuity_reconcile_routes.py`, a failed model call

| Old node | New node, field |
|---|---|
| `::test_an_llm_failure_keeps_deterministic_candidates` | same name; `state`, `error.kind`, `result.llm`, `duplicate cached`, `duplicate proposal` |
| `::test_a_failed_run_carries_its_sweep_in_the_error` | → `::test_an_llm_failure_keeps_deterministic_candidates`, `error.sweep` |
| `::test_a_failed_model_call_says_the_findings_were_saved` | → same, `error.saved is True` |

Probes: a failed call reported as landed; `run_failed` for `network`;
`result.llm` left `off`; the discovered candidates not carried into the cache;
a proposal written by `_record`; `error.sweep` dropped by `_failed`;
`progress["saved"]` never set. All red.

### C2 — same file, an undecodable reply

| Old node | New node, field |
|---|---|
| `::test_an_undecodable_reply_fails_the_run_and_keeps_candidates` | same name; adds `error.saved is True` |
| `::test_an_undecodable_reply_says_the_findings_were_saved` | → same, `error.kind`, `error.saved is True` |

Kept apart from C1: the two failures leave `_adjudicate` by different branches.
Probes: an undecodable reply parsed as an empty answer; `network` for
`undecodable`; status 500 for 502; `result.llm` left `off`; plus the three
shared with C1. All red.

### C3 — `test_routes.py`, a reroll sent to another connection

| Old node | New node, field |
|---|---|
| `::test_a_rerolls_snapshot_names_the_model_the_reroll_was_sent_to` | → `::test_a_rerolls_snapshot_and_cost_name_the_model_it_ran_on`, `snapshot model` |
| `::test_a_rerolls_cost_is_billed_to_the_model_it_ran_on` | → same, `billed model` |

The new test also re-reads each surface after the other, so a read that wrote
would show. Probes: the prompt snapshot recorded with no model; the meter
stamped from the campaign's model; the usage projection naming the connection.
All red.

Pre-existing, recorded rather than changed: the cost half's docstring credits
`llm._stamp`, but `FakeOpenRouter` replaces the real facade and stamps the
usage model itself, so an edit to `llm._stamp` was never caught by it and is
not caught now. The merged docstring says so.

Kept apart: `::test_the_alternate_a_reroll_produces_is_stamped_with_the_model_that_ran`
(a different action — it adds `guidance` — and a different record, #77).

### C4 — `test_routes.py`, a typed director note (#83)

| Old node | New node, field |
|---|---|
| `::test_director_note_in_a_normal_scene_is_recorded_but_is_not_a_post` | same name; `final sent message`, `times the note was sent`, `stored notes`, `note speaker is synthetic`, `user-role messages` |
| `::test_a_director_turn_is_charged_to_its_own_note` | → same, `charged posts` |

Reads stay in the old order (sent prompt, transcript, ledger). Probes: the
template's wording sent instead of the note; no note stored; the stored text
altered; `DIRECTOR_SPEAKER` dropped from `SYNTHETIC_SPEAKERS`; the meter's
post index skipping notes; the director gate charging nothing. All red.

The seventh probe deleted `_project_history`'s director-note skip, and the old
tests and the merged one both stayed green: `scenes.in_context` has already
dropped the note by then, so that mutant is **equivalent**, not a gap. With
both layers removed the merged test goes red (`times the note was sent: 2`).
What no test asked was whether a stored note stays out of the prompts *after*
the turn that stored it; `test_context.py::test_a_stored_director_note_never_reaches_a_later_prompt`
now does, and goes red with both layers removed.

Kept apart: `::test_a_director_turn_with_no_note_is_charged_to_nothing` (the
negative half of the pair) and
`test_usage_routes.py::test_a_director_turn_is_charged_to_the_scene_and_to_no_post`
(a different producer — the shipped `character_turns` path — and a different
action). That second test's docstring predates #83 and is stale; noted, not
changed here.

### C5 — `test_context.py`, an empty campaign

| Old node | New node |
|---|---|
| `::test_story_so_far_absent_when_empty` | → `::test_an_empty_campaign_emits_none_of_the_optional_ledger_sections` |
| `::test_plot_threads_absent_when_none` | → same |
| `::test_character_state_absent_when_none` | → same |
| `::test_relationships_absent_when_none` | → same |

One assertion names every optional section that leaked. Probes: each of the
Story so far, Plot threads and Relationships templates emitting its heading
with nothing under it. All red. The garbled-file and populated-section tests
stay apart: they reach different code.

### C7 — `test_absorb_store.py::test_parse_output_treats_a_non_list_section_as_empty`

75 cases (15 list sections × 5 bad values) → 5 (one per bad value). Each case
splits the sections in two and runs twice, each half carrying the bad value in
turn while the other half carries one well-formed row, so every section is
checked both broken (it comes back empty) and beside a broken neighbour (it
keeps its row). The section list is still derived from `parse_output("{}")`.
The old cases never checked the second half; the 92 gained arcs are it.
Probes: `_rows` wrapping a scalar; `_rows` iterating a string; one bad section
emptying all. All red. This saves pytest overhead per case, not work, and is
reported as such.

## Kept, with the reason

- **C6** (`test_inference_resolve.py`, five tests over eight states). Each
  state is built over HTTP, and most of a build was FastAPI analysing the app's
  routes — the cost phase F's route-graph memo removes for every route test.
  Merging the five tests into one per state would also change the order in
  which they see the store, and `inf.resolve` is not read-only (it migrates a
  `pre_connections` store, spec finding S1). The frozen
  `inference_baseline.json` is untouched.
- **C8** (commitment titles). The two tests cost about 20 ms each; the
  normalised-title case is its own regression. Below the noise.
- **C9** (sheet and card rejection tables). The two files take 2–4 s in all.
  Parametrising them is maintenance, not speed (spec §6), and batching
  rejections into one store needs a proof each is non-mutating; not worth it
  for the time involved.
- Not candidates, per spec §7: retry vs regenerate, `test_eval_graders.py` vs
  `test_evals.py`, the AST guards and their negative tests, the
  timeout/stream/lock tests, and every frozen fixture and golden.

## Citation check (spec finding S2)

None of the removed names is cited by `test_capstone_acceptance.py` (the
capstone spec's Appendix B) or by `test_docs_guard.py`'s test lists. Six old
plans quote removed names in their task text (the continuity capstone's slice D
plan, and five scene plans of 2026-06-30 and 2026-07-01); a plan is a record of
what was planned, so they are left as written, and the merged tests' docstrings
name every test they absorbed so a reader following an old plan finds where it
went.
