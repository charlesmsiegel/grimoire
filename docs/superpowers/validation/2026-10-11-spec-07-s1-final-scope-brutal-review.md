# Spec 07 S1 final-scope brutal review

Frozen target: `3947aaa56b02350ff4440cc948baf885ffdb4918`.
Scope baseline: `41da96e4d`.

**Verdict: BLOCKING.** One supported P2 finding remains in the publication owners adjacent to the reviewed fixes. Both push and promote reproduce the same stale-scope defect. No additional supported finding was established in the corrected demotion or native-alias branches.

This independent `$brutal-review` covers the overlay/sync delta, its owning publication calls, and analogous scope/partial-failure cases under the approved [S1 plan](../plans/2026-10-11-roadmap-07-s1-members-field.md) and [design](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md). S2–S8 remain deferred.

## F1 — P2: push and promote combine a pre-lock World with a recreated campaign

Frozen locations: `backend/src/grimoire/store/sync.py:756` and `:1106`, immediately before the campaign holds in `promote` and `push`.

Both entry points resolve and validate their World root before acquiring the campaign lock. The bodies then read the current campaign while continuing to publish to that earlier root. The demotion fix correctly rechecks current bindings inside its captured hold, but this equivalent publication boundary remains open. The pre-lock ordering predates this delta; it is reported as a concrete defect in the reviewed owning/analogous scope, not as a newly introduced line.

Independent push reproduction:

1. World Realm contains group Salt Circle with Seraphine's text; campaign Saltmarch inherits it.
2. Begin a forced push and pause immediately after `_require_world` resolves Realm, before the campaign hold.
3. Delete Saltmarch and recreate the same campaign ID bound to World Saltmarch. Materialize its same-ID group and edit its body to Mara's text.
4. Resume the original push.
5. The call succeeds and overwrites Realm's group with the recreated campaign's Mara text. The assertion that the old World's bytes remain unchanged fails (byte 31 differs).

Independent promotion reproduction:

1. Saltmarch initially belongs to Realm and owns a local Salt Circle group; Realm has no such group.
2. Pause promotion at the same pre-hold World-resolution seam.
3. Recreate Saltmarch bound to another World and create its own same-ID group with Mara's text.
4. Resume promotion.
5. The call succeeds and creates the new campaign's group in the old Realm. The assertion that Realm still lacks that group fails.

The test seam deterministically schedules real campaign delete/create and record operations between root resolution and lock acquisition. It does not substitute record bytes or bypass the publication writers. This models another operation completing before the pending publication obtains its campaign hold.

Required correction: select/validate the campaign's World inside the same campaign hold as its record and strict-ledger reads, for both entry points. A coherent operation on the current bound scope, or an explicit stale-scope refusal, is acceptable; publishing current campaign bytes into the earlier World is not. Regression assertions should preserve the old World's bytes/absence and inject the interleaving before lock acquisition.

## Independent verification

All execution used a public-source archive of the exact frozen target in `C:/Users/charl/AppData/Local/Temp/spec07-final-scope-3947aaa`, snapshot `backend/src` on `PYTHONPATH`, and isolated fixture stores. Reviewer probes were appended only to the Temp membership-store test module.

- `test_review_push_recreated_campaign_does_not_publish_into_old_world`: **1 failed**, 156 deselected, exit 1, 1.32 seconds. Direct wrong-World overwrite reproduction.
- `test_review_promote_recreated_campaign_does_not_publish_into_old_world`: **1 failed**, 159 deselected, exit 1, 4.16 seconds. Direct wrong-World creation reproduction.
- `test_review_demote_partial_copy_stamps_held_and_keeps_world` and `test_review_alias_match_uses_supplied_roots_without_world_rediscovery`: **2 passed**, 157 deselected, exit 0, 0.85 seconds. The first fails the second real materializer after the first copy lands, verifies both campaign locks remain held, both revision tokens change, and the World original survives. The second proves native alias matching uses supplied captured roots without rediscovering either root.
- Frozen membership-store, library-move, sync-store, sync-pins, and membership-route suites: **284 passed**, 1 deselected, exit 0, 67.35 seconds. The failing push probe was excluded. The other three reviewer probes were added after this run collected and executed separately. JUnit evidence: `checks.xml` in the Temp snapshot.

The sole warning was the environment's FastAPI TestClient/httpx deprecation warning. The six committed Windows-native alias variants ran in the passing selection; they were not skipped.

## Corrected branches and limits

Demotion now captures discovery once, validates current bindings/copies/strict ledgers under its sorted hold, excludes campaigns rebound elsewhere, preflights hidden bound campaigns, and always invokes the locked materializer. Its cleanup and revision stamps use the current bound subset. The committed take-world and campaign-recreation regressions passed; the independent partial-copy failure probe also passed.

The shared native marker matcher now protects detached push, tombstoned group reads/preflight/materialization, and captured-view diagnostics. Exact identity and native samefile comparisons remain distinct from blanket case folding. The reviewed alias refusal regressions and captured-root control passed.

This artifact does not certify a current-source full repository gate, the earlier full Py2 run, active Pydantic-1 validation, browser behavior, unrelated slices, or later commits. No product or tracked test changes were made by this reviewer, and no private store was accessed.

## Scoped independent recheck — frozen 882cc05d2

Recheck target: `882cc05d2c47389575136c86d42809512ccaf9d0`.

**Verdict: NON-BLOCKING for the publication-scope correction. F1 above is closed at this SHA.** The earlier BLOCKING verdict remains the assessment of frozen `3947aaa56b02350ff4440cc948baf885ffdb4918`; it is not the verdict for this corrected target.

Both `push` and `promote` now resolve and validate the destination World inside their campaign hold, before the source and strict-ledger reads. The promotion actor/flat dispatch and push helper receive that held root. A source-wide caller search found no additional invocation of `_promote_actor`, `_promote_flat`, or `_push_locked` outside these corrected owners. The analogous accept path already resolves its World under its hold. No supported remaining stale-root publication defect was established in this scope.

Independent verification used an archive of the exact recheck SHA in `C:/Users/charl/AppData/Local/Temp/spec07-publication-recheck-882cc05`, snapshot `backend/src` on `PYTHONPATH`, and isolated fixture stores:

- Frozen membership-store, library-move, sync-store, and sync-pins suites: **263 passed**, exit 0, 38.29 seconds. This includes both new publication regressions. Their hook executes real campaign deletion/recreation before the actual publication lock's acquisition, after migration, and confirms both the old World's bytes/absence and publication into the current World. Unlike the earlier root-resolution attack seam, this hook remains a valid interleaving after moving root resolution under the lock. JUnit evidence: `checks.xml` in the snapshot.
- Independent reviewer-only `test_review_publication_world_lookup_is_inside_campaign_hold`, parameterized for push and promote: **2 passed**, 158 deselected, exit 0, 5.04 seconds. The probes assert that every `world_root_of` lookup reached by each publication holds the campaign lock, then verify the actual published group body. These were added only to the disposable snapshot and collected separately from the 263-test run.

The runs emitted only the environment's FastAPI TestClient/httpx deprecation warning. No product or tracked test edits were made by the reviewer, and no private store was accessed. This closes the supported publication finding only; it is not a current-source full-gate, browser, Pydantic-1, deferred-slice, or separate native-review certification.
