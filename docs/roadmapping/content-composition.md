# Worlds, content packs, and mechanics

> **DISCUSSION LOG, NOT AN ACCEPTED ARCHITECTURE.** Later entries revise earlier proposals. Current open decision and repository findings are in [content-architecture-study.md](content-architecture-study.md).

Status: architectural direction recorded; detailed resolver and UI policies still open.

## Agreed design boundaries

- **Worlds are independent of mechanics.** A world can declare a recommended/default mechanical system, but that is a suggestion, not a requirement. Mythic Europe may recommend Ars Magica 5e while supporting campaigns using D&D, World of Darkness, or no mechanics at all.
- **A campaign selects one mechanical system**, optionally none. A deliberate ArsM5+WoD20 hybrid must be implemented as a new composite mechanics module, not implicitly assembled from two simultaneous engines.
- **Content packs supply reusable world definitions**, such as Item, Creature, and associated supporting records. A world can reference a pack without copying or surfacing every one of its entries as local world entities.
- **Content visibility is contextual.** A Forgotten Realms world may declare use of a D&D creature pack; a particular campaign or scene refers to a Goblin only when relevant. Do not clutter the world editor or routine prompt context with every entry in a referenced pack.
- **A single conceptual entity may have multiple mechanical representations.** A goblin has a shared fiction-level identity/description, potentially with independently authored ArsM5, D&D, or WoD-specific stat representations. The campaign's selected mechanics controls which representation to use. Absence of a compatible representation must be explicit: never silently use a D&D stat block for WoD.
- **Mechanics plugins define rules and mechanical semantics; content packs hold entities.** An entity can carry system-specific mechanical overlays without changing its core fictional identity. Campaign instances hold state (e.g. current wounds, possessions, dispositions).

## Illustrative resolution

World: Forgotten Realms
  references: generic D&D creatures content pack, other chosen packs
  recommended_mechanics: D&D (specific edition optional)
Campaign: a specific Forgotten Realms campaign
  mechanics: D&D 5e
  visible/relevant definitions: Goblin when encountered
  runtime instance: Goblin #17, specific injuries, inventory, relationships

The same world in a World of Darkness campaign should keep fictional Goblin identity while finding a WoD-compatible mechanical overlay (if present). Campaign-local state should not leak back into the shared definition.

## Key unresolved design questions

1. Are pack references transitive? What happens with missing/incompatible packs and version constraints?
2. How is conceptual entity identity represented across content packs? Stable IDs, namespaces, aliases, variants, and duplicate handling.
3. How should mechanical overlays be keyed (system ID and edition/version), how are conflicts resolved, and can world/campaign overrides select one?
4. Does a referenced pack appear in catalog search by default, only on-demand, or through filtered views? Keep routine prompt context sparse even when thousands of items are available.
5. How are campaign instances created and tracked separately from templates/definitions?
6. What rules govern reusable generic entries vs setting-specific versions (e.g., a setting-specific goblin)?
7. How does importing third-party content preserve provenance, licenses, and user edits?
8. Should a world or campaign select only whole packs, or subcollections and exclusions?
9. How does a chosen mechanics plugin advertise required mechanical data, and how should missing data be synthesized, manually entered, or left unknown?

## Design warning

Avoid hardcoding D&D objects into the core ontology or treating stat blocks as primary identity. Equally, avoid prematurely constructing an elaborate universal ontology. Prototype with Creature and Item across contrasting mechanics systems to find the minimum viable resolution and overlay model.

This is a discussion record, not an approved implementation spec. Promote a narrower slice to a separate spec and implementation branch when ready.


## 2026-10-15 correction: reject universal entity identity as the default

The earlier suggestion of one conceptual Goblin with multiple ruleset overlays was **too aggressive**. World of Darkness goblins and D&D goblins are fundamentally different things despite sharing a name. Even D&D and Ars Magica dragons may only partly overlap. Longswords are much closer across systems, but that alone does not justify a universal canonical Longsword.

**Revised provisional preference:** definitions are pack-local by default. Similar names imply nothing. Optional explicit cross-pack relationships (equivalence, adaptation, specialization, inspirational reference) are available only where a genuine need emerges. Don't require cross-system mappings or inferred semantic identity.

A simpler first implementation could retain existing Creature and Item world records, allow worlds to reference packs, keep definitions distinct and expose only context-relevant entries, and attach compatible mechanics data to entries that actually need it. Duplicating a small amount of descriptive content may be less costly than maintaining fragile global ontology rules.

Before specifying mechanical overlays or cross-pack identity, prototype real examples: D&D goblin vs WoD goblin (distinct), D&D vs ArsM dragon (potentially related but not identical), and mundane longsword (high conceptual overlap). Determine whether shared content provides enough concrete value to justify another abstraction. This correction supersedes the implied universal-creature identity in the earlier illustrative section.


## 2026-10-15 scenario: Forgotten Realms with and without mechanics

A Forgotten Realms world may have a **recommended/default D&D mechanics module** and references to D&D generic content packs plus Forgotten Realms-specific content packs (creatures, items, lore, places, people such as Drizzt and Elminster). Starting a campaign defaults to the world's recommendation but lets the player explicitly select **no mechanics** or another module.

**Content remains available independent of selection.** A mechanics-free Forgotten Realms campaign still draws on those packs for fictional context. D&D-only stats have no adjudicative force when mechanics is none; they should not disappear from storage or cause their parent content to vanish. Selecting another system uses that system's rules; incompatible mechanical data may require explicit adaptation.

**Pack availability is not prompt inclusion.** Referenced packs are searchable/catalogued; only relevant records enter prompts or active state. Do not dump the bestiary into every scene.

**Setting identities:** a Forgotten Realms world can reference a named pack character directly rather than copying it. If both world and pack contain a Drizzt or Elminster record, names alone are insufficient to merge: require an explicit correspondence, a deliberate override, or distinct identity. Consider pack-defined base record + world-local overlay + campaign instance/state; avoid making world and pack compete as two sources of truth.

**Unresolved:** whether choosing alternate mechanics automatically adds corresponding generic packs (versus keeping the world's fixed content selection), how much mechanical metadata is retained for nonmechanical descriptive use, and UX for inspecting/reconciling potential duplicate characters. No cross-pack name-based auto-merge.


## 2026-10-15 reconsideration: is Content Pack even a distinct type?

**Not settled.** The separation between a world and a content pack may be artificial: both can contain Creatures, Items, Characters, Locations, factions, lore, dependencies and reusable references. Do not adopt the previously sketched pack/world architecture as approved.

Candidate alternatives:
- **A. Distinct world and content-pack types:** clear user-facing labels, but blurred membership boundaries and duplicated storage semantics.
- **B. Composable content collections:** one underlying collection/reference model; World, Bestiary, Equipment Catalog, Setting Supplement, etc. are roles/entry-point labels, not necessarily storage types. A world is a collection selectable as a campaign setting, carrying default/recommended mechanics and starting assumptions.
- **C. Worlds with attached libraries:** preserve world as fundamental object, with reusable supporting libraries, but potentially awkward cross-world reuse.

Provisional preference to test: **B**, subject to inspecting current world-information storage and layering first. Identity collision must not imply automatic name-based merging. Keep campaign state separate from reusable definitions. Keep world content selection and the campaign's single selected mechanics system independent.

Before committing to an architecture, evaluate (1) Forgotten Realms with reusable D&D bestiary, (2) Drizzt defined in a setting collection versus linked by a world, (3) same entity name with differing meaning, (4) a mundane longsword used across several settings, (5) a mechanics-free campaign, (6) how prompt retrieval stays sparse, and (7) what existing Grimoire layering already offers.

Next step should be a constrained design comparison against existing code, **not** a universal ontology or a full implementation spec.


## 2026-10-15 working direction: composition primarily solves authoring reuse

Motivating requirement: a shared D&D bestiary and item catalog should not be copied into Faerûn, Athas, Krynn, and every other setting. These settings should be easy to author, assembling common content while adding local material.

Candidate minimal model: **Content Module** as a reusable, named collection of existing world-information records. A selectable **World** can be a content module with campaign-entry metadata (recommended mechanics, premise, defaults), rather than a wholly separate content-storage type.

- Importing a module should create **references**, not duplicate records or inline everything into every prompt.
- Each record needs stable, namespaced identity. Names are labels and cannot establish equivalence.
- Imports should support an explicit **include/exclude** policy (Athas, for example, may exclude generic D&D material).
- Setting-specific variation should initially be modeled as an explicit locally named variant referencing its base, rather than automatic implicit shadowing, merges or mutable global overrides.
- Active campaign instances remain separate from reusable definitions and share no mutable state with them.
- Campaign mechanics remain independently selectable (one module or none) even if a world recommends a system.
- Retrieval and visibility must remain relevance-driven; import makes a catalog available, not perpetually prompt-resident.

**Defer** universal creature identity, arbitrary overlay graphs, cross-system stat conversion, global record merger, and generic inheritance algorithms until clear authoring cases demand them.

Next design test: build a tiny example using one shared bestiary and equipment catalog and three worlds (Faerûn, Athas, Krynn). Attempt (1) adding a setting-specific creature; (2) excluding an imported item; (3) customizing an imported creature without changing other worlds; (4) a campaign using no mechanics. Measure authoring friction and ambiguity before committing to a major refactor.


## 2026-10-15 correction: don't split a world's defining content by default

The earlier example importing `forgotten-realms/characters` and `forgotten-realms/locations` as separate modules was needless architecture. If Forgotten Realms lacks its signature characters, places, factions and lore, it is not meaningfully the Forgotten Realms setting.

**Working principle:** a world owns its defining setting content directly and **optionally imports truly reusable content** such as a shared D&D bestiary or equipment catalog used across Faerûn, Athas and Krynn. Imports are not mandatory: a world may be entirely self-contained. Content modules remain useful for genuinely shared catalogs or deliberate separately maintained expansions; there is no requirement to package each record category separately.

A separate Waterdeep regional module, an alternate edition, or an optional supplement might be valuable *when there is an actual independently reusable, versioned or optional boundary*. These are optional authoring choices, not a default structure. Physical folders or lazy retrieval do not imply logical package dependencies. World size alone is no reason to split modules: selective retrieval controls prompt size.

The world continues to recommend, not require, mechanics; the campaign selects one mechanics module or none. A character's setting identity is world-owned; optional mechanical statistics are selected according to the campaign's mechanics.

This supersedes the example that treated core Forgotten Realms characters and locations as external required imports. Keep the module abstraction minimal until authoring requirements demonstrate that more machinery is useful.


## 2026-10-15 example: third-party Faerûn character-card collections

**Use case:** A Chub.ai creator supplies many cards for characters in Faerûn. Current Grimoire stores them inside a custom Forgotten Realms world. With a reusable canonical Forgotten Realms world, how can a user play with that collection without copying or creating a second full world?

**Working answer:** distinguish the **base world** from optional **world-compatible content add-ons**. A Chub Faerûn character collection is not necessarily a standalone playable world; it can be a content module containing new NPC definitions and/or alternate portrayals of existing characters. A campaign selects the Forgotten Realms world, independently chooses one mechanics system or none, and enables that collection. The enabled collection is searchable/retrievable rather than pasted into every prompt or activated as an entire speaker roster.

Possible reusable personal configuration:
- **Direct campaign composition:** Forgotten Realms world + optional Chub cards + selected mechanics, for one campaign.
- **Saved world configuration/preset:** Forgotten Realms + preferred collection(s) and settings reused across many campaigns; no duplicated underlying setting content.
- **Derived/forked world:** only if the user actually intends to change setting-defining facts, continuity, cosmology, or other world identity, not just enable another NPC catalog.

Distinguish two import cases:
1. **New character:** a unique Chub character becomes another available world-compatible definition.
2. **Alternate portrayal of an existing character:** e.g. a Chub Drizzt card vs the standard world Drizzt. Do **not** auto-merge on name. Explicit choices might be ignore, add as distinct/alternate, or apply selected portrayal attributes to the canonical character via an overlay. Preserve the imported card's source identity and provenance.

A Chub card may also contain card-local roleplay scenario instructions, greeting, or scene assumptions; those are not necessarily permanent world facts. The importer must not blindly insert them into canonical setting truth.

**Repository layout is a publication/storage concern, not the runtime ontology.** The same module format can be stored in grimoire_worlds, grimoire_content_packs or a private/unofficial repository; personal campaign saves and user state belong outside versioned reusable definitions (e.g. grimoire_data). World and add-on membership do not need to mirror Git repository boundaries.

This is a **provisional pattern**, not a finalized schema or decision to add a World Profile entity. Further questions: are add-ons attached to individual campaigns, personal world defaults, or both? How should existing-character portrayal conflicts and optional overrides work? When does a card's scenario text apply? How is compatibility signaled without enforcing nonexistent universal world identity?
