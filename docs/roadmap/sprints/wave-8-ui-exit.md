# Sprint 115: Wave 8 UI Exit — Reliable Campaign Play

> **Planned P0 UI-completion tail; scheduled *after* this wave's relevant domain first slices.** Not a parallel peer of those sprints and not an already implemented frontend. [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md#later-wave-ui-exit-matrix).

**Outcome:** The user-visible capabilities introduced by Wave 8 (049–072) are operable and inspectable in the unified V2 UI, not just headless tests.

**Required baseline:** Wave 7 UI sprints 108–114. Same-wave backend/user-facing domain slices must be merged and their typed API contracts frozen before this tail consumes them. Unmerged optional milestones are labelled unavailable; the UI tail can be split into independently testable merges, but wave UI completion requires the applicable first slices.

## UI integration scope

- Conflict-safe offline sync, fork/retcon preview and historical recovery; explicit losing branch preservation (049, 054).
- Campaign memory, commitments and clock, searchable chronology, individual party/capture/scene-presence and rule contract visibility (050, 051, 053, 055, 058, 062, 065).
- Catalog/source composition, faction/relationship and location topology, scenario prep and optional authoring controls (056–060, 063–064).
- Richer unified library/workspace navigation, character/scenario filters, draft preservation, voice privacy/accessibility, optional image input and VN cues when supported (061, 065, 067, 070, 072).

## First proving scenario

From the UI reconcile divergent offline continuations, select a reviewed retcon without duplicated effects, inspect active party/time/chronicle, switch chats without losing a draft, and resume play.

## Negative cases

Keep both conflicting candidates until human resolution, preserve actor authority and memory privacy, and make unavailable optional voice, VN or theory-of-mind functionality explicit.

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
