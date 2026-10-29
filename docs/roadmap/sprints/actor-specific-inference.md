# Actor-Specific Inference and Optional Reasoning Experiments

**Outcome:** Preserve separately generated NPC contributions as the V2 default, and determine whether *additional* actor-specific decision/reasoning calls are worth their **extra** cost.

## V2 baseline (non-negotiable)

V0.2.1 already generates separately for each speaking NPC. This preserves the distinct voices of RP-friendly models. **Do not redesign V2 to produce all NPC dialogue from one shared narrator by default.** The baseline is one independently contextualized generation for each contributing NPC; a non-speaking NPC incurs no mandatory inference cost.

## Scope

- Build actor perspectives deterministically from source-qualified definitions, typed relations, events, observed speech, evidence and memory. Graph traversal/knowledge filtering do not trigger a model call.
- Compare baseline one-call-per-contribution narration against optional **extra** actor planning, theory-of-mind, consistency-critique and other specialist calls.
- All extras require feature flags, logged actual tokens/cost/latency, budgeting, cancellation and non-authoritative proposal status.
- Judge voice distinctiveness, characterization, secrecy, latency and total charges against a matched V0.2.1 tag baseline.

## Milestones

1. Demonstrate separate two-NPC replies with actor-private contexts and no shared-generation shortcut; meter actual billed cost.
2. Compare optional extra decision calls on a synthetic benchmark and reject variants that do not justify cost/quality.
3. Explore adaptive, salience-gated or local model reasoning for rare high-stakes decisions.

## Moonshot

Hundreds of coherent NPCs can exist without hundreds of simultaneous model calls. A relevant character comes vividly to life when speaking; deeper independent reasoning is available only when user policy and actual dramatic value justify it.

## Proving scenario

Four NPCs are present; two contribute. Two distinct RP-generation calls are made. No extra decision call is made with the optional flags off. A cost report compares this to V0.2.1 and shows voice fidelity separately from money spent.

## Independence and interfaces

Consumes [per-NPC generation](per-npc-generation-runtime.md), [cost flags](turn-costs-and-feature-flags.md) and [V1 comparison](v1-cost-and-voice-regression.md). Owns experiments, not baseline generation semantics or the canonical event writer.

## Open questions

- When does additional actor-specific planning measurably improve roleplay?
- Which optional decisions can use local/cheap inference without compromising character fidelity?
- How do we prevent extra reasoning calls from changing already-committed outcomes?
