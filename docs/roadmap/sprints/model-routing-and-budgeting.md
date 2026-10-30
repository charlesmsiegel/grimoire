# Model Routing, Provider Resilience and Budgets

> **V2 scope correction:** Port the minimum provider stack to V2 behind named task interfaces. All extra planning, epistemic inference, critique and background actions are explicitly flaggable and metered; real billed token/cost comparison against 0.2.1 is mandatory. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


**Outcome:** Use appropriate models for decisions, retrieval and prose while preserving latency, cost and local-provider choice.

## Scope
- Route Primary, Fast, Decision, Embedding and optional specialists independently, using existing orchestration.
- Track real usage, retries, fallbacks and provider errors per complete turn/job.
- Define latency/cost/privacy caps and fail-soft choices that do not fabricate successful state changes.

## Milestones
1. Expose actual per-role cost and latency for a representative scene.
2. Implement a measured fallback and budget policy.
3. Experiment with adaptive routing based on task difficulty and quality evidence.

## Moonshot
Optimize a user-controlled frontier of dramatic quality, response latency, monetary cost and private/local inference in real time.

## Proving scenario
A model outage falls back or fails visibly; an accepted roll/effect is never silently redone by a new narrator.

## Independence and interfaces
Infrastructure works with existing role routes; real-time voice may demand tighter budgets but is not required.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1--foundational-contracts-isolation-and-cost-baselines) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Issue a provider request only for a `PlannedCall` of the turn's `TurnInferencePlan`, and book every attempt (failures and route fallbacks included) on its `TurnUsageLedger` as `provider_reported`, with `None` for any count the provider did not report (004, [inference](../../INFERENCE.md)). Usage sums are lower bounds while any count is unreported; reports must say so.
- Authorize every provider call as `execute` on `providers/<name>`; keys live under reserved roots and are read only by trusted adapter code (007, [tool authority](../../TOOL_AUTHORITY.md)).

**First-proof additions**
- A scripted provider fake with injectable timeout, error, truncated-stream and cancellation faults: a failed call never yields accepted state, and violations are named, seeded and bounded by call/token counts (006 milestone 2, the inference-run half of its fault harness).

## Open questions
- How are token accounting discrepancies handled?
- What objective signals trigger more expensive models?
- Which jobs may use local-only providers?
