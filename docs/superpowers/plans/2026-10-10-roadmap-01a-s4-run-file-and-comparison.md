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
