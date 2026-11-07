# Early phone play and synchronized handoff

**Outcome:** Phone play is a primary migration acceptance gate, not a distant presentation moonshot.

## Scope and first proof
Use a minimal phone web/Android client against V2's text API, open a scene offline, and exchange updates/images with desktop using built-in sync.

## Subsequent milestones and moonshot
Background/automatic synchronization, low-friction paired device UX and optimized mobile reading/voice.

## Independence and interfaces
Owns minimal mobile UI and sync wiring, not canonical graph/storage; depends on text runner and sync transport.

## Proving scenario
Add a post on a phone, sync to desktop, and resume without duplicate turns or missing art.

## World-less chat navigation on phone

The mobile first proof must include a **world-less** chat launched from a character/scenario with optional lore and one created from an imported V1 world, both using 107's canonical chat ID. Find/continue through Recent Chats and a selected character/scenario. A chat appearing under multiple filters is transferred **once** by the sync revision protocol; mobile does not create a parallel history by opening it through another entry.

Persist and sync the lightweight scope, source associations, actor IDs, transcript, art references and unresolved-source/conflict state. Sync transport must not assume a world manifest exists. Richer library browsing is sprint 065, and one-click launch UI is sprint 052. [Chat model](../CHAT_MODEL.md).

## Inherited from Wave 5

The [Wave 5 audit](../README.md#wave-5-status) records what the phone client depends on:
- **Turns sync** since 028's turn merge ([SYNC](../../SYNC.md#played-turns-035-028s-turn-merge)). Turn ids are device-qualified on an identified library, so two devices playing one chat offline make two distinct turn files, and the chat forks on both until its owner chooses which branch goes on. A UI for that choice is 065's.
- **Kept turns are transcript, not 021-accepted effects** until 035 milestone 2. "Sync an accepted turn" means a kept turn until then.
- **No text API.** 035's runner is in-process Python; the interface the phone calls is this sprint's to define.
- **Imported history is not heard** until 035 milestone 2's first item, which is due before this sprint.
- **Pictures:** 038's `unresolved` placements sync without a blob ([IMAGES](../../IMAGES.md)).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
