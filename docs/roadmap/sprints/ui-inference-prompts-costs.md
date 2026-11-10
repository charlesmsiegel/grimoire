# Sprint 112: UI Inference, Prompts and Costs

> **Planned Wave 7 P0 UI sprint, not a completed feature.** [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md).

**Outcome:** UI Inference, Prompts and Costs makes the corresponding Wave 1–6 functionality usable from the graphical app.
**Owner:** frontend/models-and-observability. **Required earlier contracts:** 004, 005, 013, 014, 015, 027, 031, 044; 045/048 when integrated.

## Scope and first slice

- Use proven main provider/model screens as UX references: create/test local or hosted endpoints, choose effective NPC and director roles, fallbacks and supported presets, and display errors without leaking credentials.
- Show actual per-NPC calls, optional decision/planning requests, metered billed/modelled/unknown costs and user budgets; expensive stages must be opt-in.
- Audience-safe context inspector shows source provenance, omissions, current model selection, and historical prompt snapshots only when a backed V2 service exists.
- Expose responsive read-only diagnostics and latency when supported; do not invent provider capability, prices, embedding behavior or full prompt-template support.

## Proving scenario

Configure a local model, run two separate NPC calls, read each usage entry, and verify disabling optional calls changes the next turn.

## Refusals, privacy and recovery

- A missing provider prevents generation but not offline editing.
- An unpriced call shows unknown, not free.
- Prompt inspector does not disclose another NPC's private evidence.

## Milestones

1. Land one end-to-end screen using real V2 domain calls, not a mock-only demo.
2. Cover all relevant Wave 1–6 first slices in the [coverage matrix](../UI_COVERAGE.md), adding narrow authorized API adapters where required.
3. Validate startup/restart, narrow-screen, keyboard, failed-request and no-V1-write scenarios in the full Wave 7 acceptance test.

## Ownership / independence

Own only the named frontend module and its domain-specific API adapters. Consume 108's declared route/design interface, not private UI implementations. If shared work is discovered, serially land a frozen contract before dependent work; do not introduce same-wave hidden hard dependencies or a second canonical store. Port V1 interaction patterns selectively, not V1's PC/NPC/world-copy ontology.

## Open questions

- Which existing headless operations need new UI adapters and authorization checks?
- Which later milestone controls must remain explicitly unavailable?
