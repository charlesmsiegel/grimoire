# 01a-S4: Run file and comparison — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Several configurations in one live run, a saved `eval-run` v1 file,
and an offline `--compare` table built from run files alone.

**Architecture:** `--decide-backend` becomes repeatable and each backend is a
config (`c1`, `c2`, …) with an open `axes` dict. `live_all` runs each decide
case once per config and repeat, and each generate case once per repeat listed
under every config. A new `evals/runfile.py` builds, writes, reads and compares
run files; it imports nothing from the store, so `--compare` reads no settings.

**Tech Stack:** Python, argparse, json, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01a-eval-cost-latency-reporting-design.md` §8 (run file, configs, compare), C3, §10, §11 tests 11, 12, 19, 22 and the end-to-end check.

## Global Constraints

- `format: "eval-run"`, `version: 1`; `MAX_REPEAT = 10`; `MAX_DETAIL_CHARS = 200`.
- Errors in the file are `{"kind": ..., "status": ...}` only; no prompt, reply or provider text.
- `--record --repeat N>1` is refused by argparse. One refused backend refuses the run unsent, exit 2.
- `--compare` refuses `--gate` and every other mode flag; an unknown format/version is one sentence, exit 2. A missing cell is blank, never zero.
- `--gate`'s clash list gains `--repeat`, `--out`, `--compare`.
- `.gitignore` gains `evals/out/`; README commands are spelled in both `bin/` and `Scripts\` forms.

## Review Focus

- A run file from a replay run (no rows): cells show pass counts and `-`, no money.
- A run file with an unknown `axes` key or extra top-level keys: read.
- Two files sharing config ids: columns are `<file stem>:<config id>`, never merged.
- A case one file lacks: blank cell, and its totals omit it.
- `--out` to a directory that does not exist yet: created.

---

### Task 1: `evals/runfile.py` and the CLI

**Files:**
- Create: `evals/runfile.py`
- Modify: `evals/run.py`, `evals/runner.py` (`Result.configs`, `Result.repeat`, `Result.error_kind`, `Result.error_status`; `live_all(backends=, repeat=)`), `evals/README.md`, `.gitignore`
- Test: `backend/tests/test_eval_runfile.py`, `backend/tests/test_evals.py`

**Interfaces:**
- Consumes: `costs.fold`, `costs.merge`, `costs.cost_text`, `costs.tokens_text`, `costs.money` (S3); `Result` (S2/S3).
- Produces: `runfile.FORMAT`, `VERSION`, `MAX_DETAIL_CHARS`; `config(id: str, label: str, axes: dict) -> dict`; `build(results, configs, *, run_id: str, started: str) -> dict`; `write(path: Path, doc: dict) -> None`; `read(path: Path) -> dict` (raises `RunFileError`); `compare(docs: list[tuple[str, dict]]) -> str`; `runner.MAX_REPEAT = 10`.

- [ ] **Step 1: Failing tests** — test 11 (two synthetic files, blank cells, `version: 2` → exit 2, `--compare` with `--live` refused); test 12 (repeatable backend with one refused → exit 2 before `live_all`; `--record --repeat 2` refused); test 19 (failed call `{"kind","status"}`, the fake's detail nowhere in the file bytes, a 500-char check detail truncated to 200); test 22 (generate case listed once with `configs: ["c1", "c2"]` and filled in both columns; an unknown `axes` key read); the end-to-end `--live --out` with a fake client then `--compare` of the file against itself.
- [ ] **Step 2: Run** → fail. **Step 3: Implement.** **Step 4: Run** `tests/test_evals.py tests/test_eval_runfile.py tests/test_eval_costs.py` → PASS.
- [ ] **Step 5: README + .gitignore**, then `make check`. Commit `01a-S4: eval run files and --compare`.

## Plan gate (substitute review, 2026-10-10)

Folded into this slice:

- **B2** `build` folds with each result's own `rates` (S3's `Result.rates`).
- **S3** Every (config, case, repeat) runs in its own `isolate()`, so a
  harvest never re-reads an earlier repeat's rows.
- **S4** Each call entry in the file carries `bucket` (its one row folded with
  the run's rates), so `--compare` can show a native item's money without
  reading rates. A structured call's bucket is never drawn on an item cell:
  that cell says `(chunk)`. `costs.merge` adds buckets (it never folds a row),
  sums every numeric key so the lazy counts and coverage tallies carry, and is
  tested for both.
- **M2** `runfile` imports `costs` (and through it `store.usage` for folding);
  `--compare` reads no store and no settings, which is the property C3 needs.
- **M6** `--repeat` is live-only; `--repeat` defaults to None so `--gate
  --repeat 1` clashes; the README states `--repeat` multiplies spend.
- A case that is `partial` or whose ledger was unreadable makes its column's
  totals say `incomplete`.

## Review and final gate (substitute review, 2026-10-10)

One hostile subagent review over the whole 01a diff stood in for both
`/codex:review` and the final spec gate (the Codex CLI is not installed). No
blocking finding; every section 11 test and every C1-C3 guarantee was traced
to a test. Folded: a non-provider exception in one case now fails that case
and the run carries on (its spend and report kept); `runfile.read` refuses a
`version` that is not the integer 1 and a malformed case, and `--compare`
turns any malformed inner shape into one sentence and exit 2; a hand-edited
bucket with a non-number figure is not costed rather than read as zero; the
drain-failure message keeps the exception that caused it; an interrupt keeps
the isolate; a structured held-back chunk has its own no-record test; case
lines name their config and repeat in a multi-config or repeated run; the
`Decision.calls` comment says its order is not `usage`'s. Left as drift, on
purpose: `Result.calls` is one flat list (today's cases make one decide
invocation; 02's play cases add per-invocation lists), and the console
aggregate is lines rather than a columned table.
