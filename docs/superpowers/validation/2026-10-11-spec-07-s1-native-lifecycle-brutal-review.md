# Spec 07 S1 native lifecycle identity: independent focused brutal review

Date: 2026-10-11

Frozen commit: `882cc05d2c47389575136c86d42809512ccaf9d0`.

**Verdict: blocking.** F1's aliased reclassification can discard the effective detachment boundary and overwrite an unrelated World record. The actor reattachment defect is also reachable, with a corrected reproduction that respects the existing name guard. Two adjacent manifest/marker cleanup symptoms were independently confirmed.

This is a focused investigation of F1 from the supplied final-gate result, not a recheck of later repairs or of the other final findings. Applied brutal-review and repository conventions. Read committed source extracted to Temp; all reproductions used disposable synthetic stores on the native Windows filesystem. No product/test edits, commits, private-store reads or application launches.

## 1. Blocking: aliased reclassification strands identity markers and permits a forced overwrite

**Where:** `backend/src/grimoire/store/overlay.py:1298-1311`, called after movement from `backend/src/grimoire/store/reclassify.py:157-161`.

At this commit, the relevant writer is `repoint_record`; there are no separate `repoint_detached` / `repoint_deleted` functions. It rewrites the manifest, whole-record tombstones, asset tombstones and detachment with exact string matches after the source has moved.

**Reproduction:**

1. Create World Realm, dependent Saltmarch, and World group Salt Circle.
2. Save an owned campaign copy whose body is `campaign original`.
3. Delete the World group through `routes.entities.delete_world_entity`. Verify `detached_strict(cid) == {'groups/salt-circle'}`.
4. Prove the owned lowercase path and uppercase `SALT-CIRCLE.md` are the same file. Call `reclassify.campaign_entity(cid, 'groups', 'SALT-CIRCLE', 'lore')`.
5. Create an unrelated World lore record named Salt Circle with body `unrelated replacement`.
6. Call `sync.push(cid, 'lore', landed_id, force=True)`.

Observed:

```text
initial detached: {'groups/salt-circle'}
landed: SALT-CIRCLE
markers after move: {'groups/salt-circle'}
replacement id: salt-circle
World body after forced push: campaign original
```

The lowercase marker remains under the abandoned kind, so the identity-aware push check finds no `lore` detachment. Force, which must only override a content conflict, now overwrites a different World identity.

**Disproof attempt:** The marker was produced by the actual World-delete lifecycle. The alias was proven with native `samefile`, not inferred by casefold. The public reclassification/publication handlers pass these safe IDs through; no route canonicalization rejects this spelling. No Members or actor-validation bypass is required to reach the overwrite.

**Adjacent manifest/asset result, same writer:** Start with an attached materialized World group, a lowercase manifest key `groups/salt-circle`, and valid slot marker `assets/groups/salt-circle/default/map`. Call `reclassify.world_entity(wid, 'groups', 'SALT-CIRCLE', 'lore')`. Both World and owned record move, but the old manifest and old-kind asset marker remain unchanged. Edit the landed World lore body; `sync.incoming(cid)` returns `[]` rather than offering its update. The manifest never follows the original to its new kind.

**Correction seam:** Capture the source's proven native identity and all matching ledger/manifest spellings under the campaign hold **before** `entities.reclassify` removes the old path. Carry that capture to `repoint_record` for manifest drop/carry, record-marker movement, and asset-prefix movement. Cover both campaign movement and World-follow movement, including the actual suffixed landed id. Looking up aliases only after the source moves loses the proof; globally casefolding keys merges distinct records on a case-sensitive volume.

**Regression seams:** `test_group_membership_lifecycle.py` / `test_reclassify_store.py`: real delete -> aliased campaign reclassify -> replacement -> forced push must refuse and preserve replacement bytes; aliased World move must carry the original base and slot marker, and a subsequent World edit must remain discoverable by incoming sync.

## 2. Should fix: successful actor reattachment can retain a native-alias detachment and block Members publication

**Where:** `backend/src/grimoire/store/overlay.py:437-444`; rollback capture at `backend/src/grimoire/store/sync.py:642-653`.

**Reachable reproduction:**

1. Create World character Mara and materialize its campaign copy using the lowercase id.
2. Call the actual `routes.characters.delete_world_character(wid, 'MARA')` handler. The retained campaign copy gets marker `characters/MARA`.
3. Call `sync.promote(cid, 'characters', 'mara')` using the canonical spelling.
4. Create owned Salt Circle with Members `characters:mara` and promote the group.

Observed:

```text
alias World deletion markers: {'characters/MARA'}
canonical actor published: True
markers after successful promotion: {'characters/MARA'}
group promotion refused: groups/salt-circle members names characters:mara,
which is not this campaign's World actor
```

The World actor genuinely is the successfully promoted campaign original, but exact-key `_undetach` leaves its alias marker. The Members publication guard correctly sees that stale marker and refuses the later group.

**Disproof / correction to the original candidate:** Simply creating campaign Mara and promoting it as uppercase `MARA` does not reach `_undetach`: `actor_names.require_unique` first rejects it as a collision with its own lowercase campaign row. That earlier false-conflict is not used as evidence that an uppercase promotion succeeded. The alias-delete/canonical-promote sequence above reaches the stated cleanup failure without mocking or bypassing name validation.

**Correction seam:** Capture proven detachment and asset-marker aliases while the owned actor still exists; clear those aliases on successful reattachment. Update `_reattaching_on_failure` together: its `was_detached = ref in severed` is also exact-only, so merely fixing `_undetach` would remove a marker that the exception path did not remember to restore. Restore the captured original marker identity on failed publication. Keep raw Members/Leader text and bindings unchanged.

**Regression seams:** `test_group_membership_lifecycle.py` and promotion tests: alias World deletion -> canonical actor promotion -> successful Members/Leader group publication; inject a promotion publication failure and verify the captured detachment remains protective. These tests must preserve distinct identities when native samefile proof is absent.

## 3. Should fix: aliased deletion leaves a stale manifest and a listed-but-unopenable group

**Where:** `backend/src/grimoire/store/overlay.py:880-883`, `:1063-1079`, and the exact-marker listing at `:891-897`.

**Reproduction:** Create World Salt Circle, materialize its lowercase owned copy, then call `overlay.delete_entity(cid, 'groups', 'SALT-CIRCLE')` (the delete route passes the supplied ID directly).

Observed: the owned file is removed, but `sync.md` retains `groups/salt-circle`; `deleted.json` contains `groups/SALT-CIRCLE`; `overlay.list_entities(cid, 'groups')` still returns `salt-circle` from the World.

**Important limit:** The specialized `read_group_membership` endpoint does use native identity matching and refuses the canonical group read. Thus the supported user-facing symptom is a ghost listed row / inconsistent deletion, not a claim that the specialized Members GET reveals the deleted group. The ordinary `read_entity` still uses exact markers, but a broad audit of all legacy display readers is outside this investigation.

**Correction seam:** Capture the original's proven identity before unlink, use it when removing manifest keys and publishing the deletion marker, and preserve the strict preflight/lifetime retirement ordering. Apply the same capture discipline to `_undetach` callers that remove the owned file before cleanup. Avoid re-proving identity from a vacated slug that a replacement could later claim.

**Regression seams:** delete through a native alias, then assert no stale manifest, no listed group, specialized GET remains refused, and the original Members offer remains retired. Include detached-original deletion with a same-slug World stranger to ensure it is not wrongly tombstoned by exact-only `_inherits_world` decisions.

## Verification and boundaries

Configured frozen lint: `python scripts/ratchet.py ruff` passed, **1,146 findings at baseline**.

Existing frozen tests, isolated fixture homes and pytest cache disabled:

```text
backend/tests/test_group_membership_lifecycle.py
backend/tests/test_group_membership_store.py
backend/tests/test_reclassify_store.py
backend/tests/test_sync_store.py
287 passed, 1 existing FastAPI/Starlette warning (39.25s)
```

The passing existing tests do not cover the reproduced spelling transitions. All three reproduction families above ran independently against frozen sources on this host. No correction was implemented during review.

No spec amendment is required to retain identity markers, manifest ancestry and successful reattachment semantics across proven native aliases. The fix should operate at the owning lifecycle boundary rather than redefine raw actor-reference equality.

## Not reviewed

The other final-gate findings (image-promotion root capture and rebound-dependent scopes), later/live repairs, full S1 UI or full-gate completion, every unrelated marker/display store, performance and crash recovery beyond the stated correction seams. No physical case-sensitive filesystem was used in this pass; preserving distinct identities is a required regression seam, not claimed execution evidence. The optional python-code-doctor analyzer was not run; configured lint ran and passed. The separate final whole-S1 gate remains required.

---

## Independent repair recheck at 0a4c59535

Date: 2026-10-11. Frozen commit: `0a4c59535a972e4591bb97f6029fb8b3e827d182`.

**Latest scoped verdict: blocking.** The original ordinary native-alias failures above are repaired, including promotion rollback. A new physically reproduced mixed-filesystem ownership failure still permits an unrelated World overwrite. The supposedly case-sensitive regression also fails when actually run on a case-sensitive directory because its fixture lacks one expected marker.

### Resolved at this frozen repair

Independent original scripts now report:

```text
group move: salt-circle; detached: {'lore/salt-circle'}
forced push refused; unrelated World replacement body unchanged
canonical actor promotion after alias World deletion: marker set empty
following group publication: Members characters:mara succeeds
World move: manifest key and asset marker follow lore/salt-circle
World edit after move: incoming reports lore/salt-circle update
aliased group delete: manifest empty; canonical tombstone; list empty
```

A separate publication-failure injection asserted the alias detachment had already cleared when the actor's World commit failed. The exception path restored exactly `{'characters/MARA'}`; no World actor commit existed. The rollback now remembers the original marker set rather than an exact-spelling boolean.

### New blocking finding: a World-root alias proof overrides distinct owned identities

**Where:** `backend/src/grimoire/store/overlay.py:1140-1143` and `:1165-1183`.

`capture_record_refs` asks `record_is_marked` to consider both campaign and World roots. A positive match in the other layer is treated as proof about the selected owned source even when the two owned files are proven distinct.

**Physical reproduction, not a mock:** In a disposable Temp store, enable the Windows per-directory case-sensitive flag on `campaigns/saltmarch/groups` using `fsutil file setCaseSensitiveInfo ... enable`. Leave the World groups directory case-insensitive. This host successfully supports the flag.

1. Create World `groups/salt-circle.md` with body `Seraphine keeps the roster.` and materialize its lowercase campaign copy.
2. Create a distinct owned `groups/SALT-CIRCLE.md` with body `Winifred keeps the roster.`, and mark that uppercase original detached. This is the same distinct-case fixture shape used by the new control test, on a real supporting filesystem.
3. Verify owned lowercase/uppercase paths have `samefile == False`, while their World spellings have `samefile == True`.
4. Capture the lowercase source and reclassify only that lowercase owned original to lore.
5. Force-push the still-present uppercase owned group to the World.

Observed:

```text
Own distinct: True
World aliases: True
Captured LOWER source refs: ['groups/SALT-CIRCLE', 'groups/salt-circle']
Markers after moving LOWER: {'lore/salt-circle'}
Unmoved UPPER still exists: True
World overwritten by unmoved UPPER: Winifred keeps the roster.
```

The uppercase detachment belonged to the unmoved original. Capturing it through the World directory steals its marker for the lower source's relocation. The subsequent force push finds no remaining group detachment and overwrites the unrelated World record. `_inherits_world` also uses the same cross-layer predicate, so a late filter in `repoint_record` alone would leave the identity decision inconsistent.

**Disproof attempt:** Both identities and the differing policies were checked with actual `samefile` calls. No monkeypatch, guessed casefold, private content or unsupported linked record is involved. The input files use valid IDs and the same direct fixture creation pattern as the repository's distinct-case control. The API/store accepts both IDs on that filesystem.

**Specific correction:** Establish identity in the layer that owns the selected original. A positive World alias must not override a proven distinct campaign candidate or capture a marker belonging to an independently owned candidate. Preserve World-only alias support when the record is genuinely inherited; do not solve the issue by rejecting all World-root proofs. Carry this owner-specific result through `_inherits_world`, source capture, reclassification and cleanup. Add a mixed-policy regression that asserts the unmoved uppercase original keeps its marker and cannot force-overwrite the World. Include the inverse ownership arrangement and missing-source cases as controls where appropriate.

Exact independent scratch reproduction: `C:/Users/charl/AppData/Local/Temp/grimoire-mixed-native-policy.py`. It ran against the frozen Temp snapshot and creates/removes only its disposable store.

### Should fix: case-sensitive control lacks the source marker it expects to move

**Where:** `backend/tests/test_group_membership_store.py:1035-1047`.

The normal Windows run skips `test_record_reclassification_keeps_distinct_case_sensitive_identity`. Running that exact test under a physically case-sensitive Temp base instead fails: expected `{'groups/SALT-CIRCLE', 'lore/salt-circle'}`, actual `{'groups/SALT-CIRCLE'}`.

This is a fixture precondition defect, distinct from the production overwrite: `overlay.create_entity` for a flat group does not add the lowercase detachment; the test adds only the uppercase marker and then expects both. Explicitly establish the lowercase detachment too, or use the real delete-detach lifecycle for that source. Do not weaken the final expected marker set or treat the test as passing because it skipped on the usual directory.

### Recheck evidence and limits

Frozen configured ratchets: **ruff 1,146 / mypy 161**, both at baseline.

Frozen standard-directory run of `test_group_membership_store.py`, `test_group_membership_lifecycle.py`, `test_reclassify_store.py`, and `test_sync_store.py`: **297 passed, 1 skipped**, one existing FastAPI/Starlette warning (36.58s).

Physical case-sensitive run of the skipped control alone: **1 failed**, for the missing fixture precondition described above. Independent ordinary-native repaired scenarios and promotion rollback passed. The mixed-policy native reproduction failed the required identity invariant and demonstrated the overwrite.

No product/test changes were made. Only this authorized artifact was appended. Not reviewed: later repairs, all final-gate scope findings, full S1/UI certification, or full-gate completion. Unlike the earlier checkpoint, this pass includes actual physical case-sensitive and mixed-policy NTFS evidence. The optional analyzer was not run; configured mechanical passes did run.

---

## Independent native namespace recheck at 11d0ca049

Date: 2026-10-11. Frozen commit: `11d0ca04931d045b1dba6b5ea3eaecabdbf3deab`.

**Latest scoped verdict: blocking.** The previously demonstrated mixed-policy overwrite and the broken case-sensitive fixture are repaired. The inverse physical namespace layout still moves a tombstone belonging to another World record and loses the campaign's saved hide choice. Actor appearance-key bookkeeping is explicitly outside this checkpoint.

### Confirmed resolutions and preservation checks

The original mixed-policy script (case-sensitive campaign groups, insensitive World groups) now captures only `groups/salt-circle`; the unmoved distinct `groups/SALT-CIRCLE` keeps its detachment. Forced push of that original is refused, and the World body stays `Seraphine keeps the roster.`. This was rerun with real per-directory Windows case sensitivity and actual samefile checks.

The corrected distinct-case control now runs and passes on this host: its empty fixture directory is made case-sensitive before creation, and the lowercase detachment expected to move is explicitly established. Inherited-alias deletion/reclassification regressions preserve the distinct owned lowercase original. The four unsafe greeting/actor slash and backslash traversal cases refuse and preserve the campaign tree. Ordinary native alias actor publication failure still restores exactly `{'characters/MARA'}` in an independent fault-injection rerun.

Source inspection confirms owned-source canonicalization happens before destructive effects and inherited operations retain the submitted source spelling rather than reselecting an unrelated owned canonical spelling. These fixes address the preceding checkpoint's concrete cases.

### Remaining blocking finding: inverse namespace policies steal another World record's tombstone

**Where:** `backend/src/grimoire/store/overlay.py:455-469`, `:1139-1148`, and `:1194-1201`.

A whole-record deletion marker describes the hidden World record. Choosing the currently owned record's namespace for every marker proof still merges distinct World records when the namespace policies are reversed.

**Physical reproduction:**

1. Create a disposable Realm/Saltmarch store. Enable case sensitivity on the empty **World** groups directory; leave the campaign groups directory case-insensitive.
2. Create two distinct World files: `groups/salt-circle.md` with body `Seraphine keeps the roster.`, and `groups/SALT-CIRCLE.md` with body `Winifred keeps the roster.`. Prove their `samefile` result is false.
3. Delete inherited uppercase `SALT-CIRCLE` through `overlay.delete_entity`. This correctly records `groups/SALT-CIRCLE` in Gone; the lowercase World record is distinct and visible.
4. Materialize lowercase `salt-circle` into the campaign. In that directory, uppercase/lowercase now resolve to the same owned file.
5. Reclassify only the lowercase campaign original to lore.

Observed:

```text
Hidden unrelated upper before: {'groups/SALT-CIRCLE'}
Capture lower source: ['groups/SALT-CIRCLE', 'groups/salt-circle']
Hidden markers after: {'lore/salt-circle'}
Groups now listed: ['SALT-CIRCLE', 'salt-circle']
Upper World unchanged: Winifred keeps the roster.
```

The uppercase tombstone moves away even though its distinct World original did not move. That hidden record becomes visible again. `_inherits_world` also treats the lowercase source as already hidden by this unrelated uppercase marker, so it fails to add the lowercase source tombstone expected after campaign reclassification; the old lowercase World group reappears too. The World bytes are unchanged, but the user's persisted deletion/hide decision is lost and the campaign presents the moved content under both kinds.

**Disproof attempt:** This is the inverse of the repaired layout, tested with real NTFS per-directory policy, not a mock or casefold guess. The tombstone is created by the actual inherited deletion operation before the owned lowercase copy exists. The two World files are proven distinct. Merely taking aliases only from the selected owned root therefore cannot justify moving that tombstone.

**Specific fix / regression seam:** Preserve the meaning and owner of each marker when proving a lifecycle match. For whole-record Gone markers, a positive owned alias must not override proof that the candidate identifies a different extant World record. Apply this distinction both to pre-move source capture and the `would_inherit` / `_inherits_world` decision; fixing only the final rewrite still leaves the missing source tombstone. Keep native aliases supported when the source and marker truly share identity. Add this inverse mixed-policy sequence with assertions that the unrelated uppercase Gone remains, the lowercase old source is hidden after its move, and only the intended lore original appears.

Exact scratch reproduction: `C:/Users/charl/AppData/Local/Temp/grimoire-inverse-native-policy.py`. No production/test edits were made.

### Verification and limits

Frozen configured checks: **ruff 1,146 and mypy 161**, all at baseline.

Frozen owner tests (`test_group_membership_store.py`, `test_group_membership_lifecycle.py`, `test_reclassify_store.py`, `test_sync_store.py`) excluding only `native_actor_promotion_keeps_version_locked_sync_flow`: **305 passed, 1 deselected**, one existing warning (49.02s). There were no native-policy skips in this run. The exclusion follows the author's explicit unfinished actor appearance-bookkeeping scope; it is not a passing claim for that test.

Independent probes confirm the preceding mixed-policy repair and rollback, and reproduce the inverse-policy failure above. Not reviewed: later repairs, pending actor appearance-key changes, final S1/UI/full-gate certification, or broader unrelated marker readers. This checkpoint cannot close the native lifecycle gate while the inverse-policy marker loss remains. The optional analyzer was not run; configured mechanical checks ran and passed.

---

## Independent owned/World identity split recheck at 11e96e0c8

Date: 2026-10-11. Frozen commit: `11e96e0c83eb5ac3275cfa2b44db75e8fed335d5`.

**Latest scoped verdict: non-blocking, with one should-fix asset-slot residual.** Both physical mixed-policy data-loss findings are resolved. The saved appearance-alias promotion refusal matches the explicit Task 5 ruling and occurs before publication in the tested path. This closes those specific blockers, not final S1 or full-gate certification.

### Confirmed resolutions

The `RecordIdentityRefs` split preserves the owners of the keys: owned Off/base aliases are proved in the campaign namespace; Gone/asset aliases are proved in the World namespace. Extra requested spellings are admitted through each namespace's proof rather than blindly unioned. Both sets are captured before movement destroys the source paths and used separately by repoint/cleanup. `_inherits_world` checks Gone in the World namespace.

Both independent physical mixed-policy scripts were rerun, changing only diagnostic printing for the new DTO:

- **Campaign sensitive / World insensitive:** the lower capture excludes the distinct owned uppercase detachment. After moving the lower record, the upper original keeps its marker; forced push is refused and the World body stays unchanged.
- **World sensitive / campaign insensitive:** the lower capture excludes the distinct uppercase World tombstone. After moving the lower campaign original, Gone contains both old World groups, neither group is listed, and the distinct uppercase World bytes are unchanged.

The independent failed-promotion probe also still restores exactly `{'characters/MARA'}` after the alias marker clears and the World actor commit raises. Existing native regressions confirm canonical base movement, asset slot movement while the World source exists, canonical deletion cleanup, inherited-source preservation and unsafe-ID refusal.

### Saved appearance lock ruling

Read the explicit Task 5 ruling: an appearance lock saved under a native alias different from the owned canonical ID refuses promotion rather than silently migrating historical role keys. `_promote_actor` canonicalizes only the owned source, inspects saved appearance refs using native proof in that owned namespace, and raises `GroupIdentityConflict` before creating/publishing the World destination or reattaching the actor.

Both branches of `test_native_actor_promotion_keeps_version_locked_sync_flow` passed in the frozen reviewer run. The saved-alias branch snapshots both stores and confirms refusal leaves their files byte-for-byte unchanged. The ordinary canonical-lock branch accepts an aliased promotion request, keeps one canonical sync flow, advances World-version sync, and retains the appearance version lock after take-world. This supports the stated ruling; it does not claim an automatic alias-key migration.

### Should fix: an aliased asset tombstone survives its removed World source and affects a successor

**Where:** `backend/src/grimoire/store/overlay.py:2315-2320` (authored asset marker), `capture_record_refs` / `repoint_record` (World-only slot proof after source removal), and `image_root` / `list_images` (observable leftover).

**Concrete ordinary native-Windows sequence:**

1. Create World Salt Circle and its `map` image; materialize the group in Saltmarch.
2. Call `overlay.delete_image(cid, 'SALT-CIRCLE', 'default', 'map', 'groups')`. This genuine image operation records `assets/groups/SALT-CIRCLE/default/map`.
3. Delete the World group through its real delete route, retaining the owned original. Move that owned original to lore.
4. Recreate an unrelated World Salt Circle and give it a new `map` image.

After the World source is removed, the lowercase source's World identity set cannot prove the old uppercase asset-parent alias. The marker remains under `assets/groups/SALT-CIRCLE/default/map` instead of following or being retired with the original. The actual public image-list handler then returns:

```text
Replacement canonical images: ['map']
Replacement native-alias images: []
Replacement alias image serving selects empty campaign: True
```

Thus this is not merely an invisible stale ledger entry: a valid native alias request hides the successor's image and its serving path selects an absent campaign image. The canonical URL remains usable, and no record bytes are overwritten, so this is ranked **should fix**, not another blocking data-loss finding.

**Disproof and safe correction seam:** The slot marker was written by the existing public store image-deletion operation, not fabricated. The successor effect was checked through `routes.entities.list_campaign_entity_images` and the same `image_root` used by the serving route. Do not repair this by unioning the DTO sets or letting campaign aliases override proven distinct World identities; that reopens the fixed inverse theft. Canonicalize the authored marker from the actual World image/record identity while it exists, or capture/normalize its proven World aliases at World deletion before the source disappears. Retain tests for both physical mixed directions.

Independent scratch reproduction: `C:/Users/charl/AppData/Local/Temp/grimoire-asset-alias-successor.py`. This residual is within the requested asset-key preservation check.

### Verification and limits

Frozen configured checks: **ruff 1,146 / mypy 161**, all at baseline.

Frozen owner suites (`test_group_membership_store.py`, `test_group_membership_lifecycle.py`, `test_reclassify_store.py`, `test_sync_store.py`): **308 passed**, no skips or exclusions, one existing warning (47.04s). The physical distinct/mixed-policy cases and both saved appearance-lock branches actually ran. Independent scripts verified both original counterexample closures, exact rollback restoration, and the narrow asset successor symptom above.

Only this authorized artifact was appended; no production/tests were edited and no private data was accessed. Not reviewed: later changes, all final S1/UI behavior, full `make check`, unrelated reader/marker stores, or general appearance-key migration. The optional analyzer was not run; configured mechanical checks ran and passed. A final whole-S1 independent gate remains required.

---

## Focused final-gate reproduction: wrong owned original follows a World move

Date: 2026-10-11. Frozen commit: `1246e34b7b2a6eb87546bfab07d6e17d55656331`.

**Latest verdict for this specifically requested seam: blocking.** Independently confirmed the final native gate's World-follow identity failure. This investigation does not expand into the other reported final-gate seam.

**Where:** `backend/src/grimoire/store/reclassify.py:232-245` and `:343-357`.

The owned/World marker-set split fixes which keys follow a selected original. It does not yet establish that the owned record selected by `has_own_copy` actually belongs to the World original being moved. A case-insensitive campaign lookup can select a different edited original than the case-sensitive World source.

### Physical reproduction

A synthetic Temp World groups directory was made case-sensitive before file creation; the campaign directory retained its native case-insensitive policy.

1. Create distinct World `groups/salt-circle.md` and `groups/SALT-CIRCLE.md`, with different placeholder bodies.
2. Save the lowercase group's campaign copy with edited body `Mara keeps the roster.` and Members `characters:mara`, using `journalled_group_members`. Preserve the owned bytes and its `groups/salt-circle` Members Undo target.
3. Verify the World paths are distinct, but an uppercase lookup in the campaign resolves to the lowercase owned file.
4. Call `reclassify.world_entity(wid, 'groups', 'SALT-CIRCLE', 'lore')`.

Observed:

```text
Distinct World records: True
Owned uppercase lookup aliases lower: True
Owned canonical stem: salt-circle
Before Undo target: groups salt-circle
World move returned: {'id': 'SALT-CIRCLE', 'campaigns': ['saltmarch']}
Wrong owned original survives in groups: False
Moved file is wrong edited original: True
Unselected World lower unchanged: True
After Undo target: lore SALT-CIRCLE
Original Members Undo now refuses: this record is no longer a group
```

The lowercase World original was not selected and did not move, yet its edited campaign copy did. Its Members Undo offer is suspended and completed against the wrong relocation, becoming unavailable as a nongroup target. No detachment is needed: the campaign lower record is a legitimate edited copy of the distinct lowercase World original.

**Disproof attempt:** Native `samefile` proves both the World distinction and the campaign alias; the owned canonical stem is independently resolved before movement. The before/after byte comparison proves the moved file is the lower edited original, not a newly materialized copy of the selected uppercase World group. The journal row is reread after the move and an actual Undo call confirms the wrong-kind refusal. No name-validation bypass, mocked filesystem identity, private data or linked record is involved.

Exact independent scratch reproduction: `C:/Users/charl/AppData/Local/Temp/grimoire-world-follow-native.py`, run against `C:/Users/charl/AppData/Local/Temp/grimoire-review-1246e34b7`.

### Earliest correction and regression seam

Under `world_entity`'s existing sorted hold, after validating/canonicalizing the selected World source and before building aliases or calling `journal.begin_membership_relocation`, verify each apparent owned follower belongs to that selected source:

- Resolve the actual owned file's canonical stem in the campaign namespace.
- In the **World namespace**, prove that stem names the selected World source (native samefile proof, not casefold).
- A proven distinct or absent counterpart must not permit that owned original to be suspended, moved or repointed for this World operation. Preserve ordinary insensitive aliases that do prove the same World file.

The filter belongs in the construction of `following`. Adding a check only in `_follow_in_campaign` after the World move would leave the unrelated original's offer suspended. The captured alias/ledger plan must be built only after this ownership decision.

Pin the exact physical sequence with assertions that the lowercase owned path and bytes remain, its lower manifest/markers and Members offer retain their identity, and Undo remains available for that original. The selected uppercase World record should still move normally. Keep the existing ordinary native-alias World-follow positive control.

### Evidence and limits

Frozen configured ruff passed: **1,146 findings, all at baseline**. The isolated native script above is the verification for this narrow reproduction; no additional broad pytest or mypy run was performed in this investigation. Earlier tests at other frozen commits are not reused as evidence for this head. Only the authorized review artifact was appended; no production/test edits or commits.

Not reviewed here: the other final-gate finding, later fixes, the prior non-blocking asset-slot residual, final whole-S1 integration or full-gate completion. This additional finding prevents treating the earlier narrower namespace closure as a final S1 approval.

## Adjacent World-delete probe at frozen 1246e34b7

Frozen SHA: `1246e34b7b2a6eb87546bfab07d6e17d55656331`. This is a single requested adjacent identity probe, not review of the implementer's uncommitted corrections.

**Blocking identity regression confirmed.** With distinct `groups/salt-circle.md` and `groups/SALT-CIRCLE.md` in a physically case-sensitive World directory and only an edited lowercase owned group in a case-insensitive campaign, `delete_world_entity(wid, "groups", "SALT-CIRCLE")` removes the selected World uppercase record but `_forget_in_campaign` mistakes the campaign's uppercase lookup for ownership of that selected record. It authors `groups/SALT-CIRCLE` into Off. Native campaign identity matching consequently detaches the unrelated surviving lowercase group: `independent_owned_record(cid, "groups", "salt-circle")` becomes true and `sync.push(cid, "groups", "salt-circle", force=True)` raises `NotInLibraryError`.

Reproduction: `C:/Users/charl/AppData/Local/Temp/grimoire-world-delete-native.py`, executed successfully against the frozen Temp snapshot using real `fsutil` directory case policy and a disposable synthetic store. Positive controls prove distinct World files and aliased campaign lookup. The unselected World file and owned bytes remain unchanged. The lowercase manifest base remains unchanged: `_drop_manifest_ref` receives uppercase and therefore does not remove that exact lowercase key. The Members journal entry also remains unchanged, and its Undo succeeds. Incoming still reports the lower World edit as a conflict. The supported consequence is wrongful detachment and publication refusal, not lost bytes, dropped lowercase base, or invalidated Undo.

Correction belongs to the World-delete owner before its first effect: `routes/entities.py:461` enters the held `world_identity_change` region, then deletes at line 462 and invokes cleanup at line 465. Capture selected-source versus canonical-owned identity there while both World records exist; pass eligibility or the captured identity into cleanup. `_forget_in_campaign` (`overlay.py:2991`, own-presence branch at line 3006) cannot reconstruct native proof against an already deleted source. Preserve the unrelated lowercase group's Off/base/offer. Reuse the native World-namespace comparison used by the reclassification repair rather than blindly trusting campaign alias existence. No production or repository test files changed; no additional general gate claimed.

## Held World-source identity final scoped recheck

Reviewed immutable commit `52c5ab3e1ad64f8461464b5ce2dff23ddc94cbbd`, archived at `C:/Users/charl/AppData/Local/Temp/grimoire-review-52c5ab3e1`. **Scoped verdict: non-blocking; the wrong-original World movement and adjacent deletion findings above are resolved.** No concrete remaining blocker was found in this correction.

Independent reproduction `C:/Users/charl/AppData/Local/Temp/grimoire-world-native-refusal-recheck.py` retains the original physical Windows mixed-policy setup: distinct lowercase/uppercase World group files, a case-insensitive campaign with the edited lowercase original and live Members offer. Both uppercase World reclassification and actual entity-delete route now raise `GroupIdentityConflict`. File paths and bytes throughout both roots remain identical after each refusal, including the lowercase base, Off state and journal entry. Calling the original Members Undo afterward succeeds. This is positive native filesystem evidence, not a simulated casefold assumption.

The fix is at the necessary release boundary. `world_identity_matches_owned` (`overlay.py:1166`) proves canonical source IDs within the World namespace and checks the reverse campaign identity when present. `_following_campaigns` calls the scope guard before alias capture or journal suspension. `world_identity_change` performs the same scope check after acquiring and filtering its dependent hold, before yielding to removal. All four record-delete routes (characters, entities, greetings, PCs) supply their actual kind/id and invoke no removal before that context yields. `_prepare_demote` checks the entire held identity scope before copy-down/removal. This implements the execution ledger's explicit conservative-refusal ruling: ambiguity blocks the operation rather than silently excluding a campaign and stranding its references.

Frozen verification: `test_group_membership_store.py`, `test_reclassify_store.py` and `test_group_membership_lifecycle.py`: **295 passed**, no skips, one existing Starlette deprecation warning, 87.87 seconds. These include physical mixed-policy move/delete/demote refusals, canonical World movement and edited-copy preservation, valid native alias World reclassification with manifest/asset relocation, actor reattachment rollback, and both namespace directions. Configured `scripts/ratchet.py ruff`: **1146 findings, all at baseline**. Mypy was not independently rerun for this checkpoint. All stores were synthetic Temp fixtures; no product or repository test edits were made.

Not reviewed/certified here: whole-S1 completion, the full repository gate, uncommitted successor work, or general external-editor concurrency. The previously documented asset-alias successor issue remains deferred and is not claimed resolved. The additional three delete routes were inspected for exact pre-effect guard placement; this checkpoint's independent route attack exercised group entity deletion. This scoped verdict does not replace the final whole-slice gate.
