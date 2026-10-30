# Skill to translate 0.2.1 worlds, campaigns and art

**Outcome:** A first-class agent skill inventories V0.2.1 assets with exact provenance before importing them into a distinct V2 home.

## Scope and first proof
Author the migration SKILL.md and a *read-only* inventory plan for characters, PCs, lore, groups, scenes, image-store blobs/objects, placements, expressions and galleries.

## Subsequent milestones and moonshot
Add scanner CLI, stable source-ID mapping and preview/verify receipts; every paid model call opt-in.

## Independence and interfaces
Owns V1 source interpretation and preview; target writers are owned by the import-apply sprint.

## Proving scenario
A source avatar and an expression sprite both reference one image object and remain distinct placements in the preview.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Choose the V0.2.1 source explicitly and check the pair with `check_migration_paths`; the source is never written (001).
- List, for review, every V1 path a `LegacyId` refuses (format characters such as ZWJ) and every pair of paths that differ only by Unicode normalization; never normalize them silently (002, [identity](../../IDENTITY.md)).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
