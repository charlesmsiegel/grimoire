# Performance, Cost Attribution and Operational Tracing

**Outcome:** Make each player turn and authoring job fast, diagnosable and economically predictable.

## Scope
- Trace prompt assembly, retrieval, decisions, provider calls, retries, cache use and UI latency by operation.
- Measure wall time and tokens/cost separately for each model role and optional service.
- Optimize measured bottlenecks without changing authoritative Markdown/JSON state.

## Milestones
1. Define representative turn and import benchmarks with baselines.
2. Expose end-to-end time/cost breakdowns and missing-data states.
3. Improve a measured bottleneck and prove no recall or continuity regression.

## Moonshot
Offer a player-facing latency budget planner that predicts and optimizes RP quality per unit of latency, privacy and cost.

## Proving scenario
A slow complex scene identifies where time went, which work was retried, and whether reduced context actually harmed retrieval.

## Independence and interfaces
Observability can attach to current pipelines; it need not wait for new provider routing or agentic workers.

## Open questions
- What budgets matter to a live RP player?
- Which metrics are safe to retain for private campaigns?
- What measurement qualifies a cache optimization?
