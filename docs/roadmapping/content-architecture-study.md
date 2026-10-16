# Content architecture decision study

**Status: OPEN — no design selected.** Research note for the permanent, unmerged `roadmapping` branch. This is neither a spec nor permission to refactor.

## Why this needs a deliberate decision

Grimoire's 0.2.x architecture already has meaningful world/campaign inheritance and mechanics/content integration. Adding general composition potentially changes entity identity, resolver semantics, synchronization, provenance, copy-on-write, asset lookup, and prompt activation. Repo partitioning (mechanics, worlds, content packs, private/unofficial, personal data) is a *distribution decision*, not by itself a model for runtime composition.

## Current implementation to account for (main, inspected 2026-10-15)

- `backend/src/grimoire/store/overlay.py`: campaign-over-one-world copy-on-write: inherited flat records and actors, tombstones, detachments, record/sidecar/image rules, sync bases. Record identities are currently effectively `kind/id` within a world/campaign view.
- `backend/src/grimoire/store/campaigns/read.py`: campaign metadata resolves a single world root.
- `backend/src/grimoire/store/campaigns/lifecycle.py`: campaign creation references one world and optionally selects a mechanics module; existing campaigns need migration safety.
- `backend/src/grimoire/store/modules/binding.py`: mechanics module may be inherited from world's default, overridden per campaign, or explicitly set to `none`; resolution returns one selected module or no mechanics.
- `backend/src/grimoire/store/modules/pack.py` and `modules/content.py`: mechanics packs *already contain* world-entity-like content templates, optionally with stat sidecars.
- `backend/src/grimoire/routes/worlds.py` and `routes/campaigns.py`: instantiating module templates copies entities into a world/campaign.
- `docs/superpowers/specs/2026-07-12-mechanics-phase7-content-creation-design.md`: some direct content references implicitly mean "content in the *currently resolved mechanics module*" rather than naming a specific pack. That assumption needs scrutiny if content becomes independent of mechanics.

These are read-only observations, not an exhaustive code audit.

## Constraints / invariants

1. A World owns core defining setting content: Forgotten Realms already includes Waterdeep, Drizzt, Elminster, factions, setting lore, etc. No mandatory artificial separation by entity kind.
2. Faerûn, Krynn, and Athas should be able to reuse a shared D&D equipment catalog or bestiary without duplicating thousands of entries.
3. A campaign selects **one** mechanics module or **none**, independently of setting content. Cross-system hybrid rules must be packaged as a single custom mechanics module.
4. Mechanics-free campaigns can access the same setting fiction and referenced reusable catalogs.
5. Optional creator-imported collections (e.g. Chub Faerûn NPC cards) can augment a world for one or many campaigns without needlessly copying or forking the setting.
6. World/pack character name collisions (e.g. two Drizzt portrayals) cannot silently merge. Explicit identity correspondence and authorship provenance matter.
7. Ordinary definitions, setting-specific variants, and campaign instances are distinct concerns. Runtime injury/state never rewrites reusable source records.
8. Changing a mechanics module must never reinterpret an unrelated fictional-content reference as a different item/character solely because of an unqualified ID.
9. Updating or uninstalling a pack must have a defined, safe effect on existing campaigns, including missing-content behavior, versioning or snapshots.
10. Only context-relevant material enters model prompts. Library availability does not imply prompt residency.
11. Preserving today's campaign data, asset paths, sync behavior, and migration tools is a priority; no flag-day global refactor.

## Alternatives to evaluate

### A — Instantiate/copy (existing approach)

Content templates are copied into worlds/campaigns. Keeps existing overlay largely unchanged. Weak at shared upgrades, provenance, authorship reuse and duplicate editing.

### B — Referenced content libraries (working hypothesis, NOT selected)

A world owns defining records and may reference read-only reusable libraries; campaigns may select additional optional collections. Libraries are not worlds by default. Keep a stable qualified source identity for referenced entries; avoid automatic name-based collisions. Resolve via a central interface; extend current overlay instead of bypassing it. Conflicts, optional local variants, and lifecycle still need design.

### C — Fully composable worlds/modules

World-like modules can recursively import, override, and export other modules. Flexible, but increases dependency graphs, cycle handling, override precedence, and migration surface. Do not implement simply because it is elegant or resembles a package manager.

## Focused proof-of-concept before any migration

Construct a tiny fixture with a shared bestiary/equipment library, Faerûn/Krynn/Athas, and one optional Chub NPC collection. Run:

- Same longsword definition in several settings; Athas excludes an inappropriate entry.
- Same shared creature with setting-specific variation, without changing other worlds.
- D&D goblin and World of Darkness goblin stay distinct even though names match.
- Standard Drizzt plus imported alternate Drizzt: explicit choice, no name-based automatic merge.
- Faerûn with mechanics on, off, then switched; content identity and fictional access remain stable.
- Live campaign instances with wounds/art/relationships; base catalog is subsequently updated, missing, or removed.
- Existing campaign data opens unchanged; sync, tombstones, actor versions and asset resolution remain correct.
- A large imported catalog does not flood prompt context or turn latency.

Measure authoring friction, data duplication, resolver complexity, migration burden, and how much of the existing implementation has to change. Compare A/B/C against these exact tests.

## Engineering strategy if B wins

Introduce a **source-aware content resolution boundary** while maintaining compatibility adapters for the existing one-world-plus-campaign view. Consider explicit qualified references (library/source + kind + id, eventually version) for new reusable content while preserving old identifiers through migration/translation. Do not presume the existing actor, entity, image, and group paths can all be generalized identically. Separate logical source IDs from physical Git repos/paths.

Only after the prototype and migration plan should this document be promoted into an independently reviewed spec on a branch created from `main`.


## 2026-10-15 proposed refinement: single-root World facade over dependency DAG

**Status: strongest current hypothesis; not approved for implementation.**

A campaign addresses exactly **one World interface**. That world owns defining setting content and acts as the sole read facade for a directed acyclic dependency graph (DAG) of reusable catalogs or supplements. Child modules may import further modules; do not require recursive *world-to-world* inheritance. Other top-level worlds (Faerûn, Krynn, Athas) may depend on the same catalog nodes. Mechanics remain independently chosen per campaign (one module or none); the world may recommend a default.

High-level interface concept:

```python
world = resolve_world("forgotten-realms", additions=optional_addons, lock=content_lock)
world.get(qualified_id)           # definition with source/provenance
world.find(kind="items", name="Longsword")  # discovery, potentially multiple candidates
world.available(kind="creatures")  # lazy query/index, not prompt inclusion
campaign = Campaign(world=world, mechanics="dnd5e")  # or mechanics=None
```

The exact APIs are illustrative only. Campaign-specific additions could be applied as a **world-view manifest** at the resolver boundary, without creating a second World or mutating the underlying base setting. Saved presets may reuse the manifest across campaigns.

**Core invariants:**
- Single top-level World facade from the campaign's perspective; all dependencies resolve beneath it.
- Graph must be acyclic, detect missing dependencies, and have deterministic stable resolution.
- Imported definitions retain namespaced source identity (`source/kind/id`) regardless of collisions in display names; *do not* merge by name.
- World-owned records are authoritative about the setting; explicit local substitution, exclude, or variant can override a deliberately targeted imported entry. Avoid silently choosing winners based on import order.
- A reference to a record should never change identity when mechanics changes.
- The campaign's own state remains mutable through current copy-on-write semantics; definition/template is distinct from campaign instance.
- Content availability and prompt activation are separate. A resolved world may index tens of thousands of records and still expose only relevant text to models.
- Dependency version/snapshot policy must be chosen deliberately so edits, removal, or upgrades of shared libraries don't silently rewrite established campaigns.

**Implementation alternatives to spike:**

1. **Virtual world facade**: all readers use an object that resolves the DAG on access. Conceptually pure, but Grimoire's existing `overlay.py`, actors/assets/sync code expects physical `wroot: Path` in many places, so this may be an invasive refactor.
2. **Compiled/materialized effective world**: resolve the DAG into a stable, versioned, read-only filesystem projection plus provenance map. Campaigns continue inheriting from a single physical `wroot`, minimizing initial overlay changes. Requires lifecycle, version, cache, asset and actor identity conflict design. Materializing files is not the same as eagerly inserting content into prompts.

Both preserve the external single-world contract. Compare against the existing copy-on-write/synchronization system rather than selecting based on abstract elegance. A stable *source-qualified ID* could be the storage identity while a resolved/exported world view provides convenient local names or aliases.

**Concrete proving cases:** Faerûn/Krynn/Athas share catalogs; Faerûn contains its own signature NPCs; campaign enables Chub character cards as a view addition; alternative Drizzt remains separate until explicit reconciliation; mechanics-free play still sees all lore; mechanics switch cannot rewrite identities; library upgrade and missing library do not corrupt campaigns. Also verify image refs, character versions, record deletions/detachments, and sync behavior.

**Open questions to settle before code**: world additions per campaign vs saved presets; library versions pinned at campaign creation vs auto-follow + sync; local overrides against imported source; conflicts/alias exports; virtual vs compiled resolution; reversible upgrades.


## 2026-10-15 insight: existing campaign view already models inheritance-until-divergence

The current campaign is not just a consumer of static world records: it presents a **logical effective world**. Unmodified records are read live from the selected parent world; campaign edits materialize a local copy, hide an inherited record, or detach a reused identity. This is already a one-parent, two-layer resolution model.

Read from `backend/src/grimoire/store/overlay.py`: `list_entities` combines local records and non-tombstoned world records; `read_entity` tries campaign first, then world. `backend/src/grimoire/store/sync.py:incoming` checks the base hash of materialized records against the current parent; when the world changed, it can offer updates or identify conflicts. `modules/binding.py` separately resolves campaign mechanics over a world default (including explicit `none`). These observations suggest a general **layered view** abstraction is worth prototyping.

Conceptually:
```
Shared catalog(s) --+--> effective World: Forgotten Realms
                     |          [local records + world overrides]
                     +------------------|
                                        v
                             effective Campaign world
                             [campaign records + campaign overrides/state]
```

Or symbolically, with provenance preserved:
`CampaignView = Overlay(CampaignLocal, WorldView)`
`WorldView = Compose(WorldLocal, ImportedLibraries)`

This need not create physical duplicate worlds, nor imply that runtime campaign state is literally the same type as an authored content module. It suggests **reuse the same resolver concepts** (lookup, exclusion, qualified identity, provenance, divergent-copy reconciliation) at both boundaries, with distinct policies where appropriate.

**Important distinction:** the existing overlay is over ONE physical `wroot`. Its `kind/id` references, assets, actor directories, group membership, sync manifests and identity ledgers assume a single parent. Turning it directly into an arbitrary DAG by recursive call is NOT proven safe. Multi-parent imports need source-qualified identities and explicit conflict handling; inheritance of world *definitions* is not automatically identical to inheritance of mutable campaign records.

A compiled effective world is potentially an **implementation/cache strategy** for the same logical layered model, not necessarily a competing product architecture. Compare a direct source-aware resolver with a materialized read-only projection. The central study question is whether the existing `overlay` APIs can be generalized incrementally, or whether the world resolver should produce a legacy-compatible effective root until migration is safe.

Changes in dependencies: unchanged inherited entries see new parent content; modified entries should be offered update/reconciliation via recorded bases, rather than silently overwritten. Pin/snapshot content selection and a consistent view at campaign-turn boundaries so a model call cannot observe mismatched versions of the same world mid-turn.


## Dependencies transferred from older backlog issues

The future World View and qualified identity work must account for requirements formerly tracked by [#50](https://github.com/charlesmsiegel/grimoire/issues/50): intentional cross-world character variants with provenance, version/asset safety and per-campaign selection; [#42](https://github.com/charlesmsiegel/grimoire/issues/42) and [#43](https://github.com/charlesmsiegel/grimoire/issues/43): source-aware spatial graph links, queries and map views; and [#55](https://github.com/charlesmsiegel/grimoire/issues/55): source-aware edit activity and affected campaigns. These are **future consumers** of this open architecture decision, not evidence that the resolver design is settled. See [deferred-systems.md](deferred-systems.md) and the [full issue archive](archived-issue-text.md).

Inventory item definition vs possession identity is likewise a consumer. The [inventory roadmap](inventory.md) deliberately avoids locking in old issue assumptions about whether `items` already exists.

## 2026-10-15 source-of-truth constraint

**User preference:** authored data must remain editable Markdown (and the existing human-editable JSON records remain authoritative where already in use). SQLite and other derived databases are allowed as disposable indexes/projections, never the only location of campaign facts. Moving canonical data to a DB requires evidence of a superior RP experience—not speed or easier development alone. See [file-authority.md](file-authority.md) for details, test criteria and the possible transaction/correctness exception. The virtual-vs-compiled World View comparison must preserve the file authority constraint.


## 2026-10-15 authoring requirement: external and in-app agents share one file-backed world model

The author currently uses Claude and Codex to integrate character cards, internal lorebooks, groups and related records. Grimoire should eventually offer equivalent world-maintenance flows in-app, but external agents and hand editors must remain supported. The dependency resolver must provide the same effective world identity/provenance to players, in-app curation and external authoring tools. Structured, reviewable change sets should target explicit scopes (owning world, campaign or shared library), rather than mutating an arbitrary physical parent in the DAG. See [world-maintenance.md](world-maintenance.md).
