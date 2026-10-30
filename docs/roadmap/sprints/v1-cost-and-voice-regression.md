# Measured V0.2.1 cost/quality comparison

**Outcome:** V2 should improve character fidelity without silently increasing ordinary play costs.

## Scope and first proof
Replay synthetic equal-model V0.2.1 comparison with two distinct NPC generation calls; record actual billed input/output, cached tokens, latency and quality.

## Subsequent milestones and moonshot
Matched V2 trials, budget-controlled optional reasoning and long-form voice fidelity regressions.

## Independence and interfaces
Independent evaluation harness; consumes cost flags and RP quality benchmarks.

## Proving scenario
Four characters are present but two respond; V2 does not pay for four separate background decisions.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1--foundational-contracts-isolation-and-cost-baselines) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- A run's usage counts as measured only when built from `TurnUsageLedger.summary()` with `provider_reported_only`; a run file's own `"provenance": "measured"` is self-declared, and the gate report says so (004, 005).

**First-proof additions**
- Reconcile the story-quality format with the inference contract (`/2`): narration, provenance, token fields and optional-call actors, per the [divergence table](../../evals/STORY_QUALITY.md#divergence-from-the-inference-contract).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
