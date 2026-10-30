# Skill to translate 0.2.1 worlds, campaigns and art

**Outcome:** A first-class agent skill inventories V0.2.1 assets with exact provenance before importing them into a distinct V2 home.

## Scope and first proof
**Implement and run** a deterministic read-only V0.2.1 inventory scanner (CLI/module, not only skill prose), and update the migration skill so it can invoke the scanner and interpret its output. Scan characters, PCs, world lore/groups/items/creatures/locations, greetings, campaign scenes, source-qualified identity candidates, image objects/blobs/placements, gallery/scene images, expressions, outfits and inheritance. Produce a concrete source-ID/art-placement mapping and loss/ambiguity report against a frozen synthetic V1 library, with no V1 or V2 canonical writes and no model calls.

## Subsequent milestones and moonshot
A user-approved V2 import/apply, target image staging, resumable cross-scope reconciliation and real legacy campaign verification belong to sprint 026 and later; this sprint only **plans**, validates and reports.

## Independence and interfaces
Owns V1 source interpretation and preview; target writers are owned by the import-apply sprint.

## Proving scenario
A source avatar and an expression sprite both reference one image object and remain distinct placements in the preview.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Choose the V0.2.1 source explicitly and check the pair with `check_migration_paths`; the source is never written (001).
- List, for review, every V1 path a `LegacyId` refuses (format characters such as ZWJ) and every pair of paths that differ only by Unicode normalization; never normalize them silently (002, [identity](../../IDENTITY.md)).

## Executable migration inventory contract

The skill `.agents/skills/migrate-v1-to-v2/SKILL.md` already exists but is *documentation only* on entry to Wave 2; the runnable inspector is the required missing deliverable. Implement an explicit CLI, for example `python -m grimoire.migrate_v1 scan --source /explicit/frozen-v1 --report /private/report.json` (exact command chosen by the implementation), plus a machine-readable, deterministic manifest with a schema/version and a human-readable review summary.

- Require an **explicit, quiescent V1 source** and a distinct V2 destination passed through `check_migration_paths` before any V2 target is mentioned. The source remains read-only, no implicit `~/.grimoire/` discovery, and a scanner run never calls a provider or writes canonical V2.
- Record `LegacyId` provenance and *verbatim* legacy source-relative paths, plus rejected paths (including zero-width/format characters and Unicode normalization collisions) **without coercing two distinct sources into one**.
- For pictures, separately inventory blob SHA/bytes, pixel-image object identity, object descriptions/subjects, and every placement: logical name, entity/version/campaign scope, focus/crop, avatar/gallery/greeting/scene role, outfit and expression label, inherited/hidden/overridden state. Missing bytes or dangling placements become explicit warnings.
- An authentic synthetic V0.2.1 fixture must include a shared image object used as an avatar and a distinct expression placement, two same-named characters from different source scopes, a PC, at least one scene, an alternate portrayal and a broken reference. Repeated scans produce equivalent manifests; source hashes and files remain unchanged. Unreadable entries, traversal links and malformed cards **fail closed or report unresolved**, never silently skip.
- No real user assets, names, private reports or charged-model prompts in this public repository or CI. The report is written only to an explicit output path outside the source and target libraries; dry-run itself should be possible without an output file.

**Parallelism:** consumes only Wave 1 ID and path contracts plus the tagged V1 file formats. This scanner does **not** import 009's facets, 010's storage or 018's V2 image-store code. The future 026 importer consumes the reviewable manifest. See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#parallel-ownership-and-handoffs).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
