# Contributing to Grimoire

Grimoire is a FastAPI backend (`backend/`, pytest) plus a Vite/React frontend
(`frontend/`, vitest), packaged for Android as a Kotlin/WebView shell
(`android/`). [`README.md`](README.md) covers installing and running it as a
user; this page covers changing it.

Its companions, which it points at rather than repeats:

- [`AGENTS.md`](AGENTS.md) — universal project agreements and task routing.
  The linked skills own detailed conventions; this page owns gate commands.
- [`docs/reference/storage.md`](docs/reference/storage.md) — what the store
  promises about atomicity and concurrency, and what it deliberately does not.
  Read it before writing anything that mutates campaign-scoped state.

---

## Before you start: the privacy rule

**This repo is public, and the data store is not.** Real worldbuilding,
campaign and character content lives under `store.home()` (`~/.grimoire` by
default), outside the repo by design.

That boundary holds in both directions, and the second direction is the one
that gets broken:

- Never commit anything under the data store.
- **Never use a real world / campaign / character name as a "concrete
  example"** — in a design doc, a commit message, a test fixture, or a
  screenshot. Reuse the codebase's placeholder names (Seraphine, Mara,
  Winifred, Realm, Saltmarch) instead of inventing one that might coincidentally
  match something real.

This has gone wrong before: real slugs and character names leaked into
`docs/superpowers/`, were adopted as recurring test fixtures, and needed a full
git-history rewrite to remove. Screenshots are the highest-risk artifact,
because a screenshot of a working install shows a real library by default —
capture against an isolated store (see [Screenshots](#screenshots) below).

---

## Setting up

```bash
git clone https://github.com/charlesmsiegel/grimoire.git
cd grimoire
scripts/unix/install.sh          # macOS / Linux
scripts\windows\install.ps1      # Windows (PowerShell)
```

The installer creates `backend/.venv` and installs `-e ".[dev,desktop]"` into it,
installs the frontend packages, builds the UI bundle, and drops a desktop
launcher. For frontend work launch with `scripts/unix/run.sh --dev` (or
`scripts\windows\run.ps1 -Dev`): uvicorn `--reload` on **8173** and vite with HMR
on **5173**, in the current terminal. Without the flag the launcher is the
player's one — the backend alone, serving `frontend/dist` — so source edits
appear only after its next rebuild, on the following launch.

Requirements: **Python 3.11+** (CI tests 3.11 and 3.14), **Node 24+** (CI uses
24), and an OpenRouter key only if you want to exercise real model calls — the
test suite never makes one.

---

## The gate: `make check`

**Run `make check` before you push.** It runs exactly the targets
`.github/workflows/ci.yml` runs, one job per target, so a CI failure reproduces
locally with the same one-line command.

| Target | What it runs | CI job |
|---|---|---|
| `make check-lint` | ruff, against `lint-baselines/ruff.json` | lint |
| `make check-mypy` | mypy, against `lint-baselines/mypy.json` | mypy |
| `make check-py` | `pytest backend -q` under coverage, floored at `COV_FLOOR`, across `WORKERS` processes | backend (py3.11, py3.14) |
| `make check-web` | `npm run typecheck && npm run test:coverage` in `frontend/`, the coverage floored by `test.coverage.thresholds` in `frontend/vite.config.ts` | frontend |
| `make check-eslint` | eslint, against `lint-baselines/eslint.json` | eslint |
| `make check-templates` | `scripts/verify_templates.py` — builders and templates agree byte-for-byte, and every template is rendered by a check or named in `UNRENDERED` with why | templates |
| `make check-pydantic1` | the same suite again, in a throwaway venv resolved to what the APK ships, across `WORKERS` processes | pydantic1 |
| `make check-apk` | builds `frontend/dist`, runs Android unit tests, then builds the debug APK | apk |

The two backend runs, `check-py` and `check-pydantic1`, spread the suite over
`WORKERS` pytest-xdist processes, four unless told otherwise; coverage is
combined across them before the floor is checked. `make check-py WORKERS=0`
runs it serially, which is the comparison to make when a failure appears only
in parallel — and a failure that does is a test-isolation bug to fix, never a
reason to pin the suite back to one process.

Both frontend targets take `frontend-deps` — the `npm ci` — as a prerequisite
rather than running it themselves, so `make check` installs the frontend once
and `make -j` cannot put two installs in one `node_modules` at the same time.

`make check` runs every target in that table except **`check-apk`**, which is
excluded deliberately: it needs a per-machine `make android-bootstrap` first,
so folding it in would break `make check` on any un-bootstrapped machine. CI
runs it as its own job. The `check:` line in the `Makefile` is the executable
list; keep this table current when that list changes.

### The three ratcheted gates

`check-lint`, `check-mypy` and `check-eslint` do not just run their tool: each
compares what it found to a committed count per (file, rule) under
`lint-baselines/`, and fails **in both directions**.

- **More findings than the baseline** — ordinary failure. Fix them. A file with
  no entry at all is held to every rule, so nothing you add inherits the
  backlog.
- **Fewer findings than the baseline** — also a failure, and the message says
  so. Regenerate and commit the smaller file:

  ```bash
  make baseline
  ```

  Do that in the same commit as the fix that earned it, the way
  `snapshot.json` is committed with the change that moved it.

  `make baseline` only ever writes a *smaller* file. If a count would go up it
  stops and names the pairs, because otherwise regenerating would be the one
  command that turns any red gate green. A rise that is not a regression — a
  rename, a widened rule set, a merge bringing code the gate has not seen —
  goes through `make baseline ACCEPT=1`, which leaves the word in the shell
  history rather than in a silent diff.

`scripts/ratchet.py` opens with why these landed against a baseline instead of
as a report nobody reads. The short version: the tools between them report
about two and a half thousand things today, that is a program of work rather
than one change, and in the meantime none of it may get worse.

### Four things that will bite you

1. **In a worktree, pass `PY` explicitly.** The default points at
   `backend/.venv`, which only the main checkout has:

   ```bash
   make check-py PY=/path/to/main/checkout/backend/.venv/bin/python
   ```

   (`.../Scripts/python.exe` on Windows.) CI passes `PY=python`, where the deps
   really are global to the runner.

2. **Never run a bare `pytest` from inside a worktree.** The venv's editable
   install resolves to whichever checkout created it, so the run tests the
   *other* tree and passes without having seen your change. `check-py` sets
   `PYTHONPATH` ahead of site-packages to prevent that; use the target, or
   export it yourself.

3. **`cd frontend` before running vitest.** Driving it from the repo root with
   `--prefix` looks equivalent and is not: the config file never loads, so
   `globals` is off and every mock-based test fails for a reason that has
   nothing to do with your change. `make check-web` gets this right.

4. **Both suites measure coverage; a bare `pytest` or `npm test` does not.**
   Same tests and same verdict either way, but the targets also write the two
   reports CI uploads (`backend/coverage.xml`, `frontend/coverage/lcov.info`)
   and hold the backend to `COV_FLOOR`, the percentage in the `Makefile`. So a
   change that passes under `npm test` can still fail `make check` — by
   dropping coverage rather than by breaking anything. Raise the floor when the
   number rises; the settings behind each report are load-bearing and their
   comments say why; the [frontend testing guide](.agents/skills/grimoire-development/references/testing/frontend.md)
   explains the coverage contract.

For a single test while iterating:

```bash
cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_scenes.py -q
cd frontend && npx vitest run src/components/GreetingEditor.test.tsx
```

### Faster loops, which are not the gate

Three `make` targets shorten the edit-and-retry loop on the backend. None of
them is a `check-*` target or part of `make check`, each prints `NOT A GATE`
before it starts, and none measures coverage unless asked — so a green one
means "these tests pass", never "the suite passes". Like the gate, each puts
this checkout's `backend/src` first on the import path, so a worktree run
tests the worktree (pass `PY` as above).

| Target | Runs |
|---|---|
| `make test-py-fast TESTS=backend/tests/test_scenes.py` | the paths or node IDs in `TESTS` (default: the whole suite), stopping at the first failure; `ARGS="-k reroll"` is handed to pytest |
| `make test-py-failed` | only what failed last time; with nothing on record it selects nothing and pytest exits 5 (a failure recorded for a test since renamed makes pytest select everything, and it says so — delete `backend/.pytest_cache` to reset) |
| `make test-py-profile TESTS=… [COV=1]` | the selection with a per-node phase profile written to `build/perf/profile.json`, then a summary of where the time went -- printed even when a selected test failed, and the target still fails with it; `COV=1` adds `check-py`'s coverage measurement, without the whole-suite floor a subset would always miss |

The profile behind the last one is the `--phase-profile` option in
`backend/tests/phase_profile.py`; `scripts/profile_report.py` summarizes and
compares profiles, and `scripts/coverage_arcs.py` compares the line and branch
sets two coverage runs executed. Before pushing, `make check` is still the
command.

---

## Test quality

[AGENTS.md](AGENTS.md#test-behavior-not-repository-structure) owns the test-quality
rules. Tests exercise application behavior, useful failure cases and supported
wire/data contracts. Imports, source layout, naming and style belong to static
analysis and review; do not build AST/regex source guards inside pytest or Vitest.

Use state and output assertions for persistence, fault handling, lock exclusion,
usage accounting and compatibility. A source string containing a lock name does
not prove exclusion; a file existing after a save does not prove atomicity.
Tests may inspect generated files and test-owned fixtures when those are the
functional inputs or outputs of the operation being exercised.

---

## Writing tests

The conventions — store isolation, the shared LLM fakes, the frozen-campaign
fixture, what a record-list page's tests must cover — are in
[the testing branch](.agents/skills/grimoire-development/references/testing/index.md)
and [record pages](.agents/skills/grimoire-development/references/frontend/pages.md).
Read the relevant branch; what follows is only the part that is a command
rather than a rule.

Every backend test isolates the store with
`monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`, and every LLM call is served
by `backend/tests/llm_fakes.py` through
`app.dependency_overrides[routes.get_llm]`. Do not hand-roll either.

Regenerating the frozen campaign's `snapshot.json` — deliberately, when a
template or render moved on purpose and you have read the new text:

```bash
cd backend && PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep
```

Commit it alongside the change that moved it. Its sibling `home/` is never
regenerated; the [backend fixture guide](.agents/skills/grimoire-development/references/testing/backend.md)
and that directory's README explain why.

---

## Prompts and templates

The [prompt verification guide](.agents/skills/grimoire-development/references/testing/templates.md)
owns the rationale; the layout is in
[`templates/README.md`](templates/README.md). Operationally: after editing
anything under `templates/`, run `make check`. It covers both harnesses —
`scripts/verify_templates.py` (via `make check-templates`) and the offline
`evals/run.py`, which runs inside `pytest backend`.

`evals/run.py --live` is separate, makes real model calls, and is opt-in. See
[`evals/README.md`](evals/README.md).

---

## Android constraints

Three rules keep the two platforms in lockstep, and breaking one of them is
invisible on the desktop build. The [packaging guide](.agents/skills/grimoire-development/references/android/packaging.md)
routes to their source owners, with the architecture contract in
[`docs/reference/android.md`](docs/reference/android.md): use the
filesystem resolvers, keep base dependencies installable on Android, and keep
pydantic usage version-agnostic.

What is worth knowing *here* is which command proves which:

| Rule | Proved by |
|---|---|
| version-agnostic pydantic, across the whole suite | `make check-pydantic1` |
| the APK still builds with your change in it | `make check-apk` (needs `make android-bootstrap` first) |
| relocated runtime inputs still load | `test_env_overrides.py` and `test_where.py`, in `make check-py` |

Neither of the first two runs as part of `make check-py`, so a change that only
breaks Android passes a backend-only test run.

---

## Screenshots

Documentation screenshots go in `docs/screenshots/` and **must be captured
against an isolated store**, never a real library — see [the privacy
rule](#before-you-start-the-privacy-rule).

The procedure has one owner:
[`docs/screenshots/README.md`](docs/screenshots/README.md), which sits beside
the images and is what the next person to re-capture will actually open. It
defers in turn to [`.agents/skills/verify/SKILL.md`](.agents/skills/verify/SKILL.md)
for the harness itself.
Neither the ports nor the isolation check are repeated here, because a
safety procedure kept in three places is a safety procedure with two stale
copies.

---

## Review gates

Work that goes through the spec → plan → implementation pipeline has four
mandatory Codex checkpoints. **[`AGENTS.md`](AGENTS.md) defines them** — which
command runs at which stage, and what the final one is looking for — under
*Development workflow: Codex review gates*. Do not restate that list anywhere;
follow it there.

Two things worth adding for a human contributor: temporary specs live in
`.superpowers/specs/` and plans in `.superpowers/plans/`, and a gate
you could not run (no Codex access, an exhausted quota) is a thing to say in
the pull request rather than to pass over silently.

## Maintaining documentation

The manual starts at [`docs/README.md`](docs/README.md). Update the page that owns
a changed workflow or constraint, follow its links, and review screenshots
against synthetic fixtures. Keep historical plans, generated reports and review
logs in the ignored working area or Git history.

Application tests and executable tools must not use documentation as input.
Preserve useful examples as fixtures owned by the relevant tests; remove checks
whose only assertion is that prose contains a particular phrase or inventory.
Runtime store Markdown and Jinja prompt templates remain application inputs.

---

## Submitting a change

1. Branch from `main`.
2. Keep the diff to one concern. The commit message should say *why*, not
   restate the diff — the surrounding history is the model to match.
3. `make check` passes locally.
4. Open a PR against `main`. CI runs one job per target; a red job reproduces
   with the one command in [the table above](#the-gate-make-check).

And once more, because it is the one mistake with no cheap fix: **no real
world, campaign or character names anywhere in the diff.**
