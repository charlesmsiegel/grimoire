# Roadmap 03 S2 Source Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The human chose Native execution; preserve that choice.

**Goal:** Establish one live-path validation primitive whose persisted stamp-to-hash shortcuts are restricted to visible, recoverable derivations.

**Architecture:** Extend store.compiled, the sole SQLite owner, with checksummed source rows and request-owned source staging. Every path is statted live; the filesystem-clock sample precedes all source stamps, and only old matching stamps may vouch for hashes. Decision paths keep their current readers and in-process statcache behavior.

**Tech Stack:** Python stdlib, optional sqlite3, existing statcache/proclock/paths, pytest and AST guards. No dependencies or frontend changes.

**Spec:** [Roadmap 03 design](../specs/2026-10-09-roadmap-03-content-addressed-compiled-cache-design.md), sections 4-6, 9, 14 and 03-S2. S1 is the prerequisite, with its code and fidelity findings resolved before S2 implementation.

## Global Constraints

- The store remains the authoritative markdown/JSON filesystem. Callers supply live paths; no cache inventory API exists.
- Source hashes are SHA-256 of exact bytes, without newline/BOM/encoding normalization.
- Persist only when `t0_fs - max(mtime_ns, ctime_ns) > PERSIST_WINDOW_NS`. Set `PERSIST_WINDOW_NS = 5_000_000_000`: twice FAT/exFAT's two-second granularity plus one second of margin, exceeding the three-second floor.
- A source hit requires mtime, ctime, size and inode to match, a valid checksum, a valid hash, and the age test at the pre-stat filesystem time.
- No writer records its own fresh source stamp. Artifacts keyed by actually read bytes may be stored immediately; atomic.py stays free of SQLite.
- A missing absent-ok input contributes `ABSENT = "absent"`, outside the 64-hex hash alphabet. Absence is statted on every request and never stored in sources.
- Once an input becomes unavailable, byte-fed compute cannot run. Raise InputUnavailable, discard all cache staging for that batch, and leave fallback behavior to the existing reader.
- Sources are never used for sync bases, integrity checks, or a write/delete/refusal decision. The registry flag is required at the primitive boundary.
- All S1 containment, immutable ownership, operation/recovery exclusion, bounded staging, root/pid/epoch fencing, worker-only SQL, fixed privacy-safe logging and optional-cache rules remain binding.
- Only placeholders and temporary stores are used. Frozen campaign home and snapshot remain byte-identical. Commit liberally at coherent checkpoints.

## Review Focus

1. A slow read ages a fresh stamp past the window; eligibility must still use the pre-stat time. Task 2 pins this with an advancing clock.
2. A server filesystem clock lags the machine clock; only sentinel time can permit persistence. Task 2 injects opposite clock values and a failed sentinel.
3. Permission failures and vanished listed paths resemble absence; only FileNotFoundError for a declared absent-ok role is ABSENT, and failures abort request staging. Task 3 pins all three states.
4. A valid-but-damaged source row can invent a hash for different bytes; path, all stamp fields and hash are checksum-bound and independently type-validated. Task 1 plants swapped, malformed and oversized rows.
5. A decision helper reaches the cache through a wrapper or alias; runtime flag checks and a planted AST call-chain case both reject the shortcut. Task 4 pins direct and resolvable wrapped uses and documents the guard's limits.

## File Ownership and Interfaces

- Extend `backend/src/grimoire/store/compiled.py`: schema, source SQL, filesystem clock, registry declarations, public validation interfaces and staging.
- Create `backend/tests/test_compiled_sources.py`: real temporary files and source-row protocol regressions; reuse existing cache fixtures/helpers rather than introducing an LLM fake.
- Extend `backend/tests/test_compiled_guard.py`: bounded sources SQL, path-supplied public reads and decision-site reachability.
- Modify `backend/src/grimoire/store/migrations.py` and the existing scene-backfill tests: age measurement at pre-stat time, without introducing compiled persistence into migrations.
- Update CONTRIBUTING.md's guard description and ROADMAP-CHECKLIST.md only as delivery is evidenced.

Public interfaces in store.compiled:

```python
ABSENT: str
PERSIST_WINDOW_NS: int
@dataclass(frozen=True)
class FilesystemClock:
    now_ns: int
    device: int  # positive st_dev identity of the clock sentinel

filesystem_clock: Callable[[Path], FilesystemClock | None]  # replaceable public test seam

@dataclass(frozen=True)
class Input:
    role: str
    path: Path

@dataclass(frozen=True)
class ValidatedInput:
    role: str
    path: Path
    content_hash: str  # exact-byte SHA-256, or ABSENT
    stamp: statcache.Stamp | None
    data: bytes | None  # present if hashed/read in this call; None on trusted hits/absence

class InputUnavailable(OSError): ...

# Add a defaulted field so S1's Kind constructors stay compatible:
Kind.absent_ok_roles: tuple[str, ...] = ()

Batch.validate(kind: Kind, inputs: Sequence[Input], *, with_bytes: bool = False)
    -> tuple[ValidatedInput, ...]
Batch.validate_one(kind: Kind, input: Input, *, with_bytes: bool = False)
    -> ValidatedInput
Batch.known_hashes(kind: Kind, paths: Sequence[Path]) -> dict[Path, str | None]
```

Validation preserves input order and roles, permits repeated paths with different roles, and hashes a repeated path only once per call. `with_bytes=True` reads present files even when a source row matches; the returned hash always describes those returned bytes. A cold hash retains its bytes for the caller, avoiding another read. A trusted source hit may return data=None; a caller needing bytes for an artifact miss requests with_bytes and recomputes its key. No byte-fed derivation wrapper or production consumer is wired in S2.

## Task 1: Source Rows and Bounded Selection

**Files:** compiled.py; test_compiled_sources.py; test_compiled_guard.py.

**Interfaces:** Private source row lookup takes supplied relative paths and returns only those rows. Source encode/decode binds `(relative_path, [mtime_ns, ctime_ns, size, ino], content_hash)` using S1's canonical JSON conventions. No public connection accessor is introduced.

- [x] Write failing tests for the sources schema, primary key path, checksum binding, valid-but-altered hashes, swapped paths, malformed hashes, invalid field types and excessively large fields. Assert every damaged row costs a miss and one fixed source warning per process, never a changed file-reader result or a corruption rename. Repeated damaged-row reads warn once; ordinary missing rows remain silent.
- [x] Run the named source tests to establish RED before implementation.
- [x] Advance SCHEMA to 2 and add sources on new database creation: `path TEXT PRIMARY KEY`, `mtime_ns INTEGER NOT NULL`, `ctime_ns INTEGER NOT NULL`, `size INTEGER NOT NULL`, `ino TEXT NOT NULL`, `content_hash TEXT NOT NULL`, `check TEXT NOT NULL`, `last_used INTEGER NOT NULL`. Old schema-1 databases remain untouched and unreachable through the new filename. Store inode as canonical decimal text because Windows may return a 128-bit inode; invalid or unrepresentable metadata simply cannot be persisted. Integers must be native ints within signed SQLite range, size nonnegative, inode nonnegative with at most 40 decimal digits. Do not let OverflowError escape optional persistence.
- [x] Implement private path-keyed source selection in bounded chunks of 128 supplied paths, with SQL length/type predicates before Python loads hostile fields. Each chunk is one query. A single-file call uses the same batch machinery. Build static placeholder SQL from fixed sizes, with its narrow planted guard exception; no data or identifier interpolation. Sources SQL never enumerates membership.
- [x] Implement source checks and native type/hash validation outside the connection lock. Add sources to S1's optional staged-row/byte budgets; artifact and source writes share one context-exit transaction. Over-budget warming skips rows.
- [x] Extend the guard to reject unbounded, quoted/JOIN and dynamic sources reads, while permitting the named bounded-placeholder helper and metadata operations. Public validation/known-hash reads must require supplied inputs/paths, with no default inventory form.
- [x] Run new source/guard tests and existing compiled/lifecycle tests; commit the schema/selection checkpoint.

## Task 2: Filesystem Clock and Trust Eligibility

**Files:** compiled.py; test_compiled_sources.py.

**Interfaces:** filesystem_clock(root) is called at most once per Batch that validates/queries sources; its returned time and filesystem identity are captured before any source stat. Keep the existing `_clock` monotonic timer seam separate.

- [x] Write failing tests asserting filesystem-clock call precedes input stat/read, one sample per batch, strict five-second boundary on both mtime and ctime, coarse two-second timestamps, slow reads, local/server skew, future timestamps and unavailable clock. Do not sleep or try to backdate POSIX ctime.
- [x] Write linked-sentinel/directory and injected Windows reparse tests proving external target bytes and metadata remain unchanged. Read-only or unavailable optional clock must still return the hash of readable source bytes. Add linked-source and parent-junction cases plus a deterministic differing-device/server-clock-skew case: readable inputs hash live, known_hashes returns None, and no source row lands.
- [x] Write a helper-level regression, not merely an injected public-clock result: matching sentinel/source devices on a mocked Windows remote volume, client time ten seconds ahead of source filesystem time. Invoke the production provider and require None, no utime call, no source hit/staging, and known_hashes=None. Pin final-path resolution for a mapped drive/root junction, successful local-volume classification, unknown/API-failure opt-out, and module-level import/Android guards. This proves matching devices do not authorize a client-timed remote sentinel.
- [x] Run to establish RED.
- [x] Implement a private `_clock_supported(path: Path) -> bool` provenance check before touching the device-prefixed `-clock` sentinel. On Windows, resolve the chosen root boundary to its final filesystem path after cache-component containment checks and obtain its containing volume root with GetVolumePathNameW, then call GetDriveTypeW on that volume root through a module-scope ctypes helper. Permit only DRIVE_FIXED, DRIVE_REMOVABLE or DRIVE_RAMDISK; UNC/mapped remote, unknown drives and API failures return False. An unsupported clock provider returns None and no sources shortcut is permitted. Linux/Android additionally classifies the final sentinel filesystem through the supported native statfs ABI and permits only proven local kernel filesystems before the null-times operation. Network/FUSE/overlay/unknown providers and unsupported ABIs opt out; CIFS can transmit client time even with null times. These conservative opt-outs keep readable files and content-addressed artifacts usable, with fewer persisted source hits.
- [x] Implement the supported clock by touching and statting a device-prefixed `-clock` sentinel in the already guarded compiled directory. Validate its parents and leaf with S1's lstat/reparse helpers before create/touch; never follow a linked clock leaf. The sentinel's resulting mtime_ns is the filesystem's now, and its st_dev is the identity that source stats must match. A missing/unsupported/nonpositive device identity forbids persisted shortcuts. Any optional sentinel failure returns None, warns once with a fixed failure kind and forbids source hits/recording for that batch. No local-clock fallback is allowed.
- [x] Apply eligibility at the captured pre-stat t0_fs, for both existing source hits and new rows. Match all four live stamp fields and prove the source belongs to the sentinel filesystem before using or staging a row. Capture st_dev and all Stamp fields in the same live stat; construct the existing statcache.Stamp shape from that result rather than taking two inconsistent stats. Paths through any linked/reparse source component below the chosen root are conservatively excluded from persistence, while their ordinary live byte reads continue. A mount with a different st_dev or an unprovable filesystem identity is excluded too. A fresh/mismatched/damaged row causes exact-byte hashing; it does not change the authoritative result. Hashing and source IO stay outside the SQLite lock.
- [x] Test a stamp that moves between stat and read: retain the pre-read stamp so the next validation rehashes. Do not pair post-read stamps with earlier bytes.
- [x] Run source/containment/lifecycle tests; commit the trust-point checkpoint.

## Task 3: Live Input Validation, Absence and No-Read Probes

**Files:** compiled.py; test_compiled_sources.py.

**Interfaces:** Input, ValidatedInput, InputUnavailable, validate, validate_one and known_hashes as above; Kind gains absent_ok_roles.

- [x] Write failing tests for exact CRLF/BOM/non-UTF-8 hashes, single/batch parity, duplicate paths under different roles, same bytes at different stems with explicit path-derived params, source hits after close/statcache reset, with_bytes forcing bytes, and changed version/BUILD still moving artifact keys.
- [x] Write absence tests: declared absent input gives ABSENT and no sources row, every call stats it, creating it moves the key, deleting it gives ABSENT again; missing required input, PermissionError, directory-as-file and a file vanishing after stat each raise InputUnavailable and erase all source/artifact staging. Catching that error inside the batch cannot revive staging. Caller exceptions remain untouched.
- [x] Write edit/rename/add/delete/rename-replace tests, and a POSIX same-size rewrite restoring mtime that still changes ctime. Run both in-process and after close/statcache reset; mark only actual POSIX ctime semantics platform-dependent.
- [x] Write known_hashes tests with input read_bytes/open forbidden: every path is statted; matching old valid rows return hashes; unknown, fresh, changed, absent or unreadable paths return None. No source bytes are read or hashed and no source row is staged. Cache disabled/unavailable also returns None.
- [x] Run to establish RED.
- [x] Implement validation on supplied paths, with a live stat before every use. Resolve relative cache path keys lexically under the pinned root, then apply Task 2's independent filesystem-identity and source-link eligibility checks. Outside-root, linked and differing-filesystem paths can be hashed for the caller but cannot use/stage persisted source rows; known_hashes returns None for them. Distinguish FileNotFoundError from other OSError states; statcache.stamp returning None alone cannot establish absence. Preserve user-selected root boundary semantics.
- [x] Unregistered kinds may validate readable present inputs live but cannot select or stage persisted rows; missing inputs fall back through InputUnavailable. Reject unregistered kinds before cache access. `allow_persisted_sources=False` never selects or stages sources, even if a matching row exists; validate still hashes readable bytes. A false flag can never be overridden by an API argument. make_key accepts ABSENT only for roles declared absent-ok and continues canonical ordered input hashing.
- [x] On unavailable input, permanently disable that batch's cache operations and clear both staging groups before raising InputUnavailable; no caller compute is executed by validation. Empty input requests are valid and do not create cache directories.
- [x] Run source/compiled/guard/lifecycle tests; commit the public primitive checkpoint.

## Task 4: Decision Guard and Migration Timing Fix

**Files:** test_compiled_guard.py; migrations.py; existing backfill test file identified with `rg`; CONTRIBUTING.md.

**Interfaces:** Guard starts from existing decision functions: entities.entity_hash, characters.dir_hash, characters.card_hash, pcs.dir_hash, pcs.version_hash, greetings.plotmap_hash and image_store.blob_intact, plus appearances.versions.actor_hash and sync._actor_incoming. These production functions are not changed to use compiled caching.

- [x] Write planted guard failures for direct compiled validation, module aliases and a resolvable helper wrapper reachable from a decision function. In particular, plant persisted-enabled calls in characters.card_hash and pcs.version_hash and require traversal through appearances.versions.actor_hash from sync._actor_incoming to catch both version-locked update/conflict paths. Reject a decision entry declaring allow_persisted_sources=True. Pin permitted recoverable calls and decision validation with an explicit false registry flag, if a test-only example uses that form. State limits for dynamically constructed call targets; do not claim a complete data-flow proof.
- [x] Run the planted cases to establish RED, then extend the guard's module import/name resolution and direct call traversal only as needed for those forms. An unresolved compiled source call reachable from a named decision site is refused rather than assumed safe. Keep today's in-process memo/hash formulas unchanged.
- [x] Write a migration regression where the scene's stamp is fresh when read begins, its head read advances migrations._clock beyond RACY_WINDOW_NS, and the per-scene entry must not be recorded. Also pin an already-aged stamp that can still be recorded.
- [x] Run those tests to establish RED. In migrations._backfill_campaign, take `started = _clock()` immediately before each scene stat and use that value in the existing two-timestamp age check. Preserve campaign locking, directory marks, identity repair and fixed existing comments; add why a slow read must not age the stamp into trust.
- [x] Update CONTRIBUTING.md's guard description. Run decision hashes, sync/integrity, scene identity/backfill and source/architecture tests; commit the guard and timing fix.

## Task 5: Slice Verification and Review Gates

**Files:** S2 plan; ROADMAP-CHECKLIST.md. Runtime changes only if fresh checks or reviews find a defect.

- [ ] Run focused S2 tests with branch coverage plus existing cache/build/lifecycle/guards. Keep source IO counts and SQL counts test assertions beside behavior; no real-store measurements.
- [ ] Run `make check PY=build/venv-check/Scripts/python.exe` and record actual output. If it stops at the already-established Windows mypy pairs, verify the exact Linux-platform counter against all 165 baseline findings and run the remaining gate targets explicitly. Do not increase baselines. Distinguish preexisting Windows failures and platform skips from new failures; fix every new regression.
- [ ] Run `make check-apk PY=build/venv-check/Scripts/python.exe` because runtime package source changes affect the embedded fingerprint. Assert packaged source stamp matches today's source/templates and all SQLite layers remain present. Do not claim on-device execution.
- [ ] Confirm no diff to frozen_campaign/home or snapshot.json; update only actually evidenced roadmap contracts. S3 collections, S4 materialized extension, S5 privacy/eviction and S6 consumers remain separate units.
- [ ] Run native `/codex:review` against the complete S2 diff and resolve findings, using the plugin's native review/start API with full snapshots if the local shell cannot inspect the branch. Then run `/codex:adversarial-review` on the complete diff plus original spec specifically for S2 fidelity. Remove review transport files; commit fixes and gate evidence liberally.

## Self-review and Human Handoff

Tasks 1-3 establish sources, validation, absent roles and the no-read form; Task 4 protects decision sites and fixes the existing timing bug. Task 5 verifies the slice. Each Review Focus input has an owning regression test. The C4 response's materialized rows remain S4's explicit schema/API extension, as the spec's slice ownership requires; S2 does not invent empty rows as evidence of completed S4. The full-spec completion checkbox stays open.

The first Codex S2 plan gate (`01a12c73-e836-7170-a5f6-c54b92f67a1e`) found two material omissions: version-locked actor hashes were outside the decision guard, and lexical source containment did not prove the sentinel shared its filesystem clock. The plan now guards those hashes and their sync wrapper, carries sentinel device identity, and conservatively opts linked/differing/unknown filesystem inputs out of persistence. Deterministic regressions pin both corrections. The revised plan must pass its Codex gate before implementation. Its second gate (`01a12c7c-1413-7a51-b4c0-e847b005a293`) found that Windows touch uses client time even on a remote filesystem. The provider now refuses remote/unknown Windows volumes before touching and tests that production path directly. [CPython's Windows utime implementation](https://github.com/python/cpython/blob/v3.12.0/Modules/posixmodule.c#L5885-L5893) obtains client time before SetFileTime; [GetDriveTypeW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdrivetypew) distinguishes remote volumes. Those facts motivate the conservative Windows opt-out; the clock equality proof is not inferred from matching st_dev alone.

This plan is ready for the revised Codex plan gate and human review. The previously chosen execution method is **Native**. Implementation waits for those gates; neither changes the existing authorization to work on Spec 03 and commit regularly.

Codex plan gate passed on 2026-10-11 (`01a12c82-d8fb-7953-91dc-675efa2da88d`): all seven complete snapshots and pinned caller context reviewed, no remaining material S2 gap. All three prior findings are resolved. This is plan approval only; human plan review remains pending, with Native execution preserved.

Execution authorization (2026-10-11): the human explicitly instructed independent brutal-review loops and completion of all slices. Proceed with the preserved Native implementation method through S2-S6 without further per-plan human handoff questions; independent plan/code/spec reviews remain mandatory, with findings fixed and re-reviewed until non-blocking. This instruction overrides the earlier pending S2 human handoff.

Task 1 evidence: 12 source-schema/selection cases RED -> GREEN. Combined source/cache/lifecycle/architecture tests passed 168 with five platform skips; new-file ruff is clean. Fixed the independent S1 nested-query guard finding with a failing planted case; four source-query guard cases and two constant-shadow cases also failed before fixes. Final guard suite passed 38 tests. Sources and artifacts share the bounded single transaction; 128-bit inode roundtrip is pinned.

Task 2 trust-point checkpoint: the production clock refuses remote/unknown Windows volumes and API failures before touch; source age checks cover both timestamps strictly, same-stat device identity, linked sources and source-parent exclusions. The clock tests failed with missing helpers before implementation; focused source/guard/import checks then passed (75), and combined lifecycle checks passed (92, four real POSIX skips). Batch sequencing, slow reads and readable-file fallback are pinned with Task 3's public validation API, where those operations first exist; this is a test-order adjustment, not a waived requirement.

Task 3 primitive checkpoint: 19 new cases RED before implementation; combined source/cache/guard/lifecycle/import run 194 passed, five platform skips, ruff clean. Validation hashes exact bytes, keeps cold bytes and pre-read stamps, samples clock once before stats, rechecks paths live, and honors registry policy. InputUnavailable permanently clears both staging groups even when caught by a caller. Probes never read/hash source bytes. A focused mypy check found the Task 1 NULL-padding list needed a str-or-None annotation; corrected without baseline growth. Further file-edit and no-read exclusions are pinned before final slice verification.

Task 3 movement/exclusion checks:63 passed, one Windows ctime skip. Task 4 added17 initially failing decision/public-path/migration cases, followed by two failing unresolved-target/policy cases. The guard traverses normal imported helper and batch aliases from all nine named decision sites; explicit false Kind policy is accepted, unresolved compiled getattr is refused. Migration age now uses pre-stat time, preserving existing locks and comments. The guard remains a syntax/call-graph check, not arbitrary dynamic Python data flow.

Independent S2 review at a3484c56e found import/argument shadowing could preserve a stale false-policy name in the decision guard. Import, argument and loop-binding regressions all failed before correction. False named policies now require an unshadowed single explicit assignment. A production-provider remote-volume regression also pins live byte fallback and no source-row selection; an empty eligible set now skips the private selector entirely.
Verification checkpoint:305 passed/six skips with89% compiled branch coverage, and305 passed/six skips underPydantic1. Ruff remains exactly1146 findings; make check stops at the established Windows atomic/proclock mypy pairs. Linux-platform mypy matches all165 baseline findings exactly. Fullbackend gate runs, APK and native review are in progress.

Native code gate01a12d17-e00a-7a02-aeab-8e51c2c616a6 found five material issues at a3484c56e: containment failure retaining eligibility, lexical parent traversal hiding a link, unbounded numeric SQL fields, annotated false-policy rebinding, and public sources readers bypassing required-path interfaces. Eight new tests failed before runtime/interface corrections; the annotated false-policy case was already rejected by the earlier unshadowed-policy fix and is pinned by an additional passing regression. Independent S2 round2 at29e7a2026 found later alias rebinding hiding earlier calls (oneRED). Resolved source targets now accumulate ordinary bindings conservatively, including annotated assignments; this remains a syntax guard rather than arbitrary Python flow analysis. Re-review is pending.

Independent S2 round3 at b1aff325 found breadth-first AST ordering left a nested alias dependency unresolved. The exact nested-branch/delegated regression failed before the fix. Alias targets now propagate to a fixed point, bounded by the number of binding nodes to prevent cycles hanging the guard. All78 guard cases pass; ruff clean. Further pinned review follows.

Native follow-up01a12d27-76d5-7263-a478-febc851ee311 confirmed the five original fixes and found conditional-import alias ordering (already corrected by fixed-point resolution, now pinned) and mixed source/artifact public readers suppressing the required key (RED before correction). Independent S2 round4 found constructor aliases could falsely prove inline policy; constructor-origin/parameter regressions failed before correction. Proofs now use only singleton possible targets, with arguments/definitions/exception/pattern bindings represented as unknown alternatives; unshadowed literal policies are derived from that same proof map. Extracted or forwarded primitive references and named primitives on unknown receivers are conservatively refused.

Fidelity gate01a12d28-6114-7481-8c97-adbc2a7432c6 found native POSIX touch still supplies client time on CIFS. Verified against [Linux notify_change](https://github.com/torvalds/linux/blob/v6.12/fs/attr.c#L393-L403) and [CIFS timestamp transmission](https://github.com/torvalds/linux/blob/v6.12/fs/smb/client/inode.c#L1533-L1536). Eleven production-provider tests failed before correction. Linux/Android now require statfs proof of a local kernel filesystem before touching; ext, btrfs, xfs, tmpfs, ramfs, f2fs, FAT and exFAT are allowed, while network/FUSE/overlay/unknown mounts and unsupported ABI/API hosts opt out. Existing leaves are classified directly, including mounted leaves; only a missing sentinel uses its guarded parent. An aligned oversized native-word buffer reads only the [generic ABI f_type](https://github.com/torvalds/linux/blob/v6.12/include/uapi/asm-generic/statfs.h); type IDs come from [Linux magic.h](https://github.com/torvalds/linux/blob/master/include/uapi/linux/magic.h). Windows's existing final-volume proof is unchanged. Readable sources still hash live and artifacts remain keyed by exact bytes. Post-fix source/guard/import run:169 passed, two platform skips; further native/fidelity and independent review required.


### Binding proof correction after independent round 5 and native follow-up 2

The independent reviewer runtime-confirmed global function/class rebinding and local loop/walrus/unpacking rebinding could falsely retain compiled.Kind constructor provenance. Native thread 01a12d44-d0c8-7762-bc1c-390bc6531b8c additionally found an unresolved-expression alias lost its unknown origin, and cyclic dotted aliases expanded exponentially. Seven planted regressions failed before the fix; the small cyclic case produced 10,945 targets. Alias proof now uses a finite binding graph, includes definition namespace bindings without leaking their locals, and treats unmodelled stores/expressions as unknown alternatives. Cycles and proof budgets fail conservatively. Focused guard/source/import verification: 181 passed, 2 platform skips; configured ruff passed.

Fidelity thread 01a12d46-639d-7042-bdeb-8cfe688f7eab approved S2 runtime at c5a77252b, including the corrected Linux clock provenance. Py1 focused checkpoint: 267 passed, 7 skips. APK checkpoint: assembleDebug successful (49 tasks). Full intermediate native checkpoint: 18,072 passed, 39 skips, 17 failures and 10 Windows environment-size errors, coverage 93.61%; 14 failures are independently reproduced baseline behavior, one source test was collected before its reviewed fix, and two route timing cases are being replayed serially. Full Py1 checkpoint has 18,065 passed, 45 skips, 18 failures and the same 10 errors; baseline/timing classification remains explicit rather than calling the gate green. Later whole-spec verification runs against the final tip.


### Cyclic reachability correction after independent round6/native follow-up3

Independent round6 reproduced a hash-seed-dependent helper omission caused by memoizing visiting-dependent truncated targets. Native thread01a12d54-c3c0-7df0-a475-29264850930e confirmed the same defect and a65-edge chain silently exhausting the former depth budget. Root reproduced the helper regression RED in an explicit traversal order and the old depth result as unknown_binding.a64. The graph now propagates bounded simple paths to a fixed point; back-edges add uncertainty while every cycle member retains concrete reachable helpers. There is no depth cutoff hiding a source primitive, and target-budget exhaustion explicitly fails closed. Guard/source/import checkpoint:183 passed,2 skips; guard after long-chain regression:96 passed; configured ruff clean. Fresh independent/native review required before S2 completion.


### Namespace effects and unconditional budget rejection

S2 fidelity gate01a12d55 found helper global writes retained caller constructor/False-kind proofs. Both constructor and kind setters failed planted tests; ordinary constructor attribute mutation was additionally pinned RED. Namespace mutations now revoke inherited proofs, including aliases to compiled objects. Independent round7 found the128-target cutoff substituted a normal source primitive that could accept a False kind. Native follow-up4 found projecting an attribute from that marker erased its source identity. Both cutoff tests failed RED. Budget exhaustion now has an unconditional unresolved-source state that remains rejecting through attribute projections. Focused guard/source/import:190 passed,2 skips; ruff clean. Review loops remain pending at this correction.

Transient route replay: native routing/follow-up selection13 passed; Py1 voice-drift passed on both head/base. Py1 dossier deadline test initially failed head, passed base, then passed alone at head, confirming timing sensitivity rather than claiming the intermediate full run green. Existing Windows baseline classification remains unchanged. Linux focused compiled mypy clean; other gates443 template checks,727 ESLint baseline findings and4600 frontend tests across212 files passed.


### Shared constructor namespace summary

Native follow-up5 (01a12d64-96db-7ce0-9d85-16d591645c14) found helper-local alias writes were invisible to caller proof revocation. Root reproduced both the same-module helper alias and shared-module-object variant RED. Mutation discovery now resolves helper-local aliases separately from caller globals and summarizes compiled module/Kind mutations across modules before approving any caller constructor/False policy. Function locals still do not seed the global namespace. Focused192 passed,2 skips; ruff clean. Independent addendum and native follow-up remain pending.

Final S2 runtime checkpoint before further additive slices:350 passed,7 platform skips; owner branch coverage89% (830statements). Full Linux mypy counter exactly165 baseline findings with no additions/removals; ruff exactly1146 baseline findings. Current APK source/templates digest matches, and nested stdlib sqlite3/_sqlite3 plus native SQLite libraries are present. Frozen fixtures have no diff.


### Lexical mutation scopes and exported receivers

Native followup6 (01a12d6f-3e27-7250-85d9-44cbb0469821) found two independent helper locals could be conflated and falsely revoke a valid False policy. Both plain/global-shadow controls failed RED. Mutation analysis now walks owned lexical scopes, respects local shadowing and closures, and keeps helpers separate. Fidelity followup (full thread recorded in build/s25-followup-fidelity.log) found an exported namespace receiver aliases.cache.Kind was not resolved; module/class/dictionary variants failed RED. Receiver resolution follows the supplied modules' exports with conservative cycle/budget rejection. Opaque constructor receivers (defaulted parameters, namespace factories and nested containers) also failed planted tests and now reject uncertain constructor effects; a known independent SimpleNamespace remains accepted. Combined guard/source/import200 passed,2 skips; configured ruff clean. Native/fidelity follow-up and narrow independent signoff remain required.


### Re-exported calls and class/comprehension scopes

Final fidelity thread01a12d8a-9723-75e3-adbf-95ded27c7546 found ordinary helper re-exports stopped decision traversal; both module-qualified and directly imported forms failed RED. Native followup7 thread01a12d8a-5ea2-7a91-94f5-dbebbb99ba5f found class-body global fallback, nested-class global writes, and comprehension-local pollution. Six scope cases failed RED; a comprehension constructor-shadow control already rejected. Calls and forwarded references now resolve exports before classification/traversal, class scopes retain enclosing alternatives and contribute explicit global writes, and comprehension bindings stay in their own scopes. A256-node export work budget fails closed rather than exponentially exploring alias paths. Focused guard/source/import210 passed,2 platform skips; configured ruff clean. Independent/native/fidelity follow-up pending at this checkpoint. No runtime-owner changes.


Independent followup at bf5bd3973 reproduced an escaping comprehension walrus assignment: its target belongs to the enclosing scope and was hidden with iteration bindings. Actual isolated cache replay supplied a persisted-enabled source hash with zero source reads. Root reproduced the guard RED, with a passing lambda-local control. Enclosing traversal now includes comprehension assignment expressions while lambda assignments retain their own scope. Focused212 passed,2 platform skips; ruff clean. Exact-pin independent and mandatory gate followups remain pending.


Native followup8 (01a12d9f-ecca-76f3-81c6-2adc721a1fda) and fidelity followup8 found nested nonlocal writes retained enclosing constructor/kind proofs. Both planted cases failed RED, while independent nearer-local and nearer-parameter controls passed. Mutation propagation now resolves writes to the nearest enclosing function binding; class locals do not become closure bindings for methods. Native focused215 passed,2 skips plus the final parameter control passed separately; Pydantic1 full focused216 passed,2 platform skips. Configured ruff clean. Followup review required before S2 completion.


Independent review at exact605885 is non-blocking for unsafe nonlocal writes, with a should-fix rejection of valid named False policies when a nearer helper owns a same-named K parameter/definition. Parameter control reproduced RED. Literal policy binding discovery now uses the policy owner's scope; SQL constant checks retain whole-tree shadow discovery. Guard129 passed, then both safe controls passed after adding the definition variant; ruff clean. Exact-pin artifact build/reviews/s2-binding-nonlocal-fixed.md. Mandatory followup9 remains pending against605885.


Native followup9 (01a12da9-20b5-7581-85a6-17716e89ec6d) found enclosing definition defaults/first comprehension iterable were analyzed under new bindings, class-local policies leaked into method closures, and nonlocal helper forwarding lost call traversal. Fidelity9 found the same default-expression bypass. Three walrus-default cases failed RED; remaining native cases3 failed,1 already passed after default fix. Scope-entry expressions now belong to the enclosing evaluation scope, method bindings/policies bypass class locals, and resolvable ordinary helper references conservatively enter traversal even when forwarded without a direct call. Full guard137 passed; ruff clean. Current source has additive S3 collections, whose separate checkpoint is recorded in the S3 plan. All S2 signoff gates remain required.

Whole-spec implementation closure: this slice's runtime and contract tests are delivered in the shared all03 branch. Fresh cache/source/derive/collection/history/vector/guard/lifecycle/consumer/import/docs matrix917native/911Py1 passed with6/12platformskips; known unchanged Windows CPU-clock test was separately reproduced atbase and excluded. Final mandatory whole-branch native and original-spec followups remain open; their final verdicts and accurate partial full-gate record will be attached before marking review/final-gate columns.
