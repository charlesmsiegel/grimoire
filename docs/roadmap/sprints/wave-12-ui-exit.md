# Sprint 119: Wave 12 UI Exit — Voice and Immersion

> **Planned P0 UI-completion tail; scheduled *after* this wave's relevant domain first slices.** Not a parallel peer of those sprints and not an already implemented frontend. [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md#later-wave-ui-exit-matrix).

**Outcome:** The user-visible capabilities introduced by Wave 12 (105–106) are operable and inspectable in the unified V2 UI, not just headless tests.

**Required baseline:** Wave 7 UI sprints 108–114. Same-wave backend/user-facing domain slices must be merged and their typed API contracts frozen before this tail consumes them. Unmerged optional milestones are labelled unavailable; the UI tail can be split into independently testable merges, but wave UI completion requires the applicable first slices.

## UI integration scope

- Low-latency spoken interaction with visible mic consent, speech-to-draft versus PC speech versus safe app commands, captions, stop/confirm and transcript-aligned NPC voices (105).
- Optional visual/spatial scene exploration with stable authority, active location and event truth; no maps/VR requirement to play by text (106).
- Privacy settings for audio capture, failures, provider choice and accessible typed/manual fallback; no always-on microphone requirement.

## First proving scenario

Hold a voiced scene turn with one NPC, inspect the accepted text and stop/recover from a misrecognized command; switch to typed play without changing history.

## Negative cases

A mistaken command or captured ambient speech cannot delete or alter canon; no unsanctioned audio recording or unreviewed world writes.

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
