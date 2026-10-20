Source: [logs/](../../../../../backend/src/grimoire/store/logs/), [incoming-llm-capture.md](../../../../../docs/incoming-llm-capture.md).

## Observability: one writer, three views

`store/logs/` is the only thing in the app that writes a log line, and
`<home>/logs/YYYY-MM.jsonl` is the only file it writes to. `store/errors.py`
(#156) and `store/metrics.py` (#154) are *readers* over it and over the usage
ledger — there is no second store, and adding one would mean the same failure
written twice by two appends that can disagree.

Four rules that are easy to undo by accident:

- **Never attach a second `logging` handler, and never attach one to the root
  logger.** `logs.install()` (from `create_app`) puts one on the `grimoire`
  logger, and that boundary is a privacy rule, not a preference: `httpx` logs
  full request URLs at DEBUG, an OpenAI-compatible endpoint can carry its key
  in one, and this is a file a user may hand to someone else. What it *does*
  carry is campaign and scene ids, and occasionally a character name — that is
  what makes a failure findable, and Settings says so where sharing it is
  decided, rather than leaving the file to look emptier than it is.
- **`logs.record` may not raise and may not re-enter itself.** It runs beside a
  turn and inside exception handlers, and writing a row resolves the store root
  — which reads the bootstrap pointer through `failsoft`, which *logs*. The
  thread-local latch is what makes that terminate.
- **The level floor stops at `error`** (`logs.FLOORS`). The error store is a
  view over ERROR rows, so a floor above them would be a setting that silently
  switches #156 off — which the size backstop and Settings both promise it
  cannot.
- **Instrument LLM failures at `usage.Meter.done`, not at call sites.** Every
  LLM call in the app runs under a meter, so that is the one place that sees
  all of them fail, and the meter's `task` is already the module axis the error
  store aggregates on. Sixteen call sites each remembering to pass a `kind` is
  how half of them stop appearing in the per-kind counts.

Incoming LLM response capture also uses this writer, at Debug level. Adapters
record decoded SSE lines or SDK messages before selecting content and usage
fields; a native decisions adapter, which has no stream, records the whole
reply as one `decision_body` event before reading any field of it. Long
payloads are split into numbered parts rather than clipped; the
monthly cap still applies. Request bodies, URLs and headers are never passed
to the sink. Response bodies can include private prose and reasoning, so the
Settings page describes that sharing boundary. See
[`docs/incoming-llm-capture.md`](../../../../../docs/incoming-llm-capture.md) for reconstruction
and completeness limits.

The two error counts on `/stats` come from two places **on purpose**: a latency
bucket's `errors` counts calls that failed (the usage ledger, the only source
that also knows how many succeeded, so the only one that can give a rate its
denominator), and the `errors` block counts failures recorded anywhere
(including those that were never a call). Reconciling them into one number
would answer neither question.
