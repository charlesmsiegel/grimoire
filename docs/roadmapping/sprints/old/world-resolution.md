# World Resolution and Content Identity

**Priority:** early architecture proof; expensive-to-reverse interface. **No global data migration before a successful spike.**

## Goal

Give gameplay, world authoring, imports and maintenance one coherent **effective World view** independent of the physical location of a record. Imported libraries must not erase local content, collide by display name, or make a campaign reinterpret its existing actors when mechanics or catalog order changes.

## Work to plan

- Expose a narrow get/list/search/resolve interface over a campaign's chosen world, campaign-local differences and optional imports. Retain the current working one-parent, campaign-over-world copy-on-write semantics through adapters.
- Introduce **source-qualified identities** for reusable definitions (source, kind, ID and any necessary version pin), while resolving old kind/ID references safely. Distinguish *same character*, *alternate portrayal*, *related adaptation*, *overridden definition* and *unrelated records sharing a name*.
- Record readable provenance, owning scope, origin and explicit override/variant relationships. Never use similar names or import order as proof of equivalence.
- Make character/version, image, expression library, lore/group/plot references, sheet definitions, asset URLs and other cross-record IDs work through that view without dangling or accidental substitution.
- Preserve actor and file sync bases, detachments, tombstones, local edits and their explicit conflict/reconciliation behavior.
- Allow an entire campaign turn to read a **consistent effective-world snapshot** rather than a mixture of dependency revisions.
- Compare a **virtual resolver** against a **compiled, read-only effective filesystem view plus provenance index**. Compiled paths may simplify legacy store compatibility, but projections remain rebuildable from authored files.
- Validate identity through future consumers: creature → character, inventory definition/holding separation, cross-world character portrayals, shared spatial links and source-aware library activity. Expose explicit missing/uninstalled-source behavior.

## Proof before selecting architecture

Use invented Faerûn/Krynn/Athas settings, shared equipment and bestiary records, plus an optional alternate Drizzt character card. Make D&D and WoD goblins *different* sources despite identical names; keep one character's campaign changes independent from its parent definition. Switch the campaign from D&D to no mechanics and back. Update, remove or reorder a dependency. Verify original characters, variants, images, expressions, sync bases, prompt relevance and file authority survive unchanged unless the player explicitly chooses a change.

## Dependencies and boundaries

Related [Content Composition](content-composition.md) decides *which* external definitions are in a World; this sprint owns *how* they are identified and read. [State Transactions](state-transactions.md) owns writes and crash safety. [Creature → Character](creature-to-character.md), [Spatial World](spatial-world.md) and [World Maintenance](world-maintenance.md) are proving clients. Do not assume arbitrary recursively inherited worlds are required or that world definitions share the same mutation policy as campaigns.

## Open questions

- Should World and reusable Content Library share one storage type, or only a common resolver interface?
- Can content imports be recursive under a constrained acyclic graph, or should the initial contract allow only one level?
- Which source-qualified ID, alias/variant/override, migration and reference spellings preserve existing campaigns?
- Should the view be virtual, a compiled read-only projection, or both behind one contract?
- What happens when a parent updates: follow live until divergence, pin at campaign creation, choose update, or version-lock per record?
- How are missing, deleted or unavailable sources represented without silently picking a namesake?
- How are changes in imported actor art, sheets, expressions and group references propagated or flagged?
- At what consistency boundary is the effective-world version pinned for a turn, export, fork or agent job?
