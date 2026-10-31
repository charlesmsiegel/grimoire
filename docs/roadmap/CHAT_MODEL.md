# World-optional chats, reusable portrayals and navigation

**Status: V2 architectural decision and implementation handoff, not an existing playable feature.** Implement the follow-up to the shipped Sprint 010 contract in [Sprint 107](sprints/world-optional-chat-scopes.md). The first usable world-less text chat is a **Wave 5** gate; desktop/phone navigation starts in Waves 5–6, with a richer library browser later. The [ordered roadmap](README.md) schedules these tasks. Do **not** re-open or rewrite 010 to get there.

## Decisions

1. A **chat** is a persistent conversation thread, with its own stable ID, posts, scene segments, revision and provenance. It is not a character and is not the same thing as a world or scenario.
2. Each chat has **exactly one durable play scope** for campaign-local actor identities, history, beliefs, effects and controller assignments. In V2 this is a minimal **campaign namespace**, created automatically even for a one-off chat, because `InstanceRef` already uses a `campaign_id`. Creating the namespace must be a constant-time local operation with **no world**, location graph, mechanics, character roster wizard or provider inference required. `world = null` is a valid, deliberate value, not an error or a phantom world.
3. A campaign is an optional **user-facing collection/ongoing continuity**, not a mandatory setup ceremony. An unnamed one-chat scope is shown as a chat. Naming/grouping it as a campaign changes presentation and grouping metadata, not the campaign/actor IDs or its accepted history.
4. The chat can reference **multiple** characters, scenarios, personas and lorebooks; one optional current world may be attached later. These are source-qualified, scope-validated associations, **not mutually exclusive parents**. The chosen primary launch character/scenario (or a blank/premise launch) is retained as provenance and for display even after other sources are added.
5. A character definition is reusable without a world. A world may provide a **derived portrayal** using an explicit revision-pinned base definition and typed inherit/replace/clear decisions. The same source concept and the four Elderfen alternatives are **not identical campaign actors**; worlds bind their own authored variants, while each play scope instantiates independent lived versions. Sharing a name never asserts the derivation.
6. Source definitions, lorebook entries and scenario instructions are **authored material**, not automatically accepted world events or known actor beliefs. Adding a card or world is reviewable and does not retroactively make every participant aware of its secrets.
7. The **chat owns its history once**. Character, scenario, world and campaign screens show *derived reverse indexes* of that same chat record. Deleting a card, hiding a world or changing a display name does not delete or reparent a chat; broken source references remain visible with retained IDs and a clear repair path.

## Concrete use cases

### Elderfen: one reusable concept, setting-specific portrayals

An optional reusable `catalog:elderfen-cast/character/charlotte@r1` can own the stable characterization, voice anchors and shared art associations. Independently authored portrayals such as `world:elderfen-modern/character/charlotte@r1`, `world:elderfen-mecha/character/charlotte@r1`, and `world:elderfen-vampire/character/charlotte@r1` may declare explicit `derived_variant` links to a **pinned** common base. A Regency Elderfen portrayal can itself be authored/derived without being the one true ancestor.

Effective-definition composition must:

- combine explicit named authored fields/sections and supported facets in a deterministic, bounded order; distinguish **absent** (inherit), **replaced**, **explicitly cleared/suppressed**, and **local-only** values, with field-level provenance;
- reject derivation cycles and ambiguous simultaneous bases; do not silently equate `alternate_portrayal` with inheritance or infer inheritance from matching names;
- preserve exact pinned source revisions, authored origin, and future reviewed source rebase; one update must not rewrite another world portrayal or campaign history;
- allow world-local substitutions of species, occupation, biography, relationships, voice anchors and art placements **without leaking source-incompatible facts**. Relationship remapping is explicit and belongs to the graph/portrayal consumers, not a name-based rewrite; art/voice merging is provided by their owners;
- support **no common base at all**: a world-local or imported standalone card remains fully valid.

This is a first-slice composition responsibility of **022** (authored sections/facets and provenance), with **027/038/046/042** owning generated voice, art, and mature inheritance/rebase workflows. The core 009 entity instance resolver already composes instance facets; it does **not** yet compose one definition from another.

### Quick chat from standalone cards

A player selects either (a) one character card, (b) a scenario card, (c) a character plus one or more lorebooks, or (d) a simple premise. They choose a persona and optional boundaries only if desired, then press **Start Chat**.

**Scenario-only play must still be genuinely playable.** Without any speaking NPC, a reply may come from a separately authorized, explicitly metered **director/narrator generation stage** instead of a fake `Agent` NPC. The current Wave 1 `TurnInferencePlan` and `story-quality/2` **do not have a narration stage**; 027 must introduce a deliberate planned stage and compatible evaluation version (`/3` if necessary) **before** 035 can claim scenario-only generative play. Adding actual NPCs uses one independent RP generation per speaking NPC. Extra director calls when NPCs already answer are opt-in/visible, not an automatic hidden token tax.

Conceptually the runtime creates:

```yaml
play_scope:
  campaign_id: quick-123             # generated internal stable namespace
  world: null                        # intentionally absent, no world wizard
chat:
  id: thread-1                       # stable id independent of display title
  scope: quick-123
  launched_from:                     # optional, pinned or explicitly following source
    type: character_or_scenario
    ref: catalog:elderfen-cast/character/charlotte@r1
  attached_sources:                  # references, not full materialized copies
    - ref: library:personal/lorebook/notes@r2
  actors:
    - campaign:quick-123/character/charlotte-1
  primary_label: Mara           # editable navigation label, not identity
  world: null
```

*Illustrative shape only—not a committed on-disk schema or an implemented command.* A character card may carry embedded `scenario`, `first_mes`, alternative greetings and lore; the importer preserves distinct authored roles. A **scenario-only** card is a scenario/source document, not a fake NPC with an `Agent` facet. An imported card never gains tool authority.

Adding a second character, scenario or lorebook to this same chat updates associations, creates *new* campaign actor instances when appropriate, and records the effective source revision and time of introduction. An added actor did **not** witness the preceding history by default. Prompt retrieval is bounded and only sees relevant, audience-authorized entries. Extra cards cannot multiply background model calls; each **speaking** NPC still receives its own generation.

### Promotion without re-creation

At any point the player can name/group the quick scope as a campaign, choose an existing world, or author a new world from **selected, explicitly approved** facts, definitions, locations and relationships.

- **Naming/grouping:** retained scope ID and chat ID; no content copies.
- **Attach an existing world:** add a revision-pinned (or reviewable follow) source binding. Preview contradictions with established chat events and actors; no automatic replacement, rewriting or global knowledge grant.
- **Create a world from the chat:** propose a new reusable source containing selected authored setting material, with provenance back to the chat; approved canon belongs to that source. The chat retains its own actor histories and does not become the world's only owner.
- **Add more cards:** source relationships and actor instantiation are explicit, with version and conflict checks; text history does not reset.
- **Start a fresh chat with the same character:** create a separate play scope/actor instance unless the player deliberately chooses to continue within an existing campaign. Same card identity does **not** imply shared memories.

These promotions are later functionality in **042 / 052 / 056** as appropriate; Sprint 107 only proves link metadata survives and no identity is rewritten, not a full world editor.

## Navigation model: one record, many views

| Entry point | Shows/creates | Relationship to chat |
|---|---|---|
| **Recent / All Chats** | Every conversation, filter and resume | Direct index by chat ID and last activity |
| **Characters** | Library and world-specific portrayals | Start new with a selected portrayal; list existing chats referencing the *exact* source, with explicit equivalent/derivation filters |
| **Scenarios** | Standalone scenario cards and authored openings | Start a chat from a selected scenario; list chats launched from/attached to it |
| **Campaigns** | Named continuing play scopes and grouped conversations | List chats in that scope; promote an unnamed quick scope to a visible named campaign without changing IDs |
| **Worlds** | Optional setting sources and associated campaigns | List connected campaign/chat links only; world-less chats stay visible elsewhere |
| **Collections / Lorebooks** | Optional source packs and context bundles | Attach to an existing chat, or start a chat with selected source material |

**Filters are derived**, using stable source and instance references, not copied chat folders or a mutable single `character_id` foreign key. One chat may appear under multiple characters and scenarios while its canonical record appears **once**. A character is **not** a unique owner of a chat (group chats exist). A chat may have a different current cast than its original launch source; `launched_from` is immutable provenance, while `attached_sources` and roster are versioned mutable associations.

Minimal UI behavior: a unified chat list with search/recency, recognizable provenance chips, a **Continue** action, and **New Chat** from a character or scenario; allow adding cards mid-chat and a **Make/Attach Campaign or World** action without changing chat ID. Mobile and desktop should use the same association semantics. The fuller browsing and cross-link experience belongs to 065, but **035 cannot require users to build a world to start**.

## Migration and continuity

- **037** independently imports raw character, scenario and lorebook cards into sources (first usable formats, then V2/V3/PNG/CHARX), preserving original bytes, fields, greetings, alternate starts, art and provenance. It must run without a world and must be in **Wave 4** so **035** can consume it in Wave 5. It is independent of **026** (V0.2.1's reviewed world/campaign import), not another mandatory step in that importer.
- **036** extends fidelity to actual **SillyTavern chat-history exports**, including user persona, ordering, edits/swipes/alternate branches, group-chat speaker attribution, and linked lore/author notes. Only accepted alternatives become the active branch; importing a text line does not infer which actors saw it. Distinguish exact imported transcript, inferred state, and unverified content. No model calls by default.
- **035** proves two paths: a normal V0.2.1 imported world campaign and a **new world-less chat** launched from a character/scenario plus optional lore. Both must be persistent, resumable and charge exactly one independent RP-generation call per speaking NPC.
- **044 / 052** expose mobile handoff and quick-start onboarding; **065** improves cross-index navigation; **056 / 042** provide richer source composition and later world promotion.

## Acceptance and failure cases

- Two separate chats with the same character card have independent identities, memories and histories; a same-named but different-source character is never auto-matched.
- Quick chat starts **without** world manifest, mechanics, location topology or a background NPC LLM call; save/reload keeps chat, source provenance and persona.
- Scenario-only + lorebook can start a chat with no fake NPC. A second card added later does not see the preceding private dialogue unless explicitly introduced/authorized.
- A derived Modern Elderfen portrayal inherits selected core Mara voice/traits, replaces occupation and background, and explicitly clears incompatible facts; a Vampire variant can replace species. A disconnected namesake is not inherited.
- One chat is discoverable through its character **and** scenario filters without duplicate state. Removing the source reports missing, keeps the chat and offers repair; deletion of a navigation link does not delete history.
- Converting to a named campaign or attaching a world changes associations only and preserves accepted events and actor IDs; conflicting world facts require review.
- ST history import preserves exact source order, speaker, swipes/alternates and attachment references or reports fidelity losses. Never silently interpret out-of-character instructions as objective truth.
- All source updates/imports and synced chat links are revision-guarded; no V1 mutation, untrusted card execution or private data in public fixtures.
