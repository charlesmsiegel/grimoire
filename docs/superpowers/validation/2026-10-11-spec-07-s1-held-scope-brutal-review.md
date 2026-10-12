# Spec 07 S1 held-scope brutal review

Frozen target: `882cc05d2c47389575136c86d42809512ccaf9d0`.

**Verdict: BLOCKING.** Three supported held-scope defects reproduce independently. The earlier push/promote correction remains valid within its stated scope; these are different owners.

Scope: the native held-scope findings and their surrounding callers under the approved [S1 plan](../plans/2026-10-11-roadmap-07-s1-members-field.md) and [design](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md). The supplied actual native result identifies thread `01a12e23-437b-7493-8f2f-b871d433a23c`. Its separate native-marker lifecycle finding is outside this assigned review.

## Reproduction method

All probes use frozen public source in `C:/Users/charl/AppData/Local/Temp/spec07-publication-recheck-882cc05`, snapshot `backend/src` on `PYTHONPATH`, and isolated fixture stores. The helper `_review_recreate_at_acquire` in the Temp `backend/tests/test_group_membership_lifecycle.py` intercepts the real campaign lock object's `acquire` method before it is owned. It performs actual campaign delete/create under the same ID, bound to World Saltmarch instead of Realm, then delegates to the original acquisition. This covers both direct campaign holds and `hold_all`; no fake lock, fake source read, or bypassed write is used. A one-shot guard permits the real nested mutation locks to execute normally.

## F1 — P2: image promotion combines old roots with the recreated campaign

Location: `backend/src/grimoire/store/overlay.py:2242–2248`, `promote_image`.

Realm and Saltmarch each contain group Salt Circle and a different `gallery_1` PNG. Start promotion from the campaign originally bound to Realm. Immediately before its campaign hold acquires, recreate that campaign under Saltmarch. The function has already captured Realm's root; it now validates the new campaign's ledgers but copies Realm's red image into the new campaign's avatar, instead of Saltmarch's blue image.

`test_review_image_promote_uses_recreated_campaign_world` fails its actual PNG byte comparison. The strengthened fixture creates real same-ID group records in both Worlds as well as their image slots. Final isolated run: **1 failed**, 56 deselected, exit 1, 1.49 seconds. The mismatch starts at byte 36.

Correction/test requirement: resolve both roots inside the campaign hold, retain that coherent scope through arrival checks, description carry, image links, avatar swap, and tombstone decisions. Keep the real pre-acquisition recreation test with distinct World images.

## F2 — P2: World reclassification suspends another World's membership offer

Location: `backend/src/grimoire/store/reclassify.py:205–234`, `world_entity`.

Start moving Realm's Salt Circle group to lore. Recreate the captured Saltmarch campaign under the other World before its sorted hold acquires; give it an owned same-ID group and a live journalled Members edit. `world_entity` treats that new campaign as following Realm and writes a relocation suspension into its journal. `_follow_in_campaign` later correctly notices the different World and skips the move, leaving the unrelated original and offer with an incomplete relocation fence.

`test_review_world_move_does_not_suspend_other_world_offer` fails byte-for-byte preservation of the newly created campaign's journal. The first difference is at byte 1074, where the target gains additional state.

Correction/test requirement: filter captured IDs by current existence and World binding inside the existing sorted hold, before strict preflight, computing `following`, suspension, or affected-scope stamps. Verify the unrelated campaign's journal and record remain unchanged and its original offer remains usable.

## F3 — P2: World image cleanup removes a different World's hide choice

Locations: `backend/src/grimoire/store/overlay.py:2739–2744`, `world_identity_change`, and its `world_images.delete_image` cleanup owner.

Start deleting Realm's library image `map`. Before the captured campaign lock acquires, recreate the campaign under Saltmarch, create Saltmarch's own `map`, and hide it with `assets/library/map`. The context yields its old captured ID without checking the current World binding. The image cleanup removes the recreated campaign's marker, making its unrelated image visible.

`test_review_world_image_delete_preserves_other_world_tombstone` fails because `overlay.deleted_strict(cid)` becomes the empty set.

Correction/test requirement: while retaining the original sorted lock set, yield only IDs still bound to the operation's World. Filter before strict ledger/journal reads. Keep the same-named hidden image in the unrelated World unchanged.

## Surrounding cases and exact evidence

The three primary tests ran together: **3 failed**, 50 deselected, exit 1, 6.84 seconds. F1 was subsequently strengthened and rerun as recorded above.

Two additional probes parameterize World move and World image deletion with a recreated other-World campaign whose `deleted.json` contains `{`. Both incorrectly reject the original World's operation with `GroupIdentityConflict` during strict preflight: **2 failed**, 55 deselected, exit 1, 1.90 seconds. These are consequences of F2/F3, not separate findings. They establish that filtering only immediately before writes is insufficient; binding must be checked before preflight of unrelated state.

The deletion-without-recreation controls pass for both World operations: **2 passed**, 53 deselected, exit 0, 0.98 seconds. No separate defect is claimed for that sequence. The image tombstone remover's unchanged empty-set path does not write when no marker exists.

Frozen lifecycle, World image-store, and campaign image-store controls, excluding all seven reviewer probes: **120 passed**, 7 deselected, exit 0, 18.36 seconds. JUnit evidence: `held-scope-controls.xml` in the snapshot. The only warning was the environment's FastAPI TestClient/httpx deprecation warning.

Caller inspection found the shared context in World entity, greeting, character, PC, and library-image deletion. Record cleanup already has a later World-binding check; library-image cleanup lacks that check. Filtering the context before preflight also prevents unrelated damaged ledgers from blocking these World operations. This review does not claim newly discovered campaigns must join an in-progress operation or require an S5 discovery barrier.

No product or tracked test changes were made by the reviewer, and no private store was accessed. This scoped artifact does not certify the full gate, separate native-marker work, browser behavior, Pydantic variants, or deferred S2–S8.

## Scoped independent recheck — frozen 73fb54a19

Recheck target: `73fb54a19177aefd23989a94df5df3065af094a7`.

**Verdict: NON-BLOCKING for these held-scope corrections. F1, F2, and F3 above are closed at this SHA.** The preceding BLOCKING verdict remains the historical assessment of `882cc05d2c47389575136c86d42809512ccaf9d0`.

Image promotion now resolves campaign and World roots under its campaign hold and supplies those captured roots to native marker matching. Both World identity cleanup and World reclassification filter the original captured IDs through `bound_dependents` while holding the sorted domain. Filtering precedes strict identity/journal preflight, selection of following records, relocation fences, cleanup, and affected-scope bookkeeping. The helper skips deleted or differently bound campaigns without discovering or acquiring any new campaign. Source-wide search found exactly two callers, both inside their captured holds.

Independent verification used public source archived from the exact recheck SHA in `C:/Users/charl/AppData/Local/Temp/spec07-held-recheck-73fb54a`, snapshot `backend/src` on `PYTHONPATH`, and isolated fixture stores. The prior reviewer-only probe helper and all seven tests were copied into this disposable snapshot without changing frozen product source:

- **7 passed**, 53 deselected, exit 0, 4.31 seconds. This closes all three direct reproductions: current-World image bytes reach the avatar, unrelated Members journal bytes remain unchanged, and the unrelated library-image tombstone survives. Both corrupt-other-World preflight probes now complete successfully without altering the corrupt ledger. Both deletion-without-recreation controls remain passing. JUnit evidence: `held-scope-probes.xml`.
- Frozen lifecycle, World image-store, and campaign image-store controls: **123 passed**, 7 deselected, exit 0, 13.31 seconds. This includes the three committed regressions and retains the strict failure behavior for campaigns actually bound to the operation's World. JUnit evidence: `held-scope-controls.xml`.

Both JUnit files are in the recheck snapshot directory. The only warning was the environment's FastAPI TestClient/httpx deprecation warning. No remaining supported held-scope finding was established in this bounded recheck. This does not certify the separate native-alias lifecycle work, a global gate, browser behavior, Pydantic variants, or deferred slices. No product or tracked test changes were made by the reviewer; no private store was accessed.
