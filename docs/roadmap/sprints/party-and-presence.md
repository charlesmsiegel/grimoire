# Party, Reserve and Scene Presence

**Status:** first slice implemented: membership, moves and scene casts ([PARTY](../../PARTY.md)). Milestone 1 is met; a creature in a chat's roster comes before milestone 2.

**Outcome:** Know which characters are controlled, accompanying, reserved, nearby, present in a scene or merely associated with a trainer.

## Scope
- Separate PC control, ally/companion relationships, active party, reserve/box, scene cast and audience.
- Support join, leave, store, release and move with clear authority and history.
- Prevent owner/trainer relation from automatically making every companion a speaker or present actor.

## Milestones
1. Track party vs reserve membership for two unique creatures.
2. Stage a scene with selected party members without losing reserve identities.
3. Support parties spanning multiple PCs or troupe play.

## Proving scenario
A boxed monster is available in the campaign but never speaks as present in an unrelated scene; releasing it doesn't delete its memories.

## Independence and interfaces
Can use generic actor links and existing scene cast today; does not depend on network multiplayer or mechanics.

**Owned here: non-character actors in a cast.** An individualized creature keeps its own kind (for example `campaign:c1/creature/goblin-1`, see [Creature Individualization](creature-individualization.md)), but today only `character` instances can be reused as chat actors (CHATS.md, the 107 reuse check) and a chat's roles admit only character, scenario and lorebook sources. Before milestone 2, 033 must let a creature (or another non-character instance) join a chat or scene cast as an actor without a re-key, keep the one-id rule (`campaign_taken_ids`, IDENTITY.md), and prove the boxed monster above with a creature instance.

## Open questions
- Which membership relations need persistence?
- Who may command an autonomous companion?
- How does moving a party affect scene cast defaults?
