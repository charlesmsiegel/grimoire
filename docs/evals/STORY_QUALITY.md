# Story-quality gate (Sprint 005, first slice)

**Status:** offline, deterministic, automatic pass/fail gate. It scores *recorded* runs; it never
calls a model. Blinded human comparison, long-horizon evaluation, NPC agency/initiative
(sprint 039) and LLM judges are not part of this slice.

```bash
python -m grimoire.evals.story_quality SCENARIO.json BASELINE.json CANDIDATE.json
```

Exit status: `0` pass, `1` gate failed, `2` invalid input or usage. The report goes to stdout and
cites ids and indices only, never contribution or marker text. Options: `--max-cost-increase`
(default `0.10`), `--max-voice-drop` (`0.0`), `--cached-input-weight` (`0.1`),
`--max-latency-increase` (unset: latency reported, not gated) and repeatable `--allow-flag FLAG`.
Any non-default tolerance is printed in the report.

The shipped benchmark lives in `backend/src/grimoire/evals/benchmarks/guild_betrayal/`. Its
baseline is a **synthetic, hand-authored imitation of V0.2.1's call shape** (one RP call per
speaking NPC plus narration), not a measurement of V0.2.1. A pass against a synthetic run prints
"no regression against a synthetic baseline — not a cost/quality certification". Measured V0.2.1
billed-token capture is sprint 015.

## Format `grimoire.story-quality/1`

This is an **eval-only interchange format**, not V2 library storage. It will follow the
authorship (sprint 003) and call-accounting/flag (sprint 004) contracts with a version bump when
those land. Parsing is strict: unknown or duplicate keys, `NaN`/`Infinity`, booleans as numbers,
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

Markers match whole phrases after normalisation: NFKC, curly quotes and dash variants folded to
`'`, `"` and `-`, zero-width spaces and word joiners read as spaces, other invisible format characters (soft
hyphen) removed, casefold.
`duke` does not match `dukedom`; at a marker edge written in a script without spaces between
words (CJK ideographs, kana, Thai, Lao, Khmer, Myanmar, Tibetan), so a CJK marker also
matches inside a longer compound no word boundary is required. Blank or
punctuation-only model contributions count as silence for script, orphan-call and voice
scoring, but remain model-authored for sovereignty and call-attribution checks.

## Checks

Hard invariants (the candidate fails on any; a baseline that has one is `baseline_invalid`):

| Check | Fails when |
|---|---|
| `knowledge_leak` | an NPC line states a fact before that NPC's `known_from` turn; narration states a fact no player character knows yet |
| `player_sovereignty` | the model authors a player character's contribution, or any model text contains a PC authorship marker |
| `player_input_altered` | each scripted choice is not played exactly once, verbatim, or other player contributions appear |
| `choice_negated` | the outcome label is `negated`; model text in the window has a negation marker; or no model text after the player's line (and in the window) has an effect marker (`unrealized`) |
| `continuity_contradiction` | model text contradicts an established fact from its turn on |
| `script_incomplete` | a scripted turn is missing or a scripted speaker did not speak |
| `separate_generation` | an NPC line is not from its own `npc_generation` call, narration is not from a `narration` call, or one call produced several contributions |
| `orphan_generation` | an `npc_generation`/`narration` call produced nothing (paying for a silent NPC) |
| `unflagged_inference` | any other call is not behind a flag listed in the run's `enabled_flags` |
| `voice_sample_copy` | any model line (NPC or narration) shares 24+ contiguous normalised characters with a voice sample |

Comparisons with the baseline: `incomparable` (any `conditions` value differs),
`flag_not_allowed` (candidate enables a flag the baseline didn't and the gate did not allow),
`no_npc_contributions` (nothing to score fails closed), `voice_regression` (cast
distinguishability below baseline minus tolerance), `cost_regression` and opt-in
`latency_regression`.

- **Cast distinguishability:** the share of NPC lines whose character-trigram profile is closest to
  their own speaker's voice samples, without using the speaker label. Ties or zero overlap count as
  wrong. It measures whether the cast can be told apart, not fidelity to an author's intent.
- **Weighted billed tokens:** `uncached input + cached input × weight + output`, compared in exact
  fractions. Cache writes are not costed separately yet.
- **Judge scores** are shown as informational and never change the verdict.

## Limitations

Marker matching catches stated phrases, not paraphrase. Outcome labels are recorded inputs, used
only next to marker evidence. Within-turn ordering of *learning* is not modelled; `known_from` is
per turn.
