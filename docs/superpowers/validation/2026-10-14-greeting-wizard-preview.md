# Issue 68: greeting preview validation

The wizard's Character step previews the world's greeting availability using
the proposed player tags and empty play history. Existing PCs supply their own
tags; deselecting one restores the draft's tags. Available and blocked openings
are visible before any campaign, PC, or scene is created.

## Checks

- Baseline: 90 backend greeting/play tests and 20 wizard tests passed.
- RED: the new world preview returned 404; the client method was absent; four
  wizard regressions failed before implementation.
- GREEN: 101 focused backend tests, 172 API client tests, and 31 preview/wizard
  tests passed. `npx tsc -b` passed.
- All 11 new backend preview regressions also passed under coverage and under
  the pydantic-1 dependency environment.
- The full frontend gate passed: 242 test files, 5,253 tests.
- Ruff, mypy, and ESLint remained at their existing baselines. All 551 template
  checks passed. No lint baseline changes were needed.
- The full pydantic-1 backend run completed: 21,944 passed, 48 skipped,
  9 failed, and 10 setup errors. Its dependency install and `pip check` passed.
  Failures are confined to the unchanged history/investigation tests listed
  below.
- `make check` passed its frontend, lint, and template stages. Its main backend
  coverage run stalled at 92% after running for more than two hours. After
  over 30 minutes without new results, all four workers showed zero CPU
  progress during a ten-second sample. The run was interrupted and its process
  tree was confirmed stopped. It had emitted failure markers but no final
  traceback, totals, or coverage result; those failures cannot be attributed
  from this incomplete run. This local run did not produce a green full gate.
- Final `git diff --check` passed. The worktree contains only the preview
  implementation, regression tests, and its spec/plan/validation documents.
- A real Chrome browser used a fresh isolated library and shared LLM fake.
  Selecting Mara unlocked the matching opening while the successor remained
  blocked. Previewing created no campaign. Desktop (1100 × 1000) and mobile
  (390 × 844) screenshots were inspected; there were no page errors or mobile
  horizontal overflow. Both temporary verification servers were stopped.

## Review gates and decisions

- Spec review found a route collision: `/greetings/availability` would hide a
  greeting whose ID is `availability`. The endpoint now lives outside that
  namespace at `/greeting-availability`, with a regression preserving that
  greeting's detail read. The revised spec and implementation plan each
  received Codex approval.
- A secondary spec review questioned CSV tags. `pcs._write_meta` joins tags
  with commas and `pcs._tags_of` splits/strips them on read; the preview mirrors
  that existing storage behavior. A regression compares the preview with the
  subsequently created PC's actual campaign availability. Changing storage to
  preserve commas inside an atomic tag would be separate work.
- The native Codex diff reviewer could not launch its Windows sandbox helper.
  An independent Codex reviewer read all tracked and untracked changes and
  approved them with no correctness findings. A final source-supplied Codex
  adversarial review received every tracked diff and new file, compared them
  with the originating spec, and approved with no material findings.
- No deferred review findings. Android device/APK behavior was not exercised;
  the repository's pydantic-1 gate is the dependency compatibility check.

The implementation reuses the existing pure availability engine; TypeScript
owns request lifetime and display only. Each settled choice makes one GET
after 250 ms. Cleanup discards late results, and a world/tag key immediately
clears obsolete preview rows. The world PC and vocabulary reads are fenced
against the same old-world race. No storage format or tag namespace changed.

## Resumed verification on 2026-10-15

The same 101 focused backend tests passed again under coverage in 29.92 seconds
using the requested main-checkout Python 3.14.5 environment and this worktree's
`backend/src`. Product source is unchanged from the approved reviews.

The saved full-run timeout stack includes a Windows asyncio event-loop
constructor blocked in `socket._fallback_socketpair`'s `accept`. That establishes
one stalled worker's location, rather than attributing every stalled worker or
failure marker to a single cause. The Windows results above are retained;
the review branch's Linux CI checks supply the full-gate result separately.

## Existing Windows gate blockers

A source copy exported from the unchanged base commit
`92a55a50dd8360d8a46eb50de590e50296d7c7f3` reproduced these failures/errors using
the same Windows Python 3.14 / pydantic-1 environment. The imported `grimoire`
module's path was checked to ensure the copy, rather than this worktree's
editable installation, supplied the code.

- `test_history_evals.py::test_real_corpus_gold_and_all_offline_arms`:
  `WinError 32` deleting an open compiled SQLite cache during temporary-home
  cleanup.
- `test_history_plan_evals.py::test_offline_arms_report_recall_gain_and_quiet_controls`:
  the same compiled-cache cleanup failure.
- `test_history_recall_rerank.py::test_timeout_obeys_remaining_phase_and_preserves_file_evidence`:
  the phase reads `skipped:deadline`, rather than the asserted `skipped:timeout`.
- `test_investigation_evals.py::test_every_built_entry_point_runs_both_arms_over_the_corpus`:
  setup fails while deleting an open compiled SQLite cache in the eval's
  temporary home.
- `test_history_semantic.py::test_a_stalled_endpoint_costs_one_phase_per_turn_and_keeps_file_evidence`:
  the phase reads `skipped:deadline`, rather than the asserted `failed:timeout`.

The selected base checks produced four failures, one setup error, and one
passing test. The additional 0.6-second planning-deadline assertion passed in
that isolated comparison but failed in the loaded compatibility run. These tests and
their history/eval implementations are unchanged by issue 68. No unrelated
history, cache-lifetime, or timing changes are included in this feature.

### Full pydantic-1 failure list

All paths below are relative to `backend/tests/`. The history planning-route,
recall-rerank, and semantic assertions have the deadline mismatch described
above. The remaining failures/errors arise from temporary-home cleanup trying
to delete an open compiled SQLite cache on Windows.

```text
FAILED test_history_evals.py::test_real_corpus_gold_and_all_offline_arms
FAILED test_history_plan_evals.py::test_offline_arms_report_recall_gain_and_quiet_controls
FAILED test_history_plan_routes.py::test_a_late_step_is_bounded_by_what_is_left
FAILED test_history_recall_rerank.py::test_timeout_obeys_remaining_phase_and_preserves_file_evidence
FAILED test_history_semantic.py::test_a_stalled_endpoint_costs_one_phase_per_turn_and_keeps_file_evidence
FAILED test_investigation_evals.py::test_the_hidden_route_probe_sees_hidden_text_that_reached_the_index
FAILED test_investigation_evals.py::test_the_investigation_arm_runs_as_the_entry_point_does
FAILED test_investigation_evals.py::test_an_arm_leaves_its_home_only_once_no_tool_thread_runs
FAILED test_investigation_evals.py::test_both_arms_search_with_the_embeddings_client_they_are_handed
ERROR test_investigation_evals.py::test_every_built_entry_point_runs_both_arms_over_the_corpus
ERROR test_investigation_evals.py::test_the_scripted_investigation_is_correct_and_the_offline_incumbent_abstains
ERROR test_investigation_evals.py::test_no_leakage_line_fires_and_the_hidden_route_is_clean
ERROR test_investigation_evals.py::test_the_gate_reads_per_entry_point_and_e1_is_absent
ERROR test_investigation_evals.py::test_every_case_runs_in_a_long_history_and_a_short_one_fails_the_gate
ERROR test_investigation_evals.py::test_a_run_that_came_back_failed_is_an_error
ERROR test_investigation_evals.py::test_an_incumbent_that_errored_is_no_baseline
ERROR test_investigation_evals.py::test_the_gate_fails_without_the_margin
ERROR test_investigation_evals.py::test_the_gate_refuses_a_run_that_did_not_cover_the_corpus
ERROR test_investigation_evals.py::test_each_case_refutes_the_inversions_its_claim_would_miss
```
