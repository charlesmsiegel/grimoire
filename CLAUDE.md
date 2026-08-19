# grimoire — project conventions

FastAPI backend (`backend/`, pytest) + Vite/React frontend (`frontend/`, vitest).
The app and its data live in a markdown/JSON store rooted at `~/.grimoire` by
default. The root is resolved by `store.home()`: `GRIMOIRE_HOME` env var (tests /
overrides) → the user-chosen path recorded in the bootstrap pointer
`~/.grimoire.json` → `~/.grimoire`. The path is editable from the Configuration
page (Storage location); point it at a synced folder to share a library across
devices.

## Privacy: real data — and references to it — never get committed

This repo is public. `~/.grimoire` (wherever `store.home()` points) holds
real, private worldbuilding/campaign/character content, kept out of the repo
structurally. That boundary holds in both directions:

- **Never commit anything under the data store itself.** Worlds, campaigns,
  and calendars all live outside the repo by design — that's the point of
  `store.home()`.
- **Never use a real world/campaign/character name as a "concrete example"**
  in a design doc, commit message, or test fixture, even in passing —
  invented names only. This has happened before (real slugs/character names
  leaked into `docs/superpowers/` and got adopted as recurring test
  fixtures) and required a full git-history rewrite to fix. Reuse the
  codebase's existing placeholder names (e.g. Seraphine, Mara, Winifred,
  Realm, Saltmarch) rather than inventing a new one that might coincidentally
  match something real.
- **Personal/homebrew calendars follow the same split as any other private
  content**: only `gregorian` and `hebrew` (real-world, real holidays) ship
  in `store/calendars/`; anything else — a fictional calendar, a custom
  holiday set — is a plugin loaded from `<GRIMOIRE_HOME>/calendars/` (see
  `store/calendars/plugins.py`), never committed.

## Frontend: the list/detail page pattern

**Build every record-list page with this pattern.** A page that manages a list of
records (greetings, lore, locations, characters, …) is a two-pane editor with a
read-only detail view and an explicit edit step. Canonical implementations:
`components/GreetingEditor.tsx` and `components/EntityEditor.tsx`.

**Structure** — `.editor` containing:
- `.editor-list` — the rail. A `+ New …` button plus one `.row` button per record
  (its name). The rail is `position: sticky` and `overflow-y: auto`, so it scrolls
  **independently** of the page.
- `.editor-body` — shows either the read-only view or the form.

**Modes** — a `mode: "view" | "edit"` state:
- Clicking a row loads the record and opens it in **`view`** (read-only) — records
  are never editable by default.
- `+ New …` opens the **form** directly (`edit`, with no record selected).
- An **Edit** button (top of the sidebar) switches a viewed record into the form.
- Save returns to `view` (re-select the record); Cancel returns to `view`.

**View** — `.detail-view` containing:
- `.detail-main` — an `<h3>` title and `.detail-rendered`, the body rendered with
  `<Markdown remarkPlugins={[remarkGfm]}>` (react-markdown). Localized images are
  markdown, so they render here.
- `.detail-sidebar` — an `<aside>` holding the **Edit** button (in `.form-actions`,
  full-width) and the record's metadata in `.side-section` blocks (`<h4>` label +
  chips/hints). Metadata that references **other records** renders as clickable
  `chip` buttons that navigate to that record; plain attributes render as
  non-clickable `<span class="chip on">` or `.field-hint` text.

**Tests** — cover: clicking a row shows the read-only view (rendered body, no
`textarea`) with its sidebar; **Edit** reveals the form; `+ New` opens the form
directly. See `GreetingEditor.test.tsx` / `EntityEditor.test.tsx`.

## Android (`android/`)

The Android app is a thin Kotlin/WebView shell that packages `backend/src` and
the built frontend **verbatim** (Chaquopy + APK assets) — never copy grimoire
code into `android/`. Rules that keep the platforms in lockstep
(docs/android-architecture.md):

- Backend code must not assume a repo checkout layout or a desktop `~`:
  filesystem access goes through `store.paths`, `prompts.templates_dir()`
  (`GRIMOIRE_TEMPLATES`) and `main.dist_dir()` (`GRIMOIRE_DIST`).
- `pyproject.toml` **base** deps must stay Android-installable (pure python or
  Chaquopy-wheel'd); compiled desktop-only deps go in the `desktop` extra, and
  the pip block in `android/app/build.gradle.kts` mirrors the base list.
- pydantic usage stays v1/v2-agnostic: plain `BaseModel` fields only, dump via
  `routes.common._dump` (no `model_dump()`, `Field`, validators, `ConfigDict`).

Build: `make android-bootstrap` (once per machine — JDK 17, Android SDK,
licenses, `android/local.properties`), then `make apk` (debug) /
`make apk-release`. On Windows make works from PowerShell, cmd, or Git Bash
(recipes are pinned to cmd.exe).

## Development workflow: Codex review gates

The spec → plan → implementation pipeline (`superpowers:brainstorming` writes
specs to `docs/superpowers/specs/`; `superpowers:writing-plans` writes plans to
`docs/superpowers/plans/`) has mandatory Codex checkpoints — do not advance to
the next stage until the gate passes and its findings are resolved:

- **Spec → planning**: run `/codex:adversarial-review` against the spec before
  starting `superpowers:writing-plans`.
- **Plan → implementation**: run `/codex:adversarial-review` against the plan
  before starting implementation.
- **Implementation → done**: run `/codex:review` against the diff before
  considering the work complete (merge, PR, `finishing-a-development-branch`).
- **Done → actually done**: once implementation is otherwise complete, run
  `/codex:adversarial-review` a final time against the diff *and* the
  originating spec, asking specifically whether the changes implement the
  spec (not just whether the diff is clean code) — gaps, drift, and
  quietly-dropped requirements are the target, not style.

If a gate surfaces findings, address them (or explicitly note why not) before
moving on. Don't skip a gate because a change feels small — ask the user
first if you think one should be skipped.

## Working notes

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Nothing creates the store until the first API call that needs it, so the
  installers end by printing where it will land — `python -m grimoire.where`,
  which asks `store.paths` rather than assuming `~/.grimoire` (wrong wherever
  `GRIMOIRE_HOME` or the bootstrap pointer already names somewhere else).
  `scripts/unix/install.sh` and `scripts/windows/install.ps1` also check the
  Python and Node floors up front, and `test_install_scripts.py` holds those
  floors to `requires-python` and `engines.node`.
- **Run the gate with `make check`** — the same targets `.github/workflows/ci.yml`
  runs, so a CI failure reproduces locally with one command. Individually:
  `make check-py` (pytest), `check-web` (npm ci + typecheck + vitest under
  coverage),
  `check-lint` (ruff), `check-templates` (`verify_templates.py`),
  `check-pydantic1` (the whole suite against the Android dependency set —
  pydantic 1.10, no `desktop` extra — in a throwaway venv). `make check-apk`
  is excluded from `check` because it needs `make android-bootstrap` first.
  - In a **worktree**, pass `PY` explicitly: the default points at
    `backend/.venv`, which only the main checkout has — e.g.
    `make check-py PY=~/github/grimoire/backend/.venv/bin/python` on macOS/Linux,
    `make check-py PY=C:/Users/<you>/github/grimoire/backend/.venv/Scripts/python.exe`
    on Windows. The venv's interpreter lives under `bin/` on macOS/Linux and
    `Scripts/` on Windows; every command below that names one spells out both,
    and `test_install_scripts.py` fails if a new one names only one.
  - `check-py` sets `PYTHONPATH` to this tree's `backend/src` on purpose:
    `backend/.venv` holds an editable install whose `.pth` points at whichever
    checkout created it, so a bare `pytest` inside a worktree silently tests the
    *other* tree's sources.
  - Run vitest **from** `frontend/` — `npx --prefix frontend vitest run` executes
    from the repo root, which skips `frontend/vitest.config.ts` and disables
    `globals`, failing every mock-based test. `make check-web` does this right.
  - `check-web` runs `npm run test:coverage` (`vitest run --coverage`), which
    writes `frontend/coverage/lcov.info` — gitignored, uploaded by CI as the
    `frontend-coverage` artifact, and the file external readers discover by
    name. `npm test` still runs the suite bare when you only want pass/fail.
    Coverage config lives in `frontend/vite.config.ts` under `test.coverage`;
    the **istanbul** provider and `all: true` are load-bearing there, and the
    comments say why — do not switch to the v8 provider, which reports a file
    no test imports as 100% covered rather than 0%.
- **The frozen campaign** (`backend/tests/fixtures/frozen_campaign/`) is a whole
  store tree checked in as a fixture — the only store in the repo that today's
  code did not write, which is the only way to catch a change that breaks
  reading what an *older* version wrote. `home/` is **never regenerated**; its
  value is being old. `snapshot.json` (the expected output of the read-only
  sweep) *is* regenerated deliberately, when a template or render moved on
  purpose and the new text was reviewed — `cd backend && PYTHONPATH=src
  .venv/bin/python -m tests.fixtures.frozen_campaign.sweep` (on Windows:
  `cd backend; $env:PYTHONPATH="src"; .venv\Scripts\python.exe -m
  tests.fixtures.frozen_campaign.sweep`), committed with the change that moved
  it. See that directory's README.
- **Faking the LLM**: use `backend/tests/llm_fakes.py`, injected at
  `app.dependency_overrides[routes.get_llm]` — never write another inline fake.
  Scripted turns (`FakeOpenRouter`, `FakeOpenRouterComplete`, …) answer by call
  order; a *cassette* (`from_cassette("campaign_flow")`) answers by what the
  request looks like, replaying hand-authored bodies from
  `backend/tests/fixtures/llm/`. A request matching no cassette entry raises
  rather than defaulting, and `test_llm_fakes.py` renders every real prompt
  template to prove the matchers still match — reword a system prompt and it
  fails there rather than silently everywhere else. These bodies are canned, not
  recorded: they prove the code handles a reply, never that a model would send
  one (`evals/run.py --live` is the only thing that answers that).
- Several architecture rules are enforced by tests that parse the package's own
  ASTs: `test_atomic_guard.py` (every store write goes through `store.atomic`),
  `test_overlay_guard.py`, `test_pydantic_guard.py` (v1/v2-agnostic pydantic),
  `test_paths_guard.py` (filesystem access goes through the resolvers),
  `test_lock_order_guard.py` (only `locks.hold_all` holds more than one campaign
  lock) and `test_lock_domain_guard.py` (below). Clear a genuinely-safe call with
  `# atomic-ok: <reason>` / `# overlay-ok: <reason>` / `# pydantic-ok: <reason>`
  / `# paths-ok: <reason>` / `# lock-order-ok: <reason>` /
  `# lock-domain-ok: <reason>` — a marker with no
  reason fails, deliberately, and each guard caps how many exist.
- **Adding a module that mutates campaign-scoped state?** Classify it in
  `store/locks.py`, or `test_lock_domain_guard.py` fails naming your module. The
  campaign lock domain used to be a docstring list, which is how two mutators
  shipped outside it; it is now `DOMAIN_MODULES` (its public `cid`-taking
  mutators all take `locks.campaign_lock(cid)`), `OUTSIDE_DOMAIN` (deliberately
  not, with the reason) and `UNREVIEWED` (a frozen backlog that may only shrink
  — not open for new entries). A new mutator inside a `DOMAIN_MODULES` module
  must take the lock or carry `# lock-domain-ok: <reason>`. Scene transcripts
  are the artifact this protects: they cannot be regenerated, and `store/scenes`
  serializes its whole mutator surface through `@_serialized` to keep two
  concurrent read-modify-writes from losing one.
- **Needing more than one campaign lock at a time?** There is exactly one way:
  `locks.hold_all(cids)`, which sorts, and `test_lock_order_guard.py` fails any
  other function whose shape can hold two (an acquisition on an `ExitStack`, one
  carried around a loop, two open at once for different campaigns). Two holders once
  acquired every campaign lock in *different* orders — one by recency, one by id
  — and two concurrent requests wedged permanently (#267).
- **Imports in `backend/src/grimoire/` are all at module scope and the module
  graph is acyclic**, enforced by `backend/tests/test_import_guard.py`. Inside
  `store/`, a cross-package import binds a *submodule* and keeps it as a module
  object — `from ..campaigns import read` then `read.world_refs()`, never
  `from ..campaigns import world_refs`, and never `from ..campaigns.read import
  world_refs` either. Two distinct reasons: binding a name off a package that is
  still initializing raises at import time, and binding a function by value off
  a sibling package's leaf module makes the caller cache it, so a test patching
  `campaigns.read.world_refs` silently stops intercepting and goes green while
  injecting nothing. Both leave the file graph acyclic, so the cycle check alone
  would catch neither. The marker is `# import-ok:
  <reason>`, same convention as the guards above — and, given how often this
  refactor caught a stated reason that wasn't actually true, the reason must
  hold up, not merely be present.
- **After editing anything in `templates/`**, `make check` covers both harnesses
  that guard prompts: `scripts/verify_templates.py` (builders and templates agree
  byte-for-byte) and `evals/run.py` (see `evals/README.md`). Offline, the eval
  suite proves the *instructions* are still in the assembled prompt — it
  renders the budget, reply-format and roll-protocol sections and requires each
  one verbatim in the prompt (so all five length knobs and the whole check
  roster are covered), plus every key of the absorb contract and owned-lore
  containment — and that the graders still score recorded output correctly. It
  runs inside `pytest backend`.
  Whether the model still *follows* a reworded instruction is a question only
  `evals/run.py --live` answers; that makes real LLM calls and is opt-in.
