---
name: migrate-v1-to-v2
description: "Use when inspecting, planning, importing, verifying or repairing Grimoire 0.2.1 worlds, campaigns, characters, PCs, scenes and visual assets into a separate Grimoire V2 library."
---

# Grimoire 0.2.1 → V2 migration

**Status: the read-only inventory scanner is implemented (Wave 2 Sprint 011); nothing else is.** `python -m grimoire.migrate_v1 scan` inspects a frozen 0.2.1 library and writes a reviewable manifest ([contract](../../../docs/MIGRATION_INVENTORY.md)). V2 canonical import/apply remains Wave 4 Sprint 026, and playable scenes remain Wave 5. Do not claim importing or play works merely because these instructions or a clean inventory exist.

**Source:** an explicitly supplied, quiescent Grimoire **0.2.1** store or full backup. V1's default is `~/.grimoire/`; never discover or read it automatically. **Destination:** explicitly confirmed V2 store, default `~/.grimoire-v2/` on desktop; the source and destination MUST NOT resolve to the same path or nested writable trees. A V1 library is read-only throughout migration.

## Procedure

1. **Identify the original format.** Read [V1 source inventory](references/v1-layout.md). Pin the known V1 release `0.2.1`; reject unknown/newer formats or produce an explicit unsupported report rather than guessing. Stop active V1 use or import a separate frozen backup.
2. **Prepare source and destination.** Resolve and compare canonical real paths. The V2 writer uses `GRIMOIRE_V2_HOME` or `~/.grimoire-v2/`, never `GRIMOIRE_HOME` or the V1 bootstrap pointer. Do not modify either library in scan mode.
3. **Inventory every source record.** Identify world/library definitions, Character Card V3 versions, PC Markdown versions, locations, groups, items, creatures, lore, greetings, authorial macros, campaigns, selected actor versions, control roles, relationships, knowledge, scene posts and events. Map old IDs to new **source-qualified** IDs and distinct campaign-instance IDs. Preserve original source copies or hashes.
4. **Resolve media references, not just filenames.** Use [image and expression translation](references/images.md). Index actual byte hashes, image objects, placements, picture descriptions and subject labels, avatar/gallery/focus, outfits/expressions, version-specific override/hide rules, collections and scene/greeting images. Resolve every reference through V1 semantics. Record missing or ambiguous assets rather than substituting another picture.
5. **Run the scanner.** Ask the user for the frozen source and an archive id (one lowercase segment naming that backup), then run, without `--report` first for a dry run:

   ```bash
   python -m grimoire.migrate_v1 scan --source <frozen-v1> --archive-id <id> [--destination <v2>] [--report <path outside both libraries>] [--summary counts]
   ```

   Exit 0 means `ready_for_review`, 4 `blocked` (report still written), 5 the source changed during the scan (stop V1 or copy it first), 2/3 unusable or overlapping paths. Read the summary, then the report: `findings` (blocking first), `losses` (recognized but not carried), `unresolved` (must not be applied without the user's decision), `images` (objects versus placements, layering, references) and `identity_candidates` (unpinned proposals; collisions are withheld, never merged). Explain blocking findings in plain terms and propose fixes **to a copy**, never to the source. **Privacy:** the report and full summary name the user's records; reading them sends those names to your model provider. Prefer `--summary counts` when the user has not agreed to that, and never paste a report into a public place. See [the inventory contract](../../../docs/MIGRATION_INVENTORY.md) and [Wave 2 inventory acceptance](references/verification.md#wave-2-read-only-inventory-acceptance).
6. **Approve an isolated apply.** Apply only through V2's guarded, revision-checked import writer when it exists. Write to a staging directory separate from the original, then publish reviewed changes with a recoverable journal; never overwrite existing V2 edits silently. Do not create V2 actor histories from unverified speculation.
7. **Verify and resume.** Follow [acceptance and reconciliation](references/verification.md). Test original art bytes, placement semantics, identity separation, first/last scene text, accepted events and actor-private knowledge. On restart, repeat with the same source fingerprint/import ID and make no duplicate instances or blob copies.

## Mandatory safety properties

- V1 data is always **read-only**; no writing to `~/.grimoire/`, user-provider settings or V1 caches. An import does **not** call V1's `absorb` to re-narrate prior scenes.
- No real user names, plot details, secrets, transcripts, pictures, paths revealing private content, or aggregate library statistics may be committed to this public repo, test fixtures or GitHub CI logs.
- Do not auto-fetch remote images, Chub sources or embeddings. All billable inference is explicitly requested, estimated and attributed.
- A content hash or image ID is not an authorization to expose an image from a different library. Provenance, scope and allowed placement must be checked.
- Never treat PC/NPC as distinct V2 entity species: both become campaign actors with explicit controller assignments and preserved historical authored identities.
- If a source cannot be mapped losslessly, leave the original data intact, show an unresolved item and offer a manual review path. No silent loss.
- **Readiness gate:** Current V2 has these instructions and the **read-only inventory**. **Nothing applies to a canonical V2 library** until Wave 4's guarded apply/verification actually exists. Distinguish procedural document, runnable inventory and authorized import in every status report.

See the [V2 roadmap](../../../docs/roadmap/README.md) for the separate **migration-skill**, **image-identity**, **import-apply**, and **first-playable** work.
