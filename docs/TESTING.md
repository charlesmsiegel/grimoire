# V2 testing, failure injection and the invariant catalog

V2 data is long-lived, local-first and human-editable, so the expensive failures are partial writes, mistaken identity, stale views, private-knowledge leaks and runs that cannot be recovered. This page lists the invariants that protect against those failures and the test that proves each one. It also describes the fault-injection harness and what it does **not** simulate.

Run everything with `python -m pytest backend/tests -q`. CI runs this on Python 3.11 on Ubuntu (`domain` job) and a `compat` matrix: Python 3.12 and 3.13 on Ubuntu, and 3.11 on macOS and Windows. Every job must pass; see [CI gate](#ci-gate). A green suite proves these contracts; it does not mean a playable app exists.

## Fault-injection harness (`grimoire.testing`)

The harness is test infrastructure that ships in the package. Production packages must never import it; [`test_invariant_catalog.py`](../backend/tests/test_invariant_catalog.py) enforces this. It has three parts.

**The `FileOps` seam.** A writer under test performs its file I/O through an object that has these methods:
- `read_bytes`, `exists`, `list_dir` (returns sorted names)
- `write_bytes` (writes, then fsyncs)
- `make_temp`, `replace` (atomic), `remove`, `make_dirs`

`OsFileOps` is the real, unconfined implementation. The seam is **provisional**: the native file-store sprint owns the production storage interface and may reshape it. Because the seam uses structural typing, production code never imports this package.

To add an operation, a consumer sprint declares three things:
- whether it mutates,
- its crash, torn-write and error semantics,
- its shadow-model effect.

Expected additions are `stat` or a content-hash read, exclusive create, and lock acquire/release.

**`RootedFileOps` confines, records, budgets and faults every phase.**
- *Confinement:* paths must stay inside one root, after realpath. Links and Windows reparse points inside the root are rejected (including `link/../x`), and so are mutations of the root itself.
- *Recording:* every operation is recorded as a `FileOp`. The descriptor format is fixed: `#<index> <kind> <path>[ -> <target>][ sha=<digest> len=<size>]`.
- *Deterministic temp names:* under the harness, `make_temp` returns the lowest free `<prefix>~tmpN<suffix>`. Bytes that embed temp names are therefore stable between runs.
- *Faults:* `Crash(at, before|after|torn)`, `Fail(at)` (a non-fatal `OSError`) and `Interleave(at, action)` (an external edit). A plan holds at most one `Crash`/`Fail` and any number of `Interleave`s. `at` is an op index or a `Target(kind, path, target, nth)`. Globs use `fnmatchcase`, so `*` also matches `/`.
- *Dead process:* after a crash, the wrapper refuses all further I/O. `finally` cleanup therefore cannot do anything a dead process could not.

**`explore_crashes` / `run_with_fault`.** All runs happen one after another at one fixed root. Each run:
1. wipes the root;
2. runs `setup`;
3. runs `action` under the plan;
4. runs the optional `meddle` (an edit made while the process is down; never in the baseline);
5. runs `recover` twice, each time with fresh `FileOps` (a "restarted process"; module-level state in the same interpreter is **not** reset, so writers must not keep in-process state across phases).

`explore_crashes` first runs a fault-free **baseline**. It then crashes each mutating operation `before` and `after`, `torn`-writes each non-empty write, and with `errors=True` injects `Fail`. With `recovery_crashes=True` it also crashes each mutating recovery operation `before` and `after`, and tears each non-empty recovery write (depth 2). A point is named by its Crash/Fail operation (or by the interleaved operation when the plan has no crash). Paths written by an `Interleave` or a `meddle` are excluded from `atomic_outcome` and `completes_after`, because that content is not the writer's. **Decision:** preserving a *meddled* edit is checked by the built-in `preserves_meddle`; preserving an *interleaved* edit (a lost update during a live race) needs a scenario-specific invariant, as in A9. A `preserves_interleave` built-in is left to the sync sprint (019), which owns concurrent-edit semantics. Exceptions, sandbox escapes and budget overruns inside an `Interleave` or `meddle` are fixture bugs and propagate raw, not as named violations. The harness refuses to wipe a pre-existing `<workdir>/root` that it did not create. Point names are unique; an illustrative depth-2 point is `action:#11 replace a.json.~tmp1.new -> a.json @after > recover:#7 remove .journal @before`. A failure is a `Violation(invariant, point, detail, trace)`, and `CrashReport.assert_ok()` lists every violation by name.

The harness provides no clocks, randomness or IDs. A consumer must inject seeded ones. `normalize` applies to trees only, so digest determinism stays strict.

### Harness invariants (always checked)

| Invariant | Meaning | Proven by |
|---|---|---|
| `baseline completes` | The fault-free action returns and mutates something. | `backend/tests/test_fault_harness.py::test_baseline_must_complete_and_mutate` |
| `planned fault fired` | Every planned action fault actually triggered, preventing that class of false pass (the recover-phase variant is exercised only indirectly). | `backend/tests/test_fault_harness.py::test_a8_a_fault_that_never_fires_fails` |
| `deterministic action` | The faulted trace prefix, write digests included, equals the baseline; recovery traces in depth-2 runs equal their depth-1 recovery. | `backend/tests/test_fault_harness.py::test_a4_exploration_is_repeatable_and_nondeterminism_is_named`, `backend/tests/test_fault_harness.py::test_a12_nondeterministic_recovery_is_named_at_a_recovery_point` |
| `simulated crash propagated` | The writer does not report success after it died. | `backend/tests/test_fault_harness.py::test_a6_dead_process_cleanup_does_nothing_and_swallowing_is_named` |
| `failure surfaced` | An injected I/O error is not swallowed. | `backend/tests/test_fault_harness.py::test_a11_injected_io_error_must_surface` |
| `bounded file operations` | A runaway loop of file operations fails by name, not by timeout. | `backend/tests/test_fault_harness.py::test_a7_runaway_writer_is_bounded_by_name` |
| `all mutations mediated` | The disk matches the shadow model built from recorded ops, so nothing bypassed the seam. An interleave applies only its own changes to the shadow, so it cannot absorb an earlier bypass. | `backend/tests/test_fault_harness.py::test_a13_writes_that_bypass_the_seam_are_named`, `backend/tests/test_fault_harness.py::test_a13_bypass_before_an_interleave_is_not_absorbed` |
| `recovery completes` | Recovery does not raise, including after it was itself interrupted or met a corrupt file. | `backend/tests/test_fault_harness.py::test_a12_interrupted_recovery_is_explored`, `backend/tests/test_fault_harness.py::test_a15_corrupt_journal_is_a_named_recovery_failure` |
| `recovery preserves completion` | Recovery after a successful, fault-free action leaves its result unchanged (after `normalize`), so a broken recovery cannot define the "completed" oracle. Use `normalize` for intended deferred cleanup. | `backend/tests/test_fault_harness.py::test_recovery_cannot_redefine_the_completed_state` |
| `sandbox` | Setup, action and recover phases cannot escape the test root (links are proven where the platform permits creating them; Windows runners may skip). | `backend/tests/test_fault_harness.py::test_a10_link_escape_is_a_sandbox_violation_in_any_phase` |

### Built-in scenario invariants

| Invariant | Meaning | Proven by |
|---|---|---|
| `atomic_outcome` | The recovered state equals either the pre-action or the completed state (paths touched by a meddle or interleave excluded). | `backend/tests/test_builtin_invariants.py::test_atomic_outcome`, `backend/tests/test_fault_harness.py::test_a3_naive_writer_violation_names_invariant_and_point` |
| `idempotent_recovery` | A second recovery changes nothing. | `backend/tests/test_builtin_invariants.py::test_idempotent_recovery` |
| `no_residue` | No staging or journal files remain. | `backend/tests/test_builtin_invariants.py::test_no_residue` |
| `completes_after` | Once the commit op has executed, recovery finishes the action. The commit is resolved in the faulted run's own trace. In a run with no crash or failure, a committed action that returned must keep its own result through recovery. | `backend/tests/test_builtin_invariants.py::test_completes_after`, `backend/tests/test_fault_harness.py::test_a12_always_rollback_recovery_breaks_commit_durability`, `backend/tests/test_fault_harness.py::test_commit_point_is_resolved_in_the_faulted_trace`, `backend/tests/test_fault_harness.py::test_interleave_only_runs_still_check_commit_durability` |
| `untouched` | Source paths, such as a source replica or a V1 library, never change. The glob matches entries only; `desktop/*` does not cover the `desktop` directory entry itself. | `backend/tests/test_builtin_invariants.py::test_untouched`, `backend/tests/test_fault_harness.py::test_a14_replica_copy_is_atomic_and_leaves_the_source_untouched` |
| `preserves_meddle` | An edit made while the process was down survives recovery. | `backend/tests/test_builtin_invariants.py::test_preserves_meddle`, `backend/tests/test_fault_harness.py::test_a14_blind_roll_forward_clobbers_an_edit_made_while_down` |

The proving scenario for this slice is a crash immediately after one file has been published, followed by recovery to the completed state: `backend/tests/test_fault_harness.py::test_a2_crash_after_one_publication_is_recovered`. Exhaustive crash, torn-write, I/O-error and recovery-crash coverage of the reference writer is in `backend/tests/test_fault_harness.py::test_a1_journaled_writer_survives_every_crash_point`.

## Provider fault invariants (`grimoire.testing.providers`)

`explore_provider_faults` drives the real `ProviderAdapter` and SSE parser through `ScriptedTransport` at the HTTP/SSE byte level. It enumerates every point **exhaustively and deterministically** (718 points). The `seed` only shuffles the run order and is recorded. The points are:
- every fault kind at every stream line, with and without a consumer;
- depth-2 sequences after a retryable first fault, including a denial on attempt 2;
- forced booking failures;
- `Retry-After` values.

Each point runs twice, and is bounded by a run count, sends and scripted output tokens. Each invariant has a mutant adapter or transport that the explorer must catch: `backend/tests/test_providers_faults.py::test_explorer_detects_each_invariant`.

| Invariant | Meaning | Proven by |
|---|---|---|
| `no unaccepted reply` | A reply is returned only from a booked `completed` attempt whose finish reason is `stop`. | `backend/tests/test_providers_faults.py::test_adapter_survives_every_fault_point`, `backend/tests/test_providers_adapter.py::test_f5_truncated_stream_never_accepted` |
| `every send booked` | Transport connects equal booked attempts, and sends equal booked attempts that were not `not_sent`. | `backend/tests/test_providers_faults.py::test_adapter_survives_every_fault_point`, `backend/tests/test_providers_adapter.py::test_f11_consumer_exception_and_keyboardinterrupt_are_booked` |
| `single acceptance` | Re-executing an accepted call connects nothing. | `backend/tests/test_providers_adapter.py::test_a4_completed_call_is_never_redone` |
| `bounded attempts` | Connects per call never exceed `max_attempts_per_call`. | `backend/tests/test_providers_faults.py::test_adapter_survives_every_fault_point` |
| `no retry after delivery` | Once text reached a consumer, a failure ends the call. | `backend/tests/test_providers_adapter.py::test_f4_timeouts_before_and_after_delivery` |
| `unknown is not zero` | A count the provider did not report is `None`, never `0`. | `backend/tests/test_providers_sse.py::test_bad_counts_are_none_not_zero` |
| `secret never leaks` | The key is absent from results, reports, errors (including formatted tracebacks with locals), ledgers and the audit. | `backend/tests/test_providers_adapter.py::test_secret_absent_from_results_reprs_audit_and_tracebacks`, `backend/tests/test_providers_transport.py::test_keyboardinterrupt_inside_send_leaks_no_key` |
| `deterministic` | Rerunning a point yields the same trace. | `backend/tests/test_providers_faults.py::test_explorer_is_deterministic` |
| `no resend after poisoning` | A call whose booking failed is never sent again. | `backend/tests/test_providers_meter.py::test_fallback_failure_poisons` |
| `bounded exploration` | Exploration stays within its run, send and token bounds. | `backend/tests/test_providers_faults.py::test_explorer_detects_each_invariant` |

## V2 domain invariants

| Invariant | Status |
|---|---|
| Same-name definitions from different sources never match | proven by `backend/tests/test_core.py::test_world_variants_cannot_be_matched_by_name` |
| One definition yields independent campaign instances and knowledge | proven by `backend/tests/test_core.py::test_same_definition_has_independent_campaign_instances_and_knowledge` |
| PC/NPC control is a role, not identity | proven by `backend/tests/test_core.py::test_control_changes_without_changing_actor_identity_or_history` |
| Actor views are immutable snapshots | proven by `backend/tests/test_core.py::test_actor_view_is_an_immutable_snapshot` |
| A private betrayal reaches only its witnesses: absent actors, participants and group members get nothing, and no actor view carries the event, its summary or a truth the actor was not told (a sender recalls what they said) | proven by `backend/tests/test_knowledge_scenarios.py::test_a_no_view_of_anyone_ever_carries_the_event_or_the_truth_unless_told`, `backend/tests/test_knowledge_scenarios.py::test_a_participation_and_group_membership_grant_nothing` |
| A believed lie is never canon: standing comes only from rulings, and receipt or belief never changes it | proven by `backend/tests/test_knowledge_scenarios.py::test_b_a_lie_is_believed_but_never_established`, `backend/tests/test_knowledge_scenarios.py::test_b_believing_never_changes_standing` |
| A corrected belief stays in the holder's history; only one belief per proposition is current | proven by `backend/tests/test_knowledge_scenarios.py::test_c_superseded_belief_stays_current_until_the_correction_happens`, `backend/tests/test_knowledge_ledger.py::test_every_ledger_rule_fails_closed_with_named_records` |
| Unconfirmed delivery (an off-stage transcript line) is not knowledge, and no belief may rest on it, on another actor's evidence or on unrelated evidence | proven by `backend/tests/test_knowledge_scenarios.py::test_e_unconfirmed_delivery_is_director_only`, `backend/tests/test_knowledge_ledger.py::test_every_ledger_rule_fails_closed_with_named_records` |
| An actor projection depends only on that actor's own records; projections and the first ledger error are input-order independent | proven by `backend/tests/test_knowledge_scenarios.py::test_an_actor_projection_depends_only_on_its_own_records`, `backend/tests/test_knowledge_scenarios.py::test_projections_are_equal_for_shuffled_inputs`, `backend/tests/test_knowledge_ledger.py::test_first_error_is_independent_of_input_order` |
| Being told about a subject is not traversal of its canonical data, and an unheld subject is indistinguishable from an unknown one | proven by `backend/tests/test_knowledge_scenarios.py::test_k_a_false_membership_is_only_a_held_proposition`, `backend/tests/test_knowledge_scenarios.py::test_k_unknown_and_unheld_subjects_are_indistinguishable`; graph traversal itself is owner Typed Relationship Graph (017) |
| A PC's beliefs are authored only by its controller, verbatim from that turn's interiority contribution, against the ledger's own proposition; NPC-era beliefs surface as inherited | proven by `backend/tests/test_knowledge_admission.py::test_nobody_but_the_controller_authors_a_pc_belief`, `backend/tests/test_knowledge_admission.py::test_the_proposition_is_the_players_words_verbatim`, `backend/tests/test_knowledge_admission.py::test_the_proposition_comes_from_the_ledger_never_the_caller`, `backend/tests/test_knowledge_scenarios.py::test_f_npc_era_beliefs_are_inherited_not_the_pcs_own` |
| Knowledge errors never carry record text | proven by `backend/tests/test_knowledge_records.py::test_errors_never_carry_record_text`, `backend/tests/test_knowledge_ledger.py::test_every_ledger_rule_fails_closed_with_named_records` |
| Evidence and beliefs survive persistence and retcons | not yet tested — owner: Durable Event Evidence (020) |
| Pure packages (`grimoire.core`, `core.inference`, `intent`, `security`, and `evals` outside its loader/CLI) have no side effects: they import only allowlisted pure standard-library modules and their declared grimoire packages, use no I/O or dynamic-code builtins, and load no capability module even transitively. One table, `backend/tests/purity.py`, declares the rules | proven by `backend/tests/test_purity.py::test_pure_packages_respect_their_boundary`, `backend/tests/test_purity.py::test_importing_a_pure_package_loads_no_capability_module`, `backend/tests/test_purity.py::test_checker_flags_side_effect_spellings` |
| Production code never imports the test harness | proven by `backend/tests/test_invariant_catalog.py::test_harness_is_test_only` |
| Function-scoped tests cannot write to a real home or Grimoire library through `HOME`, `USERPROFILE`, `GRIMOIRE_HOME` or `GRIMOIRE_V2_HOME` (redirected to sentinels), nor through Windows' `HOMEDRIVE`/`HOMEPATH` fallback (removed); hard-coded absolute paths are not intercepted | proven by `backend/tests/test_privacy_sentinel.py::test_sentinel_fails_a_test_that_writes_into_home`, `backend/tests/test_privacy_sentinel.py::test_windows_home_fallbacks_are_removed` |
| This catalog only cites tests that exist | proven by `backend/tests/test_invariant_catalog.py::test_catalog_references_exist` |
| A narrator cannot author a PC's intent, speech, interiority, belief or commitment; a failed attempt stays failed | proven (labelled claims, synthetic) by `backend/tests/test_sovereignty_scenarios.py::test_proving_scenario_reach_for_her_hand`; generated prose is not yet constrained (owners: 035, 047) |
| An authority denial is never an `OSError` a file adapter could swallow | proven by `backend/tests/test_authority.py::test_authority_denials_are_not_os_errors` |
| Four present NPCs, two speaking: exactly two separately planned NPC generation calls, no optional calls by default, and missing usage is never zero | proven by `backend/tests/test_inference_plan.py::test_four_present_two_speaking_npcs_cost_two_generation_calls`, `backend/tests/test_inference_policy.py::test_default_policy_disables_every_optional_stage`, `backend/tests/test_inference_usage.py::test_missing_usage_is_not_zero` |
| A story-quality candidate with a hard-check defect is rejected however fluent it is | proven by `backend/tests/test_evals_gate.py::test_no_rejection_is_rescued_by_higher_judge_scores` |
| A malicious imported card gains no authority beyond the importer's grants, and cannot use or consume a pending human approval | proven (simulated worker) by `backend/tests/test_security_card_import.py::test_malicious_card_cannot_escape_its_grants`, `backend/tests/test_security_card_import.py::test_malicious_card_cannot_borrow_a_pending_approval_or_a_lookalike_scope` |
| No worker can create a V1-only root entry, so a V2 library never reads as V1 | proven by `backend/tests/test_wave1_integration.py::test_no_worker_can_create_a_v1_root_entry_in_a_v2_library` |
| UI command, PC speech, PC action and direction are distinguished; uncertain speech does nothing destructive; a destructive command needs a yes for its exact target revision | proven by `backend/tests/test_intent_router.py::test_proving_scenario`, `backend/tests/test_intent_confirm.py::test_a_replayed_yes_after_the_command_ran_is_stale`; authorization and speaker binding belong to the executor (035, 104) |
| Independently built Wave 1 contracts still meet as documented (intent→contribution, actor ids, evals↔inference vocabulary) | proven by `backend/tests/test_wave1_integration.py::test_only_the_controller_can_turn_a_routed_line_into_their_pcs_contribution`, `backend/tests/test_wave1_integration.py::test_eval_call_purposes_are_inference_stages_or_documented_divergence` |
| V2 never resolves, opens or targets V1's library: a default launch opens no V1 directory, and every V2 write destination that is, sits inside or contains a V1 library (including Windows aliases and V1 roots that do not exist yet) is refused | proven by `backend/tests/test_library_paths.py::test_default_launch_never_opens_or_lists_v1`, `backend/tests/test_library_paths.py::test_write_destination_refuses_every_v1_location`, `backend/tests/test_library_paths.py::test_windows_spellings_of_the_v1_default_are_refused_before_it_exists`; actual writers must follow the writer obligations (owner: Native File Store (010)) |
| No stale-identity or by-name substitution in memory: unpinned references follow the declared head, pinned ones keep their revision | proven by `backend/tests/test_index.py::test_unpinned_follows_declared_head_and_pinned_stays`, `backend/tests/test_core.py::test_unpinned_actor_follows_head_and_pinned_actor_stays` |
| The V0.2.1 inventory never writes to, follows a link out of, or calls out from a scan: no write-mode open, mutation, spawn or socket outside its own report; the source tree is unchanged | proven by `backend/tests/test_migrate_v1_readonly.py::test_a_dry_run_writes_nothing_and_stays_offline`, `backend/tests/test_migrate_v1_readonly.py::test_a_report_run_writes_only_its_temp_and_report`, `backend/tests/test_migrate_v1_failures.py::test_f1_links_are_refused_and_never_followed`, `backend/tests/test_migrate_v1_units.py::test_a_file_replaced_after_the_walk_is_never_returned` |
| One V1 picture object stays distinct from its placements; same-named records in different sources are never linked; repeated scans are byte-identical | proven by `backend/tests/test_migrate_v1_scan.py::test_a_one_shared_object_stays_two_distinct_placements`, `backend/tests/test_migrate_v1_scan.py::test_b_same_named_characters_in_two_worlds_are_never_linked`, `backend/tests/test_migrate_v1_scan.py::test_h_scanning_twice_is_byte_identical_and_changes_nothing` |
| A V1 ledger that cannot be trusted blocks instead of resurrecting hidden records or art; a source edited mid-scan yields no report | proven by `backend/tests/test_migrate_v1_failures.py::test_a_corrupt_tombstone_ledger_blocks_instead_of_resurrecting_pictures`, `backend/tests/test_migrate_v1_cli.py::test_f7_a_live_source_exits_5_without_a_report` |
| A report is never written in or under a migration source, destination or V1 library | proven by `backend/tests/test_library_report_destination.py::test_a_report_inside_a_protected_root_is_refused`, `backend/tests/test_library_report_destination.py::test_a_report_inside_the_default_v1_home_is_refused_even_when_it_is_not_the_source` |
| Source-qualified IDs, owners, heads, links and broken references survive persistence | not yet tested — owner: Native File Store (010) |
| A stale write is rejected; reads come from one revision-consistent view | not yet tested — owners: Native File Store (010), Effective World Snapshots (022) |
| Concurrent device edits are preserved as conflicts | not yet tested — owner: Sync Revisions And Conflict Core (019); use `Interleave`/`meddle` with replica roots |
| A planned model call is issued only through the turn plan and an `execute` authorization covering its destination; every attempt is booked | proven by `backend/tests/test_providers_adapter.py::test_f2_unplanned_call_sends_nothing`, `backend/tests/test_providers_adapter.py::test_f1_approval_for_another_url_or_body_is_refused`, `backend/tests/test_providers_adapter.py::test_a2_outage_falls_back_visibly` |
| One accepted action commits exactly once across crashes | not yet tested — owner: Durable Action Recovery (030); use `explore_crashes(..., recovery_crashes=True)` |

## Fault model and fidelity limits

The harness models a **process crash**: completed operations are on disk, the dead process does nothing more, and nothing else is lost. Simulated crashes do not depend on the OS. The CI matrix only proves that `OsFileOps`, confinement and the platform's `os.replace` basics behave on each OS.

Not simulated:
- **Power loss or kernel crash.** Unsynced page-cache data is not lost, and directory fsync is not modelled.
- **Windows `os.replace` against open handles** (sharing violations), transient Windows errors, and partially created `make_dirs` trees.
- **Case-insensitive filesystems.** `A.json` and `a.json` can produce false `all mutations mediated` reports there.
- **Loops that perform no file operations.** These are only backstopped by pytest's `faulthandler_timeout` traceback dump and by CI job timeouts.

### Fault modes absent from CI

| Absent | Owner |
|---|---|
| Stale-run fencing of model calls across processes (provider failures, truncated streams and cancellation are covered by the provider fault invariants above) | Per-NPC Generation Runtime (027), Durable Action Recovery (030) |
| Multi-process races and file locks | Native File Store (010), Durable Action Recovery (030) |
| Network transport faults during sync | App-Managed Device Sync (028) |
| Real disk-full or permission errors (only simulated via `Fail`) | Native File Store (010) |
| Malformed authored Markdown/JSON beyond a corrupt journal | Native File Store (010); V0.2.1 inputs are covered by the inventory tests (011) |
| Property and state-machine generation across forks, retcons and imports | moonshot |
| A synthetic V2 world/campaign fixture library with expected outcomes (a V0.2.1 one exists: `backend/tests/v1_fixture.py`) | Entity Facets (009), Native File Store (010), once records exist |
| Reducing redundant setups across test modules | revisit when a second family of file-backed writer tests exists |

## CI gate

**Wave 2 entry rule:** require a completed green integration-head check for **all** configured jobs before another PR is merged into `v2`. Without a GitHub-side required-checks rule and up-to-date branch enforcement (or a merge queue), individually passing PRs are not enough: rebase or test their exact merged combination against the current `v2` first. **Repository owner action is still needed** to configure protection; adding these words does not enable a branch rule. See [Wave 2 handoffs](roadmap/WAVE2_HANDOFFS.md#wave-2-entry-gate).

Every job in `.github/workflows/ci.yml` is meant to be required, and `v2` must be green at every commit. Wave 1 showed why: eight PRs each passed against an older `v2` and merged within minutes, and their combination was red. Windows first ran Sprint 001's tests when Sprint 006 added the matrix, and then every OS rejected Sprint 002's code under Sprint 006's purity allowlist. Because `v2` has no branch protection, red checks did not block later merges. The repository owner should configure `v2` to require every `domain` and `compat` check, with either "require branches to be up to date before merging" or a merge queue (the workflow already listens for `merge_group`). Then a PR is tested against the `v2` it will actually join.

## Policies (answers to the sprint's open questions)

- **Which fault modes are absent from CI?** See the table above.
- **Where does fixture reuse hide independent failure detection?** Every fault point re-runs `setup` from an empty root, so state is never shared between points. `planned fault fired` and `all mutations mediated` turn a fixture that never exercises its fault into a failure. Each built-in invariant has unit tests showing it can fail. That does not prove a given scenario exercises it, so scenario tests also assert specific violations (A3, A12, A14).
- **How should nondeterministic inference tests be bounded?**
  - CI uses no live providers, only scripted or recorded fakes.
  - Runs are seeded, and they are bounded by call and token counts, not wall-clock time.
  - Live-model quality evaluations are opt-in and run outside CI.
- **Synthetic data only.** No user worlds, campaigns, transcripts, names or provider secrets in fixtures or CI artifacts.
