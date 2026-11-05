# Story-quality gate (Sprint 005 first slice; run format `/2` from Sprint 015, `/3` from Sprint 027)

**Status:** offline, deterministic, automatic pass/fail gate. It scores *recorded* runs; it never
calls a model. Blinded human comparison, long-horizon evaluation, NPC agency/initiative
(sprint 039) and LLM judges are not part of this slice.

```bash
python -m grimoire.evals.story_quality SCENARIO.json BASELINE.json CANDIDATE.json
```

Baseline and candidate must have the same run format, `/1`, `/2` or `/3` (mixing them is exit
`2`). For `/2` runs, `--evidence-baseline EVIDENCE` / `--evidence-candidate EVIDENCE` supply a
capture's `evidence.json`; see [Format `/2`](#format-grimoirestory-quality2). Evidence is refused
for `/1` and `/3` runs.

Exit status: `0` pass, `1` gate failed, `2` invalid input or usage. The report goes to stdout and
cites ids and indices only, never contribution or marker text. Options: `--max-cost-increase`
(default `0.10`), `--max-voice-drop` (`0.0`), `--cached-input-weight` (`0.1`),
`--max-latency-increase` (unset: latency reported, not gated) and repeatable `--allow-flag FLAG`.
Any non-default tolerance is printed in the report.

The shipped benchmark lives in `backend/src/grimoire/evals/benchmarks/guild_betrayal/`. Its
baseline is a **synthetic, hand-authored imitation of V0.2.1's call shape** (one RP call per
speaking NPC plus narration), not a measurement of V0.2.1. A pass against a synthetic run prints
"no regression against a synthetic baseline — not a cost/quality certification". The same
baseline in `/2` (narration removed, since `/2` has no narration stage) is `baseline.v2.json`,
and in `/3` (each narration call recorded as that turn's opt-in `director_generation` reply, with
the same tokens) is `baseline.v3.json`.
Measured V0.2.1 capture is [V0.2.1 baseline](V021_BASELINE.md) (sprint 015): its status is
**baseline capture pending**. Ordinary CI never invokes a paid provider, and synthetic fixtures
can never be relabeled measured.

## Divergence from the inference contract

`grimoire.evals` imports only `grimoire.core.inference` from the domain core (it never imports
`grimoire.core` itself). Format `/1` keeps these known differences from
[the inference contract](../INFERENCE.md); format `/2` reconciles them, and `/3` adds the director
stage. They are pinned by `backend/tests/test_wave1_integration.py`:

| Item | `/1` | `core.inference` | `/2` |
|---|---|---|---|
| narration purpose | `narration` is an unflagged RP purpose; the synthetic baseline spends about a third of its weighted tokens on it | narration is the optional, scene-level `director_generation` stage (027, [INFERENCE](../INFERENCE.md)) | no narration call or scene-level contribution; narration prose belongs to the NPC contribution that produced it. **Provisional** in `/2`, which refuses `director_generation`; `/3` records it (below) |
| provenance vocabulary | `synthetic` / `measured` (self-declared; the report says so) | `provider_reported` / `estimated` / `synthetic` per attempt | per-attempt `measurement`; "measured" is derived from the rebuilt ledger plus verified evidence, never declared |
| token fields | integer `input_tokens`, `cached_input_tokens`, `output_tokens`; an unreported count cannot be recorded | `None` means unreported; cache read/write and reasoning tokens separate | the `TokenUsage` fields, `null` = unreported |
| optional calls | `actor_id` only on `npc_generation`; free-form `feature_flag` names | every optional stage is an `InferenceStage`, with an optional actor | every call has a `stage` from a frozen copy of `InferenceStage`, planned under the run's `policy` |

`npc_generation` means the same in all of them. The shipped `/1` benchmark's other purposes and
flags must be `InferenceStage` values.

## Format `grimoire.story-quality/1`

This is an **eval-only interchange format**, not V2 library storage. The authorship (sprint 003)
and call-accounting/flag (sprint 004) contracts have landed; this format does not yet follow them
everywhere (see [Divergence from the inference contract](#divergence-from-the-inference-contract)).
Reconciling them is a `/2` format bump. **Wave 2's Sprint 015 owns that interchange upgrade** using the accepted Wave 1 inference policy and ledger. The upgrade must make missing usage distinguishable from zero and verified receipts distinguishable from self-declared `"measured"`. Preserve the ability to recognize `/1` without silently changing its meaning; narration stage semantics are agreed explicitly with 027 before actual runtime calls. Parsing is strict: unknown or duplicate keys, `NaN`/`Infinity`, booleans as numbers,
dangling references and malformed ids are located errors (`run.turns[0].contributions[1].call_id:
...`).

- **Scenario:** `actors` (`control` is `player` or `director` for the whole scenario; director
  actors need `voice_samples`), secret `facts` with `markers` and per-actor `known_from` turns
  (inclusive, turn-granular), continuity `established_facts` with `contradiction_markers`, scripted
  player `choices` with `effect_markers`, optional `negation_markers` and evidence `window`, the
  `script` of turns and speakers that must speak, and `pc_authorship_markers`.
- **Run:** `provenance` (`synthetic`/`measured`), `conditions` (must include `model` and
  `budget`), `enabled_flags`, `turns` of contributions (`speaker_id`, `author` `player`/`model`,
  `text`, `call_id`), `calls` (turn, purpose, NPC `actor_id` for `npc_generation`, `feature_flag`,
  input/cached-input/output tokens, latency), `choice_outcomes` (`honored`, `complicated`,
  `negated`) and informational `judge_scores`.

Markers match whole phrases after normalisation: NFKC; curly quotes and dash variants folded to
`'`, `"` and `-`; zero-width spaces and word joiners read as spaces; other invisible format
characters (soft hyphen) removed; casefold. `duke` does not match `dukedom`. No word boundary is
required at a marker edge written in a script without spaces between words (CJK ideographs, kana,
Thai, Lao, Khmer, Myanmar, Tibetan), so a CJK marker also matches inside a longer compound. Blank
or punctuation-only model contributions count as silence for script, orphan-call and voice
scoring, but remain model-authored for sovereignty and call-attribution checks.

Optional keys and their defaults: choice `negation_markers` (`[]`) and `window` (`0`); run
`enabled_flags` (`[]`) and `judge_scores` (`{}`); contribution `call_id` and call `actor_id` /
`feature_flag` (`null`). Every default fails closed (a missing `call_id` on a model line is still a
format error). Every call's `turn` must be a turn present in the run, so a truncated recording that
keeps the calls of a dropped turn is invalid input (exit 2), while one that drops both is a gate
failure (`script_incomplete`, exit 1).

In the shipped benchmark Wren hears the secret in Bellamy's turn-3 song (`known_from` turn 3), so
narration may mention it on turn 3 but not on turns 1–2, and never in a sentence naming Oda, who
never learns it.

## Format `grimoire.story-quality/2`

Runs only; scenarios keep format `/1`. A `/2` run is rebuilt turn by turn as a
`TurnInferencePlan` (every scenario actor present, player actors player-controlled) and a
`TurnUsageLedger`. Parsing is as strict as `/1`, and additionally:

- **Run keys:** `source`, `conditions` (must include `model` and `budget`), `policy`
  (`per_turn_call_caps` over optional stages, `max_attempts_per_call`), `turns`, `calls`,
  `choice_outcomes`, optional `judge_scores`. There is no `provenance`, `enabled_flags` or
  `feature_flag`; an optional stage is enabled by having a cap.
- **Calls:** `call_id`, `turn`, optional `phase` (default `main`; phases in order of first
  appearance), `stage`, `actor_id`, `purpose`, `latency_ms` (the whole call's wall time; `null` if
  unreported) and `attempts`. Each attempt has `attempt`, `outcome` (`completed`, `failed`,
  `cancelled`, `not_sent`), `measurement`, optional `route`, `usage` (the `TokenUsage` fields) and
  optional `cost` (`usd`, `basis` `billed`/`equivalent`, provisional for 013). A call the planner
  would refuse or decline (a PC's generation, a second generation for one NPC, a disabled or
  over-cap optional stage) is a located error; so are out-of-order or over-limit attempts.
- **Contributions:** a model contribution must name a `npc_generation` call of the same turn,
  whose actor is its speaker, with a `completed` attempt. A planned call with no attempts and no
  contribution is *unmetered*: reported as missing data, never free.
- **Sources:** `synthetic`; `v0.2.1-capture` (`mode` `dry_run`/`live`, pinned `v1_commit`,
  `harness`, `evidence_sha256`, `profile`, `llm_retries`, `route`); `v2-runtime` (`harness`,
  optional `route` and `evidence_sha256`; no verifier yet, so never "measured"). A synthetic or
  dry-run source may hold only `synthetic` measurements; a live capture only
  `provider_reported`/`estimated`. The 027 runtime records `v2-runtime` runs in `/3`.
- **Choice outcomes** may also be `unlabelled` (judged on marker evidence only, and counted).

The gate runs the `/1` hard checks and cast distinguishability on a `/1`-shaped view, then judges
usage from the ledgers. **Weighted billed tokens** are `(input − cache read) + cache read × weight
+ output` over every attempt (failed ones too); cache writes are billed at input weight. If either
run has an unmetered call or an attempt that left input, output or cache-read counts unreported,
the gate reports `usage_incomplete` instead of a cost verdict, and likewise for gated latency.
Usage status is `measured` (complete, provider-reported, and evidence whose digest and
re-conversion verify; the evidence bundle itself is not authenticated),
`provider-reported (declared by capture, unverified)`, `estimated`, `synthetic` or `incomplete`;
evidence that is supplied but does not verify is an `evidence_unverified` finding.

## Format `grimoire.story-quality/3`

Runs only; scenarios keep format `/1`. `/3` is `/2` with sprint 027's scene-level director reply,
so a shipped `/2` file keeps its meaning and is still read as `/2`. A `/2` file is not a `/3` file
or the reverse (`run.format` is a located error), and the gate refuses to compare one with the
other. Everything in `/2` holds, and additionally:

- **Stages** are every `InferenceStage`, including `director_generation`. Like any optional stage
  it must have a cap in the run's `policy`; a director call the policy does not enable is a
  located error, never an implicit extra. It speaks for the scene (`actor_id` `null`) and at most
  once per turn, whatever the cap.
- **Narration:** a model contribution with `speaker_id` `null` must name its turn's completed
  `director_generation` call, and a director call contributes exactly one line, with no speaker
  (a second line naming it is a located error). An NPC line still names its own `npc_generation`
  call; no other stage produces a contribution.
- **Sources** are `synthetic` or `v2-runtime`. A 0.2.1 capture has no director call under its
  profile and stays `/2`, where its evidence is verified.
- **Gate:** director lines are narration to the hard checks (`/1` purpose `narration`), so PC
  authorship, continuity, attribution, orphan calls and voice-sample copies are checked on them as
  on NPC lines. Narration is cut into sentences and has two checks of its own: a fact may not be
  stated before some player character knows it, nor in a sentence naming an NPC before that NPC
  knows it (`knowledge_leak`); and a sentence that names an actor and has quoted speech or a speech
  verb is `narration_voices_actor`, because the director describes, it does not speak for anyone.
  Opting into `director_generation` is judged from the policy, so a
  candidate that enables it when the baseline did not is `flag_not_allowed` unless allowed. Cast
  distinguishability needs NPC lines; when neither run has one and both have narration (a
  scenario-only chat), voice is not compared. A run with no model line at all still fails closed.
  The report adds a `narration lines` row.

`grimoire.evals.runtime_record.record_runtime_run` writes a `/3` `v2-runtime` run from 027 turns
(`grimoire.runtime.record.recorded_turn` takes a turn's `TurnMeter` and the `TurnOutcome` that
`run_turn` returned for that very meter). Its calls are the
turn plan's calls in plan order, each with every booked attempt, so a scenario-only turn records one
director call and no NPC call, and a two-NPC turn two NPC calls and no director call. A planned
call that never ran has no attempts (unmetered); a paid call whose reply was screened out or
withheld has no contribution (an orphan). Latency is recorded as unreported, because the meter
times attempts and not the whole call. A meter with a failed booking is refused, since its ledger
may be missing an attempt; so is an outcome from another meter (another swipe of the same plan),
an outcome whose call results disagree with the meter's, and a second take of a turn already in
the run (`duplicate_turn`), so no bill is paired with the wrong lines or counted twice.

## Checks

Hard invariants (the candidate fails on any; a baseline that has one is `baseline_invalid`):

| Check | Fails when |
|---|---|
| `knowledge_leak` | an NPC line states a fact before that NPC's `known_from` turn; narration states a fact no player character knows yet; a narration sentence states a fact and names (by scenario name or id) an NPC that does not know it yet |
| `narration_voices_actor` | a narration sentence names an actor (scenario name or id, whole words) and has quoted speech (a double quote, guillemet or corner bracket, or a single quote opening a word) or a speech verb from the closed list `grimoire.evals.text.SPEECH_VERBS`; an NPC speaks only in its own call, a PC only through the player |
| `player_sovereignty` | the model authors a player character's contribution, or any model text contains a PC authorship marker |
| `player_input_altered` | each scripted choice is not played exactly once, verbatim, or other player contributions appear |
| `choice_negated` | the outcome label is `negated`; model text in the window has a negation marker; or no model text after the player's line (and in the window) has an effect marker (`unrealized`). Identical repeated choices each need evidence after their own line |
| `continuity_contradiction` | model text contradicts an established fact from its turn on |
| `script_incomplete` | a scripted turn is missing or a scripted speaker did not speak |
| `separate_generation` | an NPC line is not from its own `npc_generation` call, narration is not from a `narration` call, or one call produced several contributions |
| `orphan_generation` | an `npc_generation`/`narration` call produced nothing (paying for a silent NPC) |
| `unflagged_inference` | any other call is not behind a flag listed in the run's `enabled_flags` |
| `voice_sample_copy` | any model line (NPC or narration) shares 24+ contiguous normalised characters with a voice sample |

Comparisons with the baseline: `incomparable` (any `conditions` value differs),
`flag_not_allowed` (candidate enables a flag the baseline didn't and the gate did not allow),
`no_npc_contributions` (nothing to score fails closed; a baseline with no NPC lines is
`baseline_invalid`, so voice is never left uncompared), `voice_regression` (cast
distinguishability below baseline minus tolerance), `cost_regression` and opt-in
`latency_regression`.

- **Cast distinguishability:** the share of NPC lines whose character-trigram profile is closest to
  their own speaker's voice samples, without using the speaker label. Ties or zero overlap count as
  wrong. It measures whether the cast can be told apart, not fidelity to an author's intent.
- **Weighted billed tokens:** `uncached input + cached input × weight + output`, compared in exact
  fractions. Cache writes are not costed separately yet.
- **Judge scores** are shown as informational and never change the verdict.

## Limitations

Marker matching catches stated phrases, not paraphrase. Narration attribution is by name: a
sentence that gives an actor's speech with only a pronoun ("She mutters, ...") names no actor, so
only the player-character leak rule applies to it. Outcome labels are recorded inputs, used
only next to marker evidence. Within-turn ordering of *learning* is not modelled; `known_from` is
per turn.
