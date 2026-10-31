# Measured V0.2.1 cost/quality comparison

> **Status (first slice):** baseline capture pending. `grimoire.story-quality/2` and the opt-in
> 0.2.1 capture harness have landed and are proven by a real-0.2.1 dry run; no live,
> provider-reported capture has been run. See [V0.2.1 baseline](../../evals/V021_BASELINE.md).

**Outcome:** V2 should improve character fidelity without silently increasing ordinary play costs.

## Scope and first proof
Build a reproducible, **explicitly opt-in** comparison harness for the real `0.2.1` tag using *synthetic story inputs* and two separately generated NPC contributions. Capture actual provider-reported input/output/cached token counts, retry/fallback attempts, cost and latency and preserve proof of what was measured. Ship a provider-free replay test and reconcile the story-quality interchange `/2` with Wave 1 inference accounting. Do not call a hand-authored baseline measured.

## Subsequent milestones and moonshot
Matched V2 trials, budget-controlled optional reasoning and long-form voice fidelity regressions.

## Independence and interfaces
Independent evaluation harness; consumes cost flags and RP quality benchmarks.

## Proving scenario
Four characters are present but two respond; V2 does not pay for four separate background decisions.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- A run's usage counts as measured only when built from `TurnUsageLedger.summary()` with `provider_reported_only`; a run file's own `"provenance": "measured"` is self-declared, and the gate report says so (004, 005).

**First-proof additions**
- Reconcile the story-quality format with the inference contract (`/2`): narration, provenance, token fields and optional-call actors, per the [divergence table](../../evals/STORY_QUALITY.md#divergence-from-the-inference-contract).

## Wave 2 measurement and interchange handoff

- **Baseline must come from V0.2.1**, not a hypothetical V2 cost estimate or hand-authored `0.2.1`-shaped imitation. Run the scenario with the actual `0.2.1` implementation and same chosen model/route, response-length budget and speaker turns. Record real billed input/output/cached tokens, attempts, cost source and wall latency **per NPC contribution**, including the narrator if present.
- Live model calls are **never default**, and no credentials/user-private scene text may enter checked-in fixtures, public PR logs or normal CI. CI instead runs on synthetic sample records / de-identified summaries whose provider-origin provenance is explicitly tagged. If live provider measurements could not be obtained, the first-slice status must say **baseline capture pending**; do not falsely declare cost parity.
- Adapt `grimoire.story-quality/2` to the `TurnInferencePlan` and `TurnUsageLedger` contracts: settle whether narration is baseline, flaggable or included in another stage **with the 027 runtime owner**; account for unreported/estimated counts, cached reads/writes, optional stages and per-call actor assignment. Version-bump without silently reinterpreting `/1` recordings.
- Measured usage must be derived from an actual ledger/verified provider receipt, not just a source file declaring `"provenance": "measured"`. Compare voice distinctiveness, knowledge fidelity and player agency alongside monetary/tokens/latency; do not optimize prompt length at the expense of separately generated character voices.
- **Parallelism:** a V0.2.1 measurement harness and versioned eval format can be developed without 013's unfinished V2 provider adapter or 027's runtime. The first **matched V2-vs-V1** live comparison is a later integration gate once 027 can speak.

See [Wave 2 handoffs](../WAVE2_HANDOFFS.md#providervoicequality-boundaries-013--014--015--027).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
