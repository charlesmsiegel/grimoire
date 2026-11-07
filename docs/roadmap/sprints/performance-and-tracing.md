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

## Status

A first slice is built ([PERFORMANCE](../../PERFORMANCE.md)). Milestone 1: representative benchmarks of a played turn, cold and warm, over a growing synthetic history (a one-NPC card chat and the migrated two-NPC V1 campaign), and of the V1 and card imports, with recorded baselines. Milestone 2: the scene runner takes a `tracer` and hands it one trace per call, with each operation's wall and self time and, for each model call, its tokens, retries, provider time and cost, every missing value said as such (unreported, synthetic, unpriced, partial). Milestone 3: the measured bottleneck, decoding every turn file of the campaign twice a call, is cut to reading only this chat's files and decoding only what changed; a warm runner and a fresh one send the same prompts over an edited history.

Later work, not in this slice: a per-chat index so a call stops reading every turn file, caching the library view, UI latency, retained traces (none is kept: the open question on safe metrics is answered "none" for now), real prices (013), and a retrieval-quality check, which waits for a change that reduces context (none is made here).

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
