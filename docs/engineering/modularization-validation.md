# Route package conversion evidence

This is the historical first-phase record. The subsequent 35 conversions and
current gate results are recorded in
[backend-package-validation.md](backend-package-validation.md); the hotspot
list below describes the first phase's reference tree, not remaining work.

This records stabilization of the WIP at `e5f3a1d7b`. The behavior reference is
`d5c4d6777`, where the original `routes/models.py` and `routes/campaigns.py`
still exist. No private library was used.

## Ownership and theory

`routes.models` owns wire request shapes, not persistence or policy. The seven
leaves group settings, world/campaign creation, mechanics, actors, scenes,
continuity, and the remaining small request bodies. The facade keeps the same
class objects available through the old imports. Runtime annotations remain
resolvable on Pydantic 1 and 2; defaults remain Pydantic fields.

`routes.campaigns` is one ordered HTTP surface over existing store operations.
Its leaves own configuration/clock, events/notices, creation/export, library
art, management, incoming changes/journal, ledger, timeline/pins, characters,
and PC revisions/art. `_router` owns the only router and `_names` the shared
source-aware naming helper. Registration order is part of dispatch behavior,
so sorting is limited to each registration group. New endpoints must consider
crossing patterns before changing that order.

Neither package adds a lock domain or another source of truth. Store modules
still own copy-on-write, provenance, authoritative files, atomic publication,
recovery and derived caches. A future operation belongs beside its domain;
repeating the store's rules in a route leaf would create competing authority.

Watch: facade aliases preserve imports and object identity, not assignment to
the defining module's globals. The owner approved updating the one existing
scalar test patch, `RELATIONSHIP_HISTORY_PAGE`, to `campaigns._ledger`.
Readable access through the facade remains. Shared store-module patches retain
their original identities. No custom module proxy was introduced.

## Characterization and review

- All 156 model definitions and 126 campaign definitions match the original
  ASTs. Every original comment remains present in the packages.
- All former bindings remain importable, including private helpers and the
  campaign module's formerly imported dependencies. Its star-import surface
  remains unchanged.
- The complete ordered table of 106 campaign routes and campaign OpenAPI match
  the originals under Pydantic 2.13.4 and 1.10.26. The model schema fixture was
  captured independently from the original source under each environment.
- The overview guard permits the moved campaign-list reader in `_settings`;
  a planted sibling import still fails. The old facade's compatibility export
  remains permitted. No wildcard permission was added.
- Independent Codex spec, plan, implementation and final spec-versus-diff
  checkpoints found no blocking implementation or specification gaps in this
  batch. The final checkpoint verified both complete-suite results and the
  documented limits before approval.

## Diagnostic accounting

| Tool | WIP at entry | Stabilized | Original baseline |
|---|---:|---:|---:|
| Ruff 0.16.0 | 1,770 | 764 | 764 |
| mypy 2.3.0 | 141 | 141 | 141 |
| ESLint, committed lockfile | 307 | 307 | 307 |

The artificial Ruff increase comprised 987 unused imports and 19 import-order
findings. Cleanup protects exports before removing imports. Baseline changes
only move one `RUF003` and two `union-attr` findings from `campaigns.py` to
`campaigns/_ledger.py`. The comparison against original source matched rule,
message, owning symbol and source line, not just totals. No lint configuration
was changed. The preceding ESLint cleanup remains 330 to 307.

## Verification environment

The shell's default Python is 3.10 and is unsuitable. Backend checks use the
main checkout's configured Python 3.14 venv, with this worktree's `backend/src`
first on `PYTHONPATH`. The Android dependency check uses a disposable venv
under `build/`, created with installed Python 3.12 because Pydantic 1 does not
support Python 3.14, and the Makefile's declared dependency constraints.

GNU Make is absent. Its check recipes are run directly with the same coverage,
worker, pin and threshold settings; this is not a claim that `make check` itself
ran. Downloading an unverified Make executable was rejected by automatic
approval review and was not attempted through another route. Tests use owned
temporary directories; the template checker also uses an isolated user home
for machine-local locks. Frontend coverage needed the filesystem sandbox lifted
for native esbuild to read the coverage harness's temporary configuration.

The initial full runs exposed stale module references in two production
docstrings and subprocess access failures in the filesystem sandbox. Child
pytest processes deliberately discard inherited pytest settings and use their
normal temporary directory; the sandbox denied access there and to a
machine-local lock. The affected CLI, parallel-harness and template tests pass
outside that sandbox (13 tests). No harness isolation rule was removed.

Two test portability assumptions also needed correction. Windows `fsutil` can
report success while printing access denied, so the native-identity fixture
now proves case distinction with a disposable file before proceeding. All six
affected identity cases execute and pass with the required filesystem access;
they skip when the capability is absent. Python 3.12.0 retains `cr_frame` on a
closed, unstarted coroutine, so the deadline test now proves it cannot resume
and that no second request starts. Production store and deadline code remain
unchanged. Those seven cases plus all 161 package contracts pass together
under Pydantic 1 (168 passed, no skips).

| Check | Evidence |
|---|---|
| Targeted v2 contracts, routes and guards | 322 passed |
| Targeted Pydantic-1 contracts and route order | 179 passed |
| Python 3.11 syntax | all 21 package modules passed |
| Ruff ratchet | 764 findings, all at baseline |
| mypy ratchet | 141 findings, all at baseline |
| Templates | 553 checks passed |
| ESLint ratchet | 307 findings, all at baseline |
| Frontend typecheck | passed |
| Frontend coverage | 260 files, 5,783 tests passed; lines 92.37%, statements 89.69%, functions 85.63%, branches 85.23% |
| Full backend coverage | 22,809 passed, 46 skipped, exit 0; 93.87% coverage against the 93% floor (35m 25s) |
| Full Pydantic-1 suite | 22,803 passed, 52 skipped, exit 0 (31m 32s) |

Python 3.11 is not installed locally. The changed production code adds no new
syntax requirement; the full Pydantic-1 run uses Python 3.12 and the primary
run uses Python 3.14. The CI Python 3.11 matrix and APK build have not been run
locally. The Android source/dependency/manifest guards run in the backend suite.

## Remaining production hotspots

These are repository source observations, not a proposed mechanical split.

| Module | Lines | Responsibility and boundary that must be recovered |
|---|---:|---|
| `routes/scenes.py` | 6,279 | Scene editing, detached turns, end-of-scene review, follow-ups and replay are separate lifecycles. Identity fences, review watermarks and the shared rolling-inflight set/lock cannot be duplicated. |
| `store/overlay.py` | 3,355 | Flat records, whole actors and per-file sidecars inherit differently. Strict identity ledgers and world/campaign provenance must precede associated effects. |
| `store/compiled.py` | 3,252 | File-derived keys select disposable SQLite artifacts. Database ownership, process/fork state, root pinning, delete intents and batch lifetime need one owner each. |
| `routes/character_turns.py` | 2,984 | One actor contributes per step in a bounded round. Speaker selection, stored plans, rerolls and continuations share snapshots and roll-resume rules. |
| `inference.py` | 2,907 | Generation, staged decisions and bounded tool loops are distinct operations. Failure advances stages; an answer does not. Backend dispatch and the loop weak-set must retain identity. |
| `routes/runs.py` | 2,724 | Subject and exclusion class are independent axes. Per-app registry data, transport and execution adapters are plausible boundaries, but cancellation and maintenance lock ownership cross them. |
| `store/context/assemble.py` | 2,430 | Gather once, freeze rendering once, then pack model variants. The section catalog and prompt/log projection must stay one model; splitting must not reroll macros or leak reader-specific history. |

The next batch needs its own reviewed spec/plan and characterization of those
boundaries. This batch finishes the two existing package conversions and the
hotspot inspection; further production conversions remain outstanding.
Mechanics II and the proposed future B+ gate remain outside this work.

Concrete preparation for that batch:

- Inference's generation and tool loop both call the structured resend helpers
  and share attempt accounting. Decision cost projection also uses tool-loop
  pricing helpers. The routing and usage guards currently name `inference.py`
  explicitly; any extraction must preserve their negative controls. Tests patch
  `reports_distribution` and `cap_sent` through the existing module, so moving
  either reader needs an explicit ownership decision first.
- Run registry methods call the maintenance-lock helpers declared later in the
  file. Transport and execution adapters depend on the same `Run` identities;
  tests replace cancellation timeouts and reservation functions through the
  current module. Characterize cancellation before start, producer unwind,
  maintenance exclusion and replay cursors before moving those responsibilities.
- Context assembly's reader calls `_check_view`, while public composition
  calls the later rendering/packing pipeline. Tests replace `SECTIONS` and
  `_assemble` through the module. A future section/render split must first pin
  catalog replacement, macro expansion count and frozen history reuse across
  model variants.
- Compiled-cache fork hooks rebind `_state`; importing that value into another
  leaf would retain stale locks and connections after a fork. Its injectable
  clocks/timers and SQLite-unavailable path need the same care. The compiled
  guard names one SQLite owner and inspects its bounded queries; a package
  conversion must retain one exact owner and apply those checks to its new
  location with forbidden-sibling controls.
- Overlay remains explicitly classified in the lock guard's frozen
  `UNREVIEWED` backlog, despite its locked identity-ledger operations. Splitting
  readers from writers must not silently classify all resulting leaves as
  reviewed or create new bypasses. Preserve each writer's current locking and
  preflight behavior before considering a separate locking improvement.
