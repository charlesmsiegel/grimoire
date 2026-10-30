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

## Wave 2 handoff: planned calls, voice and evidence

Consume 012's deterministic actor-visible evidence, 013's **real** authorized `PlannedCall` provider adapter, 014's portrayal-specific voice anchors and 015's measured V0.2.1 voice/cost baseline. Build a minimal, bounded context from those contracts **without requiring 031's unfinished retrieval system** (031 may later enhance it through an adapter).

The runtime must use one independent RP-model generation **per speaking NPC contribution** (not per NPC present and not an all-NPC narrator call), reject invalid controller assignments, prevent an NPC from receiving the director-only view, and book every attempt. If a global narrator is used, make its inference stage explicit and consistent with the `story-quality/2` format; it must not hide unplanned or unmetered calls. A failed or truncated response cannot commit an event or PC interiority. Streaming cancellation and stale-run fencing are exercised with a provider fake and 006's fault harness. Extra planning/thinking/critique is opt-in, never a baseline tax. The measured V1 baseline remains a quality/cost comparison, not a source of secrets.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
