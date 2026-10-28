# Multi-PC and Multiplayer

**Priority:** optional later work; local multi-PC input is distinct from remote multi-user synchronization.

## Goal

Support stories with several player characters in one scene and eventually multiple human participants without conflating speaker attribution, scene progression or authority to edit campaign state.

## Work to plan

- **Local multi-PC:** let one player prepare separate posts from two or more PCs, see their attributed drafts, reorder/correct them and explicitly **advance once** with an agreed combined turn. Preserve the existing transcript's speaker marker grammar and avoid premature inference between posts.
- **Presence vs authorship:** distinguish PC, NPC, director, player controller, speaker, visibility/audience and current cast. One human may control several PCs; a companion does not automatically become a PC.
- **Durable turn state:** avoid losing prepared inputs during navigation, retries or failures; use campaign-owned authority when persistence is necessary, not a hidden browser-only truth.
- **Future network multiplayer:** authenticated ownership of PCs, simultaneous edits, contribution deadlines, turn/scene locks, conflict resolution, participant privacy, joining/leaving and cross-device state.
- **Collaboration UX:** per-player controls and responsive interfaces should not interfere with normal single-player or mechanics-free play.
- **Correctness:** scene history should round-trip multiple authors faithfully and preserve who knew what before the next model response.

## Proving scenario

One player drafts actions/dialogue for two PCs in a shared scene. Neither draft triggers a GM response until the player selects advance. The combined scene has correct speaker identities and historical ordering. A partial failure or edit doesn't duplicate a post or convert an NPC into a PC.

## Dependencies and boundaries

[Campaign Management](campaign-management.md) owns scenes and state; [Generation Systems](generation.md) owns one coherent advancement/inference boundary; [State Transactions](state-transactions.md) governs authoritative concurrent writes; [Character Development](character-development.md) owns control vs party membership. Full network play may need coordination beyond one machine's file locks.

## Open questions

- Should local multi-PC drafts persist across restarts or remain ephemeral until submission?
- What is the ordering and conflict model if separate controllers draft at different times?
- Does a combined multi-PC turn trigger one narrator resolution, multiple attributed NPC responses, or selectable policies?
- How are individual PC privacy, hidden agendas and asynchronous audience knowledge represented?
- What concurrency/coordinator architecture can guarantee correctness across actual devices while retaining local file-backed state?
