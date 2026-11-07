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

## Inherited from Wave 5

The [Wave 5 audit](../README.md#wave-5-status) measured 035's first slice ([SCENES](../../SCENES.md#known-limits)):
- Turns store each call's usage but no timings, and the runner has no tracing hook.
- Every runner call reloads the library and re-reads every turn file of the campaign (every chat, not only the open one), then reads them again under the lock to write. Growth is roughly linear: on a synthetic chat, opening took about 30 ms at 10 turns and 1.1 s at 2,000, and a post plus a pass about 0.11 s and 3.4 s.

## Open questions
- What budgets matter to a live RP player?
- Which metrics are safe to retain for private campaigns?
- What measurement qualifies a cache optimization?
