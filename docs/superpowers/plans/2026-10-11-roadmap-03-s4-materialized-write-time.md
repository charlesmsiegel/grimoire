# Roadmap 03 S4 Materialized History and Write-Time Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task. Native execution, all slices, liberal commits and independent brutal-review loops are explicitly authorized by the human.

**Goal:** Store exact-byte artifacts immediately after writes, and record each path/kind/instance whose work was materialized.

**Architecture:** Add a disposable metadata table in compiled.py and connect it to the same validation/key pipeline. Byte-fed derivations validate before lookup and revalidate with bytes on misses. Materialized history describes work; it never establishes file membership or changes a computed answer.

**Tech Stack:** Existing Python dataclasses, canonical bounded JSON, optional SQLite and pytest/AST guards.

**Spec:** [Roadmap03 design](../specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md), sections2a,4,6,9,14 and03-S4. Requires reviewed S2; uses S3's collection interfaces when that slice is available.

## Global Constraints

- Keys cover exact input bytes, ordered roles, kind version, BUILD and every declared param. An absent-ok role uses ABSENT.
- Source rows still wait for the strict pre-stat filesystem window. A writer's known bytes can key an artifact immediately without recording its fresh source stamp.
- Every stored artifact leaves materialized rows for all paths it consumed. If the bounded staging budget cannot admit the whole family, skip that artifact; never store a partially described family. Source-only staging may be discarded to admit a complete artifact/materialized family.
- Materialized primary key is `(path,kind,instance)`. Instance is canonical JSON or empty string. `built_from` is the exact file hash or empty/unknown; empty is never current. Rows may have no artifact.
- Materialized checks bind path/kind/instance/built_from; malformed rows are optional misses. Runtime SQL stays inside compiled.py, nonblocking for reads and outside parsing/callback execution.
- `space_digest(space)` is SHA-256 of the exact UTF-8 space string, first16 hex characters. Vector kinds are `vector:<projection>:<space-digest>`; raw space IDs never enter them and vector files retain today's keys without BUILD.
- Preserve source/lifecycle/ownership/containment limits and caller exceptions. Only temporary placeholders; frozen fixture bytes unchanged.

## Review Focus

1. A source changes between the first validation and a byte read: recompute the artifact key from the returned bytes (Task1).
2. A large collection exhausts staging before its artifact: admit all metadata together or skip the artifact; existing optional source staging must not monopolize the family budget (Task2).
3. Several vector units have nonconsecutive passage selectors: rename-copy must preserve each independently (Task3).
4. A materialized table is missing or damaged: the normal computed value is still returned (Task2).
5. A stale source stamp is reused at write time: fresh bytes may store an artifact but cannot create/update a sources row (Task1).

## File Ownership and Interfaces

Extend compiled.py; create test_compiled_derive.py and test_compiled_materialized.py; extend vectors.py only for optional source-history context; extend test_compiled_guard.py. No SQL owner is split.

Public additions:

```python
@dataclass(frozen=True)
class Materialized:
    path: Path
    kind: str
    instance: str
    built_from: str
    last_used: int

@dataclass(frozen=True)
class Uncached:
    value: object

@dataclass(frozen=True)
class WrittenInput:
    role: str
    path: Path
    data: bytes

@dataclass(frozen=True)
class ComputeInput:
    role: str
    content_hash: str
    data: bytes | None
    path_parts: tuple[str, ...]  # only declared relative parts, never a Path

@dataclass(frozen=True)
class ComputeCollection:
    role: str
    content_hash: str
    members: tuple[ComputeInput, ...]

@dataclass(frozen=True)
class DerivationInputs:
    files: tuple[ComputeInput, ...]
    collections: tuple[ComputeCollection, ...]

ValidatedInput.materialized: tuple[Materialized, ...] = ()
Batch.materialized(paths: Sequence[Path]) -> dict[Path, tuple[Materialized, ...]]
Batch.mark_materialized(path: Path, kind: str, *, instance: object = None,
                        built_from: str = "") -> None
Batch.derive(kind: Kind, inputs: Sequence[Input], *, params: Mapping[str, object],
             compute: Callable[[DerivationInputs, Mapping[str, object]], object],
             collections: Sequence[Collection] = (), instance: object = None) -> object
Batch.store_written(kind: Kind, inputs: Sequence[WrittenInput], value: object, *,
                    params: Mapping[str, object], instance: object = None) -> Key | None
Batch.copy_materialized(old: Path, new: Path, *,
                        instance_map: Callable[[str, str], object] | None = None) -> None
space_digest(space: str) -> str
vectors.load(space: str, texts: list[str], *, sources: Mapping[str, WrittenInput] | None = None,
             projection: str = "body", instances: Mapping[str, object] | None = None) -> dict[str, list[float]]
vectors.save(space: str, text: str, vector: list[float], *, source: WrittenInput | None = None,
             projection: str = "body", instance: object = None) -> None
```

`derive` first validates inputs and collection membership, constructs the complete key and looks it up. On a miss it requests bytes, recomputes key/collection digests from those bytes, constructs compute-only immutable byte/role/declared-relative-parts views without Path/stamp/history, then calls compute outside SQL locks and stages the artifact plus all path history. Missing required bytes abort before compute. Compute may return Uncached(value), which returns value without staging an artifact or its history; S6 uses this for heuristic token counts. Cold validation retains bytes to avoid a redundant read when its exact validated set can be reused; a trusted-source miss requests bytes. `store_written` hashes supplied bytes without reopening sources, constructs the same key, and stages a complete family. It never stages a source. Empty/non-file inputs remain expressible through declared params.

`validate` also selects materialized rows for its supplied paths with a bounded128-slot query, exposing them through the defaulted field without changing tuple/order behavior. Materialized reads take caller paths, including an explicitly stated old rename path that no longer exists. They never discover inventory.

Increase SCHEMA to3 and create materialized with fields above, check, plus optional artifact_kind/artifact_key association for coordinated eviction. No foreign key: vector/rename rows may be independent. A newer stored version replaces only its `(path,kind,instance)` row and association. Coarse touch staging is implemented here; S5 enforces the once-per-day policy for all tables. Reads with a missing materialized table still use valid artifacts or compute live; optional metadata failure cannot erase a successful decoded answer.

## Task 1: Byte-Fed Derivation and Write-Time Storage

**Files:** compiled.py; test_compiled_derive.py.
**Interfaces:** WrittenInput, DerivationInputs, derive and store_written above.

- [x] Write failing tests for cold/warm/disabled/restart parity, absent roles, composite collection/non-file params, compute called once on misses, Uncached(value) returning the value without storing it, and no compute on unavailable input. Assert compute runs outside the connection lock and caller exceptions discard staging.
- [x] Assert compute receives no Path or stat/history handles, including collection members, and a rewrite after byte validation cannot cause compute to reopen a different version. Plant an edit between trusted hash lookup and byte read; require the returned byte hash/key to select the newly computed value. Test identical bytes at different stems with declared path params, changed BUILD/version, and supplied write bytes hit by the next read while fresh sources remain absent.
- [x] Establish RED with `... -m pytest backend/tests/test_compiled_derive.py -q`.
- [x] Implement one derivation pipeline using existing validation and make_key, preserving retained cold bytes and re-keying byte reads. Expose immediate write storage as a pure known-byte entry; register no production consumers yet.
- [x] Run derivation/source/collection/core tests and ruff; commit a passing checkpoint.

## Task 2: Materialized Rows, Validation Extension and Touches

**Files:** compiled.py; test_compiled_materialized.py; test_compiled_guard.py.
**Interfaces:** Materialized, ValidatedInput.materialized, bounded materialized lookup and mark/touch staging.

- [x] Write failing tests: every derived/stored artifact has one row per consumed path; two instances of one kind coexist; built_from matches exact source bytes; validation returns only supplied paths' rows; unknown/no-artifact rows remain valid; hits touch the matching instances; corrupted checks/oversized fields are rejected without excessive transfer.
- [x] Test dropping the metadata table changes no derived answer, and more path rows than fit cause a whole-family opt-out with no partially described artifact. Fill the source staging budget first and prove a fitting artifact family can still land by discarding optional source rows.
- [x] Establish RED. Implement SCHEMA3/table initialization, checksums, SQL-side field bounds, bounded selections, defaulted validation field and coordinated staging. Keep metadata encoding/decoding outside the connection lock. No cache inventory method is introduced.
- [x] Add derive to every guarded decision-source target and explicit-false policy check; plant direct, aliased, forwarded and proven-False decision calls. Extend the AST guard for supplied-path materialized selection and prohibit public metadata readers without paths; keep mixed artifact/source key requirements independent.
- [x] Run materialized/derive/source/bulk/guard/lifecycle tests and ruff; commit.

## Task 3: Rename Copies and Vector History

**Files:** compiled.py; vectors.py; test_compiled_materialized.py; existing vector tests.
**Interfaces:** space_digest, copy_materialized and optional vector load/store source-history context.

- [x] Write failing tests with three nonconsecutive passage selectors. Copy old rows to new with default identity mapping and a path-context mapper; retain selectors/kinds, reset built_from, copy no artifact/vector. Canonical instance equality must not collapse independent units.
- [x] Test exact space strings including NUL and distinct projections; assert vector file keys and fixture bytes remain unchanged when BUILD changes. Add a vector load-hit test that touches its supplied source/projection/instance row.
- [x] Establish RED. Implement mapper calls outside SQL locks. Add the declared defaulted keyword-only context to existing vectors.load/save; load takes text-to-known-source-bytes and text-to-instance mappings, save takes one known source and instance. Hash supplied bytes for built_from, without trusting a caller-provided hash. Delegate source/projection/instance history to compiled, delegating metadata to compiled while leaving existing callers and vector key derivation intact. A caller with no source context records none; later05/08 hooks supply it. Existing history rows are not inferred by enumerating vectors.
- [x] Run vector/materialized/derive/import tests in both Pydantic environments and ruff; commit.

## Task 4: Slice Verification and Review

**Files:** this plan; ROADMAP-CHECKLIST.md; CONTRIBUTING.md where new guard behavior needs documentation.

- [ ] Run focused S4/core/source/collection/bulk/guard/API tests under both dependency sets and verify frozen home/snapshot unchanged. Ratcheted findings must not grow; final whole-spec verification covers full suites and APK after all remaining source edits.
- [ ] Complete independent brutal-review/fix/re-review until non-blocking. Resolve mandatory native Codex diff review and original-spec fidelity findings before marking S4 done. Evidence C3/C4/C5 only when their full delivered interfaces are tested; commit documentation.

## Self-review and Authorization

Tasks1-3 cover write-time keys, complete path history, independent vector units, rename mapping, hits and optional-table failure. Task4 owns review gates. S5 supplies global LRU/leases/purge, and S6 supplies actual consumers and measurements. The human's Native/all-slices authorization replaces an additional handoff question; this plan still requires its Codex plan gate before implementation.


Self-review addition: an explicit Uncached result makes section6's post-compute persistence opt-out expressible. No opaque caller flag may persist a heuristic as an encoder result. This addition requires the S4 plan-gate follow-up if the initial gate reviewed the earlier snapshot.


Codex plan gate01a12d55 required compute-only views (section6) and derive decision-path protection. Both are now explicit interfaces and planted regressions; validated Paths remain internal to staging/history. S4 still requires its follow-up plan gate.


Tasks1-2 execution checkpoint: derivation12 RED, history13 RED, S4 guard6 RED2 prior passes; same pipeline now supplies byte/role/hash views without paths, rekeys trusted misses after bytes, preserves cold bytes, supports explicit Uncached values and immediate known-byte storage without source stamps. Schema3 adds bounded caller-path metadata with canonical instances, checksums and optional artifact association. Full history family admitted atomically with an artifact or skipped; optional sources yield budget to fitting families. Rejected-field warning1 RED then fixed; low-level point/bulk touch2 RED then fixed through indexed associations. A touch-time root change2 RED then fixed by final lifecycle rechecks. Touches share the128-row/4MiB staging budget. Cache foundation434 passed7 platform skips before the final fence controls; current derive/history/bulk/guard215 passed; Ruff clean, compiled Linux mypy clean. Initial5 mypy findings corrected; bulk complexity fixed by naming hit-touch staging. Task3 rename/vector and Task4 final dual-env/review gates remain pending.

Ruling: file view path_parts is empty and path-derived context comes through declared params; collection view relative parts are covered by its digest. Several byte snapshots of one path leave unknown built_from rather than falsely declare one version current. Daily touch policy for every table and global retention/eviction remain S5 work.


Task3 execution: rename/vector8 failed RED,1 prior no-context control passed. copy_materialized preserves canonical instance text without double encoding, resets built_from/association and copies no vectors/artifacts. Exact UTF-8 space digest includes NUL; optional vector save/load context records source-byte hashes, projection/space kind and independent selectors. Existing vector keys, normalization/CRC bytes and callers remain intact. Native vector/history/guard234 passed; broader Py1 derive/history/vector/guard/import248 passed; focused compiled/vectors Linux mypy clean. Ruff1146 exact baseline (existing vectors.dot B905 remains unchanged). Direct/aliased/forwarded historical materialized reads at decision sites3 RED then guarded without a False-policy exemption: history is never current-byte proof.

Ruling: mapper canonical strings are retained exactly; structured JSON objects are canonicalized for the declared hook return type. Colliding selectors and oversized complete rename history opt out rather than partially collapse units; losing optional history costs eager warm-up only. Whole-artifact families still obey their all-or-none rule. Task4 final focused dual-env/ratchets/independent/native/fidelity gates remain pending; S5/S6 runtime not implemented yet.


Task4 review corrections: independent hostile review at368a5ff reproduced the history/touch-only put admission bypass; three budget regressions RED, unconditional admission GREEN. Native Codex01a12e10-04ab-7b41-b627-fadc2e401389 additionally found file `b'[]'` versus an empty collection collision and vector-history root resolution adopting a later lifecycle epoch: three RED, corrected category-separated key encoding and fenced default resolver. Ruling: make_key accepts an optional collections keyword and serializes file and collection groups separately; this preserves read/write-time file parity and prevents deterministic structural collisions. Fidelity01a12e10-3f57-7313-adec-5521be16d3d6 found absent-ok collection disappearance, captured module-control-flow helper definitions and unbounded per-path history transfer: six RED, three safe controls passed. Collection byte acquisition now requires listed members, the existing owned-scope walker retains all module definitions, and history transfers reject complete answers above128 rows or4MiB. Current affected suite318 passed1 platform skip; new code Ruff clean. Follow-up independent/native/fidelity reviews and Py1 verification required before slice completion.

Whole-spec implementation closure: this slice's runtime and contract tests are delivered in the shared all03 branch. Fresh cache/source/derive/collection/history/vector/guard/lifecycle/consumer/import/docs matrix917native/911Py1 passed with6/12platformskips; known unchanged Windows CPU-clock test was separately reproduced atbase and excluded. Final mandatory whole-branch native and original-spec followups remain open; their final verdicts and accurate partial full-gate record will be attached before marking review/final-gate columns.
