# Inference costs and optional-intelligence flags (provisional contract)

**Status:** first slice of roadmap sprint 004, [Turn Costs And Feature Flags](roadmap/sprints/turn-costs-and-feature-flags.md). The code is the pure package `grimoire.core.inference`. It has no provider, I/O, clock or UI, and it is **not a persisted schema**: the storage (010) and provider (013) sprints must review these names before anything writes them to disk. It is deliberately not re-exported from `grimoire.core`, whose vocabulary is the fiction domain.

## The rule

V0.2.1's quality baseline is **one independent RP-generation call per speaking NPC contribution**. Every other model call that a turn might make is **optional, off by default, explicitly enabled and capped per turn**. An actor being present never implies a model call. Deterministic work is never a model call and never appears in a plan. That includes graph traversal, audience filtering, ordinary memory selection and state application.

| Stage | Status | Meaning |
|---|---|---|
| `npc_generation` | baseline | One RP generation per speaking NPC contribution. Not a flag; cannot be disabled or capped. |
| `planning` | optional | Model-assisted NPC planning |
| `decision` | optional | A model-selected choice, e.g. which NPCs answer, or a tactic |
| `epistemic_inference` | optional | Model-inferred perception or theory of mind |
| `recall` | optional | Model-assisted memory recall |
| `graph_lookup` | optional | Model-assisted graph query |
| `critique` | optional | Model critique or continuity review |

**Unlisted work cannot be planned.** Narration, embeddings, image generation and authoring/import jobs have no stage yet. They must not be issued during a turn until an enum member is added for them with an explicit baseline or optional status.

## Policy

`InferencePolicy(per_turn_call_caps={stage: cap}, max_attempts_per_call=1)`:

- A stage absent from `per_turn_call_caps` is disabled. Caps are integers ≥ 1.
- `max_attempts_per_call` bounds retries of *every* call, baseline included, so retries are visible in the plan's worst case.
- The default policy enables nothing optional and allows no retries.
- Session-level caps are a later milestone.

## Planning a turn

`plan_turn(campaign_id, turn_id, present_actor_ids=…, speaking_npc_ids=…, player_controlled_ids=…, optional_requests=…, policy=…)` returns a `TurnInferencePlan`:

- **Calls:** one `npc_generation` call per speaking NPC, then any optional calls that were requested *and* enabled *and* still within the turn's cap.
- **Declines:** requests that were refused, kept as data with reason `"disabled"` or `"cap_reached"` so a UI can say what was skipped.
- **Worst case:** `worst_case_attempts`, which is the number of calls × `max_attempts_per_call`.

The planner never decides who speaks. Speakers come from the player, the director, scene rules, or an earlier `decision` call.

These raise `InferencePlanError` before anything is planned:

- a speaker who is absent, listed twice, or already generated this turn;
- a player-controlled speaker (player sovereignty);
- an optional request about an absent or player-controlled actor;
- a blank id, or a repeated phase.

**One plan per turn.** A turn that needs a step before generation calls `plan.add_phase(...)` on the same plan. For example, a `"select"` phase holds a `decision` call that chooses who answers, and then `"main"` generates the answers. The added phase shares the turn's policy, caps and one-contribution-per-NPC rule. Re-planning a turn is not supported; use a new turn id. Plans validate every invariant on construction, so a hand-built plan cannot hold a call the planner would refuse.

## Booking usage

Each attempt at a planned call is a `CallAttempt` containing:

- the turn-scoped `CallId`;
- the attempt number;
- a `TokenUsage`;
- a `Measurement`: `provider_reported`, `estimated` or `synthetic`;
- an outcome: `completed`, `failed`, `cancelled` or `not_sent`;
- an optional opaque `route` label naming the provider/model. It must contain no secrets.

`TurnUsageLedger(plan).record(attempt)` returns a new ledger. Booking is refused in these cases:

- **Unplanned call:** `UnplannedCallError`. This covers fabricated ids, declined requests and another turn's calls.
- **Bad attempt sequence:** an attempt over the limit, out of order, duplicated, or recorded after a completed attempt.

After `add_phase`, rebind the ledger with `ledger.extend(new_plan)` before summarising.

Token semantics follow the lessons of V0.2.1's usage ledger:

- A count the provider did not report is `None`, **never 0**.
- `input_tokens` is the whole prompt, *including* cached tokens. Adapters normalise providers that report only the uncached part.
- Cache reads/writes are slices of input, and reasoning is a slice of output. They are never additions.
- Every attempt is summed, including failed retries.

A ledger's `summary()` breaks usage down into `total`, `baseline`, `optional`, `by_stage` and `by_actor` (key `None` for scene-level calls). Each breakdown gives sums and also counts how many attempts left each field unreported. The summary also exposes:

- `unmetered_call_ids`: planned calls with no recorded attempt. These are missing data, not free calls.
- `complete`: no unmetered call, and every attempt reported input and output.
- `provider_reported_only`: `complete`, and every count was provider-reported. It is never true for an empty ledger.

`SessionUsage(campaign_id)` aggregates turn summaries, counts each turn once and carries the same breakdowns and flags.

## Binding obligation on later sprints

A pure module cannot stop code elsewhere from calling a model, so the provider adapter (sprint 013) and generation runtime (027) **must**:

- accept a `PlannedCall` from the turn's plan and refuse to issue a model request without one;
- book every attempt, including failures and fallbacks to another route, to that turn's ledger.

Cost comparisons with V0.2.1 (015) must use `provider_reported_only` data.

## Deferred

The following are not part of this slice:

- provider adapters, prices and currencies;
- latency;
- session caps and guardrails that stop a turn;
- job-scoped (non-turn) ledgers;
- embeddings, which structurally have no output tokens;
- several contributions from one NPC in a turn;
- persistence and UI.
