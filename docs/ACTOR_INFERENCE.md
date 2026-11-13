# Actor presentation experiments (068)

`grimoire.actor_inference` is an isolated headless adapter over the existing provider,
authorization, inference plan, meter, voice and story-quality contracts. It does not
write canonical state, choose actions, rewrite accepted speech, change the decision
UI, or connect to the ensemble package. Every output is an unreviewed proposal.

## Planning and execution

`plan_experiment(campaign_id, turn_id, *, present, speakers, controls,
planners=(), critics=(), policy=None)` returns a sealed `TurnInferencePlan` subtype.
The four actor collections contain typed `InstanceRef` values. Instance ids must be
unique across kinds and belong to the campaign; every present actor needs one
unambiguous controller. Speakers must be present and director-controlled. Extras
must name speakers, even when their policy stage is disabled.

Planning, baseline generation and critique are separate ordered phases. Each speaker
gets one baseline call; a silent present NPC gets none. An optional call requires
both explicit selection in `planners`/`critics` and an `InferencePolicy` stage cap.
Declined requests remain in `plan.declined`. A cap cannot reset between phases.

Create a `TurnMeter(TurnUsageLedger(plan))` and an `ActorExperimentRunner(adapter,
routes, handle, approvals=..., max_output_tokens=..., clock=...)`. Then call:

```python
outcome = runner.run(
    meter, speaker_briefs, controls=current_controls, present=current_present,
    budget=ExperimentBudget(max_attempts=12, max_output_tokens=4800),
    token=fence_token, cancel=cancel_token,
)
```

`present` is mandatory. Before provider I/O, the runner checks the exact typed cast
and current controller mapping against the plan, one sealed matching `NpcBrief`
per speaker, and any brief's saved cast. Any existing `Choice`, selected or
unselected, forbids planning for that actor. Plain or altered plans are refused.
No decision authority is delegated to a planner.

The planner sees only its actor's sealed messages. Its bounded, screened suggestion
may enter only that actor's generation prompt, labelled untrusted and presentation-only.
Critique sees only its own brief and generated reply, returning a nonauthoritative
report without rewriting or rerolling. Critique screening treats only that own reply
as newly exposed input; another actor's speech is not critique exposure. Private suggestions/reports are in
`outcome.proposals`, keyed by actor and call; they are privileged data, not public
speech. Do not distribute this tuple wholesale to actors.

Baseline generation uses the same `npc_messages`/`screen_reply` contracts as the
existing runtime. Earlier accepted speech is public by default; pass
`hears(listener_ref, speaker_ref)` to restrict transmission. The predicate must
return exactly `True` to admit a line. Optional calls receive no other actor's
newly generated speech. Historic private hearing is the sealed brief builder's job.

The request bound is 100,000 characters, baseline reply bound 2,000, optional reply
bound 1,000, plus the explicit per-request output-token cap. Screening detects known
markers, copied examples and existing ownership violations; it is not semantic
proof of secrecy, presentation-only intent, or player authorship. No authority or
canonical-writer API consumes these outputs.

## Budget and failures

`ExperimentBudget` reserves the policy's worst-case retry attempts and
`attempts * max_output_tokens` before each call. Reservation is lock-protected and
never refunded, including failed, refused and cancelled calls. Share the object
across turns for a conservative session allowance; create a new object for a
separate allowance. These are authorized-attempt/output-token units, not input
usage, actual output consumption, or a dollar spending ceiling. Live use requires
separate provider authorization and an approved monetary spending limit.

An exhausted reservation sends nothing and returns `budget_exhausted`. Provider
failure, refusal, incomplete response or cancellation stops remaining calls;
already booked usage remains. Invalid optional text records `rejected` and baseline
can continue unchanged. Invalid baseline text halts. There are no extra fallback
calls. Every planned call has a status, including `not_issued`.

The runner refuses concurrent/reentrant use. A module-wide weak meter guard also
refuses replay across runner instances, even after a zero-call cancellation. The
existing adapter enforces retry/turn accounting. A `RunFence` token cancels stale
work and suppresses all line and proposal text while retaining usage/statuses.

## Measurement and comparison

`record_experiment(scenario, meter, outcome, run_id=..., turn=..., conditions=...,
player_lines=(), choice_outcomes=None, prices=None)` produces a validated
`grimoire.story-quality/2` record. It records planned calls and every booked attempt,
including retry failures and unissued calls, but only baseline text in the transcript.
It rejects a different/tampered outcome, changed ledger/statuses, or poisoned meter.
Conditions are recorded exactly as supplied; the caller owns truthful capture metadata.

The report separates:

- Baseline and optional `UsageTotals`: all token fields, cached usage and measurement
  origins are retained. Missing counts remain unknown; unmetered calls are not free.
- `quality`: existing lexical voice distinguishability, secrecy marker leak rate and
  hard-invariant findings. Semantic characterization has **no measured score**.
  Marker findings are evidence, not a semantic quality or privacy guarantee.
- Whole-call elapsed time, including retries/backoff, with its origin, versus each
  provider attempt's first-byte/total latency. Any synthetic underlying measurement
  makes the whole-call origin synthetic. The default origin is conservative synthetic;
  measured custom clocks require explicit `clock_origin='measured'`.
- `money`: `usage_cost` using the supplied `PriceTable`, labelled estimated, priced,
  partial or unpriced, with `exact_usage` indicating whether a value is a lower bound.
  No money estimate is inserted as billed cost in the /2 record. `actual_billing`
  is `None`: the current meter supplies no invoice evidence, and `cost_evidence`
  is only an eligibility boolean, not a bill.

`compare_experiments(scenario, baseline_report, candidate_report, policy=GatePolicy(...))`
uses the existing /2 hard-quality, voice, weighted-token and configured latency gates.
Optional flags require explicit gate permission. Changed conditions fail comparability;
missing usage fails cost comparison rather than becoming zero. Weighted-token tolerances
are not dollar ceilings. No judge scores are invented.

`synthetic_benchmark(scenario, factory, conditions=..., policy=..., ...)` is a runnable
injected benchmark. `factory('baseline')` and `factory('planner_critic')` must each
return `BenchmarkInput(runner, meter, briefs, controls, present, budget)` with separate
scripted adapters. It validates matching routes, output cap, cast, ordered speakers and briefs,
requires zero baseline extras and both enabled candidate stages, refuses the adapter's
real-HTTP measurement mode before execution, and checks synthetic measurements afterward.
Injected adapters/factories are trusted caller code, not a security sandbox. Use a
scripted injected clock for deterministic elapsed times.

The complete deterministic example is executable without a provider:

```sh
PYTHONPATH=backend/src python -m pytest \
  backend/tests/test_actor_inference_report.py::test_deterministic_injected_benchmark_runs_both_variants_and_reports_synthetic -q
```

It uses shared generation/evaluation fixtures, the real provider parser/meter and
scripted transport. Its label is `scripted synthetic; not live parity`. Real matched
V0.2.1 capture belongs to 015 and has not been performed here; 068's live measurement
acceptance remains incomplete. This slice has no application/UI integration or schema
migration. Removing its consumers leaves canonical campaign data unchanged.
