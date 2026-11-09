# World Maintenance and Change Sets

**Priority:** after sufficient World Resolution/Identity contracts; read-only reconnaissance may start sooner.

## Goal

Offer **optional in-app content maintenance** comparable to external Claude/Codex authoring, without requiring the user to switch to a hidden canonical database or forbidding normal Markdown/JSON edits. Import a new character, reconcile its lore and relationships, and safely propose coordinated changes to existing world information.

## Work to plan

- Inspect the **same resolved world** as gameplay, including relevant cards, embedded lorebooks, existing people, aliases, membership, groups, places, items, greetings, voice anchors and provenance.
- Support typed **Change Sets** with target scope (owning library/source, world, campaign), record ID, operation, expected base revision/hash, evidence, preconditions, validation and intended consequences.
- Implement inspect → plan → reconcile → review → apply → refresh derived views → trace/undo. Existing outside edits, user modifications and partial failures must be detected before overwriting.
- Validate duplicate candidates, missing targets, cycles, name/alias conflicts, ambiguous adaptations, permission and source ownership **deterministically**. LLMs may propose edits but cannot directly write canonical records.
- Present intelligible diffs and explanations across related files. Auto-apply only narrowly authorized, validated, non-destructive cases; preserve an explicit human review option.
- Reuse existing store writers, lock domains, activity/journal and recovery techniques rather than a second ad hoc source-of-truth.
- Track **library activity and dependent campaign impact**: new/changed/deleted content, external editor changes, origin, affected inherited campaigns, divergence, conflicts, imported dependency updates, and navigable links. Derived activity indexes must be rebuildable from recorded authority; timestamps alone are not stable content revisions.
- Support external Codex, Claude and hand-editing as equally valid authoring paths; in-app agents are another client of the same file-backed model.
- Avoid unnecessary bulk updates and embedding rebuilds when a record's content has not actually changed.

## Proving scenario

A synthetic imported character has an internal lorebook mentioning a city and guild. The worker proposes an existing-group link and a new location, updates relevant tags/greetings and preserves the character's source. A conflict with an external file edit is flagged before commit; approved changes are idempotent, auditable and leave unrelated files untouched. The campaign can still read the corrected world with all caches deleted.

## Dependencies and boundaries

Needs [World Resolution](world-resolution.md) and [Content Composition](content-composition.md) for target/source identity. Uses [Agentic Workers](agentic-workers.md) for optional tool orchestration and [State Transactions](state-transactions.md) for short, recoverable file writes where appropriate. No private user content in public fixtures. Don't require the entire generalized worker framework before a narrow manually reviewed change-set API can exist.

## Open questions

- What minimal operation schema and scope/authorization model covers actual world-maintenance edits?
- Must a multi-record change set be all-or-nothing, or can it expose recoverable partial progress with explicit compensating changes?
- Which edits can safely auto-apply, and whose configuration controls that permission?
- How can external edits not passing through the app's write API still appear in the activity feed with meaningful provenance?
- Should activity history be event files, derived comparisons of file revisions, or a hybrid without a second authority?
- How does an update propagate to dependent campaigns when one has diverged from a shared record?
- How does undo interact with subsequent edits from a different agent or human writer?
