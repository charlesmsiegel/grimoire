# Contributing to Grimoire

Grimoire is a FastAPI backend (`backend/`, pytest) plus a Vite/React frontend
(`frontend/`, vitest), packaged for Android as a Kotlin/WebView shell
(`android/`). [`README.md`](README.md) covers installing and running it as a
user; this page covers changing it.

Two documents sit beside this one and are not repeated here:

- [`CLAUDE.md`](CLAUDE.md) — the project conventions themselves (store layout,
  the frontend list/detail page pattern, the architecture guards, the Android
  rules). **It is the source of truth for conventions**; this page tells you
  how to run and satisfy them.
- [`docs/store-guarantees.md`](docs/store-guarantees.md) — what the store
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
| `make check-lint` | `ruff check .` | lint |
| `make check-py` | `pytest backend -q` | backend (py3.11, py3.14) |
| `make check-web` | `npm ci && npm run typecheck && npm run test:coverage` in `frontend/` | frontend |
| `make check-templates` | `scripts/verify_templates.py` — builders and templates agree byte-for-byte | templates |
| `make check-pydantic1` | the whole suite against the **Android** dependency set (pydantic 1.10, no `desktop` extra) in a throwaway venv | pydantic1 |
| `make check-apk` | builds `frontend/dist` and then the debug APK | apk |

`make check` runs the first five. **`check-apk` is deliberately excluded**: it
needs a per-machine `make android-bootstrap` first, so folding it in would
break `make check` on any un-bootstrapped machine. CI runs it as its own job.

### Four things that will bite you

1. **In a worktree, pass `PY` explicitly.** The default points at
   `backend/.venv`, which only the main checkout has:

   ```bash
   make check-py PY=/path/to/main/checkout/backend/.venv/bin/python
   ```

   (`.../Scripts/python.exe` on Windows.) CI passes `PY=python`, where the deps
   really are global to the runner.

2. **`check-py` sets `PYTHONPATH` to *this* tree's `backend/src` on purpose.**
   `backend/.venv` holds an editable install whose `.pth` points at whichever
   checkout created it, so a bare `pytest` inside a worktree silently tests the
   *other* tree's sources — and passes while testing nothing you changed.

3. **Run vitest *from* `frontend/`.** `npx --prefix frontend vitest run`
   executes from the repo root, which skips `frontend/vitest.config.ts`,
   disables `globals`, and fails every mock-based test. `make check-web` does
   this right.

4. **`check-web` runs `npm run test:coverage`, not `npm test`.** Same suite,
   same pass/fail, plus it writes `frontend/coverage/lcov.info` — gitignored,
   uploaded by CI as the `frontend-coverage` artifact, and the file external
   readers discover by name. Coverage config lives in `frontend/vite.config.ts`
   under `test.coverage`; the **istanbul** provider and `all: true` are
   load-bearing there and the comments say why. **Do not switch to the v8
   provider**, which reports a file no test imports as 100% covered rather
   than 0%. `npm test` still runs the suite bare when you only want pass/fail.

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
| `test_import_guard.py` | module-scope imports, acyclic graph, submodule binding inside `store/` | `# import-ok:` |
| `test_path_guard_store.py` | the store never joins a caller-supplied id onto a path unchecked | — |

Clearing a genuinely safe call takes `# <marker>: <reason>`. **A marker with no
reason fails, deliberately**, each guard caps how many exemptions exist, and —
given how often this caught a stated reason that was not actually true — the
reason has to hold up, not merely be present. Marker parsing is shared in
`backend/tests/guard_markers.py`; a marker inside a string literal does not
count.

Two rules worth knowing before you trip them:

- **Adding a module that mutates campaign-scoped state?** Classify it in
  `store/locks.py` (`DOMAIN_MODULES` / `OUTSIDE_DOMAIN` / `UNREVIEWED`) or
  `test_lock_domain_guard.py` fails naming your module. `UNREVIEWED` is a
  frozen backlog that may only shrink — it is not open for new entries.
- **Inside `store/`, a cross-package import binds a submodule and keeps it as a
  module object** — `from ..campaigns import read` then `read.world_refs()`,
  never `from ..campaigns.read import world_refs`. Binding a name off a package
  that is still initializing raises at import time, and binding a function *by
  value* off a sibling's leaf module makes the caller cache it, so a test
  patching `campaigns.read.world_refs` silently stops intercepting and goes
  green while injecting nothing.

---

## Writing tests

- **Isolate the store**: `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`. Never
  let a test touch a real library.
- **Fake the LLM with `backend/tests/llm_fakes.py`**, injected at
  `app.dependency_overrides[routes.get_llm]` — never write another inline fake.
  Scripted turns (`FakeOpenRouter`, `FakeOpenRouterComplete`, …) answer by call
  order; a *cassette* (`from_cassette("campaign_flow")`) answers by what the
  request looks like, replaying hand-authored bodies from
  `backend/tests/fixtures/llm/`. A request matching no cassette entry raises
  rather than defaulting, and `test_llm_fakes.py` renders every real prompt
  template to prove the matchers still match — reword a system prompt and it
  fails *there* rather than silently everywhere else.

  These bodies are canned, not recorded: they prove the code handles a reply,
  never that a model would send one. `evals/run.py --live` is the only thing
  that answers that.
- **The frozen campaign** (`backend/tests/fixtures/frozen_campaign/`) is a
  whole store tree checked in as a fixture — the only store in the repo that
  today's code did not write, which is the only way to catch a change that
  breaks reading what an *older* version wrote. `home/` is **never
  regenerated**; its value is being old. `snapshot.json` *is* regenerated
  deliberately, when a template or render moved on purpose and the new text was
  reviewed:

  ```bash
  cd backend && PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep
  ```

  Commit it with the change that moved it. See that directory's README.
- **Frontend record-list pages** follow the two-pane view/edit pattern in
  `CLAUDE.md`, and their tests must cover: clicking a row shows the read-only
  view (rendered body, no `textarea`), **Edit** reveals the form, `+ New` opens
  the form directly. `GreetingEditor.test.tsx` and `EntityEditor.test.tsx` are
  the canonical pair.

---

## Prompts and templates

Every prompt grimoire sends lives as a Jinja2 template under
[`templates/`](templates/README.md); nothing prompt-shaped is hard-coded. After
editing anything there, `make check` covers both harnesses that guard prompts:

- `scripts/verify_templates.py` — builders and templates agree byte-for-byte.
- `evals/run.py`, which runs inside `pytest backend`. Offline it proves the
  *instructions* are still in the assembled prompt: it renders the budget,
  reply-format and roll-protocol sections and requires each verbatim, plus
  every key of the absorb contract and owned-lore containment, and it checks
  that the graders still score recorded output correctly.

Whether the model still *follows* a reworded instruction is a question only
`evals/run.py --live` answers. That makes real LLM calls and is opt-in.

---

## Android constraints

The Android app packages `backend/src` and the built frontend **verbatim**
(Chaquopy + APK assets). Never copy grimoire code into `android/`. Three rules
keep the platforms in lockstep — the full version is in
[`docs/android-architecture.md`](docs/android-architecture.md):

1. Backend code must not assume a repo checkout layout or a desktop `~`:
   filesystem access goes through `store.paths`, `prompts.templates_dir()`
   (`GRIMOIRE_TEMPLATES`) and `main.dist_dir()` (`GRIMOIRE_DIST`).
2. `pyproject.toml` **base** deps must stay Android-installable (pure Python or
   Chaquopy-wheel'd). Compiled desktop-only deps go in the `desktop` extra, and
   the pip block in `android/app/build.gradle.kts` mirrors the base list.
3. pydantic usage stays v1/v2-agnostic: plain `BaseModel` fields only, dump via
   `routes.common._dump` — no `model_dump()`, `Field`, validators or
   `ConfigDict`.

`make check-pydantic1` is what proves (3) for the whole suite. Building:
`make android-bootstrap` once per machine, then `make apk`.

---

## Screenshots

Documentation screenshots go in `docs/screenshots/` and **must be captured
against an isolated store**, never a real library — see [the privacy
rule](#before-you-start-the-privacy-rule).

`.claude/skills/verify/SKILL.md` describes the harness: point `GRIMOIRE_HOME`
at a scratch directory, patch `grimoire.openrouter.API_URL` at a local mock
that replays `backend/tests/fixtures/llm/campaign_flow.json`, run the backend
and vite on non-default ports (8199 / 5199 — never 8173 / 5173, which is where
someone's real instance lives), and seed the store through the API using
placeholder names. Confirm isolation before capturing anything:
`curl http://127.0.0.1:8199/api/worlds` must return `[]` on first run.

---

## Review gates

The spec → plan → implementation pipeline has mandatory Codex checkpoints. Do
not advance to the next stage until the gate passes and its findings are
resolved:

| Stage | Gate |
|---|---|
| spec → planning | `/codex:adversarial-review` against the spec |
| plan → implementation | `/codex:adversarial-review` against the plan |
| implementation → done | `/codex:review` against the diff |
| done → actually done | `/codex:adversarial-review` against the diff *and* the originating spec, asking specifically whether the changes implement the spec |

That last one targets gaps, drift and quietly-dropped requirements, not style.
If a gate surfaces findings, address them — or explicitly note why not — before
moving on. Don't skip a gate because a change feels small; ask first if you
think one should be skipped.

Specs live in `docs/superpowers/specs/`, plans in `docs/superpowers/plans/`.

---

## Submitting a change

1. Branch from `main`.
2. Keep the diff to one concern. The commit message should say *why*, not
   restate the diff — the surrounding history is the model to match.
3. `make check` passes locally.
4. Open a PR against `main`. CI runs the same six targets; a red job reproduces
   with the one command in [the table above](#the-gate-make-check).

And once more, because it is the one mistake with no cheap fix: **no real
world, campaign or character names anywhere in the diff.**
