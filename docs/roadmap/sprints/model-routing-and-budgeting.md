# Model Routing, Provider Resilience and Budgets

> **V2 scope correction:** Port the minimum provider stack to V2 behind named task interfaces. All extra planning, epistemic inference, critique and background actions are explicitly flaggable and metered; real billed token/cost comparison against 0.2.1 is mandatory. See the [V2 ordered roadmap](../README.md) for prerequisites, waves and current first-slice proof. Older references to shipped V1 APIs describe behavior to *port*, not V2 capabilities.


**Outcome:** Use appropriate models for decisions, retrieval and prose while preserving latency, cost and local-provider choice.

## Scope
- Route Primary, Fast, Decision, Embedding and optional specialists independently, using existing orchestration.
- Track real usage, retries, fallbacks and provider errors per complete turn/job.
- Define latency/cost/privacy caps and fail-soft choices that do not fabricate successful state changes.

## Milestones
1. Deliver a minimal **working provider adapter** that authorizes and issues only scheduled model calls, books actual provider-reported usage per attempt, and exercises failure/timeout/cancellation/truncated-stream behavior through a scripted fake; capture per-task cost/latency with provenance.
2. Implement a measured fallback and budget policy.
3. Experiment with adaptive routing based on task difficulty and quality evidence.

## Moonshot
Optimize a user-controlled frontier of dramatic quality, response latency, monetary cost and private/local inference in real time.

## Proving scenario
A model outage falls back or fails visibly; an accepted roll/effect is never silently redone by a new narrator.

## Independence and interfaces
Infrastructure works with existing role routes; real-time voice may demand tighter budgets but is not required.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Issue a provider request only for a `PlannedCall` of the turn's `TurnInferencePlan`, and book every attempt (failures and route fallbacks included) on its `TurnUsageLedger` as `provider_reported`, with `None` for any count the provider did not report (004, [inference](../../INFERENCE.md)). Usage sums are lower bounds while any count is unreported; reports must say so.
- Authorize every provider call as `execute` on `providers/<name>`; keys live under reserved roots and are read only by trusted adapter code (007, [tool authority](../../TOOL_AUTHORITY.md)).

**First-proof additions**
- A scripted provider fake with injectable timeout, error, truncated-stream and cancellation faults: a failed call never yields accepted state, and violations are named, seeded and bounded by call/token counts (006 milestone 2, the inference-run half of its fault harness).

## Wave 2 provider adapter contract

Port only enough of the V0.2.1 inference stack to issue an **authorized planned request** through a configurable real provider endpoint, using an independently testable scripted transport. This is a functional adapter, not just an abstract enum or a fake-only plan. No V2 scene runner or character prompt composition is required.

- Accept `PlannedCall` issued by the Wave 1 `TurnInferencePlan` and reject missing/declined/forged stages. Every provider attempt, including fallback, retry, timeout, streamed partial output and cancellation, is booked through `TurnUsageLedger`; unknown token counts remain `None` rather than zero, and provider-reported provenance cannot be self-declared by a fixture.
- Enforce `grimoire.security` `execute` approval for `providers/<id>` without exposing secrets in a worker handle, audit or serialized campaign data. A fake authorization denial must issue **zero** provider calls.
- Build a scriptable fake with seeded, bounded failures: timeout before send, error after send, truncated streaming response, mid-stream cancel and fallback. No failed/partial response becomes an accepted narrative or effects commit. The fault harness proves that a retry never silently becomes a second accepted call.
- Account separately for input, cached input, output/reasoning slices, absent usage, retries and route metadata; report provider latency if available. A session/turn total is a lower bound whenever any attempt lacks counts.
- Do not add a background thinking/critique call for every NPC: separate **writing calls per speaker** are the baseline; all optional paid stages remain disabled unless explicitly planned and permitted. No production costs in CI; live calls are explicitly authorized by the tester.

**Parallelism:** use synthetic opaque prompt payloads. 013 must neither import 014's voice prompt compiler nor 015's offline story-quality runner; both will consume its stable adapter/results when ready. See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#providervoicequality-boundaries-013--014--015--027).

## Open questions
- How are token accounting discrepancies handled?
- What objective signals trigger more expensive models?
- Which jobs may use local-only providers?
