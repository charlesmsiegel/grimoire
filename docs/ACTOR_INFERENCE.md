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
acceptance remains incomplete. Removing its consumers leaves canonical campaign data unchanged.

## Runtime adoption

The app adopts one of the experiment's stages, presentation planning, through the ordinary
generation runtime rather than the experiment runner. Critique is not adopted: the app's
`critique` stage is 047's continuity extraction, a different call.

- `plan_generation_turn(..., planners=...)` plans one `planning` call per named speaker in a `plan`
  phase before the main phase, within the policy's `planning` cap; a planner that does not speak is
  `planner_not_speaking`. Requests over the cap are kept in `plan.declined`. With no cap (the default)
  the plan is unchanged: one `npc_generation` call per speaking NPC and nothing else.
- `GenerationRuntime.run_turn` issues each planning call before any reply, with the NPC's own sealed
  messages (`planning_messages`) and an output cap of 256 tokens. The reply text must be at most
  1,000 characters and pass the same `screen_reply` as a line (no copied example, no unlearned
  secret). An accepted suggestion is appended to that same NPC's generation prompt only, labelled
  untrusted and presentation-only (`with_suggestion`). It is never a line, never heard by anyone else
  and never stored.
- A failed, refused, incomplete or screened-out suggestion is recorded with its status and the turn
  goes on as with the stage off. Cancellation stops the turn, and an exception from the adapter (an
  approvals callback or adapter bug, not a provider answer) halts it, as it does for a reply. An NPC whose brief carries a decided
  choice gets no planning call: it is forgone (`not_issued`, `has_choice`) and costs nothing, so no
  suggestion competes with the decided intention.
- The app's `planning` setting (0–16) sets the cap ([INFERENCE_UI](INFERENCE_UI.md)); 035's
  `SceneRunner` names every chosen speaker without an agenda as a planner when the cap is set (an
  agenda NPC is briefed with its choice, so its call would only be forgone). Every planning attempt is
  metered and listed in usage like any other optional call, and each take (a reroll included) plans
  again. No schema or stored format changed.

## The V2-against-0.2.1 comparison

`python -m grimoire.capture.v2` makes the billed comparison one step on 015's capture directory
(`run.json` and `evidence.json`, [V021_BASELINE](evals/V021_BASELINE.md)):

```sh
PYTHONPATH=backend/src python -m grimoire.capture.v2 --scenario guild_betrayal \
  --baseline <015 capture dir> [--baseline <another capture dir> ...] \
  --out <new dir outside the repo> --dry-run [--samples N] [--with-planning]
```

It verifies the capture's evidence, then takes its provider, served model, output budget and attempt
limit, so V2 is recorded under the capture's own conditions. The model V2 records is the one that
answered: if the provider serves another model than the capture's (a resolved snapshot, say, or more
than one model), that is recorded, and the gate reports the runs as incomparable. It plays the scenario's three turns on
`GenerationRuntime`: each speaking NPC's brief has its card's description, personality and example
turns (read with V2's own importer), what was said aloud before the call and the turn's player text,
which is what the 0.2.1 side had. A scenario secret is screened only for an actor that has not learned
it by the scenario's `known_from` turn and is never shown. It writes `v2-baseline.run.json` (a `/2` run
of source `v2-runtime`), with `--with-planning` also `v2-planning.run.json`, and
`comparison-report.txt`: the gate's report of V2 against 0.2.1 (voice distinguishability, hard
findings and token usage, reported apart), then V2 with planning against plain V2.

- `--dry-run` sends nothing: a scripted transport answers with the scenario's dry-run replies, and
  every number is synthetic.
- `--live --confirm-billing --api-key-env VAR` is billed. It is refused against a dry-run capture,
  prints the request ceiling (planned calls times attempts: 18 for the scenario at three attempts, 54
  with planning) before sending, reads the key only from the named variable and writes nothing that
  contains it. If it fails or is stopped, nothing is written and it prints what was already sent, as
  counts only. Running it needs CMS's own spending approval, separate from 015's; the one
  approved run is [recorded below](#billed-comparison-2026-11-20).
- Against an `anthropic` capture V2 calls Anthropic's own Messages API, as 0.2.1 did, so cache reads
  and writes are provider-reported and the cost can be compared. The prompts still differ, so the
  report says the token sums are not like for like. (Until 2026-11-22 V2 used Anthropic's
  OpenAI-compatible endpoint, which reports no cached-token counts; that is why the first billed run
  said `usage_incomplete`.) A capture whose budget is `max_tokens=unset` is reported incomparable,
  since V2 always sends an output cap.
- `--samples N` (1 to 10) plays V2 N times, and with planning N more times, and `--baseline` may be
  repeated with other 0.2.1 captures of the same play (same provider, model, budget, attempts and
  conditions; the same capture twice is refused). With more than one sample on either side the
  report gates [every sample together](#sampled-comparison). The request ceiling printed before a
  live run is multiplied by N, and the files are numbered (`v2-baseline-1.run.json` and so on).
- The first comparison (V2 against 0.2.1) is 068's proving scenario. The second, with planning, is
  an experiment: a failure there rejects the optional stage, not V2, and the report says so.
- The V2 run has no capture evidence of its own, so its usage is provider-reported and unverified,
  and its choices are unlabelled (judged on marker evidence only). Voice is the existing lexical
  distinguishability measure; no semantic characterization score exists.

Proofs: `backend/tests/test_presentation_planning.py` (runtime, play and app),
`backend/tests/test_capture_v2.py` (dry runs against 015's committed dry-run evidence, refusals, and
the real HTTP transport against a loopback server) and `backend/tests/test_evals_sampled.py` (the
sampled gate).

### Sampled comparison

A billed play is one draw from a model that answers differently each time. With six NPC lines one
line voiced off its profile moves cast distinguishability by a sixth, and one reply that happens not
to name a choice's effect fails the choice; the two billed runs below failed on different findings
for that reason. With several samples on a side (`--samples`, repeated `--baseline`), the gate
(`grimoire.evals.sampled`) reads the same requirement over all of them:

- every hard invariant (a leak, a model line that negates a choice, a missing scripted speaker and
  the rest) must hold in **every** sample, on both sides;
- a choice is given effect when model text realizes it in a **strict majority** of a side's samples;
- voice compares the two sides' **mean** cast distinguishability. It regresses when V2's mean is
  below 0.2.1's by more than the policy's allowed drop (0) plus a noise allowance of twice the
  standard error of the difference of the means. The allowance is used only when both sides have at
  least 5 samples; with fewer it is zero;
- cost and latency compare means with the usual tolerances, and a sample with unreported counts
  still makes cost incomparable.

With one sample on each side every rule is the one-run rule, so the verdict is unchanged
(`test_one_sample_each_gives_the_one_run_verdict`). The report states the rule and prints, per
choice, how many samples on each side realized it, both sides' distinguishability values, the noise
allowance and every sample's metrics. Five samples a side is the intended size: 015's capture
(`python -m grimoire.capture.v021`, at most 18 requests each) four more times, and `--samples 5`.

### Billed comparison (2026-11-20)

One billed comparison was run with `--live --with-planning` against 015's
[measured baseline](evals/V021_BASELINE.md#measured-baseline-2026-11-20): 18 calls to
`claude-sonnet-4-5-20250929` at `max_tokens=16000`, none retried, under a request ceiling of 54.
**The gate failed both comparisons, so 068's proving scenario is not established.**

| Run | Calls | Input tokens | Output tokens | Cast distinguishability | Choices unrealized |
|---|---:|---:|---:|---:|---:|
| 0.2.1 baseline (measured) | 6 | 15,618 | 1,396 | 0.833 | 0 |
| V2, one call per speaking NPC | 6 | 3,110 | 788 | 1.000 | 1 |
| V2 with planning | 12 (6 planning) | 7,111 | 1,585 | 0.833 | 2 |

- **V2 against 0.2.1: FAIL**, two findings. `choice_negated` on turn 3 (`ask-song`, unrealized:
  no model text gives effect to the choice), and `usage_incomplete`: the OpenAI-compatible
  endpoint reported no cached-token counts, so cost cannot be compared. Leak rate 0 on both.
- **Planning against plain V2: FAIL**, six findings. The baseline's own turn-3 finding
  (`baseline_invalid`), `choice_negated` on turn 1 (`ask-ledger`) and turn 3, `usage_incomplete`
  on both runs, and `voice_regression`: cast distinguishability fell from 1.000 to 0.833.
  Planning added 6 calls and roughly doubled input and output tokens.
- The token sums are not like for like: 0.2.1 called the Messages API with its own prompt
  assembly, and V2's usage is provider-reported without capture evidence. V2's lower input
  count is not a cost claim. Choices are unlabelled and judged on exact marker phrases, which
  live text rarely contains, so the unrealized-choice findings may reflect the markers.
- No cost is in either run. At an assumed list price of 3 USD input and 15 USD output per
  million tokens the 18 calls come to about 0.07 USD, as an estimate.

One three-turn scenario on one model is a single sample. It does not show that planning helps,
and here it measured worse on both voice and choices.

### What changed after the first billed run (2026-11-22)

- **Cost.** `usage_incomplete` came from the protocol, not from the run: Anthropic's OpenAI-compatible
  endpoint reports no cached-token counts, and the gate never reads a missing count as zero. V2 now
  calls the Messages API for an `anthropic` capture (see [PROVIDERS](PROVIDERS.md)), which reports
  cache reads and writes.
- **Choices.** `ask-song` was judged only on `sings` or `song`, so a bard who "sang", or answered with
  a verse or a tune, counted as unrealized. Its effect markers now take those forms too. Negation
  markers are unchanged, so no reply that passed before can fail now, and `ask-ledger` is unchanged
  (a bare "ledger" would also match a refusal).
- **Planning.** The voice regression is an experiment result, not a scoring fault, and stays.

A rerun is needed before 068's box can be checked.

### Billed comparison (2026-11-22)

The rerun, approved by CMS on 2026-11-22 and run once with `--live --with-planning` against the
same 015 capture (evidence sha256 `1824a081…d24ff0`, not recaptured): 18 calls to
`claude-sonnet-4-5-20250929` at `max_tokens=16000`, none retried, under a request ceiling of 54.
**V2 against 0.2.1 failed the gate again, so 068's proving scenario is not established.**

| Run | Calls | Input tokens | Output tokens | Cache read / write | Weighted billed tokens | Cast distinguishability | Choices unrealized |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.2.1 baseline (measured) | 6 | 15,618 | 1,396 | 0 / 0 | 17,014 | 0.833 | 0 |
| V2, one call per speaking NPC | 6 | 3,646 | 995 | 0 / 0 | 4,641 | 0.667 | 1 |
| V2 with planning | 12 (6 planning) | 7,352 | 1,497 | 0 / 0 | 8,849 | 1.000 | 1 |

- **V2 against 0.2.1: FAIL**, two findings. `choice_negated` on turn 1 (`ask-ledger`, unrealized:
  no model text gives effect to the choice), and `voice_regression`: cast distinguishability fell
  from 0.833 to 0.667. Leak rate 0 on both. This comparison decides 068.
- **Planning against plain V2: FAIL**, three findings (an experiment; it does not decide 068).
  `baseline_invalid` for the baseline's own turn-1 finding, `choice_negated` on turn 1
  (`ask-ledger`) in the planning run too, and `cost_regression`: weighted billed tokens rose from
  4,641 to 8,849. Cast distinguishability rose from 0.667 to 1.000.
- `usage_incomplete` is gone: both sides called the Messages API and cache counts are reported
  (all zero). The token sums are still not like for like, because the prompts differ, and V2's
  usage has no capture evidence of its own.
- The 2026-11-20 findings did not repeat and these are different ones. Then plain V2 scored 1.000
  on voice and failed `ask-song` on turn 3; now it scores 0.667 and fails `ask-ledger` on turn 1,
  and planning moved voice the other way. Two single samples disagree, so neither shows a stable
  effect of planning, and six lines is too few for the voice measure to be steady.
- No cost is in either run. At an assumed list price of 3 USD input and 15 USD output per million
  tokens the 18 calls (10,998 input and 2,492 output tokens) come to about 0.07 USD, as an estimate.

The method changed after this run (2026-11-22): the comparison can now be scored over several
samples per side ([sampled comparison](#sampled-comparison)), so a rerun with five captures and five
V2 plays is what can decide 068.

### Billed comparison (2026-11-22, sampled): not run

The sampled rerun was approved for four more 0.2.1 captures and one comparison of at most 270
requests. **It did not run, so 068 is still not established.** The first of the four captures
(sample 2) failed in turn 1 and wrote nothing; under the approval's rule a failed billed step is
not retried, so captures 3 to 5 and the comparison were not started. There are no per-sample
figures, choice counts, noise allowance or findings to report: neither comparison was made. The
failed capture is recorded in [V021_BASELINE](evals/V021_BASELINE.md#sampled-capture-attempt-2026-11-22).

068 now waits on five 0.2.1 captures, and a live 0.2.1 capture on this route has failed in turn 1
on two of three attempts with this model.
The cause was found offline afterwards: 0.2.1's own empty-response check, when the second speaker's
reply is written under another speaker's label or leaves no text
([why](evals/V021_BASELINE.md#why-turn-1-fails-offline-2026-11-22)). The capture now reports which.
