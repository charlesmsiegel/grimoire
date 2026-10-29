# Inference economics and optional intelligence flags

**Outcome:** Engineering effort is unbounded but model-call budgets are not; per-NPC generation is baseline, extra inference is optional.

## Scope and first proof
Define per-turn/session provider token accounting and flags for planning, epistemic inference, recall, graph lookups and critique; no mandatory call just because an actor exists.

## Subsequent milestones and moonshot
Actual provider-meter integrations, budget guardrails and user-visible cost/latency breakdown.

## Independence and interfaces
Inference policy and usage accounting; may start with synthetic usage; no graph or model provider needed initially.

## Proving scenario
Four present NPCs with only two speaking ones require two RP-generation calls, not four background thinkers.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
