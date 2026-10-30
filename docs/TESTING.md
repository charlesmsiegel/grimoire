# V2 testing, failure injection and the invariant catalog

V2 data is long-lived, local-first and human-editable, so the expensive failures are partial writes, mistaken identity, stale views, private-knowledge leaks and runs that cannot be recovered. This page lists the invariants that protect against those failures and the test that proves each one. It also describes the fault-injection harness and what it does **not** simulate.

Run everything with `python -m pytest backend/tests -q`. CI runs this on Python 3.11 on Ubuntu (`domain` job), plus an advisory `compat` matrix: Python 3.12 and 3.13 on Ubuntu, and 3.11 on macOS and Windows. A green suite proves these contracts; it does not mean a playable app exists.

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

`explore_crashes` first runs a fault-free **baseline**. It then crashes each mutating operation `before` and `after`, `torn`-writes each non-empty write, and with `errors=True` injects `Fail`. With `recovery_crashes=True` it also crashes each recovery operation (depth 2). A point is named by its Crash/Fail operation (or by the interleaved operation when the plan has no crash). Paths written by an `Interleave` or a `meddle` are excluded from `atomic_outcome` and `completes_after`, because that content is not the writer's. **Decision:** preserving a *meddled* edit is checked by the built-in `preserves_meddle`; preserving an *interleaved* edit (a lost update during a live race) needs a scenario-specific invariant, as in A9. A `preserves_interleave` built-in is left to the sync sprint (019), which owns concurrent-edit semantics. Exceptions, sandbox escapes and budget overruns inside an `Interleave` or `meddle` are fixture bugs and propagate raw, not as named violations. The harness refuses to wipe a pre-existing `<workdir>/root` that it did not create. Point names are unique; an illustrative depth-2 point is `action:#11 replace a.json.~tmp1.new -> a.json @after > recover:#7 remove .journal @before`. A failure is a `Violation(invariant, point, detail, trace)`, and `CrashReport.assert_ok()` lists every violation by name.

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
| `all mutations mediated` | The disk matches the shadow model built from recorded ops, so nothing bypassed the seam. | `backend/tests/test_fault_harness.py::test_a13_writes_that_bypass_the_seam_are_named` |
| `recovery completes` | Recovery does not raise, including after it was itself interrupted or met a corrupt file. | `backend/tests/test_fault_harness.py::test_a12_interrupted_recovery_is_explored`, `backend/tests/test_fault_harness.py::test_a15_corrupt_journal_is_a_named_recovery_failure` |
| `sandbox` | Setup, action and recover phases cannot escape the test root (links are proven where the platform permits creating them; Windows runners may skip). | `backend/tests/test_fault_harness.py::test_a10_link_escape_is_a_sandbox_violation_in_any_phase` |

### Built-in scenario invariants

| Invariant | Meaning | Proven by |
|---|---|---|
| `atomic_outcome` | The recovered state equals either the pre-action or the completed state (paths touched by a meddle or interleave excluded). | `backend/tests/test_builtin_invariants.py::test_atomic_outcome`, `backend/tests/test_fault_harness.py::test_a3_naive_writer_violation_names_invariant_and_point` |
| `idempotent_recovery` | A second recovery changes nothing. | `backend/tests/test_builtin_invariants.py::test_idempotent_recovery` |
| `no_residue` | No staging or journal files remain. | `backend/tests/test_builtin_invariants.py::test_no_residue` |
| `completes_after` | Once the commit op has executed, recovery finishes the action. | `backend/tests/test_builtin_invariants.py::test_completes_after`, `backend/tests/test_fault_harness.py::test_a12_always_rollback_recovery_breaks_commit_durability` |
| `untouched` | Source paths, such as a source replica or a V1 library, never change. The glob matches entries only; `desktop/*` does not cover the `desktop` directory entry itself. | `backend/tests/test_builtin_invariants.py::test_untouched`, `backend/tests/test_fault_harness.py::test_a14_replica_copy_is_atomic_and_leaves_the_source_untouched` |
| `preserves_meddle` | An edit made while the process was down survives recovery. | `backend/tests/test_builtin_invariants.py::test_preserves_meddle`, `backend/tests/test_fault_harness.py::test_a14_blind_roll_forward_clobbers_an_edit_made_while_down` |

The proving scenario for this slice is a crash immediately after one file has been published, followed by recovery to the completed state: `backend/tests/test_fault_harness.py::test_a2_crash_after_one_publication_is_recovered`. Exhaustive crash, torn-write, I/O-error and recovery-crash coverage of the reference writer is in `backend/tests/test_fault_harness.py::test_a1_journaled_writer_survives_every_crash_point`.

## V2 domain invariants

| Invariant | Status |
|---|---|
| Same-name definitions from different sources never match | proven by `backend/tests/test_core.py::test_world_variants_cannot_be_matched_by_name` |
| One definition yields independent campaign instances and knowledge | proven by `backend/tests/test_core.py::test_same_definition_has_independent_campaign_instances_and_knowledge` |
| PC/NPC control is a role, not identity | proven by `backend/tests/test_core.py::test_control_changes_without_changing_actor_identity_or_history` |
| Actor views are immutable snapshots | proven by `backend/tests/test_core.py::test_actor_view_is_an_immutable_snapshot` |
| `grimoire.core` is pure: no I/O, providers or harness imports | proven by `backend/tests/test_invariant_catalog.py::test_core_is_pure` |
| Production code never imports the test harness | proven by `backend/tests/test_invariant_catalog.py::test_harness_is_test_only` |
| Function-scoped tests cannot write to a real home or Grimoire library through `HOME`, `USERPROFILE`, `GRIMOIRE_HOME` or `GRIMOIRE_V2_HOME` (redirected to sentinels; hard-coded absolute paths are not intercepted) | proven by `backend/tests/test_privacy_sentinel.py::test_sentinel_fails_a_test_that_writes_into_home` |
| This catalog only cites tests that exist | proven by `backend/tests/test_invariant_catalog.py::test_catalog_references_exist` |
| V2 never writes to V1's `~/.grimoire/` | not yet tested — owner: V2 Library Paths And Isolation (001) |
| Source-qualified IDs survive persistence; no stale-identity substitution | not yet tested — owners: Source Qualified Identity (002), Native File Store (010) |
| A stale write is rejected; reads come from one revision-consistent view | not yet tested — owners: Native File Store (010), Effective World Snapshots (022) |
| Concurrent device edits are preserved as conflicts | not yet tested — owner: Sync Revisions And Conflict Core (019); use `Interleave`/`meddle` with replica roots |
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
| Inference/provider failures, truncated streams, cancellation and stale-run fencing | Model Routing And Budgeting (013), extended by Per-NPC Generation Runtime (027) |
| Multi-process races and file locks | Native File Store (010), Durable Action Recovery (030) |
| Network transport faults during sync | App-Managed Device Sync (028) |
| Real disk-full or permission errors (only simulated via `Fail`) | Native File Store (010) |
| Malformed authored Markdown/JSON beyond a corrupt journal | Native File Store (010), V1-to-V2 Migration Skill (011) |
| Property and state-machine generation across forks, retcons and imports | moonshot |
| A synthetic world/campaign fixture library with expected outcomes | Entity Facets (009), Native File Store (010), once records exist |
| Reducing redundant setups across test modules | revisit when a second family of file-backed writer tests exists |

## Policies (answers to the sprint's open questions)

- **Which fault modes are absent from CI?** See the table above.
- **Where does fixture reuse hide independent failure detection?** Every fault point re-runs `setup` from an empty root, so state is never shared between points. `planned fault fired` and `all mutations mediated` turn a fixture that never exercises its fault into a failure. Each built-in invariant has unit tests showing it can fail. That does not prove a given scenario exercises it, so scenario tests also assert specific violations (A3, A12, A14).
- **How should nondeterministic inference tests be bounded?**
  - CI uses no live providers, only scripted or recorded fakes.
  - Runs are seeded, and they are bounded by call and token counts, not wall-clock time.
  - Live-model quality evaluations are opt-in and run outside CI.
- **Synthetic data only.** No user worlds, campaigns, transcripts, names or provider secrets in fixtures or CI artifacts.
