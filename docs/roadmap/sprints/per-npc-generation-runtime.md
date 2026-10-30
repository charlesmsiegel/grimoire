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

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Generate only from a `TurnInferencePlan`'s `npc_generation` calls, one per speaking NPC; take `player_controlled_ids` from the campaign's control assignments (003), never from caller convenience (004).
- Decide narration's inference stage before any narration is generated: `core.inference` has none, while the story-quality baseline assumes one unflagged narration call per turn (005).

**First-proof additions**
- Extend the provider fake's faults to stale-run fencing and cancellation of an in-flight generation (006).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
