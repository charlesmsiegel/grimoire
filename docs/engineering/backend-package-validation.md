# Backend package conversion evidence

This is the second modularization phase, against `a7ebe3961`. That reference
already stabilized `routes.models` and `routes.campaigns`; their earlier results
remain in [modularization-validation.md](modularization-validation.md). This
phase converts the remaining 35 production Python files above 1,000 lines.
No private library, frozen campaign home, runtime dependency, frontend source,
lint rule, coverage threshold or Android packaging configuration was changed.

## Responsibilities and interfaces

The [package theory notes](backend-package-theory.md) cover all 37 converted
subsystems, including the two earlier route packages. They state each package's
problem, responsibility boundary, extension point and invariant to preserve.

The 35 additional packages are:

- Gateway: `inference`, `decisions`, `llm`, `tool_calls`.
- Routes: `scenes`, `character_turns`, `runs`, `common`, `streaming`, `config`,
  `characters`, `ledger`, `todo`, `tracker`, `worlds`.
- Stores: `overlay`, `compiled`, `usage`, `cache_sync`, `locks`, `logs`,
  `world_bundle`, `assets`, `characters`, `suggest`, `sync`, `image_migration`,
  `image_gc`.
- Nested stores: `context.assemble`, `context.epistemic`,
  `continuity.reconcile`, `inference.resolve`, `inference.migrate`,
  `inference.legacy_plan`, `absorb.materializer`.

The resulting production tree has **732 Python files, none above 1,000 lines**.
Its largest file is the existing `routes/continuity.py`, at 957 physical lines.
`compiled.Batch`, `inference._Loop` and `runs.RunRegistry` remain whole cohesive
units, rather than being divided into arbitrary fragments.

Characterization was committed before extraction. The fixture pins 4,487
exposed bindings across the 35 old modules, including intentionally exposed
private helpers, imported module objects, star-import surfaces, logger names,
265 ordered routes, dependency annotations and complete application OpenAPI
under each Pydantic major. Deferred module annotation dictionaries were later
added from the saved **original** source ASTs; the original fixture fields
remain unchanged. The reference's 46 contract cases passed on both runtimes
before extraction, and the split packages still satisfy those contracts.

Facades use explicit aliases and exact `__all__` lists. Route leaves share one
router; packages with interleaved domains finalize its original ordered keys
in place, retaining route objects and rejecting missing, extra or duplicate
keys. These are eager imports, without a deferred registry or import cycle.

## Compatibility repairs

Import compatibility does not redirect assignment to a function's globals.
Internal failure-injection tests now patch the defining leaf, preserving their
assertions and existing LLM fakes. Shared module objects and mutable containers
retain identity. Production readers use the owner module when a value may be
replaced, including compiled sessions/fork state, lock registries, log guards,
the embedding probe client and canonical timer settings. Narrow read-only
facade getters preserve readable bindings after replacement; no module-wide
assignment proxy was introduced.

Logical logger names and persisted module labels retain their original values.
Runtime function annotations resolve as before under both Pydantic versions,
including the original Python 3.12 limitation on bare `Final` module hints.
Asset overloads and multi-name image-migration assignments were moved intact.
The optional SQLite and ctypes paths retain their single owners and fallbacks.

The complete runs also exposed 80 stale source references to removed monolith
filenames. Those comments/docstrings now name the actual packages or owners;
all 64 touched files retain their executable tokens, normalized executable
ASTs, physical line counts, line endings and issue context. The unchanged
module-reference check passed on both runtimes after those repairs. Its later
reviewed traversal change prunes the exact already-excluded directories before
walking them, preserving its basename census and source-checkout precondition.
Inclusion and forbidden-traversal controls pass on both runtimes.

FastAPI publishes two of those docstrings in OpenAPI. Their descriptions now
point to `routes/ledger/_policy.py` and `store/locks/__init__.py`, rather than
the removed source files. Independent comparison against both original full
schemas found exactly these two literal changes, with every other field
unchanged. The reviewed spec amendment keeps the original fixture hashes:
tests require the new pointers at those exact operations, reverse only those
two spellings on a copy, then check the entire original schema hash. Four
negative controls reject an extra endpoint, a changed parameter, unrelated
prose inside an allowed description and restoration of an old pointer. All
50 contract tests pass on each major. This is an intentional description
metadata correction; the published schema is not byte-for-byte unchanged.

An image-performance spy and the deterministic art-recovery stripe fixture
also needed their defining owners. Their original assertions remain. One
existing Windows cache-hit test could intermittently read again because
Python 3.12's wall clock lagged native file timestamps despite a zero racy
window. A coarse-clock control reproduced the failure; that single test now
places its clock after completed writes. Production stamp validation and
cache behavior are unchanged.

Further full-suite failures identified lock-timeout and bundle-export limit
patches at moved owners, plus spies that must observe both external facade
calls and internal whole-set lock acquisition. The same seven lock/bundle
assertions now pass on both majors, including the unchanged external import
cap. The sibling-import scanner's top-level package context no longer has a
trailing dot. The overview benchmark now shares and restores the actual kind
registry and injects the filesystem clock at its owner. All original draw
and benchmark counter assertions remain; 174 affected cases pass on each
major, with added relative-import and registry/cleanup controls.

Architecture checks now inspect the relocated source and all relevant package
leaves. Permissions remain exact: a new sibling does not inherit overview,
legacy-reader, overlay-resolution, knowledge-write or client-call authority.
The lock-domain adapter retains the original 18 logical registrations and
their original writer classifications, including the existing unreviewed
backlog. Moving a function does not certify its locking.

Final adversarial review exposed three gaps in the new guard adaptations:
facade imports shadowed by class/match/conditional-import bindings, proposal
writes hidden in another streaming leaf, and todo context methods hidden
behind moved class owners. Negative controls first reproduced each miss.
Repairs reject unstable exports, scan the entire streaming package and retain
the old conservative method-call boundary across the former todo module.
The latter is a call-graph boundary, not additional import permission.
Independent scratch-source probes confirm both forbidden and harmless controls.

## Review and diagnostic accounting

The spec, plan, ownership maps, implementation waves and final spec-versus-diff
checks have independent reviews. Cross-author AST/global-binding audits cover
all 35 originals, including source comments, decorators, defaults, annotations,
mutable identity and whole lock/cleanup spans. Per-package deterministic
code-doctor reports informed those maps. An optional broad final doctor pass
did not finish and is not reported as a clean audit.

The final specification checkpoint approved the frozen implementation at
`b722faf51` and these delivery documents after independently parsing the actual
complete logs, both JUnit reports and the coverage XML. Both suites collected
the same 23,187 test identities, with zero failures/errors and all package
contract/documentation cases passing. Local review evidence remains under
ignored `build/`, including `final-spec-review.md` and
`final-review-gate-evidence.json`.

| Tool | Phase reference | Current | Change |
| --- | ---: | ---: | --- |
| Ruff 0.16.0 | 764 | 764 | Existing findings relocated only |
| mypy 2.3.0 | 141 | 141 | Existing findings relocated only |
| ESLint, committed lockfile | 307 | 307 | No frontend source change |

An independent diagnostic comparison matches original module/definition,
rule and message, rather than totals alone: zero added and zero removed Ruff
or mypy diagnostic identities. Ratchet baseline edits deliberately rekey the
moved `(file, rule)` entries. The preceding safe ESLint reduction, 330 to 307,
is retained. The first phase removed the generated WIP's artificial Ruff
increase from 1,770 back to 764.

A token/AST audit also preserves all 272 lint/type/coverage/format suppression
comments, 39 guard permission markers and 64 skip/skipif/xfail expressions in
the changed source: no new suppression or skipped test was used to hide a
regression. A new late-import suppression found during review was removed by
moving that standard-library import to the normal header.

## Verification environment and results

GNU Make is unavailable. The Makefile's recipes are executed directly with
their pinned tools, four xdist workers, `load` scheduling, full branch coverage
and the unchanged 93% backend floor. This worktree's `backend/src` is first on
`PYTHONPATH`, including subprocess tests; an editable installation alone would
otherwise select the main checkout. Each run has its own temporary/cache
directories and the filesystem access required by the existing Windows lock
and subprocess tests.

The primary interpreter is the configured Python 3.14.5 development environment
with Pydantic 2.13.4 and FastAPI 0.137.1. A fresh disposable Python 3.12
environment installs exactly the Pydantic-1 Makefile recipe: editable
`backend[dev]`, `pydantic==1.10.*`, `fastapi>=0.110,<0.116`. Its resolved versions
are Pydantic 1.10.26 and FastAPI 0.115.14; `pip check` passes. Frontend checks
use `npm ci` and the committed lockfile.

| Check | Current-phase evidence |
| --- | --- |
| Python 3.11 grammar | All 732 production files parse |
| Ruff and mypy ratchets | Passed, 764 / 141 at baseline |
| Templates | Final rerun passed all 553 checks |
| ESLint ratchet | Passed, 307 at baseline |
| Frontend typecheck | Passed |
| Frontend coverage | 260 files / 5,783 tests passed; lines 92.37%, statements 89.69%, functions 85.63%, branches 85.23% |
| Full backend coverage | Passed: 23,141 passed, 46 skipped, exit 0; 94.19% coverage exceeds the unchanged 93% floor; 2,605.69s |
| Full Pydantic-1 suite | Passed: 23,135 passed, 52 skipped, exit 0; 2,348.84s in the isolated recipe environment |
| Final package contracts and docs | All 50 package contracts and 63 documentation guards passed on each major, both independently and inside the complete runs |
| Final strengthened guard follow-ups | 274 passed under each major; final overview/docs/proposal follow-up 82 passed under each major |

The initial suites exposed twelve integration failures each: 23,107 passed
and 46 skipped on the primary runtime, 23,101 passed and 52 skipped on
Pydantic 1; measured coverage was 94.19%. Subsequent attempts exposed the
two published description pointers and were interrupted after diagnosis,
without final summaries. Those attempts are failed evidence, not successful
gates. The successful complete runs include the owner/guard repairs and the
reviewed description comparison. Their logs and JUnit reports are
`build/final-check-py-3.log` / `.xml` and
`build/final-check-pydantic1-4.log` / `.xml`; coverage is in
`backend/coverage.xml`. The isolated environment also passes `pip check` after
its complete run. No production executable code changed during validation;
the prose reference repairs preserve every physical line count.

## Limits and deferred work

Python 3.11 is not installed locally: grammar checks are evidence about syntax,
not execution on that interpreter. Android source/dependency/manifest guards
run locally; an APK build and the CI Python 3.11 matrix do not. Windows skips
cannot establish POSIX fork behavior. These are platform limits, not claims
that every CI job has passed.

There are no remaining production Python files above the requested size goal.
Large tests and TypeScript components remain separate work. Existing function
complexity, unreviewed lock classifications, optimistic filesystem limitations
and concurrency/recovery policies were preserved for future focused changes.
Mechanics II, calibration of coding-skills and the proposed B+ gate remain
deferred. No PR, push or merge is authorized or performed.
