# Coding-skills calibration (069, partial)

Two offline-safe tools: deterministic specialist calibration (below) and an
[independent judge panel](#independent-judge-panel) whose live capture is billed and has not been run.

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
    --confirm-billing --spend-cap "5 USD" --max-calls 108
python -m grimoire.judgepanel score   --corpus ... --panel panel.json --capture capture \
    --coding-skills /path/to/pinned/coding-skills
```

`panel.json` is `{"trials": 3, "judges": [{"judge_id", "temperature", "model_config"} × 3]}`,
where `model_config` is the app's model configuration (`provider_id`, `base_url`,
`model`, `key_env`, `max_output_tokens`, `attempts`) with no fallback models. Each
judge's pin (provider, endpoint, model, temperature, output bound, attempts and the
prompt version) is hashed into the capture; replay refuses a capture made for a
different pin or prompt. An answer served by any model other than the pin or its dated
snapshot is `unknown` (`model_mismatch`). The report counts distinct models; using
three different models is recommended for independence but not enforced.

**Coverage, not trust.** Every listing is line-numbered and carries one unnumbered
coverage canary in its middle, new per judge, case and trial. An answer that does not
echo it is `unknown` (`coverage`), however confident. So are malformed answers,
`cannot_assess`, a `defect` without findings, findings on lines that do not exist,
refused or failed calls, and an unparseable source (never sent). Unknown is never
clean and never scored. The canary proves the listing reached the judge and was
read through its middle; it does not prove the reasoning was sound.

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
subprocess from a verified clean checkout; exemption off, since calibration measures
the judgment). The Grimoire profile adds one rule: a trial's panel is scored only when
all three judges are covered and valid. Each unit reports per-trial scores, their range
and whether the grade repeats; `ledger_cohesive` must score above `ledger_scrambled`,
the same logic with its names and documentation removed.

**Reproduced upstream inaccuracy.** At the pin, `score_panel` awards **100 (A+)** to
three verdicts that claim every dimension holds from judges that never read the
unit; it has no notion of coverage. Every report carries this probe:
`upstream_score` 100.0, `profile_score` null. The opt-in test
`test_pinned_upstream_rubric_awards_a_perfect_score_to_unread_verdicts` reproduces
it against a real checkout (`GRIMOIRE_CODING_SKILLS`). The fix lives in the Grimoire
profile, not upstream.

**Proof.** `proof.established` requires the facade not penalized, the planted
defect found, missing analysis unscored, every theory unit scored, and every
answer provider-reported. Scripted and replayed test captures meet the first three
and the fourth with the pinned rubric, but are synthetic, so `established` is false.

### Spending approval

No live capture has been run, so **no judge accuracy, repeatability or false-positive
rate is claimed**. The default corpus plans **108 calls** (12 sent cases × 3 judges ×
3 trials; the unparseable case is never sent), about 17,000 prompt characters per
judge per trial round, with each reply bounded by the judge's `max_output_tokens`.
The operator needs explicitly approved providers and exact models, credentials in
named environment variables (never pasted anywhere), and an approved maximum spend
backed by a provider-enforced limit. `--spend-cap` only records that approval and
`--max-calls` only bounds calls; neither enforces money. A failed or partial capture
can still be billed, and the capture directory keeps every call already made.
