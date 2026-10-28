# Model Routing, Provider Resilience and Budgets

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
