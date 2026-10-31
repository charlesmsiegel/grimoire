# Migration preview, acceptance and recovery tests

## Wave 2 read-only inventory acceptance

**This gate is prior to and independent of Wave 4's canonical import apply.** A complete skill document does not satisfy it; Sprint 011 must ship runnable source inspection and a versioned machine-readable report.

Use a synthetic, frozen V0.2.1-format library containing two same-named records in distinct source scopes, a Character Card V3 with two versions, a PC, a group/lore item, a scene with explicit authorship, one image object referenced by *two different slots* (avatar and outfit/expression), an independently placed gallery image, one hidden/inherited placement, a dangling reference, and an ambiguous legacy path.

Pass conditions for the Wave 2 scanner:

1. Runs using an **explicit V1 source** without configuring V2 to point at it; validates source/destination separation with `check_migration_paths`. Neither source nor default destination gains a file, directory, cache or modified timestamp because of scanning.
2. Produces deterministic, schema-versioned manifest + understandable review output, with exact V1 relative paths, proposed `LegacyId`→source-qualified mapping, unsupported fields and alias/name collisions.
3. Records each image **blob**, decoded picture **object**, and **placement occurrence** separately (including role, name, focus, subject, version, scope, gallery, expression/outfit and hide/override state). Two placements can reference one picture object; neither becomes an image-path string pasted onto the wrong actor.
4. Preserves source-private evidence and exact scene text without promoting a transcript mention into an actor's actual knowledge.
5. Reports invalid/ambiguous paths (including Unicode normalization collisions and rejected zero-width characters) and broken/dangling image references without guessing a replacement.
6. Refuses external symlink traversal, corrupt records and unsupported formats safely; rerunning is idempotent and source input hashes remain unchanged.
7. Makes **zero network/model requests** and no paid inference calls; if a future optional analyzer is proposed, it is a separate opt-in operation with a visible budget and reviewable output.

**This preview must not call the V2 canonical writer.** Wave 4 owns staged asset copying, approval, apply, crash recovery and rendering verification. Sprint 011's scanner (`python -m grimoire.migrate_v1 scan`, [contract](../../../../docs/MIGRATION_INVENTORY.md)) passes these conditions against the synthetic fixture in `backend/tests/v1_fixture.py`; see `backend/tests/test_migrate_v1_*.py`.

## Before any apply

- Confirm V1 source snapshot is idle and consistent, and V2 target is separate; the original source must remain read-only throughout.
- Preview the full source-to-target identity mapping with explicit unmatched/ambiguous references.
- Report image object/placement counts separately. State *how many image references are unresolved*, not only number of files copied.
- Report unsupported source records, alternate versions, custom fields, hidden art, scene variants and privatized knowledge.
- Provide a transaction plan, free-disk estimate and an inference bill of **zero** by default. An optional extraction step states its estimated provider cost and asks explicit approval.

## Minimal synthetic acceptance fixture

A V1 world with two variants of a single NPC, a PC persona, a group, a location, lore, two scene posts, one campaign-local relationship, an image object used as both an avatar and an expression, an independently placed gallery image, and a hidden inherited picture.

Pass conditions:

1. Correct source-qualified world definitions; no name-based collision with a similarly named record from another synthetic world.
2. The V2 PC and NPC share an entity/actor *type* and differ in controller assignment only.
3. Import a character version separately from their campaign instance's lived experiences.
4. All images have readable bytes and correct per-entity/per-version/per-scope placements; old→new image map is inspectable.
5. Hidden/inherited expression semantics remain correct or are explicitly unsupported and held for later.
6. Scenes retain exact authorship, order, accepted text and original source evidence.
7. Known/private information is not silently granted to other actors.
8. Running apply twice yields the same V2 identities and no duplicate events, actors or blobs.
9. A forced crash midway leaves a detectable, resumable staged import; an existing V2 edit is never overwritten.
10. An immutable digest of every V1 source input remains unchanged after both success and failure.

## Playing against imported data

The minimal V2 text runner should load the synthetic imported world, generate a separately assigned NPC reply using their authored voice evidence, save the turn and resume it. Once the phone client and sync transport exist, add a two-device acceptance gate: import on desktop, sync to phone, play one turn offline, reconnect, sync and resume on desktop.

Do not claim production migration support until these apply/verify scenarios are implemented and actually pass.
