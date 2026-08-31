# Contributing to Grimoire

Grimoire is a FastAPI backend (`backend/`, pytest) plus a Vite/React frontend
(`frontend/`, vitest), packaged for Android as a Kotlin/WebView shell
(`android/`). [`README.md`](README.md) covers installing and running it as a
user; this page covers changing it.

Its companions, which it points at rather than repeats:

- [`CLAUDE.md`](CLAUDE.md) — the project conventions themselves (store layout,
  the frontend list/detail page pattern, the architecture guards, the Android
  rules). **It is the source of truth for conventions**; this page tells you
  how to run and satisfy them.
- [`docs/store-guarantees.md`](docs/store-guarantees.md) — what the store
  promises about atomicity and concurrency, and what it deliberately does not.
  Read it before writing anything that mutates campaign-scoped state.
- [`AGENTS.md`](AGENTS.md) — the entry point for a coding agent. Worth a look
  even if you are not one: it is the shortest list of what will fail on you.

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
installs the frontend packages, and drops a desktop launcher. `scripts/unix/run.sh`
(or `scripts\windows\run.ps1`) then starts backend on **8173** and vite on
**5173** in the current terminal.

Requirements: **Python 3.11+** (CI tests 3.11 and 3.14), **Node 18+** (CI uses
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
| `make check-py` | `pytest backend -q` under coverage, floored at `COV_FLOOR` | backend (py3.11, py3.14) |
| `make check-web` | `npm run typecheck && npm run test:coverage` in `frontend/` | frontend |
| `make check-eslint` | eslint, against `lint-baselines/eslint.json` | eslint |
| `make check-templates` | `scripts/verify_templates.py` — builders and templates agree byte-for-byte | templates |
| `make check-pydantic1` | the same suite again, in a throwaway venv resolved to what the APK ships | pydantic1 |
| `make check-apk` | builds `frontend/dist` and then the debug APK | apk |

Both frontend targets take `frontend-deps` — the `npm ci` — as a prerequisite
rather than running it themselves, so `make check` installs the frontend once
and `make -j` cannot put two installs in one `node_modules` at the same time.

`make check` runs every target in that table except **`check-apk`**, which is
excluded deliberately: it needs a per-machine `make android-bootstrap` first,
so folding it in would break `make check` on any un-bootstrapped machine. CI
runs it as its own job. (The `check:` line in the `Makefile` is the list that
counts — `test_docs_guard.py` fails if a target here goes unmentioned, but no
test can tell you a sentence about *how many* there are went stale, which is
why there is no number in this one.)

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
   comments say why, and `CLAUDE.md` records the one you must not change.

For a single test while iterating:

```bash
cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_scenes.py -q
cd frontend && npx vitest run src/components/GreetingEditor.test.tsx
```

---

## The architecture guards

Several rules are enforced by tests that parse the package's own ASTs. They
exist because each one names a bug that actually shipped, and a paragraph
cannot fail a test run.

| Guard test | Rule | Marker |
|---|---|---|
| `test_atomic_guard.py` | every store write goes through `store.atomic` | `# atomic-ok:` |
| `test_overlay_guard.py` | campaign reads of inheritable records go through `store.overlay` | `# overlay-ok:` |
| `test_pydantic_guard.py` | pydantic usage stays v1/v2-agnostic | `# pydantic-ok:` |
| `test_paths_guard.py` | filesystem access goes through the resolvers | `# paths-ok:` |
| `test_lock_order_guard.py` | only `locks.hold_all` holds more than one campaign lock | `# lock-order-ok:` |
| `test_lock_domain_guard.py` | campaign-scoped mutators take `locks.campaign_lock(cid)` | `# lock-domain-ok:` |
| `test_usage_guard.py` | every generation route meters what it spends | `# usage-ok:` |
| `test_routing_guard.py` | every generation names a task, and every task belongs to a route in `store/routing.py` | `# routing-ok:` |
| `test_import_guard.py` | module-scope imports, acyclic graph, submodule binding inside `store/` | `# import-ok:` |
| `test_path_guard_store.py` | the store never joins a caller-supplied id onto a path unchecked | — |
| `test_absorb_writer_guard.py` | the absorb pass never edits or deletes a fact — only the user may | — |
| `test_docs_guard.py` | this page, `AGENTS.md` and `docs/store-guarantees.md` still match the code | — |
| `test_ratchet_guard.py` | the lint baselines are canonical, positive, and name files that exist | — |

Clearing a genuinely safe call takes `# <marker>: <reason>`. **A marker with no
reason fails, deliberately**, each guard caps how many exemptions exist, and —
given how often this caught a stated reason that was not actually true — the
reason has to hold up, not merely be present. Marker parsing is shared in
`backend/tests/guard_markers.py`; a marker inside a string literal does not
count.

Each guard's docstring states its own reach, including where it stops seeing
things — read the one that failed you before arguing with it. Two of them fail
on code that looks perfectly ordinary, so [`CLAUDE.md`](CLAUDE.md) explains
both at length: classifying a new campaign-scoped mutator in `store/locks.py`,
and how a cross-package import inside `store/` must bind a submodule rather
than a name.

---

## Writing tests

The conventions — store isolation, the shared LLM fakes, the frozen-campaign
fixture, what a record-list page's tests must cover — are in
[`CLAUDE.md`](CLAUDE.md) under **Working notes** and **the list/detail page
pattern**. Read them there; what follows is only the part that is a command
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
regenerated; `CLAUDE.md` and that directory's README explain why.

---

## Prompts and templates

The rule and the reasoning live in [`CLAUDE.md`](CLAUDE.md); the layout is in
[`templates/README.md`](templates/README.md). Operationally: after editing
anything under `templates/`, run `make check`. It covers both harnesses —
`scripts/verify_templates.py` (via `make check-templates`) and the offline
`evals/run.py`, which runs inside `pytest backend`.

`evals/run.py --live` is separate, makes real model calls, and is opt-in. See
[`evals/README.md`](evals/README.md).

---

## Android constraints

Three rules keep the two platforms in lockstep, and breaking one of them is
invisible on the desktop build. They are stated in [`CLAUDE.md`](CLAUDE.md)
(**Android**) and in full in
[`docs/android-architecture.md`](docs/android-architecture.md): use the
filesystem resolvers, keep base dependencies installable on Android, and keep
pydantic usage version-agnostic.

What is worth knowing *here* is which command proves which:

| Rule | Proved by |
|---|---|
| version-agnostic pydantic, across the whole suite | `make check-pydantic1` |
| the APK still builds with your change in it | `make check-apk` (needs `make android-bootstrap` first) |
| the resolvers really are used | `test_paths_guard.py`, in `make check-py` |

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
defers in turn to [`.claude/skills/verify/SKILL.md`](.claude/skills/verify/SKILL.md)
for the harness itself.
Neither the ports nor the isolation check are repeated here, because a
safety procedure kept in three places is a safety procedure with two stale
copies.

---

## Review gates

Work that goes through the spec → plan → implementation pipeline has four
mandatory Codex checkpoints. **[`CLAUDE.md`](CLAUDE.md) defines them** — which
command runs at which stage, and what the final one is looking for — under
*Development workflow: Codex review gates*. Do not restate that list anywhere;
follow it there.

Two things worth adding for a human contributor: specs live in
`docs/superpowers/specs/` and plans in `docs/superpowers/plans/`, and a gate
you could not run (no Codex access, an exhausted quota) is a thing to say in
the pull request rather than to pass over silently.

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
