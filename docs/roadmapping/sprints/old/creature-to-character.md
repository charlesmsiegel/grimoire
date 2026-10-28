# Creature → Character — Individualization and Capture

**Priority:** focused near-term gameplay and World View proving slice; full live inheritance requires a deliberate interface.

## Goal

Turn one creature of a reusable archetype into a **distinct persistent individual**, retaining its species defaults while its personal state evolves. This is not a rename or conversion of the original creature record; a species may have arbitrarily many independent individuals.

## Triggers — one operation, two contexts

1. **Individualization in conversation:** among a group of goblins, one talks to the PC or becomes personally significant. The user or accepted scene-extraction proposal establishes **this one** as a persistent NPC, possibly naming/seating it. Do not materialize every incidental extra merely because it spoke once.
2. **Successful monster capture:** after a **committed successful capture**, automatically create **or reuse** the actual encountered individual as a character in the same turn; no second manual promotion step. A monster need not speak, have a personality, or be controlled as a PC to qualify as a character.

Both routes invoke the same idempotent individualization service. Capturing an already-named, already-individualized monster updates **trainer/companion ownership**, not identity or memories. A second distinct capture of the same species produces another actor.

## State and inheritance contract

- Durable campaign-owned **character ID** and explicit **origin creature/source**; do not match by name. Store provenance such as first scene, capture event or source-qualified species ref.
- **Inherit descriptive traits, portraits/expressions and appropriate starting/base stats until the individual explicitly overrides them.** Replacing a portrait must not edit the species or other monsters. Provide an explicit reset/clear override where practical.
- **Individual state remains individual:** current wounds/HP, level/experience, moves, injury, memory, relationships, inventory, disposition, party/box/owner and future advancement.
- A ruleset may convert creature sheet fields to a character sheet only through a declared compatible mapping; never assign a creature-kind sheet directly to a character and bypass kind validation. In mechanics-free play, individualization must work without sheets.
- A named monster remains the same individual across scene changes, rename, capture, release, trade, eventual evolution/species change, campaign fork and export. Updating or deleting the source must not silently reassociate it with another same-named creature.
- **Exactly once:** a capture event/operation key creates at most one actor. Its ball consumption, actor creation and trainer/party association must form one recoverable logical effect. A failed capture may still consume a ball under rules but does not create a captured character.

## Proving scenarios

Three goblins are present; one becomes a named NPC; the other two remain generic. A second named goblin gets an independent ID and can have a different portrait. In a mechanics-free monster-catching campaign, committing capture makes the monster immediately available as a companion and consumes a single ball exactly once even after a retry. A changed source portrait appears for an individual with no override but does not overwrite one with a custom sprite. A mechanical module with no declared conversion reports an honest limitation, not an invalid sheet.

## Dependencies and boundaries

[Inventory](inventory.md) holds the capture **device**, not the companion. [State Transactions](state-transactions.md) coordinates effects. [World Resolution](world-resolution.md) supplies qualified source IDs and inheritance. [Mechanics](mechanics.md) supplies optional capture/sheet rules; simple fiction-first individualization must not wait for all rulesets. Rich active-party/box management is covered by [Character Development](character-development.md).

## Open questions

- What mechanism provides **live cross-kind fallback** for art, description, traits and compatible base-stat fields without introducing surprising dependency cycles?
- How are missing/updated/removed source creature records represented when personal overrides exist or don't?
- Which rule modules require conversion from creature sheet type to character sheet type, and how does that mapping express evolution?
- In mechanics-free RP, when does accepted narration count as an authorized capture versus a proposal awaiting explicit confirmation?
- What is the smallest correct recoverable capture effect across resource, actor and party state?
- How are scene/cast presence, trainer ownership, active party and reserve storage modeled as distinct concepts?
