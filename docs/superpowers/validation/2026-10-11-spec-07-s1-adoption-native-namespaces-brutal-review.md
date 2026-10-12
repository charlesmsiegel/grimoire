# Spec 07 S1 adoption native-namespace brutal review

Frozen target: `52c5ab3e1ad64f8461464b5ce2dff23ddc94cbbd`.

**Verdict: BLOCKING.** One shared adoption/restoration defect reproduces across inherited diagnostics, first-copy, and Members Undo. The separate publication/transfer recheck at this SHA remains limited to its stated scope.

## F1 — P2: unresolved-slot calculation misses a native identity mismatch

Frozen location: `backend/src/grimoire/store/overlay.py:777–788`, `_unresolved_group_slots`, as consumed by `_require_group_copy`, `read_group_membership`, and `group_unresolved`/Members restoration.

The calculator treats a raw actor slot as unresolved only when it is detached or its World meta file is absent. With no Off markers, a real World actor can exist under the submitted spelling while that spelling selects a different owned actor in the campaign. Presence alone therefore does not establish safe adoption of the World slot.

Independent fixture uses actual `fsutil` case-sensitive directories and native `samefile` assertions in both directions:

- Case-sensitive World has distinct `characters/mara` and `characters/MARA`; the campaign materializes lowercase Mara in a case-insensitive actor directory. Raw `characters:MARA` identifies the distinct World actor but the owned lowercase Mara.
- Case-sensitive campaign has distinct owned lowercase and uppercase actors; the case-insensitive World aliases those spellings. The same submitted token again selects different actor identities across the roots.

Each fixture asserts an empty Off ledger, proves the physical alias/distinct relationships, and verifies the campaign and World readers return different actor names for `MARA`. No actor resolution is mocked.

Concrete observed sequences:

1. Create an inherited group whose Members or Leader is `characters:MARA`. `read_group_membership` reports no unresolved Member or Leader diagnostic in either native-policy direction.
2. Perform a body-only `overlay.update_entity` with no reviewed slots. It publishes an owned group retaining the mismatched raw token instead of requiring consent. This changes the actor selected by the copied group's slot.
3. For Members, start from the same inherited group and clear Members inside the real `journalled_group_members` context with empty bindings. Clearing succeeds. Undo then restores `characters:MARA` into the owned group, without a recorded binding, despite its different owned actor identity. The test allows either an explicit conflict or conservative omission; it fails only because the token is actually restored.

Leader-specific Undo is not claimed: the scoped reversal is for Members. The GET-context and body-only copy reproductions cover both fields.

## Exact independent evidence

Public frozen source remained unchanged in `C:/Users/charl/AppData/Local/Temp/spec07-transfer-recheck-52c5ab3`. Tests ran with snapshot `backend/src` on `PYTHONPATH` and isolated stores. Reviewer probes were appended only to the Temp membership-store test module.

- `test_review_adoption_context_identifies_native_mismatch`: four failed cases (Members/Leader × both native-policy directions), because diagnostics were empty.
- `test_review_body_copy_refuses_native_actor_rebind`: four failed cases in the same matrix, because no `GroupIdentityConflict` was raised.
- `test_review_clear_then_undo_does_not_adopt_native_actor_alias`: two failed cases, one per policy direction, because the mismatched raw member was restored.
- Combined attack run: **10 failed**, 205 deselected, exit 1, 4.42 seconds; no native fixture skipped. JUnit: `adoption-attacks.xml`.
- `test_review_canonical_first_copy_control`: the same four field/policy fixtures using `characters:mara` report no unresolved diagnostic and copy successfully. **4 passed**, 215 deselected, exit 0, 2.02 seconds. JUnit: `adoption-controls.xml`.

The JUnit files are in the snapshot directory. The sole warning was the environment's FastAPI TestClient/httpx deprecation warning.

The shared unresolved boundary needs to account for the existing captured-root identity proof before an inherited slot becomes owned or is restored. This observation does not require case folding, actor roster/body reads, or broader discovery coordination. The canonical controls show that valid identity-preserving adoption must remain available.

This is a bounded reproduction of the supplied adoption/restoration finding under the approved S1 contract. No product or tracked test edits were made, no private store was accessed, and no global gate or later-fix success is claimed.

## Scoped independent adoption recheck — frozen 62270523a

Recheck target: `62270523a6315871d056c158a4df170402c8d7a1`.

**Verdict: NON-BLOCKING for the adoption/restoration correction. F1 above is closed at this SHA.** The preceding BLOCKING verdict remains the assessment of frozen `52c5ab3e1ad64f8461464b5ce2dff23ddc94cbbd`.

The shared unresolved-slot calculator now receives the captured campaign and World roots and applies the same native identity proof used for publication. First-copy validation, inherited membership context, and `group_unresolved` all reach that calculator. The public campaign-ID proof wrapper remains available to held writers, while this path uses the private captured-root proof directly. This preserves the selected view and does not introduce actor body/roster reads or case folding.

Independent checks used the exact frozen source archive at `C:/Users/charl/AppData/Local/Temp/spec07-adoption-recheck-6227052`, snapshot `backend/src` on `PYTHONPATH`, and isolated stores. Actual filesystem policy changes were confined to disposable fixture directories. No native fixture skipped.

- The ten former failing context/body-copy/Members-Undo cases plus four canonical first-copy controls: **14 passed**, 213 deselected, exit 0, 23.58 seconds. Both native-policy directions now report the mismatch, refuse unreviewed copying, and avoid restoring the mismatched inherited member through Undo; canonical copies remain available. JUnit: `adoption-probes.xml`.
- Additional independent exact-consent and demotion probes: **8 passed**, 227 deselected, exit 0, 4.56 seconds. In both policy directions and for both Members and Leader, wrong-field consent is refused inside the real journalled edit context with all campaign/World/journal bytes unchanged. The exact field/actor consent then permits the copy. Unconsented demotion with another dependent preserves every file in the isolated home. JUnit: `adoption-consent-probes.xml`.
- Frozen membership-store, membership-lifecycle, membership-route, and Undo-store regression modules: **338 passed**, 22 reviewer probes deselected, exit 0, 96.25 seconds. JUnit: `adoption-owning-controls.xml`.

All JUnit files are in the recheck snapshot. The only warning was the environment's FastAPI TestClient/httpx deprecation warning. No supported remaining finding was established in this bounded adoption/restoration correction. No production or tracked test files were changed by the reviewer; no private store was accessed. This is not a global gate, browser, Pydantic-variant, or deferred-slice certification.
