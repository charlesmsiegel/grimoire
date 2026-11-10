# Sprint 113: UI Campaign State and Canon Review

> **Planned Wave 7 P0 UI sprint, not a completed feature.** [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md).

**Outcome:** UI Campaign State and Canon Review makes the corresponding Wave 1–6 functionality usable from the graphical app.
**Owner:** frontend/campaign-and-canon. **Required earlier contracts:** 003, 012, 017, 020, 021, 023–025, 029–030, 032–035, 039–043; 047 when integrated.

## Scope and first slice

- Build campaign/chat hub and supported state panels for actors, control, party/reserve, presence, staged positions, holdings, unique equipment, powers and open obstacles.
- Inspect objective events, private observations, contested claims, actor goals, source revisions, relationships and authored changes with audience-appropriate access.
- Offer explicit review of adjudication, per-player consent, proposals/accepted effects, recovery and revocation; never treat narration as automatic state authority.
- Present supported source rebase/change-set review handoffs and clear unavailable states for later ledger, story graph, clocks and autonomy UI.

## Proving scenario

Review and accept a recorded consequence after an obstacle attempt, reopen and see it committed once; the private evidence of an absent NPC stays private.

## Refusals, privacy and recovery

- Stale/unauthorized approvals fail without modifying canonical facts.
- Cast/stage inconsistencies are explained.
- Private channels never become public narration or a global knowledge ledger.

## Milestones

1. Land one end-to-end screen using real V2 domain calls, not a mock-only demo.
2. Cover all relevant Wave 1–6 first slices in the [coverage matrix](../UI_COVERAGE.md), adding narrow authorized API adapters where required.
3. Validate startup/restart, narrow-screen, keyboard, failed-request and no-V1-write scenarios in the full Wave 7 acceptance test.

## Ownership / independence

Own only the named frontend module and its domain-specific API adapters. Consume 108's declared route/design interface, not private UI implementations. If shared work is discovered, serially land a frozen contract before dependent work; do not introduce same-wave hidden hard dependencies or a second canonical store. Port V1 interaction patterns selectively, not V1's PC/NPC/world-copy ontology.

## Open questions

- Which existing headless operations need new UI adapters and authorization checks?
- Which later milestone controls must remain explicitly unavailable?
