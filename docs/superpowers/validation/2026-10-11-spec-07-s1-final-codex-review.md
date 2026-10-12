# S1 final spec coverage review

Frozen branch: `41da96e4d993f6b9699aabe1f86cb00ee304f5b3`; actual installed Codex thread: `01a12def-f74b-7af2-84fa-d45ef5af1df4`.

Verdict: **needs-attention**. The native runtime used immutable intended-public
tracked source snapshots because its sandbox helper is unavailable. This is
the actual installed adversarial prompt and structured schema, not a substitute
reviewer.

Do not ship yet: demotion can lose retained groups or copy into a different campaign scope, and native group aliases bypass identity markers. This is a static assessment; full-gate completion is unverified.

## Recheck copy existence inside the demotion hold

dependents() captures has_copy before hold_all. If sync.accept removes that copy before demotion acquires its locks, preflight correctly sees an inherited group, but this stale has_copy branch skips materialization. Demotion then deletes the World record, leaving the campaign without the group despite copy_down=True. This violates S1's requirement to check copy existence inside the publication hold.

Owning file: `backend/src/grimoire/store/sync.py`, lines 1324-1327.

Recommendation: Remove the stale has_copy shortcut and invoke the locked materializer for every selected dependent; it already preserves existing owned copies. Add an interleaving regression with take-world between discovery and lock acquisition.

## Validate demotion targets against the captured locked scope

deps comes from an earlier enumeration than held. A dependent campaign deleted and recreated on another World between those enumerations remains in deps but disappears from held. Preflight then acquires its lock individually while other campaign locks are held. If the replacement World contains the same group ID, source_text from the original World can be published into that replacement campaign and assigned as its sync base. Neither the copy loop nor preflight rechecks the campaign's World binding.

Owning file: `backend/src/grimoire/store/sync.py`, lines 1287-1289.

Recommendation: Use one captured campaign set, acquire its sorted holds, and revalidate each selected campaign's current World identity before effects. Never process stale targets outside that held set. Add campaign-recreation/rebinding regressions; no S5 discovery barrier is required.

## Prevent native aliases from bypassing detached-group publication

On a case-insensitive store, a retained campaign group marked groups/salt-circle can resolve through SALT-CIRCLE. If the World recreates an unrelated salt-circle, forced push using the uppercase ID bypasses this exact-string detachment check. The Members/Leader guard checks actor identities only, so valid actor refs do not prevent the unrelated World group from being overwritten.

Owning file: `backend/src/grimoire/store/sync.py`, lines 1124-1130.

Recommendation: Check the strict detachment set with overlay.record_is_marked before publication, as sync advance already does. Test forced push through a native alias against a detached retained group and a recreated World record.

## Honor group tombstones through native ID aliases

On a case-insensitive store, deleted.json may contain groups/salt-circle while PUT targets groups/SALT-CIRCLE. The World path resolves to the hidden record, but this exact-string tombstone lookup misses it and publishes an owned copy. A body-only request can therefore resurrect a deleted group. The inherited GET and materialization preflight use the same exact-string lookup.

Owning file: `backend/src/grimoire/store/overlay.py`, lines 844-849.

Recommendation: Use native record-marker matching for tombstones in inherited group reads, preflight and first-copy publication. Add native-alias GET/PUT/materialization tests requiring refusal without record or manifest changes.

No approval is inferred. Fixes, regressions and actual recheck evidence are
recorded separately; the final full gate remains required.

## Actual recheck at882cc05d2

Thread `01a12e23-437b-7493-8f2f-b871d433a23c`; frozen `882cc05d2c47389575136c86d42809512ccaf9d0`. Verdict **needs-attention**. Prior four findings accepted as resolved; adjacent lifecycle boundaries yielded these new supported findings.

### Preserve detachment through native-alias reclassification

On a case-insensitive store, reclassify detached groups/salt-circle through SALT-CIRCLE. The caller constructs old_ref from that spelling, but these rewrites match only exact keys. The canonical detachment remains under groups instead of following the landed lore record. If the World subsequently creates lore/<landed id>, forced push finds no matching detachment and can overwrite that unrelated record. The same exact-key cleanup in _undetach also leaves an actor promoted through an alias detached, preventing subsequent Members publication.

Correction: Capture the proven native source identity before movement or reattachment and carry it through marker and manifest updates. Test aliased reclassification followed by forced push, and aliased actor promotion; preserve distinct case-sensitive identities.

### Resolve image-promotion roots inside the campaign hold

promote_image still captures croot and wroot before acquiring the campaign lock. Delete/recreate the campaign on World B while this operation waits: its strict ledgers then belong to B, while wdir still points to A. With the same record ID and empty campaign image slots, the operation copies A's images and descriptions into B's campaign, swaps its avatar, and can tombstone B's unrelated gallery slot. The push/promote root-capture correction does not cover this publication path.

Correction: Resolve both roots and the promotion target under the campaign hold, then use that scope throughout validation and publication. Add a delete/recreate-before-acquire regression with different images in the two Worlds.

### Check World binding before suspending dependent membership offers

world_entity enumerates campaigns before hold_all, then suspends offers without rechecking their World binding. Recreate a captured campaign on World B with an owned, attached group and a Members journal offer at the same ID before acquisition. This loop suspends B's offer for A's relocation. The later _follow_in_campaign binding check skips B, so its unchanged group retains an incomplete-relocation fence and loses Undo/Redo availability.

Correction: Within the captured sorted hold, discard deleted or rebound campaigns before computing following or persisting any suspension. Add the campaign-recreation interleaving already covered for demotion.

### Exclude rebound campaigns from World image cleanup

world_identity_change yields its pre-lock campaign set without validating current World bindings. If a captured campaign is recreated on B before acquisition and hides B's map image, deleting A's map passes preflight and world_images._forget_in_dependents removes that campaign's assets/library/map marker. B's unrelated image becomes visible and its saved hide choice is erased. The record-delete sweep has a binding recheck; the library-image sweep does not.

Correction: Revalidate existing captured campaigns against wroot inside hold_all before yielding the cleanup scope. Test recreation on another World with a same-named hidden library image; no S5 discovery barrier is needed.

## Actual persisted recheck at0a4c59535

Thread `01a12e23-437b-7493-8f2f-b871d433a23c`, verdict **needs-attention**. Prior four fixes are present.

### Reject unsafe IDs before canonicalizing deletion targets

With campaign files groups/salt-circle.md and greetings/salt-circle.md, delete_greeting(cid, '../groups/salt-circle') probes the group file and canonicalizes the invalid ID to salt-circle. Deletion then removes the legitimate greeting instead of refusing the traversal-shaped input. Encoded backslashes provide the equivalent Windows path. Validation in capture_record_refs happens too late: the unsafe input has already become a valid, different target ID.

Correction: Validate the kind and safe_id before any native path probe or resolution. Preserve the owning NotFound refusal without effects. Add traversal regressions for greeting and actor deletion.

### Canonicalize actor promotion before writing sync bookkeeping

On Windows, promote a campaign actor through MARA while its existing appearance entry is keyed characters/mara. Promotion writes characters/MARA into the manifest, but _advance_appearance_base searches that uppercase key and misses the existing lock. After a World edit, the uppercase manifest entry is classified as unpicked. Accepting it takes the whole-actor dematerialization branch, removing campaign metadata and versions while the lowercase appearance lock remains. Clearing native detachment aliases does not reconcile these identities.

Correction: Resolve the owned actor's canonical ID under the campaign hold before publication, manifest writes and appearance-base updates. Test uppercase promotion of a version-locked actor followed by a World edit and take-world; retain the locked flow and campaign metadata.

## Actual persisted recheck at11e96e0c8

Frozen `11e96e0c83eb5ac3275cfa2b44db75e8fed335d5`; thread `01a12e23-437b-7493-8f2f-b871d433a23c`. Verdict **needs-attention**.

Do not ship: mixed filesystem namespaces still allow moving the wrong owned group or publishing a different actor as a member. Prior fixes are present; full-gate completion remains unverified.

### Verify which owned original follows a World move

Use a case-sensitive World containing distinct salt-circle and SALT-CIRCLE groups, with a case-insensitive campaign holding an edited copy and Members journal offer for salt-circle. World-reclassify SALT-CIRCLE. has_own_copy resolves that spelling to the campaign's lowercase file, so this branch moves the wrong original and repoints its journal offers. Its effective membership at salt-circle then reverts to the unchanged World baseline. Separating captured ledger namespaces does not validate the record selected for movement.

Correction: Before suspension or movement, verify the owned candidate's canonical ID identifies the selected source in the World namespace. Preserve unrelated owned originals, or refuse the ambiguous operation before effects. Add this inverse-policy World-move regression.

### Check actor identity across publication namespaces

Let a case-insensitive campaign own a materialized characters/mara, while its case-sensitive World contains distinct characters/mara and characters/MARA actors. A campaign-owned group's characters:MARA token resolves to campaign Mara, but the same token resolves to the other World actor. With an empty detachment ledger, this guard accepts World existence and publishes the changed identity. This affects Members and Leader despite valid canonical actor-promotion bookkeeping.

Correction: For an owned actor reached through a native alias, require its canonical ID and the submitted ID to identify the same destination World actor. Otherwise refuse before publication and require correcting the field. Test both Members and Leader under mixed policies.

## Actual persisted recheck at52c5ab3e1

Frozen `52c5ab3e1ad64f8461464b5ce2dff23ddc94cbbd`; thread `01a12e23-437b-7493-8f2f-b871d433a23c`. Verdict **needs-attention**.

Do not ship: first-copy and Undo checks still permit an unreviewed actor change across native namespaces. Previous publication/lifecycle fixes are present; full-gate completion is unverified.

### Treat cross-namespace actor mismatches as unresolved before copying

Reuse the supplied mixed-policy actor setup: campaign characters/MARA resolves to its owned mara, while World characters/MARA is a distinct actor; Off is empty. Put characters:MARA in an inherited group's Members. A body-only save passes this check because the World actor exists, then publishes an owned group whose token selects the different campaign actor without consent. GET supplies no unresolved-slot warning. Clearing the token and undoing likewise passes group_unresolved and can restore that unreviewed binding. The new cross-root proof runs at publication, but not these adoption/restoration boundaries.

Correction: Include cross-root actor identity mismatch in the shared unresolved-slot calculation using the captured roots. Require exact Members/Leader consent for retained mismatches, including Undo revalidation. Add first-copy, copy-down and clear-then-Undo regressions for both mixed-policy directions.

## Actual current-code final approval

Frozen `62270523a6315871d056c158a4df170402c8d7a1`; persisted installed native thread `01a12e23-437b-7493-8f2f-b871d433a23c`. Verdict **approve**, no findings.

Static S1 recheck approved: the shared identity check closes the last adoption blocker. No supported substantive S1 gap remains in the supplied context. Full frozen-head gate completion remains unverified.

Current code gates are closed. Full frozen repository validation remains
required before S1 completion or shipping; no full pass is inferred.
