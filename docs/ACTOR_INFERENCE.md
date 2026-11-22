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
- `--rescore DIR` scores an earlier comparison's V2 runs again, read back from its output `DIR`,
  against the same `--baseline` captures in the same order (their evidence digests must match the
  ones `DIR`'s report names, and their mode must be the one it names), with the scenario and the
  gate as they are now. It sends nothing and bills nothing, takes none of `--samples`,
  `--with-planning`, `--api-key-env` or `--confirm-billing` (the sample count and planning come from
  `DIR`'s files), and writes only a new `comparison-report.txt`. That report copies the earlier
  report's paragraphs about its run word for word, says it is a rescore, and gives new verdicts. It
  is how a change to a scenario's markers is measured on a run already made: the replies are read
  again, never played again. Any change to the gate since that run is applied too, and nothing binds
  the run files to the earlier report beyond their names, so a rescore is a measurement, not a
  verdict.
- `--label-sheet DIR` writes `label-sheet.json` and `label-key.json` to `--out`. The sheet holds
  every play of the comparison in `DIR` (its 0.2.1 captures, plain and planned V2 runs), shuffled
  under neutral ids, each choice with the model text the choice check reads and an empty outcome to
  fill in. The key says which run each id is and is not given to the reader.
  `--rescore DIR --labels FILE --label-key KEY` then decides every choice from the filled-in sheet
  instead of from phrase markers ([why](#choices-decided-by-labels-2026-11-22)). Both send nothing
  and bill nothing.
- The first comparison (V2 against 0.2.1) is 068's proving scenario. The second, with planning, is
  an experiment: a failure there rejects the optional stage, not V2, and the report says so.
- The V2 run has no capture evidence of its own, so its usage is provider-reported and unverified,
  and its run carries no choice labels: choices are read from marker evidence unless a rescore is
  given [reader's labels](#choices-decided-by-labels-2026-11-22). Voice is the existing lexical
  distinguishability measure; no semantic characterization score exists.

Proofs: `backend/tests/test_presentation_planning.py` (runtime, play and app),
`backend/tests/test_capture_v2.py` (dry runs against 015's committed dry-run evidence, refusals, and
the real HTTP transport against a loopback server), `backend/tests/test_evals_sampled.py` (the
sampled gate) and `backend/tests/test_evals_labels.py` (choices decided by labels).

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
Offline, 0.2.1's own empty-response check on the second speaker reproduces the same observable
failure; the live error kind was not recorded, so the cause is not confirmed
([why](evals/V021_BASELINE.md#why-turn-1-fails-offline-2026-11-22)). A failed capture now reports
0.2.1's error kind and the shape of each reply.

### Billed comparison (2026-11-22, sampled, second approval): not run

A second approval allowed up to 10 attempts at the four missing 0.2.1 captures, then one sampled
comparison of at most 270 requests. **The comparison did not run, so 068 is still not
established.** The 10 attempts gave one success and nine `empty_response` failures
([attempts](evals/V021_BASELINE.md#capture-attempts-2026-11-22-with-the-failure-report)), fewer
than the four needed, and the rule was then to stop. There are no per-sample or mean figures,
choice realization counts, noise allowance or gate findings for either comparison: none was made.
29 calls were billed on the captures (71,082 input and 4,309 output tokens, about 0.28 USD
estimated) and none on V2.

What stands between 068 and a sampled result is the capture rate: unmodified 0.2.1 completed
this scene on 1 of 10 attempts, so five baseline samples would take on the order of 50 attempts
at this rate. The one new capture is a valid completed sample. Scored alone it has an unrealized
choice (`ask-ledger`, turn 1), but the sampled gate does not judge that per sample: a choice
counts against a side only if it is realized in no strict majority of that side's five samples.

A cheaper route to the missing samples is now in place, proved offline only: the 0.2.1 capture
can press 0.2.1's own Retry on a reply it rejects as empty (`--retry-empty-replies`), which asks
that one reply again from the same prompt instead of replaying the whole play. The rejected
replies are recorded and reported but kept out of the baseline run, so 0.2.1's side stays a lower
bound on its cost ([how](evals/V021_BASELINE.md#retrying-a-rejected-reply-offline-2026-11-22)).

### Billed comparison (2026-11-22, sampled)

The sampled comparison was run once, with `--samples 5 --with-planning`, against five 0.2.1
captures (the 2026-11-20 capture, the one success of the ten earlier attempts, and
[three made with Retry](evals/V021_BASELINE.md#captures-with-retry-2026-11-22)). It printed a
ceiling of 270 requests and made 90: 30 for plain V2 and 60 with planning, none retried, all on
`claude-sonnet-4-5-20250929` at `max_tokens=16000`.
**V2 against 0.2.1 failed the gate, so 068's proving scenario is not established.**

**V2 (one call per speaking NPC) against 0.2.1: FAIL.** This comparison decides 068.

| Sample | 0.2.1 calls | 0.2.1 input / output | 0.2.1 distinguishability | V2 calls | V2 input / output | V2 distinguishability |
|---:|---:|---|---:|---:|---|---:|
| 1 | 6 | 15,618 / 1,396 | 0.833 | 6 | 3,484 / 937 | 0.833 |
| 2 | 6 | 15,254 / 978 | 0.667 | 6 | 3,612 / 917 | 1.000 |
| 3 | 6 | 14,972 / 773 | 0.500 | 6 | 3,469 / 1,005 | 0.833 |
| 4 | 6 | 16,177 / 1,380 | 0.667 | 6 | 3,258 / 874 | 0.833 |
| 5 | 6 | 15,409 / 1,188 | 0.667 | 6 | 3,231 / 836 | 0.833 |
| Mean | 6 | 15,486 / 1,143 | 0.667 | 6 | 3,410.8 / 913.8 | 0.867 |

Cache reads and writes are 0 in every sample on both sides. Mean weighted billed tokens:
0.2.1 16,629, V2 4,324.6. Leak rate 0 and no unmetered call in every sample.

| Choice | 0.2.1 samples realizing it | V2 samples realizing it |
|---|---:|---:|
| `ask-ledger` (turn 1) | 2 of 5 | 1 of 5 |
| `follow-bellamy` (turn 2) | 4 of 5 | 5 of 5 |
| `ask-song` (turn 3) | 4 of 5 | 5 of 5 |

Findings (2):
- `baseline_invalid`, turn 1, `ask-ledger`: the 0.2.1 side gives effect to the choice in 2 of 5
  samples, not a majority.
- `choice_negated`, turn 1, `ask-ledger` (unrealized): V2 gives effect to it in 1 of 5 samples,
  not a majority.

Voice did not regress: mean distinguishability rose from 0.667 to 0.867, with an allowed drop of
0.000 and a noise allowance of 0.125. Cost did not regress. Both findings are the same choice. It
fails on 0.2.1's side as well, and that baseline finding alone fails the gate whatever V2 does.

**V2 with planning against plain V2: FAIL.** An experiment; it does not decide 068.

| Sample | Plain V2 calls | Plain V2 input / output | Plain V2 distinguishability | Planning calls | Planning input / output | Planning distinguishability |
|---:|---:|---|---:|---:|---|---:|
| 1 | 6 | 3,484 / 937 | 0.833 | 12 | 6,372 / 1,412 | 0.667 |
| 2 | 6 | 3,612 / 917 | 1.000 | 12 | 6,944 / 1,522 | 0.667 |
| 3 | 6 | 3,469 / 1,005 | 0.833 | 12 | 7,028 / 1,380 | 0.833 |
| 4 | 6 | 3,258 / 874 | 0.833 | 12 | 7,901 / 1,665 | 1.000 |
| 5 | 6 | 3,231 / 836 | 0.833 | 12 | 7,090 / 1,454 | 1.000 |
| Mean | 6 | 3,410.8 / 913.8 | 0.867 | 12 | 7,067 / 1,486.6 | 0.833 |

Cache reads and writes are 0 throughout. Each planning sample is 6 planning and 6 generation
calls. Mean weighted billed tokens: plain 4,324.6, planning 8,553.6.

| Choice | Plain V2 samples realizing it | Planning samples realizing it |
|---|---:|---:|
| `ask-ledger` | 1 of 5 | 4 of 5 |
| `follow-bellamy` | 5 of 5 | 5 of 5 |
| `ask-song` | 5 of 5 | 4 of 5 |

Findings (2):
- `baseline_invalid`, turn 1, `ask-ledger`: plain V2 gives effect to the choice in 1 of 5 samples.
- `cost_regression`: mean weighted billed tokens rose from 4,324.6 to 8,553.6.

Mean distinguishability went from 0.867 to 0.833, inside the noise allowance of 0.163, so there
is no voice finding. Planning realized `ask-ledger` in 4 of 5 samples against 1 of 5 without it,
for about twice the tokens.

What the numbers leave out:
- **0.2.1's side is a lower bound on what its player paid.** 0.2.1 rejected 10 replies as empty in
  2 of the 5 captures and its own Retry asked again: 10 requests, 26,490 input and 46 output
  tokens that are not in its runs. Its cost and latency here are those of the play it kept.
- The token sums are not like for like: the prompts differ, and V2's usage is provider-reported
  with no capture evidence of its own. V2 reports no latency.
- Two of the five baseline samples came from earlier attempts made without the Retry.
- Choices are unlabelled and judged on marker phrases. `ask-ledger` is realized in a minority of
  samples on both sides, which is what failed; whether that is the play or its markers is not
  settled here, and the markers were not changed.
- No cost is in any run. At an assumed list price of 3 USD input and 15 USD output per million
  tokens the 90 V2 calls (52,389 input and 12,002 output tokens) come to about 0.34 USD, and
  the whole rerun with its captures to about 0.61 USD, as estimates.

### What changed after the sampled run (2026-11-22)

- **Choices.** `ask-ledger` was judged on one phrase, `guest ledger`, and nothing else: a reply in
  which Oda slid the ledger across, opened it, took the coin or handed over the guest book counted as
  unrealized. Its effect markers now also take the other names of the same book (`guest book`,
  `guestbook`, `guest-ledger`, `guests' ledger`, `guest register`, `ledger of guests` and the like,
  with plurals), and the acts that hand it to Wren, each tied to its giving verb (`hands over the
  ledger`, `hands Wren the ledger`, `gives the ledger to Wren`, `slides the ledger across`, `turns the
  ledger toward Wren` and the like, present and past). Acts that a refusal uses as readily (`takes
  the coin`, `opens the ledger`, `turns the ledger`, a bare `the ledger across` or `the ledger to
  Wren`) and a bare `ledger` ("Ledger's closed tonight") do not count, and neither does a `guest
  list`, which is another thing. Matching is a phrase search with no sense of negation or direction,
  so an act is kept only if a refusal would rarely use it; "Oda never hands over the ledger" or a
  ledger slid across to someone else still counts. A name of the book still counts wherever it appears
  after Wren's line, as `guest ledger` always did, so a flat refusal that names it ("No guest book
  for strangers") counts too; with more names that is now more likely. Negation markers are unchanged, so no reply that passed before can
  fail now. The full list is in the scenario file, and `backend/tests/test_evals_checks.py` holds
  the forms that must and must not realize the choice.
- **Why it looked like the markers.** 0.2.1 itself realized the choice in 2 of 5 samples, and V2 with
  planning in 4 of 5 against 1 of 5 without it. Nothing in the gate changed between those runs but
  the wording of the replies. That fits replies that do give effect to the request in other words,
  but it does not show it.
- **How it is chosen.** The markers were written from the scenario's own text and the forms a reply
  would use, without reading the run's replies, which are not in this repository. They were not fitted
  to that run.
- **Measured without a new play.** `--rescore` reads the sampled run's saved V2 runs and its 0.2.1
  captures again with the new markers, at no cost. That rescore shows whether the markers explain the
  failure. A fresh billed comparison then decides 068, so its verdict does not rest on a run read
  after the markers changed.

### Billed comparison (2026-11-22, sampled, wider ask-ledger markers)

After the sampled comparison failed on `ask-ledger` alone, the scenario's markers for that choice
were widened to the book's other names and the acts of handing it over. The plan was a free
rescore of the saved comparison, then one fresh billed comparison only if 0.2.1 now realized
`ask-ledger` in 3 or more of its 5 samples. **It did not, so the billed comparison was not run
and 068 is still not established.** Nothing was sent or billed for 068.

The rescore read the saved V2 runs back and scored them again against the same five 0.2.1
captures in the same order, with the scenario and gate as they are now.

| Choice | 0.2.1 | Plain V2 | V2 with planning |
|---|---:|---:|---:|
| `ask-ledger` (turn 1) | 2 of 5 (was 2) | 2 of 5 (was 1) | 4 of 5 (was 4) |
| `follow-bellamy` (turn 2) | 4 of 5 | 5 of 5 | 5 of 5 |
| `ask-song` (turn 3) | 4 of 5 | 5 of 5 | 4 of 5 |

- **V2 (one call per speaking NPC) against 0.2.1: FAIL**, two findings, both `ask-ledger` on
  turn 1: `baseline_invalid` (0.2.1 gives effect to the choice in 2 of 5 samples, not a majority)
  and `choice_negated`, unrealized (V2 in 2 of 5, not a majority).
- **V2 with planning against plain V2: FAIL**, two findings: `baseline_invalid` on `ask-ledger`
  (plain V2 in 2 of 5) and `cost_regression` (mean weighted billed tokens 4,324.6 to 8,553.6).
- Voice, tokens and every other figure are those of the
  [comparison as run](#billed-comparison-2026-11-22-sampled): the rescore changes only how
  choices are read. The 0.2.1 side's cost and latency still leave out its rejected replies, so
  they are a lower bound on what its player paid.

The wider markers moved one V2 sample and no 0.2.1 sample. So the markers do not explain the
failure: in three of five 0.2.1 plays, and in three of five plain V2 plays, no reply is read as
giving effect to the request for the ledger under either set of markers. Whether those replies
truly ignore the request has not been checked by a person. Planning realizes it in 4 of 5.

### Choices decided by labels (2026-11-22)

The replies of all 15 plays of the sampled comparison were then read by a person's session, with
nothing changed. Oda answers Wren on turn 1 in every play and refuses or stalls in 14 of them, as
her card tells her to ("Rules first", and a first example line that turns a visitor away). A play
was counted as realizing `ask-ledger` whenever any reply happened to say "guest ledger", the
refusals included; refusals without the word were counted as unrealized. So the choice check
measured a phrase, not what the scene did with Wren's act. Planning's 4 of 5 was the same artefact.

The choice was redefined (CMS's decision, 2026-11-22): **a choice is given effect when the scene
engages with the player's act, in character, whether or not the player gets what they wanted.** A
refusal that answers the coin and the ask is a real response to the player. Phrase markers cannot
judge that: the same refusal passes or fails on one word. So, for every choice and not only
`ask-ledger`, a comparison can now be decided by a reader's labels:

| Label | Meaning | Effect on the gate |
|---|---|---|
| `honored` | the scene gives the act the effect the player sought | gives the choice effect |
| `complicated` | the scene answers the act in character but not as hoped: a refusal, a condition, a stall or a question about the act | gives the choice effect |
| `ignored` | nothing in the text answers the act | does not; counts against the strict majority |
| `negated` | the text contradicts or undoes the act, as if it had not happened | a hard finding in that sample |

- **How labels are made.** `--label-sheet` writes a blind sheet: the plays in an order shuffled by a
  salt that is not kept, under ids `p01`, `p02` and so on, with no side, kind, sample, run id or
  digest, and each choice's text exactly as the choice check reads it (the replies after the
  player's line on the choice's turn, then its window). A separate key binds each id to its run by
  a digest (a capture's evidence digest, a V2 run's canonical-JSON digest), and the sheet and its
  key share one random sheet id, so a key cannot be paired with another sheet. The reader is given
  the sheet only: the comparison directory and its report show the captures' digests and the runs'
  text, and the key is written beside the sheet, so a reader who can open them could tell the sides
  apart. The rescore refuses a filled sheet whose key is not its own or not this comparison's, that
  misses, repeats or adds a play, leaves any choice unlabelled, or whose acts or
  any choice's text differ from what the current scenario reads from that run. So a label cannot
  move to another run, and a change to a choice's turn, window or act needs a new sheet.
- **In the report.** The per-sample tables still print the run's own recorded negations and the
  phrase-marker reading for information; under labels those rows are named "choices negated in the
  run's own record", "choices unlabelled in the run's own record" and "choices unrealized by phrase
  markers (not used)". A saved comparison with the same V2 run twice is refused, since it would
  count one play twice.
- **What stays the same.** Every hard check still reads the text: leaks, attribution, continuity,
  speakers and the negation markers ("never offered a coin", "coin vanishes", "refuses to sing"),
  any one of which fails its sample. The strict-majority rule and the voice, cost and latency rules
  are unchanged. Labels decide both sides of a comparison or neither; one side cannot be labelled
  and the other read by markers.
- **Without labels** a comparison is still decided by the phrase markers, and its report says so
  ("choices: decided by phrase markers in the model text"). With labels it says the labels decided
  and names the file.
- **Limits.** A label is one reader's judgement. The report cannot check that the reader saw only
  the sheet. The sheet hides which side a play is, but the replies' own style (0.2.1's second speaker often continues the first speaker's actions) can give
  a side away. The redefinition was made after the failing run was read; it was decided on what
  the choice should mean, not on whether 068 then passes. The scenario's scripted dry-run reply has
  Oda comply, which her card contradicts; that reply is a fixture for the dry run and is not used
  in a billed comparison.

