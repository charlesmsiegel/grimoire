# Coding-skills calibration (069, partial)

Two offline-safe tools: deterministic specialist calibration (below) and an
[independent judge panel](#independent-judge-panel) whose two live captures
did not establish the proof ([2026-11-20](#live-capture-2026-11-20), [2026-11-22](#live-capture-2026-11-22)).

The offline `grimoire.calibration` command runs two deterministic specialists
from an explicit, clean checkout of `charlesmsiegel/coding-skills` pinned to
`74f40dbd4bc301e276702b2854478f0e2eb2ac6a`. It does not install tools, call
providers, write application state, or enable a blocking grade.

```sh
python -m grimoire.calibration /path/to/pinned/coding-skills backend/tests/fixtures/calibration
```

The command emits JSON on stdout. Its inputs are synthetic Python fixture text
and a manifest; no library, campaign or private source is needed. The report
records the revision, Python version, input hashes, parsing evidence, all three
fresh-process trials and diagnostics. A different revision or dirty checkout is
refused. Syntax errors, detector warnings/errors, timeout, malformed output and
missing analysis are unknown, never clean. Cases outside a specialist's scope
are not run or counted. This is an explicitly selected tiny corpus, not a
representative estimate of accuracy on Grimoire.

## What was actually measured

The [recorded report](../backend/tests/fixtures/calibration/result.json) was
produced by the command above on Python 3.12.14. All four cases repeated exactly
across three trials each:

| Specialist | Applicable case | Structural result | Meaning |
|---|---|---|---|
| Unawaited coroutine | Awaited append | Clear | One true negative |
| Unawaited coroutine | Discarded append | Candidate, line 5 | One true positive |
| Unawaited coroutine | Incomplete syntax | Unknown | Excluded from accuracy |
| Overengineering | Required compatibility facade | Thin-wrapper candidate, line 3 | Structurally true positive; **not an actionable defect** |

There were zero structural false positives and zero structural false negatives
in these three scorable cases. This does **not** establish a zero defect
false-positive rate: the wrapper detector describes a real structure and itself
qualifies its removal advice with “unless abstraction is needed.” Removing the
facade would break its declared legacy constructor/method contract. Its
structural flag must not be promoted to a defect or grade penalty automatically.

An independent corpus reviewer executed both async fixtures: awaiting the control
appended the synthetic event; awaiting the discarded-call variant appended
nothing and emitted the unawaited-coroutine warning. Labels were reviewed before
the detector experiment. Detector output remains candidate evidence, separate
from that behavioral confirmation; the report does not manufacture independent
model verdicts. Empty findings are not proof of a complete analysis: syntax
parsing and stderr/exit/shape checks are recorded separately.

## Dependencies and limits

Independent actionable-defect and Naur-theory calibration is **unavailable**.
The pinned project's theory builder consumes three independent judges' results;
it is not a model executor. No pinned model identity, independent three-judge
execution corpus or repeated model verdicts were supplied here. Their state is
unknown, not N/A, perfect, or confirmed. N/A means a specialist does not apply;
unknown means required evidence is missing. No upstream rules or profiles were
changed, and no inaccurate actionable verdict was reproduced or repaired.

Sprint 069 remains incomplete. The judge panel below supplies the harness for an
independently pinned defect/theory panel, parsed-package coverage per judge,
repeated verdicts and adjudicated false positives; its live evidence and a larger
representative corpus are still missing. The blocking quality gate (101) must
remain inactive.

## Independent judge panel

`grimoire.judgepanel` asks three pinned judges every labelled case of
[`fixtures/calibration/panel`](../backend/tests/fixtures/calibration/panel/cases.json)
in at least three fresh trials, through the 013 provider adapter. It is report-only:
`blocking` is always false and nothing reads its output as a grade.

```sh
python -m grimoire.judgepanel plan    --corpus backend/tests/fixtures/calibration/panel --panel panel.json
python -m grimoire.judgepanel capture --corpus ... --panel panel.json --out capture \
    --confirm-billing --spend-cap "5 USD" --max-calls 126
python -m grimoire.judgepanel score   --corpus ... --panel panel.json --capture capture \
    --coding-skills /path/to/pinned/coding-skills
```

`panel.json` is `{"trials": 3, "judges": [{"judge_id", "temperature", "model_config"} × 3]}`,
where `model_config` is the app's model configuration (`provider_id`, `base_url`,
`model`, `key_env`, `max_output_tokens`, `attempts`) with no fallback models. Each
judge's pin (provider, endpoint, model, temperature, output bound and its request field,
attempts and the prompt version) is hashed into the capture; replay refuses a capture made for a
different pin or prompt. An answer served by any model other than the pin or its dated
snapshot is `unknown` (`model_mismatch`); only the attempt that produced the reply can
vouch for it, so a retry that reports no model is `unknown` too. A judge served by more than one model
(counting every attempt, retries included) during a capture (an alias moved to a new snapshot) loses every answer (`model_drift`);
the report lists each judge's served models. The report counts distinct models; using
three different models is recommended for independence but not enforced.

**Blind units.** Corpus ids and file names describe their labels (`discarded`,
`ledger_scrambled`), so a judge sees only a per-run alias such as `unit_3f9c….py`;
a test checks that no corpus id or file name reaches a judge.

**Coverage, not trust.** Every listing is line-numbered and carries one unnumbered
coverage canary in its middle, new per judge, case and trial. An answer that does not
echo it is `unknown` (`coverage`), however confident. So are malformed answers,
`cannot_assess`, a `defect` without findings, findings on lines that do not exist,
a theory verdict naming another unit, refused or
failed calls, and an unparseable source of either task (never sent). Unknown is never
clean and never scored. The canary proves the listing reached the judge and was
read through its middle; it does not prove the reasoning was sound.

**Reading an answer (parser version 2).** An answer is the one JSON object in it whose
`schema` is the task's. Prose before that object is tolerated and counted per judge
(`parsing.text_outside_json`; a fence alone is not counted), but never read: a verdict
stated only in prose is `malformed`. Nothing but a closing fence may follow the object,
because text after it can retract it. Any other mention of the schema, such as a second
object (even a broken one) or the object inside another (even one left unclosed), means the
judge may have changed its mind or is quoting an example, so the answer is `ambiguous`
(`parsing.ambiguous`), never the first or the last. So is an answer whose prose leaves a
bracket open before the object.
The prompts are unchanged, so a capture made under version 1 still replays.

**Theory citations.** A citation that is not `<alias>:<line>` on a listed line supports
nothing, so it is dropped (`parsing.citations_dropped`), never repaired or moved. The
judge's step is kept only if it still stands: a step below 1.0 whose every citation was
dropped makes the verdict `unknown` (`citation`), because the pinned rubric requires
evidence for any lowered step. A step of 1.0 needs no evidence, so a bad citation on it
costs nothing. Evidence that is not a list is `citation` too. Defect findings are not
treated this way: a finding's line is the verdict itself, so a finding on a line that does
not exist stays `malformed`.

**Off-ladder steps (parser version 3).** The pinned rubric refuses a verdict whose step is
not on its ladder (1.0, 0.5, 0.25, 0.0), so one such step leaves the whole trial's panel
unscored. When a theory answer puts any step off the ladder (`step not in LADDER`, the
rubric's own test), the harness asks that judge once more, in the same conversation. The
re-ask holds the first prompt, the judge's own answer, and a fixed message that names the
off-ladder dimensions and restates the ladder, never which rung to choose. The re-asked
answer is read on its own, in full, and stands as it is. It is never asked again: a step
still off the ladder is `unknown` (`off_ladder`), and so is any off-ladder step whose re-ask
was not captured. A step is never rounded onto the ladder, because that would be the harness
choosing the judge's grade. Defect answers have no steps and are never re-asked. The report
counts both per judge: `parsing.reasked` and `parsing.off_ladder` (still off after any
re-ask). Each re-ask is a billed call, written to its own capture record
(`<trial>.reask.json`). Its digest covers the re-ask message, so a capture made with other
wording is refused as stale. The plan counts every theory answer as possibly re-asked:
`max_calls` is 126 for the default corpus, and `capture` refuses a `--max-calls` below it.

Only an answer whose every other check passed is re-asked: the unit, the citations and the
coverage canary. An answer that also fails one of those keeps that reason (`coverage`,
`citation`), is never re-asked, and still counts in `parsing.off_ladder`. So a re-ask cures
one fault, a step between rungs, and never gives an unread or ungrounded answer a second
draw. A re-ask that brings back no readable answer (refused, interrupted, or served by
another model) leaves the trial `unknown` and still off the ladder. A re-asked trial keeps
its first answer's parse counts and verdict (`first_answer`), and both answers count in
`parsing`.

The rubric checks more than the ladder: a lowered step needs evidence, a verdict needs at
least three rehearsals, and the theory fields must not be empty. So, when the report is
scored, each re-asked trial's first answer is also put through the pinned rubric, with each
off-ladder step read as a rung (1.0 at or above the top, 0.5 otherwise) for that check only.
If the rubric refuses it for any other fault, the trial is `first_answer_invalid` and its
panel is unscored, whatever the re-ask said. That check never scores anything: the profile
comes from the re-asked answer alone.

A re-asked answer counts as that judge's answer for the trial. The judge's first answer
cannot be scored at all, and the second comes from the same judge in the same fresh
conversation, with no other judge's work and no rung suggested. Its call is measured and
watched for model drift with the trial. It is not a rerun for a better result: the second
answer stands whatever it says. The proof keys are unchanged. Each theory trial in the report
lists the judges whose answer came from a re-ask that was read (`theory.units.<unit>.trials[].reasked`),
and the audit names them.

**Defects.** A judge's verdict on a case is settled only when all trials agree;
otherwise it is `inconsistent` and excluded, and counted. A defect counts as found
only when the flagged lines include a labelled defect line. Per trial, the panel says
what at least two judges said. The report gives per-judge and panel confusion counts,
false-positive and false-negative rates, repeatability and unanimous cases. Panel
defect verdicts are *candidates*. The corpus labels are *confirmed* by
[executed behavioral checks](../backend/tests/test_judgepanel.py), never by a judge:
five real defects (the discarded coroutine, a shared mutable default, an off-by-one
window, late-bound loop callbacks, a swallowed write reported as success) and five
correct cases, including the required compatibility facade and two deliberate
Grimoire patterns (fail-closed `unknown` classification, `type(x) is int` to
exclude `bool`).

**Theory.** Theory verdicts (`theory-verdict/1`) are scored by the pinned
`coding-skills` rubric itself (`theory_rubric.score_panel`, run in an isolated
subprocess from a verified clean checkout, with a fresh bytecode cache so no `.pyc` beside
the source is loaded; ignored files also make a checkout unclean; exemption off, since calibration measures
the judgment). The Grimoire profile adds one rule: a trial's panel is scored only when
all three judges are covered and valid. Each unit reports per-trial scores, their range
and whether the grade repeats; `ledger_cohesive` must score above `ledger_scrambled`,
the same logic with its names and documentation removed.

**Reproduced upstream inaccuracy.** At the pin, `score_panel` awards **100 (A+)** to
three verdicts that claim every dimension holds from judges that never read the
unit; it has no notion of coverage. Every report scores this probe through the same
path as captured trials: with the pinned checkout, `upstream_score` 100.0 and
`profile_score` null (without it, both null). The opt-in test
`test_pinned_upstream_rubric_awards_a_perfect_score_to_unread_verdicts` reproduces
it against a real checkout (`GRIMOIRE_CODING_SKILLS`). The fix lives in the Grimoire
profile, not upstream.

**Proof.** `proof.established` requires the facade not penalized, the planted
defect found, missing analysis unscored, a complete defect panel (no unknown or
inconsistent case), every theory unit scored with `ledger_cohesive` above
`ledger_scrambled` in every trial, and every answer provider-reported. Scripted and replayed test captures meet the first three
and the fourth with the pinned rubric, but are synthetic, so `established` is false.
`established` also needs `captured_for_this_parser`: the capture's manifest must name the
parser version that scores it. A capture taken before a parser change replays and is
re-read, but it shaped that change, so it can never establish the proof; each parser change
needs a fresh capture.

### Spending approval

The default corpus plans **108 calls** (12 sent cases × 3 judges ×
3 trials; the unparseable case is never sent), about 17,000 prompt characters per judge per
trial round, with each reply bounded by the judge's `max_output_tokens`. It also allows at
most **18 re-asks**, one per theory answer if every one put a step off the ladder. Each
re-ask sends the first prompt again with the judge's own answer, up to its
`max_output_tokens`, so `capture` needs `--max-calls 126`. The `plan` command's
`prompt_characters_per_judge_trial` counts the first prompts only.
The operator needs explicitly approved providers and exact models, credentials in
named environment variables (never pasted anywhere), and an approved maximum spend
backed by a provider-enforced limit. `--spend-cap` only records that approval and
`--max-calls` only bounds calls; neither enforces money. A failed or partial capture
can still be billed, and the capture directory keeps every call already made, including
one interrupted mid-stream (`interrupted`, with its booked attempts).

### Live capture (2026-11-20)

One live capture was run under an approved cap of 10 USD: 108 calls, none retried, all
accepted and provider-reported. `proof.established` is **false**, so 069 stays unchecked
and no panel accuracy is claimed.

| Judge | Pinned and served model | Input tokens | Output tokens |
|---|---|---|---|
| `opus` | `claude-opus-5-5` | 20,418 | 14,238 |
| `sonnet` | `claude-sonnet-5-5` | 20,406 | 12,981 |
| `haiku` | `claude-haiku-5-5` | 20,426 | 25,834 |

Every judge had `max_output_tokens` 8000, one attempt, no temperature and three trials.
The calls went to Anthropic's OpenAI-compatible endpoint, which reported no reasoning or
cache token counts. No cost is in the capture. At list prices of 2026-11-03 these counts
come to about 0.55 USD; that is an estimate, not a billing receipt.

Proof keys:

| Key | Result |
|---|---|
| `compatibility_facade_not_penalized` | true |
| `planted_defect_found` | true |
| `missing_analysis_not_perfect` | true |
| `provider_reported_only` | true |
| `defect_panel_complete` | **false** |
| `theory_panel_scored_and_discriminates` | **false** |

**Defects (10 cases).** The panel found 4 of 5 real defects on a labelled line and cleared
all 5 correct cases: no false positive and no false negative among the 9 cases it settled.
The tenth, `late_binding`, is `unknown`, because two judges gave a malformed answer in the
same trial. That one unknown case fails `defect_panel_complete`.

| Judge | Settled | Unknown | Inconsistent | False positives | False negatives |
|---|---|---|---|---|---|
| `opus` | 9 | 1 | 0 | 0 | 0 |
| `haiku` | 9 | 0 | 1 | 0 | 0 |
| `sonnet` | 3 | 7 | 0 | 0 | not measured (no defect case settled) |

All 14 unknown defect answers are `malformed`: 13 from `sonnet` and 1 from `opus`. The
ones inspected put prose before the JSON object, which the prompt forbids and the parser
does not accept. `haiku` flagged the correct `fail_closed` case as a defect in one of three
trials, so it is inconsistent there; the panel still cleared the case.

**Theory.** `ledger_scrambled` was scored in all three trials: 18.75, 32.5 and 30.0, grade F
each time. `ledger_cohesive` was scored in one trial only (80.0, B-). In the other two,
`haiku` cited a line that does not exist, so the trial's panel was not scored. With one
unit unscored, `theory_panel_scored_and_discriminates` is false and discrimination is not
established. The one scored cohesive trial is above every scrambled trial. The
missing-analysis probe behaved as designed: upstream 100.0, profile score withheld.

Still unproven: a complete defect panel, a `sonnet` false-negative rate, theory
discrimination and theory grade repeatability for the cohesive unit. Closing them needs a
new capture with its own approval.

**After this capture.** Both failures were in reading answers, not in what the judges
concluded, so [parser version 2](#independent-judge-panel) reads an answer out of prose
before it and drops a citation of a line the listing lacks. This was decided from this
capture's failures, so re-scoring this capture under version 2 checks the change but
proves nothing: its manifest names no parser version, so `captured_for_this_parser` and
`established` stay false. The proof needs a new capture. Whether
version 2 recovers the two cohesive trials here depends on whether the bad citation was
the only one on a lowered step; the recorded replies are not in the repository.

### Live capture (2026-11-22)

A second capture, approved by CMS on 2026-11-22 under a cap of 10 USD and run once under
parser version 2 with the same panel: 108 calls, none retried, all accepted and
provider-reported, each served by its pinned model. `capture_parser_version` is 2 and
`captured_for_this_parser` is true. `proof.established` is still **false**, on one key, so
069 stays unchecked.

| Judge | Pinned and served model | Input tokens | Output tokens |
|---|---|---|---|
| `opus` | `claude-opus-5-5` | 20,486 | 15,308 |
| `sonnet` | `claude-sonnet-5-5` | 20,474 | 13,165 |
| `haiku` | `claude-haiku-5-5` | 20,453 | 25,054 |

No reasoning or cache token counts were reported and no cost is in the capture. At list
prices of 2026-11-03 these counts come to about 0.58 USD; that is an estimate, not a receipt.

| Key | Result |
|---|---|
| `compatibility_facade_not_penalized` | true |
| `planted_defect_found` | true |
| `missing_analysis_not_perfect` | true |
| `provider_reported_only` | true |
| `captured_for_this_parser` | true |
| `defect_panel_complete` | true |
| `theory_panel_scored_and_discriminates` | **false** |

**Defects (10 cases).** The panel settled every case: 5 of 5 real defects found on a
labelled line and 5 of 5 correct cases cleared. False-positive rate 0.0 and false-negative
rate 0.0, for the panel and for each judge. All three judges agreed on all 10 cases.

| Judge | Repeatable | Unknown | Inconsistent | False positives | False negatives |
|---|---|---|---|---|---|
| `opus` | 10 of 10 | 0 | 0 | 0 | 0 |
| `sonnet` | 10 of 10 | 0 | 0 | 0 | 0 |
| `haiku` | 10 of 10 | 0 | 0 | 0 | 0 |

**Parsing (36 answers per judge).** No judge had most of its answers affected.

| Judge | `text_outside_json` | `ambiguous` | `citations_dropped` |
|---|---|---|---|
| `opus` | 7 | 0 | 0 |
| `sonnet` | 10 | 0 | 0 |
| `haiku` | 0 | 0 | 8 |

**Theory.** `ledger_scrambled` was scored in all three trials at 30.0, grade F each time: a
score range of 0.0 and a repeating grade. `ledger_cohesive` was scored in one trial only
(80.0, B-), so it has no score range and its grade does not repeat. In the other two trials
the pinned rubric refused the panel's verdicts (`invalid_verdict`). In both, the `sonnet`
verdict held a step of 0.75, which is not on the rubric's ladder (0, 0.25, 0.5, 1.0); every
other step in those trials is on it. With one unit unscored,
`theory_panel_scored_and_discriminates` is false. The one scored cohesive trial is above
every scrambled trial. The missing-analysis probe again read upstream 100.0 with the profile
score withheld.

Still unproven: theory discrimination, and a score range and repeating grade for the
cohesive unit. The requirement is unchanged. An off-ladder step is a judge's answer the
rubric will not score, not a reading fault, and parser version 2 did not flag it before the
rubric did. Closing the proof needs another capture with its own approval.

**After this capture.** [Parser version 3](#independent-judge-panel) re-asks a judge once
when a theory step is off the ladder, so an answer like `sonnet`'s two would get one more
chance to be scored instead of voiding its trial. Re-scoring this capture under version 3
changes nothing: it holds no re-asks, so those two answers stay `unknown` (`off_ladder`), and
its manifest names parser version 2, so `captured_for_this_parser` is false.

