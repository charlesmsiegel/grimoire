# Sprint 110: UI Import, Migration and Change Review

> **Planned Wave 7 P0 UI sprint, not a completed feature.** [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md).

**Outcome:** UI Import, Migration and Change Review makes the corresponding Wave 1–6 functionality usable from the graphical app.
**Owner:** frontend/import-and-review. **Required earlier contracts:** 007, 011, 026, 036, 037, 042, 043.

## Scope and first slice

- Provide standalone card/scenario/lorebook/history import with format support, source lineage, content and loss previews, and explicit approval.
- Expose read-only V0.2.1 source scan, identity/art mapping, migration destination and reviewed import/apply receipt; never write the source.
- Reusable exact-revision proposal/diff/approve/reject controls for imports, world change sets and source rebase; no hidden canonical writes.
- Clearly report unsupported import formats, missing art, ambiguous identities, stale approvals and recovery details.

## Proving scenario

Preview and approve a synthetic 0.2.1 library import and a separate V3 card; rerunning each never duplicates records or changes the source.

## Refusals, privacy and recovery

- Changing staged bytes invalidates approval.
- Rejected changes do not affect canonical world facts.
- Imported text does not create unverified actor memories.

## Milestones

1. Land one end-to-end screen using real V2 domain calls, not a mock-only demo.
2. Cover all relevant Wave 1–6 first slices in the [coverage matrix](../UI_COVERAGE.md), adding narrow authorized API adapters where required.
3. Validate startup/restart, narrow-screen, keyboard, failed-request and no-V1-write scenarios in the full Wave 7 acceptance test.

## Ownership / independence

Own only the named frontend module and its domain-specific API adapters. Consume 108's declared route/design interface, not private UI implementations. If shared work is discovered, serially land a frozen contract before dependent work; do not introduce same-wave hidden hard dependencies or a second canonical store. Port V1 interaction patterns selectively, not V1's PC/NPC/world-copy ontology.

## Open questions

- Which existing headless operations need new UI adapters and authorization checks?
- Which later milestone controls must remain explicitly unavailable?
