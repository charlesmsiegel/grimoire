# Sprint 116: Wave 9 UI Exit — Mechanics, Authoring and Audio

> **Planned P0 UI-completion tail; scheduled *after* this wave's relevant domain first slices.** Not a parallel peer of those sprints and not an already implemented frontend. [Roadmap](../README.md) · [UI coverage](../UI_COVERAGE.md#later-wave-ui-exit-matrix).

**Outcome:** The user-visible capabilities introduced by Wave 9 (073–087) are operable and inspectable in the unified V2 UI, not just headless tests.

**Required baseline:** Wave 7 UI sprints 108–114. Same-wave backend/user-facing domain slices must be merged and their typed API contracts frozen before this tail consumes them. Unmerged optional milestones are labelled unavailable; the UI tail can be split into independently testable merges, but wave UI completion requires the applicable first slices.

## UI integration scope

- Auxiliary noncanonical writing and author notes; downtime/time advancement with explicit review and no accidental action submission (073–074).
- Ruleset and sheet selection, validated module/sheet authoring, dice and mechanical outcome review, quests, relationships, source upgrades (075, 077–079, 086).
- Long-term graph/search/navigation, model context preview, read-only investigations and worldbuilding suggestions with per-record accept/reject (081–082, 085, 087).
- Supported voice dictation/draft and character performance, separate human approval before speech becomes PC action; show progress of offscreen changes if delivered (076, 080, 083).

## First proving scenario

Use a mechanic in a scene, review a deterministic roll and accepted sheet change, follow a historical thread, accept two of three generated place proposals, then continue from the same chat.

## Negative cases

Never treat a draft suggestion or voice transcript as accepted canon, never reroll on replay, and preserve source revisions and private actor perspectives.

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
