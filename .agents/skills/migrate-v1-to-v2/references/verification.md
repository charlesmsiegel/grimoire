# Migration preview, acceptance and recovery tests

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
