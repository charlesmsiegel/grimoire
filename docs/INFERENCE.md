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
- a duplicate present actor id;
- a baseline stage (`npc_generation`) in an optional request;
- an optional request about an absent or player-controlled actor;
- a blank id, or a repeated phase.

**One plan per turn.** A turn that needs a step before generation calls `plan.add_phase(...)` on the same plan. For example, a `"select"` phase holds a `decision` call that chooses who answers, and then `"main"` generates the answers. The added phase shares the turn's policy, caps and one-contribution-per-NPC rule. Re-planning a turn is not supported; use a new turn id.

Plans validate their invariants on construction. A hand-built plan cannot hold any of these:

- a call the planner would refuse or decline;
- a decline the policy contradicts;
- calls or declines out of phase order.

Id collections must be real collections: a bare string such as `"npc-a"` is refused rather than split into letters.

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

A summary accounts only for the plan its ledger is bound to. Add later phases through the ledger (`ledger.add_phase(...)`), or rebind it with `ledger.extend(final_plan)`. `extend` accepts only a continuation of the same turn that has at least one new phase.

Token semantics follow the lessons of V0.2.1's usage ledger:

- A count the provider did not report is `None`, **never 0**.
- `input_tokens` is the whole prompt, *including* cached tokens. Adapters normalise providers that report only the uncached part.
- Cache reads/writes are slices of input, and reasoning is a slice of output. They are never additions.
- Every attempt is summed, including failed retries.
- A `not_sent` attempt used nothing, so its measurement does not affect `provider_reported_only`.

A ledger's `summary()` breaks usage down into `total`, `baseline`, `optional`, `by_stage` and `by_actor` (key `None` for scene-level calls). Each breakdown gives sums and also counts how many attempts left each field unreported. A sum covers reported counts only, so it is a lower bound whenever its field has any unreported attempts; a report must show the two together, never the sum alone. The summary also exposes:

- `unmetered_call_ids`: planned calls with no recorded attempt. These are missing data, not free calls.
- `complete`: no unmetered call, and every attempt reported input and output.
- `provider_reported_only`: `complete`, and every count was provider-reported. It is never true for an empty ledger.

Summaries are validated: their breakdowns must add up, stage by stage, and the flags are derived from their own figures. A summary or session cannot check itself against the plan, so **only a summary produced by `TurnUsageLedger.summary()` from a ledger bound to the turn's final plan is evidence of what a turn used**; never trust a hand-built or deserialised one for cost comparisons.

`SessionUsage(campaign_id)` aggregates turn summaries and counts each turn once. It carries the same totals and breakdowns (`total`, `baseline`, `optional`, `by_stage`, `by_actor`) and the same flags (`unmetered_call_ids`, `measurements`, `complete`, `provider_reported_only`). Usage values are immutable and hashable.

## Binding obligation on later sprints

A pure module cannot stop code elsewhere from calling a model, so the provider adapter (sprint 013) and generation runtime (027) **must**:

- accept a `PlannedCall` from the turn's plan and refuse to issue a model request without one;
- book every attempt, including failures and fallbacks to another route, to that turn's ledger.

Cost comparisons with V0.2.1 (015) must use `provider_reported_only` data. The story-quality run
format `/2` ([docs/evals/STORY_QUALITY.md](evals/STORY_QUALITY.md)) rebuilds each recorded turn
as a plan and ledger, and reports a run as measured only from such a summary plus verified
capture evidence.

**Status (Wave 2):** [`grimoire.providers`](PROVIDERS.md) meets this obligation for model calls:
- It refuses any `PlannedCall` its turn's plan lacks.
- It books every attempt, including retries and fallbacks, through one `TurnMeter` per turn.
- **An attempt begins at `connect()`.** A refusal before that (authorization, key, cancel) sends nothing and is not booked, so the call stays unmetered and the meter records status `refused`.
- **Evidence rule:** a `complete` summary only says every planned call was metered. For cost comparisons a turn needs `TurnMeter.cost_evidence`: provider-reported counts, every call accepted, no poisoned booking, and no loopback peer.

## Deferred

The following are not part of this slice:

- prices and currencies (the [provider adapter](PROVIDERS.md) now exists);
- ledger- and task-level latency aggregation (per-attempt latency is in the adapter's reports);
- session caps and guardrails that stop a turn;
- job-scoped (non-turn) ledgers;
- embeddings, which structurally have no output tokens;
- several contributions from one NPC in a turn;
- persistence and UI.
