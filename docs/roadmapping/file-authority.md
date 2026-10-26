# ADR candidate: Human-editable files remain authoritative

**Status: strong design constraint, not permission to implement or replace storage.**
**Context:** World composition and game mechanics planning, 2026-10-15.

## Decision preference

Keep **Markdown with frontmatter** authoritative for authored world, campaign, character and setting text. Preserve the existing store's human-editable **JSON** for structured state and configuration where already used; do not reinterpret the principle as a demand to convert established JSON records to Markdown.

Users must be able to inspect, edit, back up, restore, move and version their worlds and campaigns using ordinary filesystem tools. Coding agents should be able to author content without going through Grimoire's UI. A world remains playable without any derived database.

This is an architectural **presumption**: a canonical database should require evidence of a genuinely better RP experience (correctness, continuity, durable mechanical outcomes or player-facing capabilities that cannot reasonably be attained with a file-based design), **not just easier implementation or faster lookups**.

## Repo facts (verified against main)

- `docs/store-guarantees.md` defines the authoritative library as Markdown and JSON files and describes per-file atomic publication, per-campaign locks, and explicit limits (no cross-record transaction, no cross-device locks, no backup snapshot isolation).
- `docs/superpowers/specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md` specifically says that files remain the database: SQLite holds disposable, derived artifacts.
- `backend/src/grimoire/store/overlay.py` already implements campaign-over-world copy-on-write with local copies, tombstones and detachments; `backend/src/grimoire/store/sync.py` reconciles divergent files.

Any future composition resolver should maintain these guarantees or document intentional changes.

## How a dependency DAG works without a new SSOT

1. An authored world definition remains a Markdown tree. Its manifest declares imports (and, where necessary, selections or exclusions) in human-editable files.
2. Reusable content collections also remain human-editable files with stable source-qualified identities.
3. The World Resolver produces a **logical effective world** combining local definitions with imported content. The campaign asks its one World interface for content.
4. A compiled effective-world directory, index, FTS table, or SQLite materialized projection is **derived and disposable**, not an independent source. It can be rebuilt from authoritative source files and pinned dependency manifests.
5. Campaign-local changes continue to live in ordinary files via copy-on-write and synchronization; a dependent world or module update never silently overwrites a divergent campaign record.
6. File edits from outside the app are valid inputs. Rebuild/invalidation must observe them; parse errors must be surfaced without automatically destroying the user's text.

An illustrative invented setting manifest could be:

```yaml
---
name: Amber Coast
imports:
  - source: common-fantasy-bestiary
    version: 1.4.0
  - source: mundane-equipment
    version: 2.0.1
recommended_mechanics: example-d20
---
```

Precise manifest format, dependency pinning, conflict behavior and compatibility migration remain **undecided**.

## Distinguish authority from execution convenience

- Markdown: authored fiction, lore, descriptions, character voice, world/campaign instructions, and their explicitly editable metadata.
- Existing JSON: machine-oriented state, manifests, sheets, ledgers and settings **where appropriate**. Still canonical files, editable and portable.
- Compiled caches: search indexes, resolution tables, fingerprints, embeddings and assembled world projections. Never the sole home of user data.
- Transaction and event journals (if needed): authoritative **files**, with explicit durability and replay semantics, not indistinguishable cache artifacts.

There is no requirement that every piece of mutable gameplay state literally live inside one Markdown document. The important contract is human-editable, file-owned truth without a hidden canonical database.

## What might justify revisiting the constraint?

Only user-visible failure modes requiring something substantially stronger, backed by tests and architectural comparison. Candidates:
- Complex multi-record state transitions (one combat action updates several sheets, inventories, clocks and logs) that cannot be recovered or made coherent using file locks plus write-ahead/recovery journals.
- Real simultaneous multiplayer state changes where races produce duplicate items, contradictory injuries, or incoherent dialogue; note that a *local* SQL database still does not solve concurrent editing across multiple devices without coordination.
- Durable, auditable event sequencing or rollback essential for play continuity when the simpler file-backed event log proves insufficient.

Before proposing a canonical DB, implement/test the narrow alternative (atomic files, serialized writes, journal + recovery, snapshots, explicit validation). Compare **actual gameplay correctness and recoverability**, not benchmark speed alone. If SQL is necessary for one runtime subsystem, first consider whether it can remain a derived view over authoritative file events.

## Non-negotiable test scenarios

- Delete every cache and reconstruct world, imported content, and campaign without content loss.
- Change a character or imported item Markdown file outside Grimoire; subsequent resolution sees correct new values and does not silently disregard or overwrite it.
- Campaign with no mechanics still loads the world's fictional imports.
- Imported content's source identity remains fixed when a campaign changes its selected mechanics.
- Update or remove a dependency: clear errors, explicit conflict or pinned old version; never substitute a different creature/item with the same name.
- Restore old world/campaign files into a newer Grimoire version without losing authored content.
- Test interruptions between related state-file writes; avoid partial fictional realities reaching narration.

## Next research step

Prototype a **file-backed World View** contract, then compare a lazy virtual resolver with a compiled *read-only* projection. Both preserve file authority. Investigate transactions and update semantics separately from content organization. Don't migrate authoritative content to a database as a prerequisite for composition.
