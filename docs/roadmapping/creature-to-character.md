# Creature → Character — individualizing a creature profile

**Status:** proposed RP capability, design to validate; **not implemented or an approved specification**.
**Recorded:** 2026-10-18.
**Roadmap:** Characters & Resources / Worlds & Time / Content Identity / Mechanics Extensibility / Inventory & Party Management.
**No GitHub issue opened:** retain the pre-tech-debt issue-backlog reduction; evaluate for a small follow-on feature sprint or as a concrete World View/identity test.

## Player experience

A creature record describes a **kind of being**, such as a goblin, a marsh imp, a wolf, or an ordinary monster. A session can contain several indistinguishable members of that kind. When the players interact meaningfully with one, Grimoire should be able to **cement that one individual into a distinct character**.

The new character must have its own stable name/ID, dialogue (if any), memories, relationships, scene appearances, state, inventory/equipment and eventual mechanical consequences. Its **origin creature** supplies defaults and provenance. The source creature record remains available unchanged for every other member of that species/type, and several characters may share the same source.

**Illustration:** Three marsh imps appear. One talks to the PC and becomes an NPC. The other two remain anonymous instances of the generic creature profile. Later the individual may gain unique art, equipment, a different personality or modified statistics; none of those changes alters the archetype or the other imps.

This should be available **with or without a mechanics module**.

## Two triggers, one individualization operation

The distinction matters more than the UI:

1. **Recognition through play (the goblin case):** Several generic creatures exist in the scene; one is spoken to, named, or becomes important. The user or an accepted scene-extraction proposal promotes **that individual** to a campaign-owned character. This may use a light review/confirmation affordance so the game does not instantiate every incidental extra.
2. **Successful capture (the monster-catching case):** The game **immediately** creates or identifies a distinct character as the authoritative *result of the capture event*. The species/template remains unchanged. The newly captured monster must be addressable as a persistent actor in that same turn, not deferred to an optional later "promote" step. It can be quiet, unnamed, nonhuman or unable to speak; a `characters` record is an **individual**, not a requirement to be a conversational NPC.

The same underlying service should support both triggers: **`individualize(source_creature, existing_individual?, event_context, initial_overrides)`** (illustrative, not an API decision). It creates a character only when the encounter has no existing individual identity. If a named, already-individualized creature is captured later, **reuse that character**; do not create a duplicate because the triggering event changed.

**Example, no mechanics module required:**
- A wild creature of species Marsh Imp appears as a generic `creatures:` definition.
- A freeform roleplay scene ends with an accepted capture outcome. Grimoire immediately creates the particular Marsh Imp as a `characters:` actor, records the source species and acquisition event, and makes the individual available to the PC's companion roster. The creature does not need a dialogue/personality model yet.
- The PC later names this individual "Moss"; renaming must preserve the same actor identity, history and art overrides. Capturing a second imp mints a separate individual ID.
- The PC may have used one capture device (a fungible **inventory resource**) to do so, but the captured monster is **not itself a fungible item or a ball count**. Its actor/party identity lives alongside—not inside—the inventory ledger.

**With mechanics enabled:** the rules engine owns legality, chance/dice and whether capture succeeded. A failed attempt may still consume a ball but **does not create a captured character**. A successful outcome deterministically invokes the same individualization operation and commits the required inventory and capture/party effects as **one recoverable logical action**.

**With mechanics disabled:** a successful capture is an accepted/committed fictional event (user-confirmed or authorized by established scene-review policy). We do not require mechanics modules merely to instantiate companions, but an uncommitted speculative LLM suggestion must not automatically mint an actor.

### Required exactly-once and identity behavior

- One capture **event/operation ID** must correspond to at most one new actor. Retrying a tool call, accepting the same extraction twice, or recovering a crashed action must not create two copies. A second *distinct* capture of the same species is a new actor.
- If a specific encounter individual already exists, capture changes **ownership/relationship/party status**, not its identity, name, version or personal history.
- Record an explicit `origin_creature` ref plus optional `captured_from` / `created_by_event` / first-scene evidence. Treat the source as a qualified identity when World Composition exists; aliases and names cannot silently substitute another source.
- All actor creation and link/update operations use guarded file-backed store mutators. Partial failure of the capture effect + consumable decrement + companion association must be recoverable without duplicating the actor or losing the ball twice.
- Distinguish **existence as a persistent character** from **current cast seat**, **trainer's party/box membership** and **who may command it**. Capture can create a character and associate it with a player without forcing it into the active scene cast or conflating that with a PC identity. Full party-management rules are separately staged.

---

## What inherits and what diverges?

| Aspect | Default from creature | Individual override |
|---|---|---|
| Category/type, baseline physical description, general abilities | Reusable creature profile | Character-specific card text, traits, history and experience |
| Portrait / artwork | Creature primary image (potentially gallery) | Character/version art when supplied; source retained as fallback or explicit reset |
| Starting/base stats | Creature's **effective sheet for this campaign's selected module**, when the module declares valid cross-kind applicability or a conversion | Individual mechanics state and overrides; current HP, wounds, level/experience, conditions and equipment never update generic templates |
| Lore of the creature type | Relevant creature lore, activated by source relation | Personal knowledge, memories, relationships, dialogue and voice |
| Identity/provenance | Explicit origin ref (`creatures:<id>`, eventually source-qualified when composition supports it) | New unique `characters:<id>` per individual |

The user's stated intended behavior is **inherit defaults until the individual replaces them**. For portraits and general descriptions this favors **read-through fallback**, not a one-time copy masquerading as inheritance. An edited individual image or trait shadows only that default; the source remains available for other creatures and for explicit reset. When the source disappears, existing personal overrides survive and missing inheritance is visible rather than silently re-associating by name.

An implementation may materialize or cache inherited data internally, but the **logical contract** remains source-backed. At first instantiation, an authored snapshot of inherited fields might be a deliberately limited MVP, but we must describe it as a compromise and test the difference; changing the creature's portrait later would not update a snapshot.

**Mechanics distinction:** species/base statistics are definition data; current HP, conditions, experience, capture status, trained moves and other changing attributes belong to the individual. A change to the creature template cannot overwrite those personal values. Different mechanics packs may map creature sheet fields to individual character sheet types differently; an explicit module mapping/conversion is required, not a bypass of `sheet_kind` checks.

**Evolution/species change (future-proofed but not a launch prerequisite):** a captured individual can change the creature archetype from which its base traits are derived without losing its character ID, owner, personal provenance or relationship history. A ruleset determines which individual stats are recalculated, retained or migrated; existing individualized overrides must not be dropped silently.

## Existing Grimoire code relevant to feasibility (main, inspected 2026-10-18)

1. `store/overlay.py:create_character` creates and identifies campaign-owned characters under the campaign lock, marking them detached from any accidental later world-character ID collision.
2. `store/characters.py:create_character` creates V3 versioned character cards, using `character.md` frontmatter and a per-version JSON card.
3. `store/entities.py` keeps `creatures` as generic Markdown entities. `store/entity_schema.py` explicitly separates their typed descriptive fields from module-defined **sheets**.
4. `store/sheets/paths.py` and `writer.py:_validate_write_target` check sheet-type **kind**: a type declared `kind: "creatures"` cannot be assigned unchanged to a `characters` sheet. This is the principal hard edge in inheriting stats; don't bypass the validation or copy a file under a new kind and call it valid.
5. `store/record_art/` already supports reuse of a visible record's portrait into a different record's art slot, and the content-addressed image store avoids unnecessary duplicate blobs. Reusing a placement **at creation** is available; a persistent cross-kind fallback when the source changes is **not** the existing same-record world→campaign overlay.
6. `store/passage_evidence.py:save` and `routes/scenes.py` already support a reviewed "create character from this passage" workflow, including campaign-local creation, speaker/cast seating and provenance. The creature-origin UX should reuse it where applicable, not create a second emergent-NPC stack.

**Conclusion:** a minimal **Instantiate Character from Creature** is plausibly a modest scoped feature using existing builders and image reuse, and is shared by both recognition and capture. **Automatic, exactly-once creation on successful capture** adds action/transaction and companion-link semantics. The promised live cross-kind inheritance (especially sheets and art) is **larger than a tiny UI action** and should be designed with the World View and Mechanics Extensibility boundaries.

## Proposed small vertical slice

1. Expose the same core individualization operation through **Create character from creature** on a campaign-visible creature record / scene review, **and automatically from a committed successful capture**. Optional user naming and scene seating must not be prerequisites for creating a captured actor.
2. Resolve and validate the **effective** campaign-visible creature ID before creation; avoid resolving by name. Create a distinct campaign-local `characters` record with the origin ref in durable, human-editable metadata (including source provenance if available).
3. Seed the character's card from appropriate creature description and traits, but don't blindly copy a creature's entire generalized lore into personal memories or construct an unsupported personality as fact. Optional generative enrichment should be previewed/reviewed.
4. Reuse the creature's visible primary image as the character avatar through the existing art reuse path. Keep the provenance. The character may replace its image independently. If the user expects live fallback after source art updates, defer or explicitly design that subsequent read behavior rather than implying one-time copy is live inheritance.
5. With a selected mechanics module, **only** initialize individual stats through a module-declared valid conversion or shared applicability; do not bypass kind validation to copy a `creatures--id.json` sheet into a `characters--id.json` slot. A capture can still create the character without an available conversion, but game-specific capture completion may require that the module supply one and should flag any missing required sheet.
6. The generic creature stays unchanged. Individual player/NPC interaction state lands only on the character. Naming multiple individuals from the same creature must always produce **different IDs**.
7. Use existing locks, **exactly-once capture/instantiation identity**, guarded store writers, actor/cast/companion APIs, undo/deletion review and image constraints. If capture also consumes a ball, coordinate the multi-record operation with recovery semantics rather than implementing two unrelated writes. Do not introduce a new canonical DB.
8. Test against **successful capture without mechanics**, **mechanical capture success/failure and ball consumption**, retry after interruption, capture of an already-individualized NPC (no duplicate), second capture of same species (new identity), multiple inherited vs overridden images, valid and invalid sheet mappings, deleted/moved creature sources, scene appearance, campaign fork/export and cross-scope collisions.

## Later work (only if genuinely needed)

- True read-through fallback of art/description and per-field creature defaults; clarify overrides, clearing overrides, inherited-image tombstones and missing source handling.
- Declared sheet-type mapping/shared applicability between `creatures` and `characters`, with separate identity for mutable state; don't relax kind constraints universally just to satisfy one species.
- Copy-on-write defaults with optional "refresh from archetype" / view incoming changes, and explicit resolving when generic and individual diverge.
- More sophisticated **recognition** offers: when the narrator identifies one anonymous creature as a recurring individual, present a non-destructive "cement into character" suggestion. Never materialize every unnamed goblin merely because one spoke once. **This does not apply to a successfully captured monster: authoritative capture automatically individualizes it.**
- When the World View DAG is implemented, source-qualified origin identity and provenance must not change if mechanics switches or catalog ordering changes.
- Richer monster-catching party management: active team, reserve/box, release/trade, ownership and optional evolution. **Capture-to-character itself is part of the main feature, not optional later work.**

## Acceptance rehearsal

**Recognition case:** In a mechanics-free campaign, the scene describes three ordinary marsh imps. One is given a name and instantiated as a character. The character appears in the cast, initially shows the creature's portrait and baseline description, and can develop unique dialogue/state/relationships; the two anonymous imps remain unchanged. The character receives a unique avatar: no other imp changes. A second individually named imp gets its **own** character ID. When a mechanics module is selected, the character may inherit or initialize a valid individualized sheet **only** through an explicitly supported conversion; its wounds never alter the creature template. If the source is deleted/unavailable, its absence is visible rather than silently reassociating the character with a different entity of the same name.

**Capture case:** In a mechanics-free monster-catching campaign, a successful capture event of a Marsh Imp automatically creates the individual as a campaign character, linked to the source creature and the trainer, without extra manual "promote" action; a named, already-individualized imp is linked rather than duplicated. A second capture of the same species is a new individual. A distinct ball consumed by a failed capture decreases inventory if the game's rules say so, but no captured character appears. Retries/cancellation/recovery never make duplicate monsters or duplicate inventory consumption. The creature's portrait appears until the individual gains another; generic source art changes can flow through only while not overridden, per the intended live inheritance behavior.

**Architectural decisions still open:** What granularity of cross-kind fallback can the World View support without unsafe coupling? Which mechanics modules can map `creatures` sheet definitions to `characters` sheets? What is the minimal recoverable multi-record capture transaction? Does a mechanics-free capture default to confirmation by the user, or can established user-selected automation policies authorize it when narration commits the result? These are implementation questions, not reasons to model captured monsters as inventory items or defer individual creation.
