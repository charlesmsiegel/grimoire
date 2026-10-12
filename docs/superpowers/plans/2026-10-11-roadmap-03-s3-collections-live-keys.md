# Roadmap 03 S3 Collections and Live-Key Lookup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. The human chose Native execution and explicitly authorized all slices and independent brutal-review loops.

**Goal:** Deliver collection-keyed derivations and bounded bulk lookups over keys supplied from the live filesystem.

**Architecture:** Extend the existing validation pipeline rather than introduce another invalidation rule. Directories establish membership on every collection request; the registry declares the member filter. SQLite receives only the caller's complete keys and returns per-kind counts.

**Tech Stack:** Python dataclasses, pathlib/os, optional standard-library SQLite; existing pytest and AST guards.

**Spec:** [Roadmap 03 design](../specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md), sections 2a, 7, 9, 14 and 03-S3. S2 implementation and its review findings must be resolved before S3 code begins. Planning follows the already-approved whole-spec gate.

## Global Constraints

- Every collection lists its directory live. Neither sources nor artifacts discovers membership.
- Hash the canonical, sorted `(store-relative-path, exact-byte-content-hash)` member list. Roles remain ordered in make_key, with non-file inputs in declared params.
- The member predicate is registry code: `Kind.member_filter: Callable[[str], bool] | None = None`. It receives the member's store-relative POSIX path. BUILD and kind version cover its identity. A predicate may inspect that path only; dependent inputs remain live or are named separately.
- Preserve the strict pre-stat five-second source window, device/link exclusions, exact bytes, abort-on-unavailable behavior and all S1 lifecycle/ownership bounds.
- SQLite reads remain nonblocking, off the event loop, and contain one bounded selection. Parsing/checksums stay outside the lock. Fixed chunks have128 placeholders, unused slots NULL.
- No FTS/index is built. The guard defines the boundary any future ranking query must obey.
- Only temporary placeholder data is used; commit each coherent code, test or documentation checkpoint.

## Review Focus

1. A listed member vanishes before validation: the entire batch must abort, not quietly turn it into an empty collection (Task1).
2. Review sidecars sit beside scene members: excluded files must not change the digest (Task1).
3. A member changes between enumeration and byte reading: retain its pre-read stamp and return bytes with their exact hash (Task1).
4. More than128 keys span several kinds: every requested unique key is accounted for, without selecting unrelated rows (Task2).
5. A ranking-shaped query constrains only its inner query or reverses its predicate: the existing lexical guard must reject it (Task3).

## File Ownership and Interfaces

Extend `backend/src/grimoire/store/compiled.py`; create `backend/tests/test_compiled_collections.py` and `backend/tests/test_compiled_many.py`; extend `backend/tests/test_compiled_guard.py` and CONTRIBUTING.md. Keep SQLite ownership in compiled.py.

Public interfaces:

```python
@dataclass(frozen=True)
class Collection:
    role: str
    path: Path
    recursive: bool = False

@dataclass(frozen=True)
class CollectionDigest:
    role: str
    content_hash: str
    members: tuple[ValidatedInput, ...]

@dataclass(frozen=True)
class Counts:
    hits: int
    misses: int

@dataclass(frozen=True)
class BatchLookup:
    values: dict[Key, Lookup]
    counts: dict[str, Counts]

Batch.collection(kind: Kind, collection: Collection, *, with_bytes: bool = False) -> CollectionDigest
Batch.lookup_many(keys: Sequence[Key]) -> BatchLookup
```

The collection's members preserve sorted relative-path order, retain cold bytes and use the same source-validation code as validate. Internal `_SourceInfo` gains regular-file classification from the same stat so directory entries are skipped without a second inconsistent source stat. Refactor byte/hash validation into one private helper taking already captured infos; the public file and collection forms both use it. Missing or unreadable directories and listed candidate members raise InputUnavailable and permanently clear staging. Ordinary directories are excluded; symlinked regular members remain readable but never source-trusted. Recursive traversal enumerates each directory live and never follows directory links. An outside-root collection cannot name store-relative membership and raises InputUnavailable.

Bulk lookup deduplicates equal keys, groups them by kind, and returns a miss for invalid/unregistered keys. Counts describe unique requested keys per kind. No keys are logged. Every value is freshly decoded; one corrupt row does not hide valid neighbors. SQL is a static128-slot `WHERE kind=? AND key_digest IN (...)` form, with payload/check fields gated before transfer to Python.

## Task 1: Filtered Collection Digests

**Files:** compiled.py; test_compiled_collections.py; existing source tests.
**Interfaces:** Collection, CollectionDigest, Kind.member_filter and Batch.collection above; reuse the single input-validation pipeline.

- [x] Write failing tests: empty directory has a stable digest; add/edit/delete/rename/rename-replace changes it; registry filter excludes review sidecars; non-file settings changes the composite key while unrelated files do not; ordering is independent of enumeration order. Test both close/reopen and same-process reads.
- [x] Write failure tests: listed member vanishes, directory listing is refused, or bytes fail after stat; earlier staged artifacts/sources are discarded even if caught. Include linked regular members, linked directories under recursive traversal, and duplicate content at different relative paths.
- [x] Run `build/venv-check/Scripts/python.exe -m pytest backend/tests/test_compiled_collections.py -q` to establish RED.
- [x] Implement the interfaces and shared validation helper. Capture the batch's filesystem clock before candidate stats; filter after enumeration; sort before canonical hashing. No directory stamp substitutes for a membership listing.
- [x] Run collection/source/lifecycle tests and configured ruff; commit a passing checkpoint.

## Task 2: Bounded Bulk Lookup and Per-Kind Counts

**Files:** compiled.py; test_compiled_many.py.
**Interfaces:** Counts, BatchLookup and Batch.lookup_many above; use make_key and existing decoder.

- [x] Write failing tests for mixed registered kinds, all-hit/all-miss/partial results, empty inputs, duplicate keys, invalid keys, mutation-isolated values, corrupted/oversized rows, disabled cache, contended locks and root changes during decode. Seed unrelated rows and assert none is returned.
- [x] Assert257 requested keys use three selection queries within one kind, fixed parameter padding, and exact per-kind counts; no input/key inventory SQL occurs. Verify a collection plus non-file params selects the expected artifact.
- [x] Establish RED with `... -m pytest backend/tests/test_compiled_many.py -q`.
- [x] Implement grouped fixed-slot selection with decoding outside the connection lock and post-decode lifecycle validation. A cache failure returns all requested keys as misses without changing authoritative computation.
- [x] Run bulk/collection/core/lifecycle tests and ruff; commit.

## Task 3: Live-Set Query Guard and Slice Gates

**Files:** test_compiled_guard.py; CONTRIBUTING.md; this plan; ROADMAP-CHECKLIST.md.
**Interfaces:** Permit only kind-bound key_digest point/IN selections for artifact reads. A future ranking read must take required caller keys and bind its selection to that set.

- [x] Plant failures for an unbounded ranking query, kind-only IN query, predicate reversal, nested selection, quoted/commented fake predicate, and a public ranking reader without keys. Pin the legitimate fixed128-slot bulk query. Establish RED before broadening the guard.
- [x] Extend the lexical allowlist and required-key API checks; add collection to the guarded decision-source primitives with the same explicit-false policy rule. Unknown cache/index SELECT shapes are rejected; explicitly permitted metadata probes remain separate. Preserve all prior planted regressions and state syntax/data-flow limits in CONTRIBUTING.md.
- [ ] Run core/source/collection/bulk/guard/import/API tests under both Pydantic environments, check ratcheted lint and Linux-platform mypy counters, and verify frozen fixtures untouched. Full backend/frontend/APK evidence is collected at final whole-spec verification after remaining additive slices; repeat earlier only for a new failure or concern.
- [ ] Run independent brutal-review/fix/re-review against committed S3 until non-blocking. Run the mandatory native Codex diff review and original-spec fidelity gate, resolving findings before marking S3 done. Update only evidenced C1/C6 contract checkboxes and commit documentation.

## Self-review and Execution Authorization

Tasks1-2 deliver every S3 interface and acceptance case; Task3 enforces future live-set ranking without building an index. Every Review Focus has an owning regression. S4 materialized rows, S5 retention/purge and S6 consumers remain separately planned units. Native execution and liberal commits persist from the user's explicit instructions; no further human handoff question is required. This plan still requires its mandatory Codex plan gate before implementation.


Task1 execution: collection contract suite14 failed RED for missing registry/filter/interfaces. Shared captured-info validation now serves files and collections; regular-file type comes from the same stat. Live enumeration precedes filtering, recursive directory links are excluded, contained members are sorted and canonical manifest bytes streamed into SHA-256. Added order-independence/filter-before-stat control. Collection/source/lifecycle118 passed,6 platform skips; configured ruff clean; focused Linux compiled.py mypy clean. Initial complexity finding resolved by separating enumeration from digest construction. No source fixture changes. S2 known guard findings fixed at1e254; completion gates will include those fixes before S3 signoff.


Task2 execution:12 RED bulk-interface tests; bulk/collection/core/lifecycle120 passed,5 platform skips. Fixed128-slot kind-bound key selection deduplicates keys, decodes one bounded transfer at a time outside the lock, and invalidates the whole result after lifecycle or query failure. Counts include every unique supplied key, including invalid keys as misses. Task3 lexical tests13 failed RED,8 prior rejection cases passed; a named collection policy additionally1 failed,1 passed RED. Kind-bound IN queries now permit at most128 keys, unknown SELECT tables are rejected, ranking/bulk APIs require keys, and collection source primitives require explicit False policies at decision sites. Bulk/collection/guard187 passed; ruff clean. Ruling: broaden the guard before committing Task2 so its new legitimate SQL never leaves a deliberately failing architecture checkpoint. Final focused dual-dependency, ratchets and independent/native/fidelity gates still pending.


S3 focused final checkpoint atfa3ea9706:393 passed,7 platform skips in each Pydantic environment; compiled owner90% branch coverage. Ruff1146 exact baseline; focused Linux compiled mypy clean; store facade/architecture/docs guards857 passed,2 skips; frozen fixture diff empty. Independent brutal-review non-blocking (build/reviews/s3-brutal-review.md),366 independent focused passes plus187 Py1 selection. Mandatory native thread01a12dbd-c03b-7a30-af5d-86b3db89230f and fidelity found entrypoint/reached helper own defaults were bound but not traversed. Both unsafe regressions failed RED while explicit-False helper control passed. Decision traversal now examines definition inputs in their enclosing scope before the body. Guard/bulk/collection190 passed; initial Ruff ISC003 explicit concatenation finding was missed in output, corrected in the next checkpoint, then configured Ruff verified clean. Narrow independent and mandatory followups required before S2/S3 completion. S4 tests are authorized and RED but no S4 runtime code at this checkpoint.


Native S3 own-default followup01a12dcf-fabb-79b3-bba2-2f618a58281f found no new code issue atda2c; independent190 passes/non-blocking. Fidelity followup found an earlier helper captured by a default was hidden by a later same-name definition. One unsafe regression failed RED, explicit-False control passed. Function traversal retains every definition for the qualified helper name, conservatively inspecting all bodies. Current guard/derive/history198 passed before further S4 touch tests; current coherent215 passes include those controls. S2/S3 final fidelity followup remains required; S4 implementation is a separate checkpoint in its approved plan.

Whole-spec implementation closure: this slice's runtime and contract tests are delivered in the shared all03 branch. Fresh cache/source/derive/collection/history/vector/guard/lifecycle/consumer/import/docs matrix917native/911Py1 passed with6/12platformskips; known unchanged Windows CPU-clock test was separately reproduced atbase and excluded. Final mandatory whole-branch native and original-spec followups remain open; their final verdicts and accurate partial full-gate record will be attached before marking review/final-gate columns.
