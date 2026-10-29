# One independent generation per speaking NPC

**Outcome:** Retain 0.2.1's actor-specific generation quality but feed each model a compact evidence-safe perspective assembled deterministically.

## Scope and first proof
Generate a two-NPC exchange using separate model calls and distinct voice anchors; only speaking actors cost generation tokens.

## Subsequent milestones and moonshot
Async streaming, cancellation, local provider integration and optional model-driven extra reasoning behind flags.

## Independence and interfaces
Owns generation orchestration; consumes source/graph/perspective, provider and cost contracts.

## Proving scenario
A reply respects an NPC's private observations without leaking another actor's director-only knowledge.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
