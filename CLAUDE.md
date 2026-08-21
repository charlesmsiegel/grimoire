# grimoire — project conventions

FastAPI backend (`backend/`, pytest) + Vite/React frontend (`frontend/`, vitest).
The app and its data live in a markdown/JSON store rooted at `~/.grimoire` by
default. The root is resolved by `store.home()`: `GRIMOIRE_HOME` env var (tests /
overrides) → the user-chosen path recorded in the bootstrap pointer
`~/.grimoire.json` → `~/.grimoire`. The path is editable from the Configuration
page (Storage location); point it at a synced folder to share a library across
devices.

Three companion documents, so this file does not have to carry everything:
`CONTRIBUTING.md` (how to run `make check`, and the guard/marker table),
`docs/store-guarantees.md` (what the store promises about atomicity and the
campaign lock, and what it deliberately does not), and `AGENTS.md` (a router
for coding agents, which points back here rather than restating it). What each
of them claims about *this* tree is held to the code by
`backend/tests/test_docs_guard.py`.

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

**Where the rail goes depends on whether the page owns the screen.** An editor
that sits *inside* another page — a library section, a world tab, a campaign
panel — brings its own `.editor-list` rail, as below. A page that IS the screen
(the ledger, the timeline, `routes/SheetsView.tsx`) already has a rail: the
274px context column `components/PageShell.tsx` gives it. Put the records in
`ColumnSection`s there and the detail in main. Building an `.editor` rail
beside that column is the "second navigation surface" `PageShell`'s own
docstring calls a misread of the page — the modes, the read-only-by-default
rule and the explicit edit step are the same either way.

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

## Frontend: keyboard bindings go in the registry, never on `window`

`src/shortcuts/` owns every key the app answers, and `no-restricted-syntax` in
`eslint.config.js` fails a `keydown`/`keyup`/`keypress` listener added anywhere
else (`registry.ts` is exempt — it installs the only one). Bind with
`useHotkeys(keys, { modal })`:

- The array is read at dispatch, so it needs no memoization and its `enabled`
  is never a render behind the screen. Mirror the control the binding stands
  for, and carry the condition that control is disabled by — a shortcut that
  reaches past a disabled button is a second copy of the guards.
- `whileTyping` is what survives the caret being in prose, and almost nothing
  should ask for it: a bare letter that did would eat the word being typed.
  `global` survives an open overlay, and only the palette and the shortcuts
  sheet may claim it.
- An overlay passes `modal`, which makes Escape reach it rather than the view
  underneath and holds that view's bindings off while it is up. "On top" is
  whichever modal registered last, and opening re-registers — so it follows
  what the reader sees, not the mount order.
- A binding with a `label` and a `group` is listed by the `?` sheet, which
  reads the registry rather than a hand-kept list. That is the whole
  discoverability story, so label anything a reader could want to find.

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
- The **manifest and resources are read by `backend/tests/test_android_manifest.py`**,
  which parses every XML under `android/app/src/main` and checks the service's
  `foregroundServiceType` against the declared permissions. `make check-apk` is
  the real build and it is outside `make check` (it needs `android-bootstrap`
  first), so without this nothing in the ordinary gate opens these files at all
  — an XML comment containing `--` is illegal, and one line of prose failed
  `processDebugMainManifest` on CI with nothing local to say so.

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

## Costs: three money columns, and no two of them may be added

`store/usage.py` is an append-only ledger of what each LLM call reported, and
every rollup over it carries **three** separate money figures. Adding any two
of them together produces a number that is wrong in a direction nobody can
recover, so the split is the design rather than an artefact of it:

- `cost_usd` — what a provider said it charged. The only figure that is spend,
  and the only one a campaign budget is measured against.
- `estimated_usd` — a call that billed against a subscription instead of per
  token (the `claude_agent` path), priced by the provider at what it *would*
  have cost. Real usage; not money anybody paid.
- `modelled_usd` — a call whose provider named no price at all, costed against
  the per-token table the user maintains in `store/pricing.py`. Arithmetic this
  side did, and the weakest of the three.

`unpriced_calls` counts what none of them covers, which is what makes an
incomplete total say so. The rule that follows from all of it, and the one the
UI exists to keep: **a price nobody reported is never rendered as zero.** Both
the ledger (an absent field, never a `0`) and the three cost surfaces
(`components/cost.tsx`, which every one of them formats through) are built
around that single sentence.

Attribution is per *player post*: a turn's ledger row carries the transcript
index it was answering, so a post and every reroll of it bucket together and
the transcript can say what getting one reply actually cost. The index is only
as stable as indices are — a cut renumbers what follows it and the ledger
cannot follow — so it is a breakdown, and the scene's own totals are the number
that is always right.

## Detached runs: a turn outlives the request that asked for it

A dropped connection used to cancel generation. It no longer does — it drops a
subscriber. **Five scene-turn handlers** start detached runs: `post_chat`,
`post_retry`, `post_regenerate`, `post_replay_turn` and
`post_roll_proposal`. (Five, not six: `post_opener` is the sixth synchronous
streaming handler, but it is a `draft` and Phase 1 leaves draft routes alone.)

- The run registry lives on **`app.state.runs`**, not at module scope: a
  `TestClient` builds an app per test, and module state would leak runs between
  them. `runner.install` adds the parts that need a running loop.
- Those handlers are `def`, so FastAPI runs them in a threadpool worker. Work is
  handed to the lifespan loop through an `anyio` **`BlockingPortal`** —
  `tg.start_soon` is not thread-safe from a worker.
- A scene run's subject is **`("scene", cid, identity)`**. The `sid` moves on
  rename and is reissued after a delete, so it cannot name a run that outlives
  its request.
- Every terminal write is **fenced** on that identity under the campaign lock,
  and every route that changes a scene's *shape* is refused with `scene_busy`
  while a turn holds it: rename, delete, message edit, cut, retcon, alternate
  promotion, replay begin/accept/cancel, a manual roll or check, every cast
  route (`appear`/`leave` append a transition line), location and datetime (the
  first `set_datetime` **renames** the scene), the review save, both greeting
  routes in `greetings.py`, and a width-crossing create. The check is taken
  **inside** the campaign-lock hold that covers the mutation — checking first
  and locking after leaves room for a send to reserve in between.
  `test_scene_freeze.py` is one case per door, because the guard is applied per
  call site, and the doors an inventory misses are the ones in another module.
- **`PUT /config/data-dir` is refused while any run is live**, anywhere
  (`runs_in_flight`). The frontend registry deliberately lets a turn survive
  navigation, so the player can now reach Configuration mid-turn; the root is
  global, so a run in any campaign would be persisted into the wrong tree.
- `store.attempts` is the durable half: whether a send's post is still in the
  transcript, recorded beside the append and cleared inside the rollback. It is
  what lets recovery after the run record expired ask a question that has a
  right answer, instead of matching text.

## Observability: one writer, three views

`store/logs.py` is the only thing in the app that writes a log line, and
`<home>/logs/YYYY-MM.jsonl` is the only file it writes to. `store/errors.py`
(#156) and `store/metrics.py` (#154) are *readers* over it and over the usage
ledger — there is no second store, and adding one would mean the same failure
written twice by two appends that can disagree.

Four rules that are easy to undo by accident:

- **Never attach a second `logging` handler, and never attach one to the root
  logger.** `logs.install()` (from `create_app`) puts one on the `grimoire`
  logger, and that boundary is a privacy rule, not a preference: `httpx` logs
  full request URLs at DEBUG, an OpenAI-compatible endpoint can carry its key
  in one, and this is a file users are asked to attach to bug reports.
- **`logs.record` may not raise and may not re-enter itself.** It runs beside a
  turn and inside exception handlers, and writing a row resolves the store root
  — which reads the bootstrap pointer through `failsoft`, which *logs*. The
  thread-local latch is what makes that terminate.
- **The level floor stops at `error`** (`logs.FLOORS`). The error store is a
  view over ERROR rows, so a floor above them would be a setting that silently
  switches #156 off — which the size backstop and Configuration both promise it
  cannot.
- **Instrument LLM failures at `usage.Meter.done`, not at call sites.** Every
  LLM call in the app runs under a meter, so that is the one place that sees
  all of them fail, and the meter's `task` is already the module axis the error
  store aggregates on. Sixteen call sites each remembering to pass a `kind` is
  how half of them stop appearing in the per-kind counts.

The two error counts on `/stats` come from two places **on purpose**: a latency
bucket's `errors` counts calls that failed (the usage ledger, the only source
that also knows how many succeeded, so the only one that can give a rate its
denominator), and the `errors` block counts failures recorded anywhere
(including those that were never a call). Reconciling them into one number
would answer neither question.

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
  `make check-py` (pytest under coverage), `check-web` (npm ci + typecheck +
  vitest under coverage),
  `check-lint` (ruff), `check-mypy` (mypy), `check-eslint` (eslint),
  `check-templates` (`verify_templates.py`),
  `check-pydantic1` (the whole suite against the Android dependency set —
  pydantic 1.10, no `desktop` extra — in a throwaway venv). `make check-apk`
  is excluded from `check` because it needs `make android-bootstrap` first.
  - In a **worktree**, pass `PY` explicitly: the default points at
    `backend/.venv`, which only the main checkout has — e.g.
    `make check-py PY=~/github/grimoire/backend/.venv/bin/python` on macOS/Linux,
    `make check-py PY=C:/Users/<you>/github/grimoire/backend/.venv/Scripts/python.exe`
    on Windows. The venv's interpreter lives under `bin/` on macOS/Linux and
    `Scripts/` on Windows; every command in this file, in `README.md` and in the
    frozen-campaign README spells out both forms, and `test_install_scripts.py`
    fails on a new one that names only one.
  - `check-py` sets `PYTHONPATH` to this tree's `backend/src` on purpose:
    `backend/.venv` holds an editable install whose `.pth` points at whichever
    checkout created it, so a bare `pytest` inside a worktree silently tests the
    *other* tree's sources.
  - Run vitest **from** `frontend/` — `npx --prefix frontend vitest run` executes
    from the repo root, which skips `frontend/vitest.config.ts` and disables
    `globals`, failing every mock-based test. `make check-web` does this right.
  - **In a frontend test, an `await` means the page has SETTLED**, not that the
    query it named passed. `src/test-setup.ts` wraps React Testing Library's
    `asyncWrapper` so every `findBy*`/`waitFor` returns only once the rendered
    DOM has gone quiet. Without it, the statement after a test's first `await`
    runs against a page still building itself — a control still `disabled` (and
    `fireEvent.click` on one dispatches nothing and reports nothing), a gutter
    not yet rendered, a `<select>` not yet filled. That is invisible on an idle
    machine and reds CI on a shared runner, in a different test every run
    (#351). `settle.test.tsx` is the guarantee, deterministically — it fails
    both with the wrapper gone and with it settling for one unchanged tick
    instead of two, which is the rule that carries the invisible stages.
  - `check-web` runs `npm run test:coverage` (`vitest run --coverage`), which
    writes `frontend/coverage/lcov.info` — gitignored, uploaded by CI as the
    `frontend-coverage` artifact, and the file external readers discover by
    name. `npm test` still runs the suite bare when you only want pass/fail.
    Coverage config lives in `frontend/vite.config.ts` under `test.coverage`;
    the **istanbul** provider and `all: true` are load-bearing there, and the
    comments say why — do not switch to the v8 provider, which reports a file
    no test imports as 100% covered rather than 0%.
  - **Shared test scaffolding goes in `frontend/src/testkit/`**, which that
    coverage config excludes. Two suites drive the campaign play view — the
    play loop (`routes/CampaignView.test.tsx`) and the end-of-scene review
    (`components/review/SceneReview.test.tsx`) — against one set of mocks:
    `testkit/campaignMocks.tsx` holds the `vi.mock` factories and
    `testkit/campaignHarness.tsx` the fixtures, per-test defaults and render
    helpers. Both halves are needed because a `vi.mock` factory is hoisted
    above every import and can close over nothing, so a suite reaches its
    factory through a dynamic `import()` — and a factory module that itself
    imported the mocked `api` would deadlock on the module waiting for it.
    Left in `src/routes/` instead, scaffolding lands in the coverage
    denominator the paragraph above exists to keep honest, and a helper only
    one suite uses belongs in that suite, not in the shared half.
- **The three lint gates are ratcheted, and fixing a finding is a two-step.**
  `check-lint`, `check-mypy` and `check-eslint` run their tool and compare the
  result to `lint-baselines/<tool>.json`, keyed by (file, rule). Landing them
  any other way was not an option: ruff's widened selection, mypy at its
  defaults and typescript-eslint's type-checked rules report ~2400 findings
  against this tree between them, and report-only jobs get ignored until the
  number is unrecoverable. The consequence to remember is that **an
  improvement fails the gate too** — resolve a finding and the recorded count
  is stale, so `make baseline` and commit the smaller file with the fix.
  `scripts/ratchet.py` and `CONTRIBUTING.md` carry the rest.
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
  renders the budget, reply-format, roll-protocol, active-speaker and
  available-art sections and requires each one verbatim in the prompt (so all
  five length knobs, the whole check roster, the nomination the speaker layer
  derived and the art handles it offered are covered),
  plus every key of the absorb contract and owned-lore containment — and that
  the graders still score recorded output correctly. It runs inside
  `pytest backend`.
  Whether the model still *follows* a reworded instruction is a question only
  `evals/run.py --live` answers; that makes real LLM calls and is opt-in.
