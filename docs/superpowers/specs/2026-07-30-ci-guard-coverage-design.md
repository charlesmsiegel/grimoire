# CI guard coverage — design

**Date:** 2026-07-30
**Status:** approved, pre-implementation (revised after two Codex adversarial reviews)
**Branch:** `cicd`

## 1. The problem

The repository has no `.github/workflows/`. Every check the project built for
itself runs only when a human remembers to run it. That is not hypothetical; it
has already cost five regressions, each of which sat on `main` undetected:

| What broke | How long | How it was found |
| --- | --- | --- |
| `test_end_to_end_regression_fixture` red — the committed `phi` column was never produced by the implementation | since `74d7b2d7`, 2026-07-28 | running the suite by hand, 2026-07-29 |
| `scripts/verify_templates.py` crashes with `CampaignNotFound` — `e1e7cb0b` changed `relationships.actor_name(croot, …)` to `actor_name(cid, …)` and updated every in-package caller but not this script | since 2026-07-10, 20 days | running the script by hand, 2026-07-30 |
| 39 `noqa` markers written against ruff rule codes, but no ruff config exists anywhere in the repo and ruff is not installed | since the markers were written | looking for a lint config, 2026-07-30 |
| 17 unused imports, 7 unused variables | unknown | first ruff run, 2026-07-30 |
| 10 `noqa` markers that suppress nothing — the rule is either not enabled or does not fire there | unknown | first `RUF100` run, 2026-07-30 |

The pattern is identical in all five: the project authors a good guard, then
leaves its execution to discipline. `docs/codemap.html` ranks this first among
codebase risks, and the sharpest case is `templates/` — Jinja auto-reload means a
bad prompt edit takes effect live, with no restart and no code change.

The first is fixed on this branch (`890ecde0`, `a745dbc2`); the rest are fixed
below. This spec exists so there is never a sixth.

**The design rule this implies.** Every guard here must be *reachable without a
human deciding to reach it*, and must be able to *fail*. A guard that is
configured but never invoked, or that passes vacuously when its scan finds
nothing, is the disease rather than the cure. Each section states how its guard
fails.

## 2. Decisions

Settled with the user before design:

1. **Nothing merges until every job is green.** See §2a for what that can and
   cannot mean given that no Linux environment exists locally.
2. **Ubuntu only, Python 3.11 and 3.14.** 3.11 is the floor
   `backend/pyproject.toml` declares and has never been tested; 3.14 is the
   development version. Windows is *not* in the matrix — accepted limitation, §8.
3. **Android portability gets all three enforcement layers**: AST guard tests, a
   pydantic-1.10 job, and a real APK build. `docs/android-architecture.md:200`
   and `:263` already specify the APK job ("CI gets one new job: assemble a debug
   APK"), so this is design intent rather than new scope.
4. **No secrets in CI.** Only the offline replay evals run (they already live
   inside `pytest`). `evals/run.py --live` stays a deliberate local command, so
   CI has no API key, no spend, and no third-party flake in the gate.

### 2a. What "green" can honestly mean here

An earlier draft promised the *first* CI run would be green. That promise cannot
be kept, and pretending otherwise would be the same self-deception this branch
exists to end:

- **No Linux is available locally.** No WSL distribution is installed and there
  is no Docker, so the suite cannot run on Ubuntu before pushing. The backend
  suite has never executed on Linux, on Python 3.11, or under pydantic 1.10 at
  its current size (2705 tests; `docs/android-architecture.md:239` records a
  manual pydantic-1.10 run at 738 tests, long superseded).
- The first PR run *is* the Linux validation. Path case-sensitivity,
  temp-directory and file-locking behaviour, locale, line endings, and wheel
  availability on 3.11 are unverified until it runs.

So the constraint is: **the branch does not merge until every job passes, and no
job is made to pass by skipping, `xfail`, `continue-on-error`, or narrowing what
it covers.** Iterating on red runs inside the PR is expected work, not failure.
If a job proves genuinely intractable, that is a finding to raise with the user —
not grounds to weaken the job. In particular the APK job either passes or the
branch waits; there is no "land it unfinished" path.

## 3. Architecture: one command surface, thin workflow

The workflow YAML must not become a second source of truth for how to run the
checks — that drift is what broke `verify_templates.py`. Each guard gets a `make`
target; the workflow calls one target per job.

```
make check           # everything except check-apk
make check-py        # pytest (includes guard tests + offline evals)
make check-web       # npm ci, typecheck, vitest
make check-lint      # ruff check .
make check-templates # scripts/verify_templates.py
make check-pydantic1 # pytest against the Android dependency set, isolated venv
make check-apk       # frontend build, then the existing `apk` target
```

`make check` excludes `check-apk`: that target needs a per-machine
`make android-bootstrap` (JDK, SDK, licences, `local.properties`), so folding it
in would make the everyday command fail on any un-bootstrapped machine. CI runs
them as separate jobs, so nothing is lost.

A CI failure then reproduces locally with the same one-line command, and
`CLAUDE.md`'s command list stops being an independent copy that can rot.

### Makefile requirements

The existing Makefile pins recipes to `cmd.exe` on Windows, defines
`GRADLEW = cd $(ANDROID) && .\gradlew.bat --no-daemon` (POSIX equivalent
alongside), and provides `fixpath`, `SDK_DIR`, `APK_DEBUG` and a `.PHONY` list.
New targets must respect three traps:

- **Interpreter selection.** `PY ?=` defaults to
  `$(CURDIR)/backend/.venv/Scripts/python.exe` on Windows and `python3` on POSIX.
  It must be **absolute** via `$(CURDIR)`, because a recipe that changes
  directory would otherwise resolve a repo-relative path against the wrong
  parent (`cd backend && $(PY)` → `backend/backend/.venv/…`). Every use is
  **double-quoted** in the recipe, so a checkout under a path containing spaces
  does not break under `cmd.exe`.
- **Working directory.** Every recipe states where it runs: `check-web` in
  `frontend/`, everything else at the repo root, each as a single
  `cd X && …` chain rather than a bare `cd` expected to persist between lines.
- **`.PHONY`.** All seven new targets join the existing list.

### Exact invocations

| Target | Command |
| --- | --- |
| `check-py` | `"$(PY)" -m pytest backend -q` |
| `check-web` | `cd frontend && npm ci && npm run typecheck && npm test` |
| `check-lint` | `"$(PY)" -m ruff check .` |
| `check-templates` | `"$(PY)" scripts/verify_templates.py` |
| `check-pydantic1` | §7 |
| `check-apk` | `cd frontend && npm ci && npm run build`, then the existing `apk` target |

- `npm ci`, not `npm install`: `install` silently rewrites resolution, defeating
  the committed lockfile. Verified 2026-07-30 that `npm ci` exits 0 against the
  committed `frontend/package-lock.json` on Node 24.16.0.
- Bare `tsc -b` / `vitest run` would **not** work as workflow steps —
  `node_modules/.bin` is not on `PATH` for arbitrary shell commands. Both go
  through package scripts. `frontend/package.json` has `test` (`vitest run`) but
  no typecheck script, so **one is added**: `"typecheck": "tsc -b"`.
- `check-lint` runs `ruff check .` from the repo root, not a list of trees. An
  explicit list already missed `backend/scripts/` (two files) and
  `android/app/src/main/python/android_entry.py`; `.` plus the `exclude` in §5
  cannot develop that gap.
- `check-apk` **reuses the existing `apk` target** rather than calling gradle
  itself. `apk` already runs `$(GRADLEW) :app:assembleDebug` — where `GRADLEW`
  supplies both `cd android` and `--no-daemon`, so repeating either would be
  redundant — and then copies the output to `$(APK_DEBUG)`
  (`build/grimoire-debug.apk`). That copy is what the workflow uploads.

### Workflow

One workflow, `.github/workflows/ci.yml`, six job definitions (seven runs):

| Job | Runs | Environment contract |
| --- | --- | --- |
| `backend` | `make check-py`, matrix Python 3.11 and 3.14 | checkout; setup-python at the matrix version; `pip install -e "./backend[dev]"`; `pip check`; target |
| `frontend` | `make check-web`, Node 24 | checkout; setup-node 24, cache keyed on `frontend/package-lock.json` |
| `lint` | `make check-lint`, Python 3.12 | checkout; setup-python; `pip install ruff==0.16.0`. No project install — ruff parses, it does not import |
| `templates` | `make check-templates`, Python 3.12 | checkout; setup-python; `pip install -e "./backend[dev]"` (the script imports grimoire) |
| `pydantic1` | `make check-pydantic1`, Python 3.11 | §7 |
| `apk` | `make check-apk` | §9 |

Each job overrides `PY` to the runner's interpreter (`make check-py PY=python`),
since no `backend/.venv` exists on a runner.

Triggers: `push` to `main`, `pull_request` targeting `main`,
`workflow_dispatch`. Concurrency keyed on the ref with `cancel-in-progress: true`.

**How this branch gets its first run.** `workflow_dispatch` cannot be triggered
for a workflow absent from the default branch, and `push` is scoped to `main` —
so `cicd` would otherwise get no run at all. The first validation is therefore
**a PR from `cicd` into `main`**, and §10 measures that PR's head SHA.

**On pinning, stated honestly.** Actions are pinned to full commit SHAs (with the
version in a trailing comment) and the runner to `ubuntu-24.04`. SHA-pinning the
actions is a real guarantee; the runner label is **not** — `ubuntu-24.04` is a
continuously updated image, so a toolchain change inside it can still turn a
green SHA red without a commit. That residual exposure is accepted and recorded
rather than claimed away.

## 4. New guard tests

These are pytest tests in `backend/tests/`, **not** workflow steps — so they run
in every `pytest` invocation, local or CI, and a violation is caught before push.
They follow the codebase's strongest existing pattern (`test_atomic_guard.py`,
`test_overlay_guard.py`): walk the package's own ASTs and let a human clear a
genuinely-safe call with a `# <marker>: <reason>` comment. Marker parsing reuses
`backend/tests/guard_markers.py`, which already refuses a marker inside a string
literal, refuses one that merely *mentions* the marker, resolves ownership by AST
containment, and returns no exemptions at all for a file that will not tokenize.

Three mechanics, verified against `guard_markers.py` rather than assumed:

- **The marker constant must include the colon.** `_stated_reason` tests
  `body.startswith(marker)` and returns `body[len(marker):].strip()`. With
  `marker="paths-ok"`, the comment `# paths-ok:` returns `":"` — truthy — so a
  reasonless exemption would be accepted, and `# paths-okay: …` would match too.
  Both existing guards already do this correctly (`MARKER = "atomic-ok:"`,
  `"overlay-ok:"`); the new ones use `"pydantic-ok:"` and `"paths-ok:"`.
- **An empty reason must be rejected by truthiness.** With the colon included,
  `# paths-ok:` yields `""` — falsy but not `None`. A guard testing
  `is not None` would accept it. Each guard tests `if reason:` and has a test
  proving a bare `# marker:` does not silence it.
- **Markers attach to AST nodes, not functions.** `marker_reason` accepts a
  marker on one of the node's own lines or in the unbroken comment block
  immediately above. (An earlier draft called this "function-scoped"; that is
  `test_overlay_guard.py`'s separate additional rule, not this helper's.)

**`test_pydantic_guard.py`.** Enforces `CLAUDE.md`'s rule that pydantic usage
stays v1/v2-agnostic. The rule is *stricter* than "no v2-only API", and the spec
says so because the distinction caused a review finding: `Field`, `validator` and
`root_validator` all exist in pydantic 1.10, so banning them is not a
v1-compatibility requirement — it is the project's "plain `BaseModel` fields
only" requirement, which exists so request parsing behaves identically on both
lines and the APK's 1.10 pin stays a pure install-time choice.

The guard enforces a **finite, enumerated list** rather than a vague promise of
"all v2 APIs", because the latter cannot be delivered by a syntactic check:

- v2-only APIs: `model_dump`, `model_dump_json`, `model_validate`,
  `model_validate_json`, `model_json_schema`, `model_copy`, `model_construct`,
  `model_rebuild`, `model_fields`, `model_fields_set`, `model_config`,
  `ConfigDict`, `TypeAdapter`, `RootModel`, `field_validator`,
  `model_validator`, `field_serializer`, `model_serializer`, `computed_field`,
  `validate_call`;
- the project's stricter bans: `Field`, `validator`, `root_validator`.

Matching is import- and alias-aware — it resolves `import pydantic` and
`from pydantic import X as Y` per module and matches bare `Name`, qualified
`Attribute` (`pydantic.ConfigDict()`), bare decorators and decorator calls —
because name-only matching is trivially evaded. It **also scans class-body
assignments**, since the canonical v2 configuration form
`model_config = {"extra": "forbid"}` mentions no pydantic name and is not a
call, and so would slip past an import-aware matcher entirely.

Two limits are documented rather than papered over: a method-shaped call like
`obj.model_dump()` can only be matched on the attribute name, so an unrelated
object with a same-named method is a false positive (accepted — the marker is
the remedy); and a fully dynamic call (`getattr(m, "model_" + "dump")()`) is not
matched at all. `routes/common.py:_dump` is the one sanctioned v2 site and
carries `# pydantic-ok:` with its reason.

**`test_paths_guard.py`.** The Android portability rule that filesystem access
goes through the designated resolvers. The claim is deliberately narrowed to what
a syntactic check delivers: it flags a fixed list of home-directory and
repo-relative idioms — `Path.home()`, `pathlib.Path.home()` and aliased `Path`,
`os.path.expanduser` and `from os.path import expanduser`, `.expanduser()`,
`os.environ["HOME"]`, `os.environ.get("HOME")`, `os.getenv("HOME")`, and
`Path(__file__).resolve().parents[N]` / `.parent.parent` chains — and does **not**
claim to prove that all filesystem access uses the resolvers. Equivalents it
cannot see (an intermediate root assigned to a variable, `os.path.dirname`
chains, a path built from a literal) are listed in the test's own docstring, in
the spirit of `test_atomic_guard.py:7-12`, whose honesty about its own reach is
the reason to trust it.

Sanctioned sites are cleared with **per-site markers, not a module allowlist**.
An earlier draft allowlisted four modules wholesale; that recreates the hole this
branch closes, since any future direct filesystem access anywhere in those files
would pass automatically. The complete current list is eight sites, each of which
gets `# paths-ok: <reason>` on the call itself:

| Site | Why it is legitimate |
| --- | --- |
| `store/paths.py:13`, `:23` | `home()`/pointer resolution — the resolver itself, built from `Path.home()` by design |
| `store/paths.py:39`, `:80` | expanding a user-supplied storage path, which is the feature |
| `store/proclock.py:87` | machine-local lock state, deliberately outside the data root |
| `main.py:19` | `DEFAULT_DIST`, overridden by `GRIMOIRE_DIST` on Android |
| `prompts.py:14` | `DEFAULT_TEMPLATES_DIR`, overridden by `GRIMOIRE_TEMPLATES` on Android |
| `store/epub.py:26` | package-relative (`grimoire/assets/fonts`), so it ships inside the wheel — Android-safe for a different reason than the two above |

If the guard flags a ninth site, that is the guard working.

**Both guards must be proven to fail, durably.** A one-time manual injection is a
demonstration, not coverage — a later refactor could break the scanner while the
suite stays green. Each guard ships parameterized tests over temporary source
snippets covering: every prohibited form, aliased and qualified variants, an
allowed form that must *not* flag, the marker inside a string literal, a bare
`# marker:` with no reason, and a valid marker. Each guard also asserts its
repository scan is **non-vacuous** — that it parsed at least one file and found
the known sanctioned occurrences — so a broken glob or renamed package directory
fails loudly instead of passing with nothing to check.

## 5. Ruff

New `ruff.toml` at the repo root — not `backend/pyproject.toml`, because the lint
covers `scripts/`, `evals/`, `backend/scripts/` and `android/` too. Standalone
`ruff.toml` uses bare `target-version` / `line-length` and `[lint]` sections (the
`[tool.ruff.*]` form is only for `pyproject.toml`).

```toml
target-version = "py311"          # the floor pyproject declares
line-length = 100                 # documentation only; E501 is not selected
exclude = ["backend/.venv", "node_modules", "build", "dist", ".worktrees", "OLD"]

[lint]
select = ["F", "BLE", "E9", "E402", "RUF100"]

[lint.per-file-ignores]
"backend/src/grimoire/store/__init__.py"  = ["F401"]  # 60-submodule aggregation
"backend/src/grimoire/routes/__init__.py" = ["F401"]  # router aggregation
```

The per-file ignores **name the two files** rather than globbing
`**/__init__.py`. Measured: those are the only two `__init__.py` with `F401`, at
11 and 3 findings. A glob would hand every future package initializer a permanent
blind spot.

Two selections deserve their reasons:

- **`RUF100`** (unused `noqa`) is the rule most on this branch's theme: a marker
  naming a rule that is not enabled is an inert guard, the same disease in
  miniature. It is what found the fifth row of §1's table.
- **`E402`** is enabled *because* the codebase already believes in it. 21 of the
  25 inert markers were `# noqa: E402` on deliberate sys.path-then-import
  patterns in `verify_templates.py`, `evals/run.py`, `backend/scripts/` and
  several tests. Deleting 21 meaningful markers to satisfy `RUF100` would discard
  real information; enabling the rule makes 15 of them live instead.

Measured triage to reach zero, whole-repo with the config above — **58 findings**:

| Rule | Count | Disposition |
| --- | --- | --- |
| `E402` | 17 | Across 5 test files (`test_llm.py` 6, `test_epub_store.py` 5, `test_calendars.py` 3, `test_changes_store.py` 2, `test_modules_store.py` 1). Same deliberate late-import pattern as the 15 already marked; add the marker, or hoist the import where it is incidental |
| `F401` unused import | 17 | Remove, after verifying per-import that nothing depends on an import side-effect. `store/absorb.py:12` (`.chronicle`), `store/overlay.py:37` (`.worlds`) and `store/calendars/config.py:10` (`CalendarError`) need that check specifically — a submodule import can exist to register something |
| `RUF100` | 10 | Delete. Six stale `E402` markers where the rule does not fire, two `BLE001` where the handler re-raises (`store/cards.py:145`, `test_modules_store.py:758`), one unused `F401` (`store/climates/__init__.py:14`), one `E731` (`routes/streaming.py:183`) whose rule stays unselected |
| `F841` unused variable | 7 | Remove or use — each is a real smell |
| `BLE001` blind except | 6 | Five in `backend/src` (`calendars/plugins.py:47`, `climates/__init__.py:35`, `context.py:732`, `module_display.py:364`, `overlay.py:140`) already carry prose comments explaining deliberate containment; add `# noqa: BLE001` with that reason. The sixth, `backend/tests/test_path_guard_store.py:530`, gets its exception narrowed — a test has no containment argument |
| `F541` empty f-string | 1 | Fix |

`E501` is deliberately **not** selected: at ruff's default 88 columns it reports
2420 findings, while the real distribution is p99 = 99 columns with 23 lines over
120. Line length needs a formatter decision, not a lint gate, and a 2420-line
reformat would bury the guards. The rest of `E7` (23 `E702`, 10 `E731`, 6 `E741`,
2 `E701`) is deferred for the same reason — with the one consequence noted above,
that the single `E731` marker gets deleted rather than honoured.

`ruff` goes in the `dev` extra of `backend/pyproject.toml`, **pinned exactly** to
`ruff==0.16.0` (the measured version), and the `lint` job installs that same pin.
An unpinned linter turns a green SHA red on someone else's release schedule. It
must not enter the base dependency list — that stays Android-installable.

## 6. Fixing `verify_templates.py`

`scripts/verify_templates.py:302` passes `croot` to
`relationships.actor_name(cid, token)`, whose first parameter became a campaign id
in `e1e7cb0b`; the three `store/absorb.py` call sites already pass `cid`. The fix
is to match them.

Acceptance is concrete, because "fix the crash" is not a criterion: the script
must exit **0** and `make check-templates` must be green. Its output has not been
seen in 20 days, so `CampaignNotFound` may not be the only finding; any further
failure it reports is in scope for this branch. The script runs from the repo
root, sets its own `GRIMOIRE_HOME` to a temp directory
(`verify_templates.py:26-27`) and inserts `backend/src` on `sys.path` itself, so
the target needs no environment beyond an interpreter with the project's
dependencies installed.

## 7. The pydantic-1.10 job

The check most at risk of passing vacuously, so it is pinned down:

- **A dedicated virtualenv**, created by the target at `build/venv-pydantic1`,
  **deleted and recreated** on every run — never the development
  `backend/.venv`. Installing pydantic 1.10 into the everyday venv would
  silently downgrade the working environment and make `make check`
  order-dependent; reusing a stale venv would let removed packages linger.
  Interpreter paths differ per platform (`Scripts/python.exe` vs `bin/python`),
  so the target resolves them through the existing `fixpath`/platform split.
- **One resolver invocation** installs the project and the constraints together,
  so pip cannot satisfy the pin and then upgrade past it while resolving FastAPI:

  ```
  pip install -e "./backend[dev]" "pydantic==1.10.*" "fastapi>=0.110,<0.116"
  ```

- **The FastAPI bound must be mirrored into `android/app/build.gradle.kts`.**
  That pip block currently says `install("fastapi>=0.110")`, unbounded, so
  constraining FastAPI only in CI would mean the job tests a known-good version
  while the APK resolves a newer, possibly incompatible one — the job would no
  longer reproduce the Android dependency set it claims to. The same bound goes
  in both places, and the gradle block's existing "keep in lockstep with
  `backend/pyproject.toml`" comment is extended to say so.
- **No `desktop` extra** — that is the point: this reproduces the Android
  dependency set, which has no `uvicorn[standard]` and no `tiktoken`
  (`count_tokens` falls back to a heuristic).
- **A runtime assertion before pytest**, so the job cannot silently test
  pydantic 2:

  ```
  python -c "import pydantic; assert pydantic.VERSION.startswith('1.10.'), pydantic.VERSION"
  ```

- **`pip check`** after install, to catch an incoherent resolution.
- Then **`python -m pytest backend -q`** with that venv's interpreter.
- Pinned to **Python 3.11**, matching the runtime Chaquopy packages
  (`build.gradle.kts:60`), unless 3.14 is separately demonstrated.

## 8. Out of scope

- **Windows in the matrix.** Chosen by the user. The store's OS file locking,
  `AppData`/XDG path resolution and atomic renames are the most
  platform-sensitive code in the app, and stay covered only by local runs on the
  development machine. Worth revisiting if a Windows-specific regression ships.
- **Live evals**, and therefore any CI secret.
- **`E501` and the rest of `E7`**, and any formatter adoption.
- **Branch protection / required status checks.** A repository setting, not a
  file in the repo; enable it once the workflow has a green run.
- **Release automation, APK signing, publishing.** `apk-release` builds unsigned
  today and signing needs a keystore secret, which §2.4 rules out.
- **Coverage measurement and reporting.** A separate decision with its own
  threshold argument.
- **A Python lockfile.** Base deps stay lower-bounded because the Android pip
  block mirrors them; pinning is applied where it is free (ruff, action SHAs, the
  pydantic-1 job's FastAPI range) rather than repo-wide.

## 9. The APK job

The heaviest job and the most likely to fight back. Requirements read out of the
Android build files rather than assumed:

- **JDK 17**, explicit distribution (`temurin`) — `sourceCompatibility` and
  `jvmTarget` are both 17.
- **Gradle 8.7** via the committed wrapper (`gradle/wrapper/gradle-wrapper.properties`).
- **AGP 8.5.2, Kotlin 1.9.24, Chaquopy 15.0.1** (`android/build.gradle.kts:2-4`).
- **`compileSdk 34`, `minSdk 26`, ABI `arm64-v8a`.** The job installs
  `platforms;android-34` and the matching build-tools via `sdkmanager`, accepts
  licences non-interactively, and sets **both** `ANDROID_HOME` and
  `ANDROID_SDK_ROOT` because the toolchain reads either.
- **No NDK is required, and none is installed.** An earlier draft said AGP would
  "resolve its default NDK", which is not how NDK provisioning works. The
  evidence that none is needed: `ndkVersion` is unset, the only `ndk {}` block is
  `abiFilters` (which selects which prebuilt ABIs get packaged, and compiles
  nothing), the project has no `externalNativeBuild`, Chaquopy consumes prebuilt
  wheels rather than building them, and `make android-bootstrap` installs no NDK
  yet `make apk` succeeds locally. If the CI build proves otherwise, the fix is
  to pin an explicit `ndkVersion` and install that exact package — not to install
  whatever the image happens to carry.
- **buildPython ≤ 3.12, passed through `make`.** Chaquopy 15's build-machine
  Python is distinct from the 3.11 runtime it packages and supports at most 3.12
  (`build.gradle.kts:47-49`). Since jobs invoke only make targets, the Makefile
  needs an interface: a `BUILD_PYTHON` variable, defaulting empty, appended as
  `-Pgrimoire.buildPython="$(BUILD_PYTHON)"` when set. The job sets up Python
  3.12 **explicitly** rather than trusting the runner image, and passes the
  resolved path: `make check-apk BUILD_PYTHON="$(which python3.12)"`. Locally the
  variable stays empty and `local.properties` continues to supply the value, so
  the existing developer flow is untouched.
- **The frontend must be built first.** The assets pipeline stages
  `frontend/dist` into the APK (`build.gradle.kts:97-120`), so
  `npm ci && npm run build` precedes gradle. This is the one real ordering
  constraint inside the target, and it means the job also needs Node 24 set up.
- **`local.properties` stays uncommitted** and is not needed: `ANDROID_HOME` plus
  the explicit `BUILD_PYTHON` cover both things the bootstrap writes.
- **The artifact upload must fail loudly.** The job uploads
  `build/grimoire-debug.apk` — the path the existing `apk` target copies to — with
  `if-no-files-found: error`. Otherwise a build producing no APK uploads nothing,
  warns, and leaves the job green: a vacuous check of exactly the kind §1 forbids.

## 10. Success criteria

1. `.github/workflows/ci.yml` exists, and all six job definitions — seven runs,
   counting the backend matrix — pass on the head SHA of a PR from `cicd` into
   `main`.
2. `make check` runs green locally on Windows; `make check-apk` runs green on the
   bootstrapped development machine.
3. `scripts/verify_templates.py` exits 0.
4. `ruff check .` exits 0 at the repo root with `RUF100` selected — so no `noqa`
   marker anywhere outside the `exclude` list names a rule that is not enabled or
   does not fire.
5. Each new guard fails on every prohibited form via committed snippet tests, does
   not fail on the allowed forms, rejects a reasonless `# marker:`, and asserts
   its own scan is non-vacuous.
6. The backend suite passes on 3.11 and 3.14, and under `pydantic==1.10.*` with
   the runtime version assertion in place.
7. `CLAUDE.md` points at the make targets instead of restating the commands.
8. No test is skipped, `xfail`ed, or made `continue-on-error`, and no job's
   coverage is narrowed, to achieve any of the above.

Follow-ups recorded, not done here: widen ruff to the rest of `E7`; decide on a
formatter and `E501`; consider Windows in the matrix; consider a coverage gate;
consider a constraints file for CI reproducibility.
