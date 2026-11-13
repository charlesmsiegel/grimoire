# Party, Reserve and Scene Presence

**Status:** first slice implemented: membership, moves and scene casts ([PARTY](../../PARTY.md)). The scheduled non-character chat roster gap is closed by explicit existing-actor bindings and a persistent same-species/reserve scene-runner proof. Later party-command and UI work is not implied.

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

**Owned here: non-character actors in a cast.** An individualized creature keeps its own kind (for example `campaign:c1/creature/goblin-1`, see [Creature Individualization](creature-individualization.md)), and the `actor` chat binding role reuses that instance without a re-key ([CHATS](../../CHATS.md#non-character-actors-033)). Two same-species individuals can share a chat; exact-instance detach preserves the other binding. The one-id rule (`campaign_taken_ids`, IDENTITY.md), boxed speech refusal, control and memory persistence are exercised through public chat and scene-runner APIs in `test_chatstore_actors.py`.

## Open questions
- Which membership relations need persistence?
- Who may command an autonomous companion?
- How does moving a party affect scene cast defaults?
