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

## World-less scenario-only director generation (mandatory for 035)

The present Wave 1 `InferenceStage` and Wave 2 `story-quality/2` intentionally model **NPC calls only**; the old V0.2.1 synthetic baseline's unflagged narrator call was removed. A **scenario-only** SillyTavern-style chat cannot play if there is neither a speaking NPC nor a director model. This sprint must explicitly resolve the gap:

- Add and enforce a **`director_generation` / narrator stage** in the same guarded `TurnInferencePlan` and usage ledger, with its own well-defined baseline/opt-in policy. Do **not** disguise it as `npc_generation`, `planning`, a fabricated character actor or a zero-cost action.
- When a world-less scenario has no NPC contribution, the selected director/narrator may produce one metered, authority-checked response. When one or more NPCs speak, each retains its **own** model call, and a separate director call is **not silently added**; if present it must be an explicit configured extra with token/cost visibility.
- Keep director generation from authoring protected PC intentions, dialogue or beliefs. Only user-visible narrative text is delivered to the player; director-private evidence must not be exposed as character knowledge by a fake NPC.
- Version the story-quality interchange and benchmark explicitly (e.g. `story-quality/3`) to represent director messages/call attribution without reinterpreting shipped `/2`; update adapters and regression tests with the appropriate eval owner.
- Acceptance: a scenario-only chat gets a real, billed reply with zero NPC calls; a two-speaking-NPC scene has exactly two independent speaking calls and zero *implicit* director calls; timeout, cancellation and retry never commit duplicate narration or unplanned cost.

This is an **additional first-playable requirement**, not something the completed Sprint 015 already implemented. It is part of the 027 runtime's agreed interface changes, reviewed against [the inference contract](../../INFERENCE.md) and [chat model](../CHAT_MODEL.md).

## World-less actors and derived voice sources

An NPC may be instantiated from a standalone card/source with no world. Construct that NPC's context only from explicitly bound card/scenario/lorebook material, 012's private evidence, and 022's effective portrayal. A missing world is a normal case, **not** a reason to create director facts or invoke a background planner.

For explicitly derived Mara variants, preserve 014's independent voice fidelity while allowing **reviewed inheritance of base voice anchors/examples** when a derived portrayal has no local voice profile, followed by local replacements/suppressions. The 014 `VoiceCatalog` currently only resolves exact profiles and authorizes explicit alternate portrayals; it does **not** automatically follow `derived_variant`. This sprint owns the adapter or explicitly coordinated update to make 022's resolved, pinned provenance meaningful for 014's prompt construction. Never fall back by display name, use an unapproved unrelated portrayal, or include a voice example revealing a secret the actor did not learn.

**Proving scenario:** one world-less chat with Mara alone; a second with Modern Elderfen Mara using her core authored voice plus setting-specific occupation; separate actor instances and knowledge, one paid generation per actual contribution. [Chat model](../CHAT_MODEL.md).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
