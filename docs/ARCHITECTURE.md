# Grimoire V2 — architecture charter

**Status:** proposal and tiny in-memory experimental kernel. The V2 `core/model.py` still names `CharacterDefinition` and `CampaignActor`; these are *temporary first-slice types*, not a commitment to a character-exclusive ontology. Follow the [ordered V2 roadmap](roadmap/README.md) before introducing persisted schemas.

## Fundamental concepts

1. **Source-qualified definition.** A catalog or world owns a reusable authored description and optionally a portrayal/era. Source identity, kind, entity ID and version must be stable; same name or slug from another source is never an automatic match. No compulsory global character catalog; self-contained worlds remain first-class.
2. **Instance and controller.** An individual campaign has its own entity instances with identity, derived-from links, overrides and lived continuity. PC and NPC are control/authority roles, not intrinsic entity types; one source can yield multiple actual instances (clones/parallel histories).
3. **Composable capabilities.** A physical object may be portable, spatial, biological, sentient or collective. Facets are typed domain contracts with invariants, not freeform LLM-created booleans. Editor templates may still be named Character, Creature, Item, Location, Group and Lore, but these do not define mutually exclusive engine species.
4. **Typed relations and graph.** Membership, possession, kinship, containment, source inheritance, rivalry, communication and causal dependency are typed, scoped and potentially time-bound edges. Complex transactions can be represented by an event node with participants. Graph traversal is bounded and perspective-aware. Canonical relation files are human-editable; a graph database/search index is rebuildable, not secretly authoritative.
5. **Objective events and subjective evidence.** Accepted physical/social events are distinct from actor observations, messages, false claims, uncertainties and beliefs. Graph reachability is *not* a right to know. A director sees appropriate canonical facts; an NPC may see only attributed percepts and their own conclusions. Revisions and retcons preserve source evidence.
6. **Ephemeral actor perspective.** For a particular moment and speaker, derive portrayal/voice anchors, relevant goals and relationships, perceived scene history, memories and supported beliefs. Do not store a giant per-character copy of the entire scene; do not send every piece of director knowledge to an NPC.
7. **Actions and state authority.** Human intentions, game rules, AI choices, adjudicated outcomes, committed effects and narrative prose have separate owners. Player-character voluntary speech and decisions remain under player authority unless explicitly delegated. Deterministic guarded writers commit accepted effects once, narration merely expresses them.
8. **Independent per-NPC generation.** V0.2.1's separately prompted individual NPC contributions are the quality baseline and must be preserved. A scene with two speaking NPCs gets two independent RP-model generation calls, not a consolidated all-NPC narrator. Hidden NPCs do not require continuously running minds.
9. **Inference economics and flags.** Graph lookup, audience filters, ordinary memory selection, image placement and most state application are deterministic. Optional model-based planning, uncertainty analysis, critiques and intensive simulations are opt-in, visibly metered and bounded by user turn/session budgets. Performance comparisons must use actual billable tokens and matched quality against the `0.2.1` tag.
10. **Versioned, sync-safe human-editable storage.** Root/campaign records and identity mapping carry stable revisions, provenance and stale-write guards. Supported transfers between phone and desktop are an *early feature*. The sync contract preserves concurrent edits with conflict review; app-managed authenticated transfer is not tied to Syncthing. Peer connections work only when reachable; off-LAN operation needs an optional reachable endpoint/relay or another transport.

## Storage root and provisional organization

**Default desktop V2 library: `~/.grimoire-v2/`.** A V2-specific `GRIMOIRE_V2_HOME` override may select a different directory. Never fall back to V1's `GRIMOIRE_HOME`, `~/.grimoire/` or its bootstrap pointer. Android will default to its separate app-private V2 area; until that milestone lands, Android requires an explicit `GRIMOIRE_V2_HOME`.

### Library location and V1 isolation (implemented: `grimoire.library`)

`resolve_library()` returns the V2 library and what selected it (`default` or `env`); `python -m grimoire.library` prints the same. Resolution is **read-only**: it creates nothing and never opens or lists a V1 directory.

- `GRIMOIRE_V2_HOME` must be absolute; `~` and `~/…` expand against the home directory, `~user`, relative paths and surrounding whitespace are rejected. An empty value means unset. Symlinks are followed; a loop or unreadable component fails closed.
- **Guarded V1 roots**, used only to *refuse*, never to resolve: `~/.grimoire`; `GRIMOIRE_HOME` (both as V1 read it, relative to the cwd, and `~`-expanded); and the `data_dir` named by V1's bootstrap pointer `~/.grimoire.json`. The pointer is the one V1 file V2 reads, solely so a relocated V1 library is refused; if it exists but cannot be used, V2 refuses rather than guess.
- A V2 path that **is, sits inside or contains** a guarded root is refused (`V1IsolationError`), comparing NFC-normalized, casefolded path components and device/inode identity.
- A directory with a `worlds` or `campaigns` directory **and** any V1-only root entry (`V1_ROOT_MARKERS`: `llm_connections`, `sampler_presets`, `response_presets`, `.world-staging`, `.module-staging`, `.char_macros_baked`, `prompt_layout.json`, `regex.json`, `chores.json`) is treated as a V1 library, as is anything inside it. **These names are reserved: a V2 library root must never create them.**
- `check_write_destination(path)` applies the same rules to any directory V2 will write. `check_migration_paths(source, destination)` additionally requires an explicit source and a destination that does not overlap it; the source's kind, format and the approval flow belong to the migration sprints.
- **Writer obligations.** The checks are point-in-time. A writer calls `check_write_destination` immediately before creating a library, creates only the final component (its parent must already exist), re-checks after creating, and records the root's device/inode, re-verifying it at the start of each write session.
- Known limit: a V1 library nested somewhere *below* a candidate is refused only if it is a guarded root; candidates are not scanned.

```text
~/.grimoire-v2/                        # target example; not implemented yet
  catalogs/                           # optional shared, reusable definitions
  worlds/<world>/                     # self-contained setting and authored content
  campaigns/<campaign>/
    instances/                        # individual entities and overrides
    scenes/                           # accepted authored contributions, exact text
    events/                           # committed happenings and consequences
    evidence/                         # observations, reports, beliefs and provenance
    relations/                        # scoped typed relationship records
  assets/                             # content-addressed image objects and placements
  .cache/                             # disposable derived graph, search, embeddings
```

The actual file names and graph schema remain subject to the native-storage sprint. Data files must remain usable by humans and external agents. Derived indexes, embeddings and cached actor views are disposable and may not become a second canonical store.

## Root change, world adaptation and campaign evolution

A world may directly define a character or explicitly adapt a catalog definition. Each campaign instance retains its source relation, retained common-ancestor revision and independently accumulated state. Possible policies: **follow compatible fields, review/merge, pin revision, detach, promote selected authored changes upstream**. A three-way merge needs actual base content, not just hash equality. A correction to a source's childhood story cannot rewrite accepted campaign history; changes to one campaign never automatically import another's private memories or bonds.

## Required proving scenario and early product gates

Use purely synthetic materials in repo/test output. Two worlds contain same-named characters, one source yields two independent campaign instances. The group relation graph identifies an organization without leaking secret membership into an uninformed actor. A private conversation gives one actor new evidence and another a mistaken belief. A source definition changes; one instance accepts a correction and the other pins its version. An image object is shared through two distinct avatar/expression placements and retains both crops.

Then **import this scenario from a `0.2.1`-format library** using a source-preserving migration plan, run one real text interaction with **separate NPC generation**, save/reload, and sync the campaign plus art to a phone and back. The original source library must remain unchanged, failures must be recoverable and all additional inference costs must be visible.

## Constraints and still-open choices

- No source ID substitution, implicit knowledge leak, forced PC narrative, or silent state write.
- No model-priced graph traversals by default and no hidden NPC inference jobs.
- Quiescent V1 source and distinct V2 destination; import requires explicit preview and approval, with loss-aware source/image records.
- Keep storage revisions and sync conflict ancestry compatible with editable file-authoritative records.
- Open design questions: exact file schema, relation granularity, revision retention, materialized versus derived beliefs, remote/relay sync, selective history merge UX, and mechanics module interface.
- Build one playable vertical slice before optimizing the full living-world/VR/simulation moonshots.
