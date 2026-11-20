# Coding-skills calibration (069, partial)

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

Sprint 069 remains incomplete. Required next evidence includes an independently
pinned defect/theory panel, parsed-package coverage per judge, repeated verdicts,
adjudicated false positives and defects, and a larger representative corpus.
The blocking quality gate (101) must remain inactive. No paid judge calls are
authorized by this experiment.
