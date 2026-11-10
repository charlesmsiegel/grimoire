# Sprint 118: Wave 11 UI Exit — Collaboration and Publication

> **Planned P0 UI-completion tail; scheduled *after* this wave's relevant domain first slices.** Not a parallel peer of those sprints and not an already implemented frontend. [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md#later-wave-ui-exit-matrix).

**Outcome:** The user-visible capabilities introduced by Wave 11 (100–104) are operable and inspectable in the unified V2 UI, not just headless tests.

**Required baseline:** Wave 7 UI sprints 108–114. Same-wave backend/user-facing domain slices must be merged and their typed API contracts frozen before this tail consumes them. Unmerged optional milestones are labelled unavailable; the UI tail can be split into independently testable merges, but wave UI completion requires the applicable first slices.

## UI integration scope

- Narrative arc workspace for revising plans after unexpected choices without changing accepted player decisions (100).
- Portable content package export/import with provenance, versioning, dependency and license review (103).
- Explicit multi-PC permission/turn controls, simultaneous actor attribution and multi-speaker microphone channel review where supported (102, 104).
- Expose code quality gate (101) only as developer diagnostics, not as a gameplay panel.

## First proving scenario

Revise a narrative arc after a refused plot beat, export a reviewed reusable source, and coordinate two controllers without either authoring the other's PC.

## Negative cases

Never publish secrets or private campaign information by default; distinguish collaborators' authorities, source packages and saved scene history.

## Deliverable and tests

- Add only the bounded frontend routes/components and authenticated adapters needed for delivered services; no parallel canonical state, no V1 endpoint assumptions.
- Record a coverage table mapping every user-facing first slice of this wave to a route, integration test and failure behavior. Identify purely internal/evaluation sprints as **no UI required**.
- Run synthetic end-to-end desktop/phone, keyboard-accessibility, failed-request, source-revision, privacy, model-cost and where applicable sync-conflict tests.
- Mark the wave **domain-complete** separately from **UI-complete**; a headless green test does not certify this UI gate.

## Independence and dependency-safe scheduling

Domain PRs in this wave retain their declared parallelism and do not depend on the UI tail. This sprint is intentionally **sequential wave exit work** that follows the specific domain contracts it consumes. If a backend feature is unfinished, name the blocked operation and do not advertise fake UI parity. Keep player control, distinct NPC inference, source-qualified identity, revision guards and knowledge safety intact.

## Open questions

- Which of this wave's domain first slices have landed and require an interactive UI?
- Which existing shared views extend cleanly versus requiring new specialized controls?
- What is the minimum real end-to-end acceptance proof, including a refusal case?
