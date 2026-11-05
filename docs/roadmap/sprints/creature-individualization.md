# Creatures Becoming Persistent Characters

**Status:** first slice implemented as pure in-memory values; see [Individualizing a template instance](../../ENTITIES.md#individualizing-a-template-instance). One of three generic goblins is promoted in place to the named individual Snik:
- it keeps its instance reference, and with it the conversation it witnessed;
- the other two stay generic;
- a second goblin can become Mog;
- template updates flow into an unpinned individual without changing its identity.
- an instance of a derived species template (a 022 portrayal) is promoted through its effective definition, recording the portrayal's pinned revision.

The provisional codec carries the record, and a test-local join proves it round-trips through the 010 store. Synthetic fixtures only; there is no runner or UI. What is still open:
- **Portrait clause.** Met with 018 art: a test gives the individual its own campaign portrait while the species card and the other goblins keep the species picture (`test_individual_portrait.py`). Milestone 2's separate art and relations (017) attach to the individual's distinct reference.
- **Direct creation.** Creating a named instance directly from a template was dropped from this slice: create a generic instance, then promote it.
- **Merging encountered identities.** "Any existing encountered identity" is read as the encountered instance itself, which promotion keeps. Declaring that two encounter instances, or a V1 legacy id, were the same creature is not covered.
- **Initial scene.** The scope's "initial scene" is recorded as the scene where the individualization was decided (`scene_id`). The scene where the creature was first encountered is only recoverable from the optional event ids; recording it explicitly is deferred.
- **Creatures as chat actors.** An individualized creature keeps a non-`character` kind (for example `campaign:c1/creature/goblin-1`), and the chat store's actor reuse currently accepts only `character` actors. Talking to an individual from a chat needs a later change there, not a re-key; [Party And Presence](party-and-presence.md) (033) owns it.
- **Species membership.** [The graph](../../GRAPH.md) assigns the species-membership relation to 024. This slice records only the template source and template provenance; the explicit relation is a later milestone. This slice allocates no instance ids, so the allocator obligation does not apply yet; a later creation path must honour it: allocate outside `grimoire.chatstore.campaign_taken_ids` and re-check at commit ([STORAGE](../../STORAGE.md#instance-ids-are-never-reused)).
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

## Obligations from 025

Per-ability inherit/replace/clear for the single `abilities` facet ([fictional abilities](../../ABILITIES.md)) is owned by 022 as a follow-up and consumed here: an individual should be able to evolve one species power without copying the rest.

## Open questions (answered by the first slice)
- *Which scene evidence authorizes individualization?* An explicit human decision, never narrator or model output. If the actor has a controlling player, only that player decides; otherwise only the director (the table owner) does. Event ids are optional recorded grounds.
- *What is the minimum encounter-instance identity?* An `EntityInstance` of the template, which is an `InstanceRef`. Promotion keeps it.
- *Which properties inherit live versus become personal?*
  - Live from the source, with pin semantics: traits and description.
  - Personal: name, state, added and replaced facets, and anything keyed by the reference (evidence, control, relations, placements).
