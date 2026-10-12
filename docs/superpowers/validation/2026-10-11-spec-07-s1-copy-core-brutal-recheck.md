# Spec 07 S1 copy-core independent brutal recheck

Reviewed commit: `abdb7be03cd1fb026704abf4db2b28faa8ae9a7d`.
Repair range: `938d2d4b57cb3b5f92084a53b50f005269d7eb01..abdb7be03cd1fb026704abf4db2b28faa8ae9a7d`.
Original implementation base remains `f816921c8d2bd91628cfe12e28bda9106a254f11`.
Date: 2026-10-11.

Verdict: **non-blocking for this scoped recheck**. Both blocking findings from the [copy-core review](2026-10-11-spec-07-s1-copy-core-brutal-review.md) are resolved. No remaining grounded finding was identified in the repairs or the additional direct asset-copy/collateral exception boundaries reviewed here. This does not certify the unfinished S1 work or replace the final independent full-S1 gate.

## Resolution of finding 1: native actor aliases

The original native-Windows sequence was rerun independently against the frozen repaired commit: campaign-local Mara is created first, a distinct World Mara is created later, and the World group stores `characters:MARA`. Actual native `samefile` confirms the uppercase/lowercase paths alias.

Observed after the repair:

- The inherited context excludes the actor from effective Members and reports `characters:MARA` unresolved, preserving its raw spelling.
- The existing campaign route helper's body-only update raises `GroupIdentityConflict` and leaves no campaign group file.
- Forced publication of an owned group carrying that ref raises `DanglingReferenceError`.

`overlay.actor_detachments` augments the captured detachment set for the queried actor refs using `samefile` in the campaign/World roots. Context, copy validation and publication use it. The pure `group_refs` filtering helper remains free of I/O, and no actor body or inverse index is consulted. Exact markers retain their meaning when actors are missing.

The new native tests cover Members and Leader, context/copy refusal, campaign-side and World-side alias proof, and promote/push/forced push. They passed without native-case skips on this host. The case-sensitive policy simulation independently returns distinct identities and verifies no case folding is applied. A separate reviewer probe likewise confirmed `samefile=False` does not create an alias, a `PermissionError` becomes `GroupIdentityConflict`, and the input detachment set is not mutated.

There is no policy/spec amendment required by this repair: it preserves authored ids and consent spelling while respecting filesystem identity. No claim is made that a physical case-sensitive Windows directory or another OS was exercised.

## Resolution of finding 2: World move before ledger refusal

`reclassify.world_entity` now preflights both strict ledgers for every enumerated dependent inside its existing `hold_all`, before `entities.reclassify` or any World/ref/copy effect.

The original corrupt-detachment reproduction was rerun independently. It raises `GroupIdentityConflict` with the World tree byte-identical; the source group remains in place. The new tests cover both `detached.json` and `deleted.json`, corrupt the second of two dependents, and compare the complete World and both campaign file trees. These tests passed, proving the preflight occurs before the first dependent's effects as well as before the World move.

This resolves the strict-ledger ordering defect. Future membership journal suspension/retirement and failure-path revision fences remain separate Task 4 work.

## Additional changed boundaries

`overlay.copy_record_dir_down` now takes the campaign hold and uses both strict ledgers before any asset or sidecar publication. Invalid kind/id and missing source return without effects. Its production caller in demotion already holds the sorted campaign domain, so the nested same-campaign hold is reentrant. The new direct-call corruption cases pass and leave campaign bytes unchanged.

`reclassify._sweep` catches `GroupIdentityConflict` only in the best-effort collateral rewrite legs, after the original move and identity ledger changes have landed. The original movement/preflight path is outside that catch. An unresolved inherited collateral group stays inherited, with a warning rather than implicit consent. The route regression tests exercise both owner and typed-ref collateral legs: the move succeeds, no collateral copy is published, World bytes remain unchanged, record-reference repointing still runs, and the campaign revision changes. No unsupported broad swallowing of the pre-move strict-ledger refusal was found.

Also inspected the new all-dependent demotion and frozen-source tests, the shared-hold interleavings for actor birth/first copy, and positive newly-authored/publication cases. Their coverage does not expand this verdict to the future API/journal/editor layers.

## Verification evidence

The reviewed commit was extracted with `git archive` into a disposable Temp tree. All source reads and executions for this recheck used that frozen tree, not the primary agent's ongoing edits.

- Configured `python scripts/ratchet.py ruff`: **passed, 1146 findings all at baseline**.
- Focused pytest: **271 passed, one existing FastAPI/Starlette deprecation warning, 68.89 seconds**. Files: `test_group_membership_store.py`, `test_reclassify_routes.py`, `test_library_move.py`, `test_lock_order_guard.py`, `test_import_guard.py`.
- Original failing sequences rerun separately: native alias route copy refused before publication; native alias forced publication refused; corrupt-ledger World move refused with World bytes unchanged.
- Independent matcher probes: distinct identity not folded; inaccessible identity refused; input set unchanged.
- Optional sibling `python-code-doctor/scripts/analyze_diff.py`: **not run for this recheck**. Its earlier foundation output is not evidence about these repairs.

Test commands used frozen `PYTHONPATH=backend/src`, isolated Temp `GRIMOIRE_HOME`, and `-p no:cacheprovider`; store fixtures additionally isolate their own homes. No real store, external model or app server was used. Only this review artifact was written in the repository; no production/test edits or commits were made by the reviewer.

## Not reviewed

This is a resolution review of the two prior blocking findings and the narrow asset-copy/collateral-boundary changes, not a new whole-repository audit. Task 4 API/Undo/journal/lifetime fences, Task 5 editor consent, and S2-S8 are not certified. Their absence is not a finding here.

The full `make check`, frontend/browser/Vitest, mypy/eslint, Pydantic 1, Android build, real case-sensitive filesystem execution, exhaustive filesystem alias behavior, and broad performance/concurrency campaigns were not run. Multi-file crash atomicity and external filesystem synchronization remain outside the stated store guarantees. The final full-S1 independent code review and diff-versus-originating-spec gate are still required after implementation.
