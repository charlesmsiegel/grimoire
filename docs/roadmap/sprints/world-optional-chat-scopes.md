# World-optional chat scopes, durable associations and lookup

**Status:** new **Sprint 107** (Wave 3, P0), a follow-up to the already designed **010 Native File Store**, *not* a reopening of 010. Read [World-optional chats and navigation](../CHAT_MODEL.md) and [Wave 2 handoffs](../WAVE2_HANDOFFS.md).

**Outcome:** A chat can begin from one standalone character, scenario, lorebook or premise **without any world** and still have safe campaign-instance identity, durable provenance and navigable associations. It can later acquire more sources or be grouped into a named campaign without copying its history.

## Scope and first proof

Implement an additive, guarded extension of 010's generic source/campaign file store:

- Create a **lightweight play scope** with stable `campaign_id` automatically when a chat starts. A full authored world is *optional*, so `world: null` is valid. An unnamed scope is not a mandatory visible campaign or a world.
- Introduce a stable **chat/thread record ID**, distinct from a campaign ID, scenario/source ID, actor instance ID and scene/event ID. The chat's canonical home is its one play scope; individual scene segments/messages live under later scene/event stores.
- Record immutable *launch provenance* (character, scenario or free premise) and mutable **many-to-many** attached source bindings (characters, scenario definitions, lorebooks, cards, optional world), current roster/persona references and meaningful source revision/pin selection. Keep authoring documents distinct from accepted facts.
- Preserve source binding edits (add card, remove source, attach world, name/group) under 010-style revision/authorization semantics without reparenting chats or recreating actor IDs. A missing/deleted source yields a broken reference, not a same-name fallback.
- Define **rebuildable inverse navigation queries**: all/recent chats, chats by exact character or portrayal, scenario, campaign and world; queries return the *same canonical chat ID*, never copies. Bound the index and avoid scanning private content into prompts.
- Allow independently authored *scenario-only* and lorebook sources: a scenario is not forced into an `Agent` entity merely so a chat can launch. No model inference is needed to create or link records. **Actual generated replies to a no-NPC scenario belong to 027's explicitly metered director stage**, not this storage sprint.

## Proving scenario

Start a new chat from a standalone Mara definition with **no world**, one optional lorebook and a chosen player persona. Save/reopen and find it via Chats and Mara's own library view. Create a *second* chat with the same card and prove the two scopes have distinct `InstanceRef`s and histories. Attach a scenario and a new character to the first chat; find it through both source views, without duplicate records or implicit access to previous private events. Give the scope a campaign display title or attach a world reference; the original chat/actor IDs and provenance remain unchanged.

Also prove a scenario-only opening without an NPC and a source-removed/broken-link recovery case. No original SillyTavern/Grimoire V1 library is read or modified.

## Storage and system boundaries

- **Consumes** 010's secure generic envelopes/CAS/recovery, 009's instances, Wave 1 source-qualified identity and 016's optional settings where applicable. It does **not** modify the accepted 010 envelope schema, simulate a world, import cards, generate dialogue or own graph edges.
- Owns the minimum play-scope/chat metadata schema and a **derived navigation-index contract**, not UI layout or rich campaign promotion.
- Reserve and validate additive paths/record families inside `campaigns/<id>/...` as required; 010's current PR reserves `scenes`/`events`/`evidence`/`relations`, but does not yet implement a chat/manifest family. Do not reuse these names or bypass its authorization/transaction boundaries. A source may remain a standalone catalog with no world source.
- World/source selection is explicit, optional and revision-aware. A display title or launch character is not an authorization credential or a primary key.
- Joining a world or publishing selected chat-derived canon is a **reviewed later change** (042/052/056); this sprint tests stable association/promotion metadata, not a full world-authoring engine.
- Cross-device revision/conflict handling belongs to 019 and transport to 028; chat metadata must be compatible and conflict-preserving.

## Required negative/failure tests

- Omitted world succeeds; a required-world validation failure is a sprint defect.
- Same-name, different-source cards never merge. One actor's past knowledge is not silently given to a newly added actor.
- Renaming, card deletion, adding a second scenario, changing launch display label or listing through two indexes never duplicates or erases chat history.
- An invalid/unauthorized binding, stale expected revision, path alias, symlink into V1, interrupted multi-record update or broken source cannot produce a successful commit. Use the 006 fault harness and 010 guards, and leave reversible conflict evidence.
- Source and reverse-lookup indices are rebuildable; cache deletion changes no canonical answer and issues no LLM calls.

## Dependencies and downstream ownership

**Required earlier-wave sprints:** 002 Source-Qualified Identity, 009 Composable Entity Facets, 010 Native File Store, and 016 Session Zero boundaries (optional config integration).

- 022 provides effective source/portrayal resolution, including world-less views and linked derived definitions, independently in Wave 3. Neither 022 nor 107 requires the other's *unfinished* implementation; **035** joins both interfaces in Wave 5.
- 037 (Wave 4) imports standalone character/scenario/lorebook cards into these source/chat contracts, independent of 026.
- 027 (Wave 4) remains responsible for individual actor generation and correct epistemic context, independent of whether a world is attached.
- 035 (Wave 5) implements the first working **world-less** text chat and a minimal chat-selection/navigation path alongside migrated-world play.
- 036 (Wave 5) adds SillyTavern exported chat histories; 044/052 (Wave 6) handle phone sync and fast onboarding; 065 (Wave 7) handles richer navigation; 042/056 handle reviewed world promotion and shared catalog composition.

## Later milestones / moonshot

Built-in chat browsing and grouping with source filter combinations, card-library previews, long-lived multi-chat campaigns, cross-device search and seamless selective promotion of chat-authored canon to a new reusable world without changing actor history.

This sprint must **not** be labeled complete merely because its schema is documented. The minimum guarded persistence and inverse-index proofs must be executable.
