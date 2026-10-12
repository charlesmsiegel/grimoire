# Spec 07 S1 publication native-namespace brutal review

Frozen target: `1246e34b7b2a6eb87546bfab07d6e17d55656331`.

**Verdict: BLOCKING.** One independently reproduced identity-changing publication case; no broader hypothetical scope is claimed.

## F1 — P2: a locally aliased actor spelling can identify a different World actor

Location: `backend/src/grimoire/store/sync.py`, `_require_world_members` (around lines 847–867).

The guard proves that the submitted actor exists in the World and is not detached. It does not prove that the actor selected by that spelling in an owned campaign namespace corresponds to that destination actor. Native case policies can differ between these directories.

Concrete fixture and sequence:

1. Use `fsutil file setCaseSensitiveInfo <Temp World characters directory> enable` on an isolated fixture directory. Create actual distinct World directories `characters/mara` and `characters/MARA`; verify `samefile` is false. Their displayed names are Mara and Winifred respectively.
2. Materialize World `mara` into the campaign's native case-insensitive actor directory. Verify campaign `mara` and `MARA` are the same file identity, and the Off ledger is empty.
3. Confirm `overlay.read_character(cid, "MARA")` resolves Mara, while the World reader for `MARA` resolves Winifred.
4. Edit an owned group so either Members or Leader contains `characters:MARA`.
5. Call `sync.push`, with both ordinary and forced variants.
6. The call publishes that literal reference into the World group. It now names Winifred instead of the campaign's Mara. The assertion that the World group remains untouched fails in all four cases.

The required boundary is identity preservation, not blanket normalization or rejection of uppercase text. When the submitted reference selects an owned actor, determine that selected actor's canonical owned ID and require that this ID and the submitted spelling identify the same destination World actor. If that identity cannot be established, refuse before publication effects. Existing missing/detached checks still apply. The canonical lowercase controls below demonstrate that the mixed policy alone must not prohibit valid publication.

## Independent evidence

Public source was extracted from the exact frozen SHA into `C:/Users/charl/AppData/Local/Temp/spec07-namespace-1246e34`. Tests used snapshot `backend/src` on `PYTHONPATH` and isolated fixture stores. No production source was modified. The only filesystem policy change was inside disposable fixture World directories; the checkout and private library were untouched.

Reviewer-only `test_review_publication_preserves_actor_across_native_namespaces`, parameterized over Members/Leader and normal/forced push: **4 failed**, 176 deselected, exit 1, 2.86 seconds. Every native identity assertion passed, including distinct World actors and aliased campaign actors; none skipped. All failures were the final World group byte-preservation assertion after publication. JUnit: `namespace-attacks.xml` in the snapshot.

Matched `test_review_publication_canonical_actor_control`, using `characters:mara` with the same real mixed-native fixture and the same four field/force combinations: **4 passed**, 180 deselected, exit 0, 1.72 seconds. These prove the canonical reference can still publish. JUnit: `namespace-controls.xml`.

The only warning was the environment's FastAPI TestClient/httpx deprecation warning. Both reviewer test functions are in the Temp membership-store module; no tracked tests were changed by the reviewer.

This is a bounded reproduction of the supplied final-gate finding under the approved S1 identity contract. It does not certify other publication shapes, a global mechanical/full gate, browser behavior, Pydantic variants, or deferred slices. No separate mechanical pass was run for this isolated reproduction request, and no private store was accessed.

## Scoped publication/transfer recheck — frozen 52c5ab3e1

Recheck target: `52c5ab3e1ad64f8461464b5ce2dff23ddc94cbbd`.

**Verdict: NON-BLOCKING for the publication/transfer correction. F1 above is closed at this SHA.** The earlier BLOCKING verdict remains the assessment of frozen `1246e34b7b2a6eb87546bfab07d6e17d55656331`.

`world_identity_matches_owned` now compares the canonical selected IDs inside each namespace, including the reverse check against a distinct existing campaign original. It does not compare a materialized copy's inode with its World ancestor. Members/Leader publication uses this proof through the shared guard in push and promotion. Flat push and the complete accept/reject batch preflight identity before record or manifest effects. Flat promotion resolves the canonical owned source spelling under the campaign hold before selecting its destination.

The frozen execution ledger explicitly permits conservative refusal for ambiguous originals before effects, requiring the alias conflict to be resolved rather than silently choosing an original. The tests below assess that contract; they do not require automatic alias repair or rewrite the authored references.

Independent execution used public source archived from the exact SHA in `C:/Users/charl/AppData/Local/Temp/spec07-transfer-recheck-52c5ab3`, snapshot `backend/src` on `PYTHONPATH`, and isolated stores. Actual `fsutil` directory policies and `samefile` assertions establish the mixed native identities; no native fixture was skipped.

- Prior reviewer Members/Leader alias attacks and canonical-spelling controls, including ordinary and forced pushes: **8 passed**, 191 deselected, exit 0, 3.40 seconds. The previously identity-changing references now refuse, while canonical references still publish. JUnit: `publication-probes.xml`.
- Added independent promotion and whole-batch transfer probes: **6 passed**, 199 deselected, exit 0, 7.36 seconds. Promotion refuses an identity-changing Members or Leader reference with both stores byte-identical. Accept and reject each receive a valid first item followed by an ambiguous second item, in both real native-policy directions. Every refusal preserves both complete stores, including the first record, manifest, and revision state. JUnit: `transfer-probes.xml`.
- Frozen membership-store, library-move, sync-store, and sync-pins selection: **295 passed**, 15 deselected, exit 0, 72.14 seconds. The `not review_` filter excluded 14 reviewer probes and one existing exact-slot-consent test whose name also matched. That existing test was run separately: **1 passed**, exit 0, 6.28 seconds. Thus all **296 frozen tests** in the four modules passed, separately from the 14 reviewer probes. JUnit for the main selection: `transfer-controls.xml`.

The JUnit files are in the recheck snapshot. The sole warning was the environment's FastAPI TestClient/httpx deprecation warning. Source inspection and these adversarial cases established no remaining supported finding in this publication/transfer delta. The check is not a global full-test, browser, Pydantic-variant, or deferred-slice certification. No product or tracked test edits were made by the reviewer, and no private store was accessed.
