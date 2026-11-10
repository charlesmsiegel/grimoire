# Sprint 114: UI Media, Sync and Resilience

> **Planned Wave 7 P0 UI sprint, not a completed feature.** [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md).

**Outcome:** UI Media, Sync and Resilience makes the corresponding Wave 1–6 functionality usable from the graphical app.
**Owner:** frontend/media-and-devices. **Required earlier contracts:** 006, 018, 019, 028, 038, 044; 046 when integrated.

## Scope and first slice

- Expose source/instance image browser, upload/reuse/description, placement and available crop/focus, hidden or unresolved art, revision/source provenance and real image bytes.
- Make pairing, explicit sync, device status, fork selection and kept-both conflict resolution accessible in app; disclose loopback/unencrypted transport limitations.
- Provide recovery and errors for offline/unreachable peers, stale picture revisions, incomplete transfers, unavailable/corrupt blobs and interrupted UI requests.
- Test responsive desktop/mobile and accessible keyboard/focus/touch behavior; never require a VN reader to upload an avatar or continue a chat.

## Proving scenario

Attach artwork to a character and its chat, sync to phone, create a deliberate offline conflict, choose a branch and verify the unused version was reported.

## Refusals, privacy and recovery

- Missing art remains unresolved rather than replaced.
- No silent timestamp conflict wins.
- An unreachable peer never deletes a local draft or chat.

## Milestones

1. Land one end-to-end screen using real V2 domain calls, not a mock-only demo.
2. Cover all relevant Wave 1–6 first slices in the [coverage matrix](../UI_COVERAGE.md), adding narrow authorized API adapters where required.
3. Validate startup/restart, narrow-screen, keyboard, failed-request and no-V1-write scenarios in the full Wave 7 acceptance test.

## Ownership / independence

Own only the named frontend module and its domain-specific API adapters. Consume 108's declared route/design interface, not private UI implementations. If shared work is discovered, serially land a frozen contract before dependent work; do not introduce same-wave hidden hard dependencies or a second canonical store. Port V1 interaction patterns selectively, not V1's PC/NPC/world-copy ontology.

## Open questions

- Which existing headless operations need new UI adapters and authorization checks?
- Which later milestone controls must remain explicitly unavailable?
