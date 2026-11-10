# Sprint 108: UI Shell, Onboarding and Navigation

> **Planned Wave 7 P0 UI sprint, not a completed feature.** [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md).

**Outcome:** UI Shell, Onboarding and Navigation makes the corresponding Wave 1–6 functionality usable from the graphical app.
**Owner:** frontend/shell. **Required earlier contracts:** 001, 002, 010, 044, 052, 107.

## Scope and first slice

- Port responsive V1-style rail, mobile navigation, global search, command palette, shortcuts, focus, themes, accessible form primitives and stable deep links.
- New first-run workflow: select a V2-isolated library, configure provider/model, choose Start Chat or import; world and campaign wizard are optional.
- Own the global routes/design-system/typed adapter conventions. Other Wave 7 teams export self-contained pages and do not edit the global shell concurrently.
- Persist unsent drafts and focus/scroll on navigation; display source-qualified links, explicit unavailable states and recoverable errors.

## Proving scenario

From a fresh empty library navigate to Start Chat and a character/scenario view, switch routes, and return to an intact draft without touching a CLI.

## Refusals, privacy and recovery

- V1 paths are refused without writing.
- Broken source links show exact refs, no name-based replacement.
- Screen-reader and keyboard-only usage; readable phone layouts.

## Milestones

1. Land one end-to-end screen using real V2 domain calls, not a mock-only demo.
2. Cover all relevant Wave 1–6 first slices in the [coverage matrix](../UI_COVERAGE.md), adding narrow authorized API adapters where required.
3. Validate startup/restart, narrow-screen, keyboard, failed-request and no-V1-write scenarios in the full Wave 7 acceptance test.

## Ownership / independence

Own only the named frontend module and its domain-specific API adapters. Consume 108's declared route/design interface, not private UI implementations. If shared work is discovered, serially land a frozen contract before dependent work; do not introduce same-wave hidden hard dependencies or a second canonical store. Port V1 interaction patterns selectively, not V1's PC/NPC/world-copy ontology.

## Open questions

- Which existing headless operations need new UI adapters and authorization checks?
- Which later milestone controls must remain explicitly unavailable?
