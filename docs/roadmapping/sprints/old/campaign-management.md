# Campaign Management, Forks, Imports and Exports

**Priority:** future usability/continuity improvements; cross-campaign scene copy waits on stable identities and storage decisions.

## Goal

Players can manage long-running campaigns, move through scenes, fork or reorganize stories, preserve source identity and create usable exports without silently importing incompatible character/continuity state.

## Work to plan

- **Campaign Management:** maintain campaign/world association, optional rules, scenes, in-world time, cast, continuity, settings, versions, backups, migration and restoration. Protect authored files and valid external edits.
- **Scene imports and cross-campaign copy:** a copied transcript becomes a *new target scene* with an appropriate order/identity. Reconcile cast by explicit character provenance/version rather than name, handle missing character assets and decide which continuity facts can transfer.
- **Preflight and recovery:** provide dry-run/preview, conflict detection, destination validation, safe cancellation and partial-operation recovery. Existing branching, scene import and store mutation paths should be reused, not duplicated.
- **Forks and retcons:** preserve source and independent destination history, speaker identities, old version references, asset URLs and association with facts, journals, inventories, mechanic effects and decisions.
- **Export formats and filters:** render a common structured snapshot to Markdown, HTML, text, EPUB or other supported formats. Strip reliably marked out-of-character or mechanics material only where metadata supports it; no brittle guesswork.
- **Anonymization and point of view:** stable per-export aliases for players/characters, explicit handling of labels versus prose names and scene-level POV/chapter grouping. Never rewrite originals just to export.
- **Export history:** optionally record chosen format, filters, timestamp and artifact metadata in readable campaign authority, distinct from the derived exported files themselves.
- **Portability:** include referenced actor art/expressions and needed world/mechanics context appropriately, with clear missing-source behavior and existing backup/restore invariants.

## Proving scenario

A campaign scene is imported into a second campaign containing a related but different character portrayal. The preview shows version/source and chronology conflicts, no source content changes, and the result has a new scene ID. Exporting a separate campaign to EPUB with anonymization does not alter its canonical names or leak a deliberately excluded OOC section.

## Dependencies and boundaries

[World Resolution](world-resolution.md) supplies stable identity; [State Transactions](state-transactions.md) gives guarded file changes; [Narrative Design](storytelling.md) handles scene/arc organization; [Content Ecosystem](content-ecosystem.md) handles externally packaged worlds and character cards.

## Open questions

- Should cross-campaign scene copy initially support same-world transcript-only content, or complete cross-world cast/continuity transfer?
- Which pieces of source continuity move with a scene, and which must be explicitly re-extracted and reviewed in the target?
- How are historical assets and character version locks handled when source campaigns or imported libraries vanish?
- Which export transformations are feasible without a formal OOC or POV annotation system?
- Should export history be a canonical human-editable record or simply a local artifact index?
