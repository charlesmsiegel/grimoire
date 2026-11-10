# Sprint 109: UI Library, Facets and Portrayals

> **Planned Wave 7 P0 UI sprint, not a completed feature.** [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md).

**Outcome:** UI Library, Facets and Portrayals makes the corresponding Wave 1–6 functionality usable from the graphical app.
**Owner:** frontend/library. **Required earlier contracts:** 002, 009, 010, 017, 022, 024, 025, 037, 042; 046 when integrated.

## Scope and first slice

- Build catalogs, full read/create/edit views, search and direct links for source-qualified characters, scenarios, lorebooks, worlds and supported entity facets.
- Facets compose: PC and NPC are authority/control roles, not permanently distinct record kinds. Entity creation must not invent unrelated backend schemas.
- Show selected revision, effective portrayal, pinned/inherited/overridden/cleared fields, underlying source and linked chats; edit with etags and stale-write handling.
- Provide typed relation/source preview and source-specific adapters; allow raw Markdown/JSON as an optional advanced editor with unknown-field fidelity.

## Proving scenario

Create two same-named characters from different sources, derive a portrayal with an override and explicit clear, and attach its exact revision to a chat.

## Refusals, privacy and recovery

- Stale edits retain drafts and permit diff/retry.
- Deleting a source does not rewrite unrelated chats.
- Unsupported facets remain visibly unavailable.

## Milestones

1. Land one end-to-end screen using real V2 domain calls, not a mock-only demo.
2. Cover all relevant Wave 1–6 first slices in the [coverage matrix](../UI_COVERAGE.md), adding narrow authorized API adapters where required.
3. Validate startup/restart, narrow-screen, keyboard, failed-request and no-V1-write scenarios in the full Wave 7 acceptance test.

## Ownership / independence

Own only the named frontend module and its domain-specific API adapters. Consume 108's declared route/design interface, not private UI implementations. If shared work is discovered, serially land a frozen contract before dependent work; do not introduce same-wave hidden hard dependencies or a second canonical store. Port V1 interaction patterns selectively, not V1's PC/NPC/world-copy ontology.

## Open questions

- Which existing headless operations need new UI adapters and authorization checks?
- Which later milestone controls must remain explicitly unavailable?
