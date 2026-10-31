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

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
