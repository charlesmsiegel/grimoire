# 01a. Eval cost, latency and token reporting

**Status:** Draft — spec gate (substitute review) folded in; Codex gate pending.
**Date:** 2026-10-09
**Roadmap:** 01a in `ROADMAP-CHECKLIST.md`. Lane: Decision (01a, 01b → 01c,
01d → 02); it also feeds the retrieval lane (01h, 09, 10, 12).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `01-inference-backend-refactor.md` §31
("Evaluation": measure latency, tokens and dollar cost per backend) and
`02-decision-integration.md` ("record latency/cost/label quality", "do not
double-charge ordinary users merely to collect telemetry"), against the landed
01 (`2026-10-07-inference-backend-refactor-design.md`, slices A–I), whose
`--live` and `--decide-backend` grade correctness only.

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| (no ID) the decide chain: `inference.run_stages`, `inference.stages`, `Decision.usage` | 01 (landed) | per-call rows and the stage that sent each call | Hard |
| (no ID) the meter and ledger: `store.usage.meter`, `record`, `Rates`, `_add` | 01 (landed, #152/#158) | the rows, and the one function that folds them into the three money columns | Hard |
| (no ID) `routes.common.require_inference` / `override_inference` | 01 (landed) | resolving each case in the real store, unchanged | Hard |

## Required by

Rebuilt from the "Dependency edges" list in `ROADMAP-CHECKLIST.md`.

| Contract (provided here) | Consumer | Edge | What the consumer uses it for |
|---|---|---|---|
| 01a-C1, 01a-C3 | 01c | H to switch a task on | the evidence `TaskPolicy.native_first` needs (01c-C1): native and structured on one model, compared on cost, latency and answers |
| 01a-C1, 01a-C3 | 01d | H to enable escalation | escalation rate and per-hop cost and latency, aggregated by `hop` (rows carry `hop: escalation`, 01d-C2b), and per backend for tuning the thresholds (01d-C3) |
| 01a-C1 | 01e | S | cost and latency of `Rank`, a finer `Score` and multi-select against what they replace |
| 01a-C1 | 01f | S | cost of structured generation against prose plus parsing |
| 01a-C1, 01a-C2 | 01h | H for C6 | embedding eval rows (recall at k) metered and reported with the same columns |
| 01a-C3 | 01h | S | comparing embedding spaces and options in one table |
| 01a-C1, 01a-C2, 01a-C3 | 02 | H for every play gate | each play gate states what the gated feature costs per turn. A play case is summed across every task it ran (§8) |
| 01a-C1 | 09 | H for live evals | the long-history eval suite's latency and cost (09-C4) |
| 01a-C1 | 10 | H for live evals | the query-planning eval's cost per turn, the repair hop included |
| 01a-C3 | 10 | S | comparing planning configurations |
| 01a-C1, 01a-C2 | 12 | H | the eval gate before broad use reads a multi-call case's totals |
| 01a-C1 | 13 | S | cost and latency of Decision-chosen NPC actions |

11 cites no 01a contract. 01g reaches 01a only through 01h-C6, and through
12's gate.

## 1. Current state (reconciled against main)

- **A live run sends from a throwaway store.** `evals/run.py:33-46`
  (`temp_home`) points `GRIMOIRE_HOME` at a fresh temp directory per case and
  deletes it afterwards. Each case's resolution is read from the real store
  first (`run.py:72-108`, `runner.resolve_connections` at
  `evals/runner.py:176-225`), and only settings and credentials are read there.
- **A generate case files no row.** `runner.live` calls
  `inference.generate(case.task, ..., resolved=target, stream=False)` with no
  `usage=` (`evals/runner.py:330-332`). `test_usage_guard.py:333-337` names
  `evals.runner` in `UNMETERED_OUTSIDE` for exactly that reason ("a live eval
  runs against a throwaway store, never the library whose ledger it would
  describe"), and `test_the_unmetered_outside_list_is_not_stale`
  (`:350`) caps that list at two.
- **A decide case is metered, then thrown away.** `inference._once`
  (`backend/src/grimoire/inference.py:259-287`) and `_native` (`:489-501`)
  open `store.usage.meter` per structured chunk and per native item.
  `usage.ledger_dir()` is `paths.home() / "usage"`
  (`store/usage.py:241-242`), so during a case the row lands in the temp
  home's ledger and `temp_home` deletes it. `Meter.done` also files error rows
  into that temp home's error store (`store/usage.py:567-582`), which is
  deleted too. The rows come back on `Decision.usage`
  (`inference.py:655`, `:679-681`), but the runner reads only `backend` and
  `errors` (`runner.backend_note`, `evals/runner.py:283-299`).
- **A row does not say which items it covered.** `_structured` knows each
  chunk's positions (`inference.py:370`), and `_native` files one row per
  item. But `run_stages` flattens both into `rows` (`:655`), and `_native`
  drops the `None`s (`:539`), so an item cannot be matched to its row.
  `_Call` (`:185-199`) carries no stage index and no batch positions either.
- **Pricing reads the active home.** `Rates.current()` reads `pricing.json`
  and every provider's model facts through `paths.home()`
  (`store/usage.py:817-822`, `store/pricing.py:204`, `:441`). Inside an
  isolate, both are empty, so a modelled figure cannot be computed there.
- **The report grades correctness only.** `runner.report`
  (`evals/runner.py:384-413`) prints pass/fail, the backend note and the
  failing checks. `Result` (`:51-67`) carries no timing, tokens or rows.
- **Comparison is manual.** The README tells a user to run `--decide-backend
  native` and then `structured` and compare the two printouts by eye
  (`evals/README.md:173-188`). `--decide-backend` takes one value
  (`run.py:130-133`).
- **No embed case exists.** `cases.CASES` has generate and decide cases only.
  `store.inference.embed.embed_sync` meters each call itself
  (`store/inference/embed.py:180-191`), so an embed case added by 01h would be
  metered into the isolate like a decide case, and thrown away the same way.
- **Folding a row is done in one place.** `usage._add` (`store/usage.py:938`)
  is the only function that puts a row into the three money columns, and
  `store/usage_rollup.py:76-83` documents why the rollup reaches into
  `_ZERO`, `_add` and `_rounded` instead of copying them.

## 2. Goal (and what is explicitly not the goal)

Every live eval says what it cost and how long it took, in the ledger's own
vocabulary, without a single eval row reaching the library's ledger:

1. Wall time, prompt and completion tokens, and the three money columns, per
   item and per case, with aggregates per backend and per route (01a-C1).
2. Every call a live case makes is metered by the production meter, and the
   rows stay inside an eval scope that no Costs page, campaign budget or
   rollup ever reads (01a-C2).
3. One table comparing backends and configurations, built from saved run
   files alone (01a-C3).

**Not the goal:** a cost preview before a live run, a stats dashboard for
evals, putting eval spend on the Costs page, changing what any production
ledger row contains, or adding eval rows to any rollup.

## 3. Where eval rows go: the isolate is the eval scope

**Decision: an eval row is filed where the production meter files it, which is
the case's throwaway home. It is harvested into the run's own report before
that home is deleted, and it never reaches the library's ledger.**

The alternatives, and why not:

- **File eval rows in the library's ledger with a `scope: "eval"` marker.**
  Every rollup would then need a filter (`summary`, `budget`,
  `campaign_scenes`, `monthly_campaigns`, `unpriced_models`, the
  `usage_rollup` aggregate and the `/stats` latency buckets). A missing filter
  in one of them is a Costs page that bills a campaign for a model test
  someone ran from a terminal. `usage_rollup.VERSION` would have to move too.
  The cost is a filter in every reader, and the benefit is a number the eval
  report already prints.
- **A second ledger file in the real store (`usage/evals/`).** This is a
  second store with nothing reading it. It would also be the first write a
  live eval makes to the real store beyond the `llm_connections` seeding the
  README already states (`evals/README.md:196-200`).

The case's isolate is already the boundary every other eval promise rests on.
The scope is structural rather than a marker someone has to filter on:

- A row filed during a case lands in `<temp home>/usage/YYYY-MM.jsonl`, which
  nothing in the running app reads.
- **A row may carry a campaign id, and that is safe.** Today's single-call
  cases pass none (`runner.py:334-335`, and §4 keeps generate the same). A
  play case (02) drives real routes over a seeded fixture campaign, and every
  route passes `campaign=cid`. That id names a campaign that exists only in
  the isolate. The guarantee that no eval row counts against a real
  campaign's budget rests on the isolate and the tripwire, not on the rows
  having no campaign.
- **A tripwire, checked before anything touches the home.** `run_live`
  records `paths.home()` before any isolate (`real_home`). `live_all` checks
  `paths.home() != real_home` as its first act inside each `isolate()`,
  before `prepare(case)`. `prepare` runs `case.build()`, which writes worlds
  and campaigns into `paths.home()` (`evals/cases.py:164-297`), so the check
  has to come first. A failed check fails the case unsent and unbuilt ("eval
  isolate is not a throwaway home"). It is re-checked immediately before the
  harvest (§6). `live()` takes `real_home: Path | None = None`, and `None`
  skips the check, so the existing tests that call `runner.live(case, target,
  client=fake)` directly (`backend/tests/test_evals.py:215-604`) are
  unchanged. `live_all` always passes it.

**Eval spend does not appear on the Costs page.** That is a stated
limitation, not an accident: the money was spent on the user's key, and the
provider's invoice will show it. The eval report is where it is shown. Open
question 1 covers the alternative.

## 4. Metering each operation

- **generate.** `runner.live` wraps the call in the production meter:

  ```python
  with store.usage.meter(case.task) as m:
      text = await inference.generate(case.task, ctx["messages"], client=c,
                                      resolved=target, usage=m.usage, stream=False)
  ```

  It passes no campaign and no scene. `evals.runner` leaves
  `UNMETERED_OUTSIDE` (`test_usage_guard.py:333`), and the cap there drops to
  one.
- **decide.** No change: `run_stages` meters per chunk and per native item.
- **embed.** No change: `embed_sync` meters each call. A case whose embed
  space must be read from the real store resolves it before the isolate, as
  `resolve_connections` does for a chat model (01h-C6 owns those cases).
- **A case that makes several calls** (a 01g loop, a 09 retrieval pipeline of
  embed + decide + generate) needs nothing more. §6 harvests every row filed
  in the isolate, whatever operation filed it.

## 5. Which items a decide call covered

A small production change, neutral for every existing caller, so a row can be
read against the items it answered (01b needs the same two `_Call` fields; see
§9):

- `_Call` gains `stage: int = 0` (the position in the chain that
  `run_stages` was handed) and `positions: tuple[int, ...] = ()` (stage
  position → batch index). `run_stages` sets both where it already builds the
  stage's call (`inference.py:653-654`):
  `replace(call, chain=..., retries=..., stage=i, positions=tuple(pending))`.
- `decisions.CallRecord` (a frozen dataclass in `decisions.py`, which imports
  nothing from the package):

  ```python
  @dataclass(frozen=True)
  class CallRecord:
      stage: int                 # index into the chain run_stages was given
      mode: str                  # one of BACKENDS
      items: tuple[int, ...]     # batch indices this call carried
      row: dict | None           # the ledger row Meter.done filed; None if nothing was sent
      error_kind: str = ""       # LLMError.kind of a failed call, else ""
      error_status: int | None = None   # its HTTP status, when it has one
      hop: str = ""              # "" for the chain; 01d sets "escalation" (reserved here)
  ```

  A failed call keeps its kind and status only, never `LLMError.detail`. The
  detail is the provider's own text (`openrouter._http_error`,
  `openrouter.py:98-105`), and CLAUDE.md keeps that out of every persisted
  store. The console report may still print the full error, as it does
  today. `hop` is reserved now because `CallRecord` is a shared structure
  (`ROADMAP-CHECKLIST.md`), so its shape is fixed once. 01a never sets it;
  01d-C2b does.

  **What one record is.** A `CallRecord` is one *metered request*: one
  `_once` call or one native item. A schema-refusal re-send is therefore a
  record of its own. 01b's capture call record is coarser: one `_captured`
  call per chunk with its re-send folded in (`inference.py:381-394`). The two
  are counted differently on purpose, because a row is what costs money and a
  capture is what a reader inspects. 01d counts escalation requests in
  `Decision.calls`.

- `_Answered` gains `calls: tuple[CallRecord, ...]`. `_structured` appends one
  per `_once` call. A schema-refusal re-send is its own call with its own row,
  as it is already metered (`inference.py:289-318`). `_native` appends one per
  item it started. `Decision` gains `calls: tuple[CallRecord, ...] = ()`, in
  the order the calls settled.
- `Decision.usage` stays as it is (the rows alone), so no caller changes.
- **When no item answered**, `run_stages` raises (`inference.py:672`) and no
  `Decision` exists, so there are no `calls` and no `items`. The runner then
  reports the case from its harvested rows alone. The rows are the
  authority for money in every case, and that is enough here. Attaching the
  records to the raised error is left out: no production caller would read
  them.

Nothing reads `calls` in production. The only consumers are the eval runner
and 01d's escalation accounting (01d-C2).

## 6. Harvesting, pricing and timing

**Rates are read once, in the real store**, next to `resolve_connections`:
`rates = store.usage.Rates.current()` before the first isolate. Every eval row
is priced against that one table, so a run has one set of rates, as a rollup
has (`store/usage.py:792-794`). A native decision row stays unmodellable
(`Rates.estimate` → `_modellable`, `:884-896`) with no special case here.

**A case is drained before it is harvested.** A play case (02) drives
routes whose turns schedule detached follow-ups: the rolling summary, the
scene-break check, and the tracker (CLAUDE.md, "A landed turn schedules its
own follow-ups"). Those land after the route's response. If the harvest runs
first, they are missing. If the app shuts down first, they are filed
`aborted`. If one is still running when `temp_home` restores
`GRIMOIRE_HOME` (`evals/run.py:41-46`), it resolves `paths.home()` to the
**real** library and files its row there. The harness contract for any case
that runs through the app:

1. The case's last request has returned.
2. `runs.runs_in_flight(app)` is empty. The harness polls it, with a ceiling
   of `DRAIN_CEILING_S = 120` seconds. That is structural, at least the
   longest single LLM call budget, and will be tuned later. Past the ceiling,
   the case fails with "follow-ups still running", and its isolate is
   **kept, not deleted**. The environment is not restored while anything
   might still write.
3. The app's lifespan exits, which joins its task group and its portal.
4. The tripwire is re-checked.
5. The case is harvested.
6. Only then does `temp_home` restore the environment.

A single-call case (today's cases) has nothing in flight, so steps 2 and 3
are immediate.

**Rows are harvested before the isolate is deleted.** At the end of each case,
still inside `isolate()`, the harvest reads strictly, so an unreadable ledger
is an error rather than an empty one:

```python
rows = [r for r in store.usage._read_rows(run_day, store.usage._today(), strict=True)
        if store.usage._is_call(r)]
```

`usage.calls` cannot be used for this, because it reads with `strict=False`
and turns an unreadable file into zero rows (`store/usage.py:719-727`), which
would print as a free case. `run_day` is the run's start date (UTC), so a run
that crosses midnight keeps its earlier rows. The isolate holds nothing else, so this is every row the
case filed. Each harvested row is copied into the run file (§8) with three
fields added: `scope: "eval"`, `eval_run` (the run id) and `case`. **Those
fields are added to the copy, never to a production row**, so nothing in
`record` changes.

**Folding uses the production function.** Buckets are built with `usage._ZERO`,
`usage._add(bucket, row, rates)` and `usage._rounded`. The harvest also uses
`_read_rows`, `_is_call`, `_today`, `_count` and `_completion_count`. This
reaches into the privates on the same documented reasoning as `usage_rollup`
(`store/usage_rollup.py:76-83`): a private copy of `_add` would be a second
opinion on what a call cost. Every rule comes with it unchanged: the cache
pair stays out of `total_tokens`, a billed price on a subscription provider
stays `cost_usd`, `unpriced_calls`/`unmetered_calls`/`unpriced_native_calls`
are counted, and `estimated_token_calls` is counted.

**Token coverage is tallied beside `_add`, never inside it.** `_add` floors an
absent count to 0 (`_int`, `store/usage.py:961-965`, `:1032-1040`), and no
`_ZERO` field records how many calls reported a count. `unmetered_calls`
covers only *unpriced* rows that lack counts. So a billed row with no counts
would add `0 / 0` and be counted nowhere, and a native row with a prompt count
but no completion count would read as a reported zero. The eval keeps two
tallies of its own per bucket, computed from each row:

- `prompt_counted_calls`: rows where `usage._count(row["prompt_tokens"])` is
  not None;
- `completion_counted_calls`: rows where `usage._completion_count(row)` is not
  None. An embed row's absent completion is counted as present here, by the
  same structural zero `_completion_count` already applies.

The renderer reads them per side. A side whose tally is 0 prints `not
reported`. A side whose tally is between 0 and `calls` prints its sum with
`(N of M counted)`. These tallies live only in the eval's bucket. `_add` and
`_ZERO` are unchanged.

**Time.** Two measures, never added together:

- `wall_ms`: one `time.monotonic()` span per case around the model work alone
  (the `ask` coroutine in `runner.live`, or a play case's requests through to
  the end of the drain). Fixture build, prompt assembly and grading are
  outside it.
- `duration_ms`: per call, from its ledger row (the meter's own span,
  retries included).

Native items run `NATIVE_CONCURRENCY` at a time (`inference.py:76`), so the
sum of their `duration_ms` can exceed the case's `wall_ms`. The report never
presents a sum of call durations as elapsed time.

**Per-hop time.** An escalation hop runs after the whole chain, so the runner
cannot measure its elapsed time from outside `decide()`. 01a reports per
`hop` the **summed `duration_ms` of its calls**, labelled `call time`, never
`wall`. A measured span from the chain settling to the hop settling needs
`decide` to stamp it. That is listed for 01d as a cross-spec item. If 01d
adds it as `Decision.hop_wall_ms`, the runner reports it as `hop wall`.

**Per item.** An item is a decide item, or the whole case for a case that
makes one call. For each decide item the runner records `backend`, `stage`,
its answer reasons (`refused`, `abstained`, `unreadable`, `error` or none),
`distribution: true` when any answer carries one, and `escalated: true` when a
call with `hop: "escalation"` carried it. 01c and 01d read exactly these. The
comparison table shows `escalated n/N` per cell (§8). Cost and tokens are reported per *call*, with the items each call
carried (`CallRecord.items`):

- A native item is one call, so its figures are its own.
- **A structured chunk's figures are never apportioned to its items.** Eight
  items in one call have one price, and dividing it would invent eight
  numbers nobody reported. The item lines name the call that carried them.

## 7. Rendering money: a price nobody reported is never zero

Eval output is console text and goes through `runner.ascii_safe`, so the rules
of `frontend/src/components/cost.tsx` are mirrored in one Python helper,
`evals/costs.py` (`money`, `column`, `bucket_line`), with the same rules:

- Each of the three columns is printed under its own label: `billed $0.0042`,
  `sub-equiv ~$0.0031`, `modelled ~$0.0050`. A column with no calls in it is
  omitted. It is never printed as `$0.00`. The bucket has no billed-call
  count of its own, so `evals/costs.py` derives it as `priced_calls -
  subscription_calls`, as `cost.tsx`'s `billedAny` does (`cost.tsx:105-108`).
- `money` follows `cost.tsx`'s `money` (`:58-66`): two decimals at or above
  $0.01, four decimals at or above $0.0001, else `<$0.0001`. A zero is printed as
  `$0.00` only when a call in that column was really priced at zero (a stated
  free model, or a provider that billed 0). `usage._money` already keeps a
  tiny positive figure from rounding to zero.
- A bucket with `unpriced_calls > 0` says `incomplete: N unpriced` beside its
  figures. `unmetered_calls` is shown as `N had no token counts`. A bucket
  with nothing priced at all prints `cost: not reported` (the `UNPRICED`
  sentence from `cost.tsx:39`).
- Tokens print `in / out`, each side from §6's coverage tallies. An absent
  count is never `0`:
  - a side no call counted prints `not reported`;
  - a partly counted side prints `(N of M counted)`;
  - a bucket where neither side was counted prints `tokens: not reported`;
  - `estimated_token_calls > 0` appends `(N estimated)`.
- **No total of the three columns is printed anywhere.** The Costs page's
  "Estimated total" projection (CLAUDE.md, "Costs") is a monthly graph reading.
  It does not extend to evals.

## 8. Report, run file and comparison

**The console report** keeps today's lines and adds one indented metrics line
per case:

```
  [ok  ] decide-continuity-reconcile.compliant  (backend: native 3, structured 4)
           wall 4.21s  calls 5 (stage 0: native 3/3; stage 1: structured 2)  tokens 8,112 / 1,040  billed $0.0091  modelled ~$0.0012  incomplete: 3 unpriced
```

**A case that runs several tasks** (02's play gates: a speaker pick, a turn
plan, the turn itself and its follow-ups) is summed across all of them. The
case's `bucket` folds every harvested row whatever its task. Under the case
line, a `by task` sub-block shows the same columns per task, so a reader can
see what each part of one played turn cost. Several decide invocations in one
case need nothing more. `Decision.calls` is per invocation, and the runner
keeps one list per invocation.

After the case lines comes an aggregate block keyed by route, backend and
`hop`:

- `route` is `store.routing.route(row["task"]).key`. It is `embed` for a task
  in `routing.EMBED_TASKS`, and the task itself for a task no route claims
  (`route()` returns None, `store/routing.py:223`), so an unexpected row
  never crashes the aggregate.
- `backend` is the row's `decision_mode` for a decide row, and the operation
  name otherwise.
- `hop` is the row's `hop` field: `escalation` for a call 01d's escalation
  sent (01d-C2b), and `-` for a row that has none, which is every call of the
  unchanged chain.

`hop` is read off the copy of the row in the run file. It is not inferred from
the stage, because an escalation hop runs after the whole chain, not as a
later stage of it. 01a adds no `hop` field to any row; 01d adds it.

```
by route / backend / hop    calls  wall      tokens in/out   billed     sub-equiv   modelled   unpriced
continuity / native / -         3  -         2,904 / not reported  $0.0060    -           -          0
continuity / structured / -     2  -         5,208 / 1,040   $0.0031    -           ~$0.0012   0
```

The `wall` column is per case, so it is `-` on route/backend rows that split a
case. A `hop` row with calls adds a `call time` figure: the summed
`duration_ms`, labelled as such (§6).

**`--out PATH`** writes the run file. Its schema is `eval-run`, version 1.

What it holds and leaves out:

- **No prompt or reply in full, and no provider error text.**
- **Check details may quote model output.** Graders quote what the model
  wrote (`evals/graders.py:283`, `:591`) and prompt needles (`:782`). Every
  fixture is placeholder-only (`evals/README.md:459-461`), so this is
  synthetic text. Each `detail` is still truncated to
  `MAX_DETAIL_CHARS = 200` in the file.
- **Errors are kind and status only.** A case's error and a call's error are
  stored as `{"kind": ..., "status": ...}`.
- **The console keeps everything**: full details and full error text, as
  today.

```json
{"format": "eval-run", "version": 1, "run": "<uuid4>", "started": "<UTC ts>",
 "configs": [{"id": "c1", "label": "openrouter / <model> [native]",
              "axes": {"backend": "native", "override": "provider+model"}}],
 "cases": [{"configs": ["c1"], "case": "decide-speaker", "repeat": 0,
            "task": "response-selector", "operation": "decide",
            "passed": true, "checks": [{"name": "...", "ok": true, "detail": ""}],
            "wall_ms": 1830,
            "error": null,
            "items": [{"index": 0, "backend": "native", "stage": 0, "reasons": [],
                       "distribution": true, "escalated": false, "call": 0}],
            "calls": [{"stage": 0, "mode": "native", "items": [0], "hop": "",
                       "row": {"...": "ledger row + scope/eval_run/case"},
                       "error": null}],
            "rows": ["<every harvested row, including embeds and non-decide calls>"],
            "bucket": {"...": "usage._rounded bucket"}}],
 "aggregates": {"by_route_backend_hop": [{"route": "...", "backend": "...", "hop": "",
                                          "bucket": {"...": "bucket + coverage tallies"}}]}}
```

`label` is `<connection kind> / <model> [<backend>]`, which is what
`run.py:106-108` already prints.

**`axes` is open.** It is a `{name: value}` dict that `--compare` prints as
the column header and otherwise treats as opaque. A later flag adds a
configuration by adding an axis, with no version bump:

- 01d: `escalation`;
- 01h: `embed_options`;
- 02: `feature`, `scene_selection` and `decision_selection`;
- 10: `planning`.

A v1 reader ignores keys it does not know. A case that ran once for several
configs (a generate case under a repeated `--decide-backend`) lists every
config it stands for in `configs`. `--compare` shows it in each of those
columns rather than as missing from all but one.

The aggregate is a list of objects rather than a map keyed by joined
strings, so no route or backend name can collide through a `/`. A run file holds the user's provider ids and
model names, so it goes where `--out` says and nowhere by default. The repo
never ships one (open question 3).

**Several configurations in one run:**

- `--decide-backend` becomes repeatable (`action="append"`).
  `--decide-backend native --decide-backend structured` runs each selected
  decide case once per backend, on the same resolution, and a generate case
  once. Each backend is refused or accepted up front by `runner.chain`, as
  today (`run.py:84-97`). One refusal refuses the run unsent.
- `--repeat N` (default 1, capped at `MAX_REPEAT = 10`; a consumer's own
  mode may set its own default, such as 02's play gates at 5) runs each
  (config, case) N times. The cap is structural: the flag is for seeing
  variance, and ten is past what a person reads in one table. A live run is
  paid, so an unbounded flag is a typo away from a large bill. Tune it later.
  `--record` with `--repeat > 1` is refused, because it is ambiguous which
  reply would become the baseline.
- At the end of a multi-config run, the comparison table (below) prints
  after the per-case report.

**`--compare FILE [FILE ...]`** is offline. It reads run files, refuses any
other mode flag (as `--gate` does, `run.py:53-61`; `--gate` in turn adds
`--repeat`, `--out` and `--compare` to its clash list, and `--compare`
refuses `--gate`), refuses a file whose
`format`/`version` it does not know, and prints one table. Rows are
`(case, item)` for decide cases and `case` otherwise. Columns are grouped per
config (`<file stem>:<config id>` with its label):

```
case.item                      | c1 openrouter/<m> [native]      | c2 openrouter/<m> [structured]
decide-speaker.0               | 3/3 ok  1.8s  - / -   $0.0006   | 3/3 ok  2.4s  412 / 18  $0.0009
decide-scene-break.0           | 3/3 ok  2.0s  ...                | 2/3 FAIL decide.answer ...
totals                         | billed $0.0102, 0 unpriced      | billed $0.0151, 1 unpriced (incomplete)
```

Each cell shows the pass count over repeats, the median `wall_ms` across
repeats, `escalated n/N` when any item escalated, and token and money figures
summed across repeats, through `evals/costs.py`. A
cell for a case a config did not run is blank, never `0`. Item cells for a
structured backend show tokens and money as `(chunk)` and point to the
case-level row, by §6's rule. **The table needs nothing but the files.** It
reads no store and no settings, which is what makes it "readable without
real store data" (01a-C3), and its test runs on synthetic run files.

## 9. Contract

**01a-C1. Reporting.** Per-case and per-call wall time, tokens and the three
money columns, never added together, aggregated per route, backend and
`hop`. A play case can be summed across its tasks. An absent price is never
shown as zero. For every live run, `runner.Result` gains `wall_ms:
int`, `rows: tuple[dict, ...]` (harvested, §6), `calls: tuple[CallRecord,
...]` (decide only), `items: tuple[dict, ...]` (decide only) and `bucket:
dict` (a `usage._rounded` bucket over `rows`, every task of the case folded in).
It also gains `by_task: dict[str, dict]`, one bucket per task. `runner.report`
prints the metrics line per case, a `by task` sub-block for a case that ran
more than one task, and the aggregate block keyed by route, backend and `hop`
(§8). The run file's `aggregates` key is `by_route_backend_hop`.
Guarantees:

- The three money columns are never added together, in any output.
- An absent price or count is never printed as zero. Token coverage is
  tallied beside `_add` (§6), and money follows §7.
- A structured chunk's figures are never apportioned to items.
- Wall time and summed call durations are never conflated.
- A row with no `hop` aggregates under `-`, never under `escalation`.

Failure behaviour:

- A harvest that cannot read the isolate's ledger (the strict read raises
  `OSError`) reports `cost: not reported (ledger unreadable)` for that case
  and still grades it. It never reports `calls 0`.
- A case with no `Decision` (no item answered) is reported from its rows
  alone (§5).
- A case whose follow-ups outlast `DRAIN_CEILING_S` fails, and its isolate is
  kept (§6). Metrics never turn a passing case into a failing one, or the
reverse.

**01a-C2. Metering in an eval scope.** Live evals are metered by the
production meter inside a throwaway home. Rows are copied to the run file
stamped `scope: "eval"` and never reach the library's ledger. A tripwire
refuses to run against the real home.

- Every call a live case sends is metered by the production door
  (`store.usage.meter`, directly or through `run_stages`/`embed_sync`).
- Every row lands in the case's throwaway home and in the run file, stamped
  `scope: "eval"`, `eval_run` and `case` on the copy only.
- A row may carry the fixture campaign's id, which exists only in the
  isolate.
- No row reaches the library's ledger, error store or rollups. The guarantee
  rests on four things:
  - the isolate;
  - the tripwire, checked inside each isolate before the fixture is built and
    again before the harvest;
  - the drain (§6), which keeps detached follow-ups from outliving the
    isolate;
  - never restoring `GRIMOIRE_HOME` while a run may still write.
- Rates for modelled figures are read once from the real store before any
  case runs.
- `evals.runner` leaves `UNMETERED_OUTSIDE`.

**01a-C3. Comparison.** `--decide-backend` can be repeated, and `--repeat`,
`--out` (eval-run v1) and an offline `--compare FILE...` are added.

- Repeatable `--decide-backend` and `--repeat N` (≤ `MAX_REPEAT`) in one run.
- `--out` writes an `eval-run` v1 file. Each config carries an open `axes`
  dict, and a later axis needs no version bump. Errors are stored as kind and
  status only.
- `--compare FILE...` prints one table across files and configs, offline,
  from the files alone.
- An unknown format or version is refused with one sentence and exit 2.
- Missing cells are blank, never zero.

**Shared structures** (`ROADMAP-CHECKLIST.md`, "Shared structures"):

- `inference._Call.stage` and `positions` (§5) belong to 01a and 01b, and are
  defined identically in 01b-C1. Whichever plan lands first adds them.
- `decisions.CallRecord` and `Decision.calls` (§5) belong to 01a. 01d uses
  them for its escalation hop. `CallRecord.hop` is reserved here and set by
  01d-C2b. 01a aggregates on the row's `hop` field and marks an item
  `escalated` from it.

## 10. Interaction with repo rules

- **`test_usage_guard.py`:** `evals.runner` is removed from
  `UNMETERED_OUTSIDE`, and the cap in
  `test_the_unmetered_outside_list_is_not_stale` drops from 2 to 1.
  `inference.py`'s new `CallRecord` bookkeeping sits beside meters the guard
  already scans and opens none.
- **`test_routing_guard.py` / `test_operation_guard.py`:** unchanged. The
  runner's `generate` call passes `resolved=` and now `usage=m.usage`.
- **Privacy:** the run file and the report hold no prompt, reply, transcript
  or store content. A live case builds placeholder fixtures in the isolate.
  The real store is read for settings, credentials and rates only. That last
  one is new, so the README's "What a live run reads" section gains "and the
  per-token rates" (`evals/README.md:192-200`). It also says that in the
  README's recommended setup (a throwaway `GRIMOIRE_HOME` holding only the
  chosen provider, `evals/README.md:187-189`), rates come from that store, so
  the run reports modelled figures only if a rate was set there. The run file is not
  committed: `.gitignore` gains `evals/out/`, the suggested `--out` location.
- **Spend:** `--live` is still never run without the user's approval, and
  `--repeat` multiplies spend by N. The README says so next to the flag.
- **Logs:** the isolate's error rows and Debug capture lines are deleted with
  it, as today. Nothing new is logged.
- **Android / pydantic v1:** `evals/` is not packaged (`android/` packages
  `backend/src` only). The `decisions.CallRecord` dataclass is plain
  stdlib.
- **Imports:** `decisions.py` stays import-free of the package.
  `inference.py` already imports `decisions`. The import graph stays acyclic.
- **`test_install_scripts.py`:** every new README command is spelled in both
  the `bin/` and `Scripts\` forms.

## 11. Tests and acceptance

In `backend/tests/test_evals.py` (`FakeLLM`'s `usage=` dict stamps a holder,
`tests/llm_fakes.py:260`):

1. A generate case run through `runner.live` with a fake stamping
   `prompt_tokens`, `completion_tokens` and `cost_usd` yields one harvested row
   with `task == case.task`, no `campaign`, and `bucket["cost_usd"]` equal to
   the stamped cost.
2. A fake reporting no cost and no rates: the metrics line says
   `cost: not reported`, and no `$0.00` appears anywhere in the report. With
   a provider rate (a `Rates` built in-test), the line says `modelled ~$...`,
   and `billed` is absent.
3. A native-decision row with a rate available stays unpriced and is counted
   in `unpriced_native_calls`. It is never modelled.
4. A subscription row with `cost_basis: "equivalent"` lands in `sub-equiv`
   only. A billed row on a subscription-tagged provider lands in `billed`.
5. A mixed chain (native stage 0 failing two items, structured stage 1
   answering them): `Decision.calls` names `stage`, `mode`, `items` and
   `hop == ""` for each call, and a failed call carries `error_kind` and
   `error_status` but no detail, the items are batch indices, and the item lines name stage 1
   for the two.
6. A structured chunk of three items: the three item records share one
   `call` index, and no per-item money figure exists in the run file.
7. The tripwire: with the isolate's `GRIMOIRE_HOME` equal to the real home,
   `live_all` sends nothing (`fake.calls == 0`), builds nothing (the real
   home gains no `campaigns/` or `worlds/` directory), and fails the case with
   the isolate message. `runner.live(..., real_home=None)` skips the check.
8. No row reaches the real home: after a `live_all` run, the real home's
   `usage/` holds no file.
9. `usage._add` is the folding function: the patched one is called (guarding
   against a private copy).
10. A case that crosses UTC midnight (patched `usage._today`) keeps its rows.
11. `--compare` on two synthetic run files prints one table with blank cells
    for cases one file lacks. An unknown `version` exits 2. It refuses
    `--live`.
12. A repeatable `--decide-backend` with one refused backend exits 2 before
    `live_all` runs. `--record --repeat 2` is refused by argparse.
13. `evals/costs.py` unit tests: `money(0.0042) == "$0.0042"`,
    `money(4e-7) == "<$0.0001"`, an all-unpriced bucket prints the
    not-reported sentence, and an absent count prints `not reported`.

14. Aggregation by `hop`: harvested rows with and without `hop:
    "escalation"` fold into separate `<route>/<backend>/<hop>` buckets, and
    a row with no `hop` lands under `-`.
15. A play case: a case whose run files rows under three tasks (a speaker
    pick, a chat turn and a scene-break check) has one `bucket` equal to
    folding all three, and a `by_task` entry per task. The report prints the
    `by task` sub-block.

16. Token coverage (B1):
    - a billed row with no token counts prints `tokens: not reported`, never
      `0 / 0`;
    - a native row with only a prompt count prints `<n> / not reported`;
    - a bucket mixing one counted and one uncounted call prints
      `(1 of 2 counted)`;
    - an embed row with a prompt count reads its completion as counted.
17. An unreadable ledger (a month file made unreadable in the isolate) prints
    `cost: not reported (ledger unreadable)`, not `calls 0`.
18. Drain: a fake follow-up that files its row after the route returns is in
    the harvest. One that outlives `DRAIN_CEILING_S` (patched small) fails
    the case and leaves the isolate in place, and `GRIMOIRE_HOME` is not
    restored while it runs.
19. A failed call in the run file is `{"kind", "status"}`. The fake's error
    detail string appears nowhere in the file bytes, and a check `detail`
    longer than `MAX_DETAIL_CHARS` is truncated.
20. `money(0.0001) == "$0.0001"`, matching `cost.tsx`'s `>=`. The billed call
    count is `priced_calls - subscription_calls`.
21. A row whose task no route claims aggregates under its task name.
22. With `--decide-backend native --decide-backend structured`, a generate
    case is listed once with `configs: ["c1", "c2"]`, and `--compare` fills
    both columns from it. A run file with an unknown `axes` key is read.

In `test_usage_guard.py`: `UNMETERED_OUTSIDE` is `{"scripts.ingest_scene":
...}` and the cap is 1.

In `test_inference_decide*.py`: `Decision.calls` is populated for structured,
native, schema-refusal re-send and mixed chains. A call held back after a
connection-wide stop has no `CallRecord`. A refused-unsent call has `row=None`
and its error.

Acceptance: a live replay with fakes through `run.py --live --out` writes a
valid `eval-run` v1 file, and `--compare` of it against itself prints the
table. `make check` is green.

## 12. Non-goals

- Eval spend on the Costs page, in budgets, or in `/stats` (§3; open question
  1).
- A pre-run cost estimate.
- Time to first token. Live cases join their reply (`stream=False`).
- Statistical tests over repeats. The table shows counts and medians, and a
  consumer (01c, 01d) decides what evidence suffices.
- Grading changes. Pass/fail is exactly as today.
- Capturing eval prompts to a prompt log (01b is about production sites).

## 13. Open questions

1. **Should eval spend also be visible in the library?** One option is
   appending a per-run summary row (not per-call rows) to the real ledger under
   a new `kind: "eval"` that `_is_call` skips. *Recommendation: no, not now.*
   It is a new reader rule in every rollup for a figure the run file already
   holds. Revisit if a user asks why the invoice exceeds the Costs page.
2. **Should `--compare` also accept `--out` from a replay run** (no money,
   pass/fail only)? *Recommendation: yes, cheap.* Replay files carry empty
   `rows`, and their cells print `-` for money and time.
3. **Default `--out` location.** *Recommendation: none.* Without `--out`,
   nothing is written. The README suggests `evals/out/`, which is gitignored.
4. **Should `Decision.calls` replace `Decision.usage`?** *Recommendation: not
   in this spec.* `usage` has production readers. Fold it in later if nothing
   but tests reads it.
5. **Missing edge (none required).** 01h-C6's embed cases must resolve their
   embedding space before the isolate. That is 01h's work, stated here as a
   requirement on it, not a contract this spec needs.

## 14. Review record

**Substitute adversarial review, 2026-10-10**
(`scratchpad/roadmap/reviews/01a.md`): 1 blocking, 6 should-fix, 9 minor.
Each finding was verified against the code before it was folded in.

- **B1 (folded).** `_add` floors absent counts to 0, which is true at
  `store/usage.py:961-965` and `:1032-1040`. Eval-only coverage tallies are
  added beside `_add` (§6, §7, C1, test 16).
- **S1 (folded).** The tripwire moves into `live_all` before `prepare`, and is
  re-checked before the harvest. `real_home` is an optional keyword (§3, test
  7).
- **S2 (folded).** The harvest uses a strict `_read_rows`, so an unreadable
  ledger is reported as such (§6, test 17).
- **S3 (folded).** The drain contract keeps detached follow-ups from
  outliving the isolate. C2 is restated: the guarantee rests on the isolate,
  the tripwire and the drain, and rows may carry the fixture's campaign id
  (§3, §6, C2, test 18).
- **S4 (folded).** Each config carries an open `axes` dict, and a consumer
  mode may set its own `--repeat` default (§8, C3).
- **S5 (folded, partly cross-spec).** `CallRecord.hop` is reserved, items
  carry `escalated`, and the comparison table shows `escalated n/N`. Per-hop
  time is summed call time, labelled as such. A measured hop span needs 01d
  to stamp it (cross-spec item).
- **S6 (folded).** Persisted errors are kind and status only, and check
  details are truncated (§5, §8, test 19).
- **Minors folded:**
  - M1: rows alone when no item answered;
  - M2: what one record is, stated;
  - M3: `>=`;
  - M4: an unrouted task aggregates under its own name;
  - M5: the billed count is derived;
  - M6: the clash lists;
  - M7: the aggregate is a list;
  - M8: `configs` list per case;
  - M9: the README note on where rates come from.
- **Rejected:** none.

**Cross-spec items, for the coordinator (no other spec was edited):**

- 01d: stamp a measured escalation span (`Decision.hop_wall_ms`, or similar)
  if 01d-C3 tuning needs elapsed hop time rather than summed call time. 01d
  also sets `CallRecord.hop = "escalation"` and the row's `hop`.
- 02: the play-gate harness must honour §6's drain. 02's `--repeat` default
  of 5 is that mode's own. 02's "never against a campaign" should read
  "never against a real campaign": rows carry the fixture campaign's id.
- 01d, 01h, 02, 10: declare their configuration axes as `axes` keys (§8)
  rather than through new version bumps.
- 01b: a capture call record and a `CallRecord` count different things (§5),
  and 01b's spec says the same.

