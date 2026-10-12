# Spec 07 S1 native-fixes brutal review

Frozen review target: `86964ac22d3df749eb680d65bd09cf283d8b573d`.
Review baseline: `02131dd93be627d6f45fb3c960c48161e04a0d20`.

**Verdict: BLOCKING.** One supported P2 finding remains in an owning caller of the captured-dependent lock-set fix. The other reviewed native findings have no supported remaining defect in this scope.

This independent `$brutal-review` follows the approved [design](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), [S1 plan](../plans/2026-10-11-roadmap-07-s1-members-field.md), and [native review](2026-10-11-spec-07-s1-native-review.md). It covers prepared-write identity validation, strict image promotion, World deletion dependent-lock capture and callers, and actual conservative Members Undo/Redo displays. S2–S8 are intentionally pending.

## F1 — P2: World image deletion still reacquires a newly discovered dependent out of order

Frozen locations: `backend/src/grimoire/store/world_images.py:217`, `:227`, and `:247–249`.

`delete_image` enters `world_identity_change` but discards its captured campaign IDs. After deleting image bytes and description, `_forget_in_dependents` enumerates dependents again and calls `drop_library_tombstone` for each. That mutator takes a campaign lock. A newly attached campaign was neither included in the original sorted hold nor preflighted by it.

Concrete reproduction in an isolated store:

1. Create World Realm, dependent campaign Saltmarch, and a World image named `map`.
2. Start `world_images.delete_image`. At the `assets.delete_in` seam, its World identity context already holds Saltmarch.
3. Create a new dependent campaign Realm on a separate thread, then finish deleting the image.
4. Intercept `overlay.drop_library_tombstone` and require `locks.holds_campaign(cid)` for every swept campaign.
5. The sweep reaches the new campaign and fails with `AssertionError: unheld late dependent realm; already holding saltmarch`.

The observable defect is acquisition outside the captured hold. The resulting contention scenario follows directly: another World operation can hold Realm while waiting for Saltmarch in sorted order; this delete holds Saltmarch while requesting Realm. The timeout is caught as best-effort cleanup after deletion effects. The review reproduced the out-of-set acquisition, not a full timed two-operation deadlock.

Required correction: retain `as cids` from `world_identity_change` and pass exactly those IDs into the image tombstone sweep. Its owning delete path must not discover additional campaign locks after the hold begins. Keep the late-created-dependent regression.

## Independent evidence

All execution used a public-source archive of the frozen SHA under `C:/Users/charl/AppData/Local/Temp/spec07-native-fixes-86964ac`, the shared Python environment, and snapshot `backend/src` on `PYTHONPATH`. Fixtures isolated the store; no private library was read.

- Reviewer-added `test_review_world_image_sweep_stays_in_captured_lock_set`: **1 failed**, 47 deselected, exit 1. This is F1's direct reproduction. It was appended only to the disposable snapshot lifecycle test module.
- Reviewer-added `test_review_prepared_boundary_refuses_before_root_probe`: **6 passed**, 48 deselected, exit 0. Both `update_entity` and `_materialize_flat` reject forward-slash traversal, Windows backslash traversal, and an invalid kind before either campaign or World root resolution is reached.
- Frozen regression and architecture selection: **1038 passed, 2 skipped, 1 deselected**, exit 0, 308.68 seconds. The deliberately failing reviewer image-sweep case was excluded; the six additional boundary cases were added after this run collected tests and ran separately. JUnit evidence: `C:/Users/charl/AppData/Local/Temp/spec07-native-fixes-checks.xml`.

The selection comprised group-membership lifecycle/routes, World and campaign image stores, Undo/journal stores, and atomic, lock-domain, lock-order, overlay, import, paths, store-path, and Pydantic guards. The passing static lock-order guard does not invalidate the runtime counterexample.

## Other reviewed fixes

- Prepared flat-copy and update entry points now reject unsafe IDs and unsupported kinds before filesystem-root resolution. Existing route traversal regressions and the six independent boundary probes passed.
- Image promotion validates both identity ledgers under its campaign hold before image effects; native identity comparisons are retained. Corrupt-ledger promotion regressions passed. A later fail-soft read under that same hold did not provide a supported independent defect.
- Entity, character, PC, greeting deletion and demotion pass their captured dependent sets to `forget_world_record`. World image deletion is the missed owner described in F1.
- Members Undo/Redo now derives display values from the actual stored before-state and sealed inverse after-state, including conservative omission of a missing inherited member. Lifecycle and journal regressions passed.

This is a scoped adversarial review, not a full-repository gate or certification of later commits. Windows dependency integration, browser verification, APK validation, and deferred slices are outside this frozen assessment. No product or tracked test changes were made by the reviewer.
