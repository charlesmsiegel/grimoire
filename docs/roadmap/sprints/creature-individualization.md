# Creatures Becoming Persistent Characters

**Status:** first slice implemented as pure in-memory values; see [Individualizing a template instance](../../ENTITIES.md#individualizing-a-template-instance). One of three generic goblins is promoted in place to the named individual Snik:
- it keeps its instance reference, and with it the conversation it witnessed;
- the other two stay generic;
- a second goblin can become Mog;
- template updates flow into an unpinned individual without changing its identity.

The provisional codec carries the record, and a test-local join proves it round-trips through the 010 store. Synthetic fixtures only; there is no runner or UI. What is still open:
- **Portrait clause.** The proving scenario is met except for its portrait clause ("changing the first's portrait does not change the species card"). Art inheritance and milestone 2's separate art wait for 018 placements, and relations for 017. Both attach to the individual's distinct reference.
- **Direct creation.** Creating a named instance directly from a template was dropped from this slice: create a generic instance, then promote it.
- **Milestone 3, changed species.** Re-sourcing is open.

**Outcome:** Turn a noteworthy generic creature into a unique actor with inherited species defaults and independent personal history.

## Scope
- Provide a single idempotent individualization operation for an encountered creature.
- Link stable actor ID to source creature, initial scene and any existing encountered identity.
- Inherit description/art/base traits without sharing wounds, memories, relationships or personal changes.

## Milestones
1. Promote one of three goblins; the others remain generic.
2. Keep two distinct goblin individuals with separate art, names and relationships.
3. Support source updates and changed species without losing personal identity.

## Proving scenario
One goblin is named and remembers a conversation, another remains generic; changing the first's portrait does not change the species card.

## Independence and interfaces
Works in mechanics-free play without character sheets; ruleset conversion is a separate optional adapter. Capturing is a distinct workflow.

## Open questions (answered by the first slice)
- *Which scene evidence authorizes individualization?* An explicit human decision. It comes from the director (the table owner, never narrator or model output) or from the actor's controlling player. Event ids are optional recorded grounds.
- *What is the minimum encounter-instance identity?* An `EntityInstance` of the template, which is an `InstanceRef`. Promotion keeps it.
- *Which properties inherit live versus become personal?*
  - Live from the source, with pin semantics: traits and description.
  - Personal: name, state, added and replaced facets, and anything keyed by the reference (evidence, control, relations, placements).
