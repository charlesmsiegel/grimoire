# World Maintenance — in-app agentic authoring

**Status: product goal / candidate sprint, not an implementation specification.**
**Date:** 2026-10-15

## Motivation

Currently the project owner uses external Claude and Codex sessions to maintain real Grimoire worlds, especially after importing character cards: newly added characters require lore and group reconciliation, internal lorebook integration, and revisions to related content. Long term, Grimoire should support these operations **inside the application**, **without favoring, replacing, or blocking external editors, scripts and agents**. In-app maintenance is an additional option, not the exclusive or mandatory method.

**Preserve the authoritative file store.** Markdown with frontmatter (and the project's existing editable JSON where appropriate) remains the source of truth. In-app world curation and external coding agents should be alternative clients of a consistent store operation contract, not independent data authorities.

## Existing in-repository workflows to reuse

Investigated on main:

- `.claude/skills/world-card-integration/SKILL.md`: character-card editorial integration; reconcile internal lorebooks against existing entries, update cast information, groups, locations, items, greetings, tags, plot-map edges and voice anchors. Explicitly prefers `grimoire.store` writers to hand-written files and uses the store's own version history for context.
- `.claude/skills/populate-world-content/SKILL.md`: batch extraction of reusable entities and greetings from character cards; index existing content, propose in batches, apply a manifest, validate references, and checkpoint in Git. Emphasizes source evidence, idempotency and avoiding accidental duplicates.
- `.claude/skills/grimoire-store-editing/SKILL.md`: authoritative editable files, existing writer APIs, safe external edits and cache synchronization. Warns that external writes need idle/quiescent campaigns and must not fake or mutate derived cache answers.
- The existing backend already implements CRUD for entity kinds and characters, and existing continuity and review workflows may offer UI patterns. They are not yet proof of a general atomic multi-record world transaction API.

Do **not** embed user/private world names or real card content in this public repository's design docs or fixtures. Test with the repo's established fictional placeholders.

## Proposed workflow

1. **Trigger:** user imports a card, edits a character, requests cleanup, or manually asks for a world consistency/integration pass.
2. **Inspect:** gather just the relevant world records, imported lorebook, aliases, existing memberships, tags, references and historical decisions.
3. **Plan:** a decision model and/or LLM produces structured proposed changes, each with source evidence, target identity and intended operation.
4. **Reconcile:** deterministic validation checks record IDs, membership/reference existence, duplicate creation, missing or stale targets, authoring constraints and conflicting edits. Ambiguities are surfaced, not silently merged.
5. **Review:** present one intelligible change set / diff across affected records; require approval for potentially destructive or interpretation-heavy changes, with possibly configurable auto-apply for genuinely safe operations.
6. **Apply:** guarded store APIs publish approved changes to human-editable authoritative files, respecting locks and recording partial progress; recovery/journaling should prevent incoherent half-applied worlds if interruptions occur. Git history can be a checkpoint, but not an excuse to ignore correctness.
7. **Refresh:** update derived indexes, embeddings and compiled projections using existing cache policy; all caches remain disposable.
8. **Trace and undo:** report created/updated/linked records, unresolved questions, provenance and available rollback or compensating operations. Preserve unrelated user edits.

## Product/architecture implications

- **World Maintenance** is a distinct candidate sprint, connected to Content Import, Knowledge Management, Worldbuilding and Quality Assurance. It consumes the general **Agentic Workers** infrastructure described in [agentic-workers.md](agentic-workers.md) rather than requiring its own tool-loop framework.
- Use the same **source-aware World View** resolver for identifying and querying records that gameplay uses. The agent should not have a separate, inconsistent view of what exists.
- If worlds import shared catalogs, adding a card to a campaign must not inadvertently edit a shared reusable definition. Every proposal names an explicit target scope: shared source, owning World, or campaign-local. This is essential for the proposed dependency-DAG design.
- Define a first-class **change set** abstraction (draft plan with proposed operations, dependency references, preconditions, evidence and approval state). Distinguish that abstract contract from any choice of internal persistence technology.
- Existing game/simulation decision systems and narration should not be authoritative editors of the world without a separately validated mutation path.
- User-facing editability, external-tool interoperability and recovering interrupted work are product requirements; speed alone doesn't justify moving authoritative data to a database.
- Private/unofficial character collections may be imported or maintained locally; Git repo/package boundaries should not dictate world identity or the logical operation interface.

## Transferred downstream feature: library activity and affected campaigns

The product requirement formerly tracked by [#55](https://github.com/charlesmsiegel/grimoire/issues/55) has been preserved in [deferred-systems.md](deferred-systems.md): a source-aware, human-readable library activity feed across worlds, external editors and agents, with the effects on inherited campaign records. This should consume World Resolution provenance and validated change-set records instead of creating a separate, authoritative database or ignoring external file edits. Its issue tracker may close while the feature remains outstanding.

## Candidate acceptance scenario

In an invented test world, import one character card with an embedded lorebook. Grimoire proposes linking the character to an existing guild and city, adding one genuinely new location, reconciling a duplicate guild description without destroying existing facts, and updating applicable tags/greetings/voice. The user can inspect exactly what would change, approve it, and see only valid human-readable file edits. The change is idempotent or flags an already-applied proposal; a concurrent conflicting edit causes an explicit review instead of silent overwrite. A subsequent session retrieves the correct integrated world facts without an unnecessary bulk prompt injection.

## Questions for future specs

- Which operations are safe to auto-apply, and which require review by default?
- Should curatorial proposals target a world, a campaign, or an imported library? How is scope selected?
- How should the editor reconcile alternative portrayals without declaring name-based identity?
- What change-set representation, precondition versioning and recovery strategy works within existing store guarantees?
- When should agents offer integration proactively and how do we avoid high recurring inference costs?
- How can external Claude/Codex tools and the in-app agent share reliable mutation APIs and diff/review semantics?
