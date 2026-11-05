# Consistent Effective-World Reads

**Status:** **first slice implemented**, headless: `grimoire.core.portrayal` and `grimoire.core.snapshot` (pure) and the `grimoire.effective` adapter. The contract is in [EFFECTIVE_VIEWS.md](../../EFFECTIVE_VIEWS.md). Milestone 1 is executable: a world-less character + lorebook chat, the Mara base with Modern and Vampire Elderfen portrayals (inherit, replace, clear, with per-field revision and etag provenance), and campaign instances over them. So is milestone 2's guarantee **on the snapshot side**: a turn holding one snapshot sees no half-update of a source, actor or binding. A derived-portrayal actor's perspective (`snapshot_actor_view`) and 024 promotion of a derived template instance compose through the snapshot. Action resolution (roster control, `propose_damage`) still reads the index, so joining it to the same snapshot, which the proving scenario's *action resolution* half needs, is 035's. The boundary is one loaded library; `load()` itself is not a point-in-time read, so a write *during* a load is reported as broken or drift rather than covered. Milestone 3, the virtual-vs-compiled comparison, is open. Snapshot-aware roster control and damage are a named limitation owned by 035.

> **V2 scope correction:** V2 does not have the V1 world/overlay reader yet. Resolve native world/catalog sources and campaign overrides from revisioned canonical files, using a frozen effective view per operation. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


**Outcome:** Every chat or scene sees a coherent, revision-pinned view of its selected sources and lived instances, **with or without a world**; world-specific derived portrayals reuse shared character definitions without copied full cards.

## Scope
- Offer get/list/search/resolve over world, campaign overlay and chosen imports.
- Pin a consistent read revision for an entire turn, export or worker job.
- Compare virtual resolution with a rebuildable compiled filesystem/provenance projection.

## Milestones
1. Resolve a revision-consistent, optionally world-less view of one direct catalog character, one explicitly derived world portrayal, and one campaign instance; preserve provenance and named inherit/replace/clear semantics.
2. Test a turn that runs while a source or overlay changes.
3. Compare virtual and compiled approaches for latency, compatibility and recovery.

## Moonshot
Make thousands of distributed authored records behave like one locally inspectable virtual world without losing source ownership.

## Proving scenario
A changed imported NPC does not appear half-updated between prompt assembly and action resolution; deleting a cache changes no answer.

## Independence and interfaces
Works against current files with a single source first; identity qualification is an interface, not a mandatory package-manager prerequisite.

## Wave 2 handoff: join opaque storage with typed instances

This is the first Wave 3 **integration consumer** of independently developed 009 entity/facet meaning and 010 revisioned opaque record envelopes. Resolve source-qualified definitions and campaign-instance overrides using 002's identity contract, return a revision-consistent view with concrete resolved heads and retained missing refs, and freeze it for one operation. The source data stays canonical Markdown/JSON; cached/compiled graph or world projections are disposable. If 009/010 disagree about payload codecs or revision semantics, fix that interface explicitly here rather than duplicating writer logic or rewriting both wave 2 modules.

## World-optional and derived-portrayal first proof

Consume the Wave 1 `DefinitionIndex`'s explicit `derived_variant` / `overrides` links, 009's `EntityDefinition` and 010's opaque record envelopes. Do **not** treat `alternate_portrayal` or coincidentally equal names as inheritance, and do not introduce an implicit compulsory world. Define a bounded and auditable **effective authored definition** before an actor is instantiated or prompted:

- Shared Mara core has authored personality, textual voice evidence and stable visual traits; Modern Elderfen derives and **replaces** age/occupation/background, while Vampire Elderfen may **replace** species and **explicitly clear** an incompatible fact. Absence means *inherit*, not *clear*; `null`/tombstone semantics are typed and recorded rather than inferred from prose.
- A derived portrayal references a **pinned** base/source revision (or an explicitly reviewed follow policy). A revision change, absent base or cycle is reported and never falls back by name. Only one unambiguous base chain is needed for the first proof; multiple bases require explicit conflict rules.
- Composed authored sections/facets and the resulting source revision/provenance are a **disposable view**. Neither base definition nor world portrayal is rewritten or copied in full. Campaign actor memory, relationships, observations and control remain owned by the instance.
- A chat that selected only a standalone character and an optional lorebook resolves correctly with `world = null`; one with a world sees only source bindings explicitly chosen for it, not the world's entire catalog. Do **not** depend on unfinished same-wave 107 for the first proof: use an in-memory source-selection contract, and publish an interface that 035 later joins with 107's persisted chat scope.
- 017 owns graph links among actual individuals, 018/038 own picture placements, and 027 owns the **textual voice** fallback/override behavior when a derived portrayal has no local profile. This sprint must provide sufficient provenance for those later merges without freezing their separate schemas.

[Chat/portrayal examples](../CHAT_MODEL.md#elderfen-one-reusable-concept-setting-specific-portrayals) and [Sprint 107](world-optional-chat-scopes.md).

## Obligations from 025

`abilities` ([fictional abilities](../../ABILITIES.md)) is a single facet, so evolving one ability on a sourced instance replaces a full copy that stops following head changes, and a head that gains the facet breaks an instance that *added* one. Per-ability inherit/replace/clear is a follow-up for this sprint (with 024).

## Open questions
- Virtual, compiled or hybrid?
- What is the exact revision boundary? *First slice: one `LibraryView` per operation (see EFFECTIVE_VIEWS); a point-in-time load is open.*
- How do UI edits display an out-of-date snapshot?
