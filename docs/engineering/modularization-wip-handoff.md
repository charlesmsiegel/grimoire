# Backend modularization handoff

**Branch:** `refactor/modularize-large-python`
**Status:** All 35 remaining oversized production modules are now packages; complete local backend, Pydantic-1, frontend and diagnostic gates pass. No PR, push or merge is authorized.
**Behavior reference for this phase:** `a7ebe3961`.

## Current scope

The earlier `routes.models` and `routes.campaigns` conversions are stabilized.
This phase converted another 35 subsystems through reviewed ownership maps.
There are now 732 production Python files, none above 1,000 lines; the largest
is 957. Large test modules and TypeScript components are separate work.

Read [backend-package-theory.md](backend-package-theory.md) for each of the
37 packages' responsibilities, extension points and state invariants. Read
[backend-package-validation.md](backend-package-validation.md) for current
contracts, diagnostic accounting, test evidence and platform limits. The
[spec](../superpowers/specs/2026-10-20-backend-package-modularization.md) and
[plan](../superpowers/plans/2026-10-20-backend-package-modularization.md) record
the reviewed scope and checkpoints.

## Compatibility model

Explicit facades preserve existing imports, intentionally exposed private
helpers, imported module objects, star surfaces and original logger labels.
Route leaves share a router; checked in-place final ordering preserves the
original route objects and overlapping URL behavior. The pre-extraction
fixture pins 4,487 names, 265 ordered routes and complete OpenAPI under both
Pydantic majors. Annotation metadata comes from the original source ASTs.
Two published description pointers intentionally follow their moved source
owners. A reviewed, exact-literal comparison retains the original whole-schema
hashes and rejects every other schema or description change; the published
JSON is not claimed to be byte-for-byte identical.

State has one owner. Consumers read its module when bindings can be replaced;
narrow facade getters preserve readable fork/probe/runtime values. There is
no assignment-forwarding module proxy. Internal test injections follow the
actual owner; external facade seams remain where production callers read them.
The approved campaign relationship-history patch remains at `_ledger`.

Guard adaptations preserve exact permissions and the original logical lock
classifications. New siblings do not inherit client, reader or writer access.
Adversarial controls cover unstable facade exports, proposal writes in new
streaming leaves and the former todo module's conservative method boundary.

## Completed validation

Ruff and mypy match all original 764 and 141 diagnostic identities; only their
file keys move. ESLint remains 307, preserving the earlier 330-to-307 cleanup.
Frontend typechecking and all 5,783 frontend tests pass; templates pass all
553 checks. Focused package and guard suites pass on both Pydantic majors.

The initial current-phase full suites each finished with twelve failures.
Do not call those runs green or substitute historical first-phase results.
Repairs already verified include stale source
filename references, a moved image-performance spy, an inert lock-stripe
fixture and a timestamp-sensitive Windows cache test, lock/bundle patches,
the sibling scanner's package context and the overview benchmark's state owners.
All initially affected selections now pass on both majors. Later attempts
exposed the published source pointers above and were interrupted after
diagnosis, without final summaries. The reviewed repair and four negative
controls pass with all 50 package contract cases on each major. Successful
complete runs are recorded in `build/final-check-py-3.log` and
`build/final-check-pydantic1-4.log`, with matching JUnit reports. The primary
run has 23,141 passed, 46 skipped and 94.19% coverage against the unchanged
93% floor. Pydantic 1 has 23,135 passed and 52 skipped. Both exited 0, and the
isolated environment still passes `pip check`. All 50 contract cases and 63
documentation guards also passed inside both complete runs.

The unchanged module-reference guard originally passed after the prose fixes
but spent nearly six minutes descending excluded temporary/dependency trees.
Its reviewed traversal repair preserves the exact source census while pruning
those same excluded trees first; its negative and inclusion controls pass on
both environments.

Make is unavailable: execute its exact recipes with the pinned development
Python, and put this worktree's `backend/src` first on `PYTHONPATH`. Use a
fresh isolated Python 3.12/Pydantic-1 environment with the Makefile dependency
constraints. Python 3.11 grammar checks pass; a Python 3.11 runtime, POSIX fork
execution and Android APK build are not locally established.

## History and constraints

The first phase's evidence is preserved in
[modularization-validation.md](modularization-validation.md). Its hotspot list
is historical; those production conversions have now been performed.
Temporary source snapshots, generators, review probes and local run artifacts
stay under ignored `build/`, outside committed production code.

Markdown/frontmatter and human-editable JSON remain authoritative; derived
SQLite is disposable. Preserve copy-on-write, provenance, atomic publication,
locking, cancellation, recovery and mechanics-free roleplay. No live library
or frozen campaign home is used. Existing unreviewed lock classifications and
filesystem limitations are preserved, not silently certified by extraction.
Mechanics II, coding-skills calibration and the proposed B+ gate remain deferred.
