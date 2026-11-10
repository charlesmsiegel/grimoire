# Sprint 111: UI Chat Launch and Scene Play

> **Planned Wave 7 P0 UI sprint, not a completed feature.** [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md).

**Outcome:** UI Chat Launch and Scene Play makes the corresponding Wave 1–6 functionality usable from the graphical app.
**Owner:** frontend/chat-and-play. **Required earlier contracts:** 003, 008, 016, 027, 031, 035, 044, 052, 107.

## Scope and first slice

- Wire 052 Start Chat from character, scenario, lorebook bundle or premise with optional persona/opening; no mandatory world.
- Implement Recent/All Chats, same-chat cross-links, cast selection, typed PC/director intents, composer, separate NPC/director generation, take review/keep/pass and pause/skip/redirect.
- Provide transcript, imported-history reading, authorial/edit/correction actions as supported by 035; do not claim V1 replay/swipe/streaming features without V2 endpoints.
- Show run state, model refusals, interrupted turns, forks, missing refs and correct continuation; preserve drafts on navigation/error.

## Proving scenario

Start a world-less chat, generate separate NPC replies, add a second card, save/reopen and continue the same canonical conversation from multiple library entrypoints.

## Refusals, privacy and recovery

- Added actors never inherit earlier private evidence.
- Generated PC speech/interiority is not silently committed.
- Scenario-only chat uses an explicit metered director stage, not a fake NPC.

## Milestones

1. Land one end-to-end screen using real V2 domain calls, not a mock-only demo.
2. Cover all relevant Wave 1–6 first slices in the [coverage matrix](../UI_COVERAGE.md), adding narrow authorized API adapters where required.
3. Validate startup/restart, narrow-screen, keyboard, failed-request and no-V1-write scenarios in the full Wave 7 acceptance test.

## Ownership / independence

Own only the named frontend module and its domain-specific API adapters. Consume 108's declared route/design interface, not private UI implementations. If shared work is discovered, serially land a frozen contract before dependent work; do not introduce same-wave hidden hard dependencies or a second canonical store. Port V1 interaction patterns selectively, not V1's PC/NPC/world-copy ontology.

## Open questions

- Which existing headless operations need new UI adapters and authorization checks?
- Which later milestone controls must remain explicitly unavailable?
