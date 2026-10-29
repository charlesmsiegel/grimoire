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

## Open questions
- How are token accounting discrepancies handled?
- What objective signals trigger more expensive models?
- Which jobs may use local-only providers?
