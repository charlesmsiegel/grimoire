---
name: test-by-marker
description: >-
  Map a plain-English testing request to the right Grimoire pytest invocation
  using the project's marker matrix (unit / conformance / integration /
  frozen_campaign / golden / scenario / perf), optionally scoped to a module.
  Use when asked to run a particular level or slice of the backend test suite.
disable-model-invocation: true
---

# test-by-marker — run the right slice of Grimoire's backend tests

Grimoire's backend tests are organized by pytest markers. Perf is excluded by
default (`addopts = -m "not perf"`). All commands run from `backend/`.

`$ARGUMENTS` is the natural-language request (e.g. "cross-module tests for
continuity", "the golden path", "everything except scenarios"). Translate it,
show the command, run it, and report the real outcome.

## Marker matrix

| Intent in the request | Marker | Command (from `backend/`) |
|---|---|---|
| unit / default / fast / "just my function" | *(none)* | `uv run pytest` |
| plugin contract / "does the plugin obey its protocol" | `conformance` | `uv run pytest -m conformance` |
| cross-module / "how X talks to Y" | `integration` | `uv run pytest -m integration` |
| regression over frozen snapshot / stability | `frozen_campaign` | `uv run pytest -m frozen_campaign` |
| golden path / LLM fixtures | `golden` | `uv run pytest -m golden` |
| end-to-end / through the HTTP API / user scenario | `scenario` | `uv run pytest -m scenario` |
| perf / benchmark / regression timing (opt-in) | `perf` | `uv run pytest -m perf` |

## Composing

- **Scope to a module/path** — append the path:
  `uv run pytest -m integration tests/continuity`
- **Single test** — `uv run pytest tests/continuity/test_facts.py::test_name`
- **Combine markers** — boolean expressions: `-m "integration or scenario"`,
  `-m "not perf and not golden"`.
- **Everything including perf** — `-m perf` runs only perf; for *all* levels run
  the default plus `uv run pytest -m perf` separately (they're split jobs in CI
  for a reason: perf is timing-sensitive).
- **Parallel** — `pytest-xdist` is available: add `-n auto` for a faster run of
  large slices (avoid for perf — it skews timings).

## How to run

1. Pick the marker(s) and any path scope from the request. If the intent is
   ambiguous between two markers, state your reading in one line and proceed
   with the most specific match.
2. Show the exact command you're about to run.
3. Run it from `backend/`.
4. Report pass/fail with the real summary line. If it fails, surface the
   failing test names and the relevant traceback — never claim green without
   the output.
