# Windows dependency integration: independent brutal review

Date: 2026-10-11

Frozen commit: `6eb1370f0fc364357f1ee54ec899960de76f9671`.
Review base: `86964ac22`.

**Verdict: non-blocking within the requested dependency scope.** No grounded blocking or should-fix finding survived the attack pass. Native append serialization, bounded record opens, Windows component-limit detection and the reviewed fixture corrections passed the checks below. This is not a final whole-S1 review or a full-gate certification.

Applied the brutal-review skill and repository conventions from CLAUDE.md / CONTRIBUTING.md. Reviewed frozen committed source exported to a Temp snapshot, using the shared main-checkout Python with the snapshot's `backend/src` first on PYTHONPATH. Tests and independent probes used synthetic Temp stores. No product/test edits, commits, private-store reads or live application runs.

## Scope and attacks

Reviewed the dependency changes in `atomic.py`, `proclock.py`, `pending_reviews.py`, `config.py`, `frontmatter.py`, `scenes/read.py`, their relevant callers, the store guarantee changes, the mypy baseline reduction, and every in-scope changed test hunk.

**Append identity and exclusion.** Windows append holds the machine-local proclock lock across open, the entire write loop and descriptor close. Its key uses the parent directory's filesystem identity plus the normalized filename, keeping ordinary parent/filename case aliases on the same lock. POSIX retains its existing path. The tested process regression starts two independent interpreters together and requires exactly all 1,000 distinct rows, including the filename-case variant; it does not merely count successful calls. The thread regression also remains present.

**Failure, release and timeout.** Acquisition errors propagate without an unlocked fallback. Open/write failures unwind through the release owner; proclock release closes the descriptor even if unlock raises. A timeout refuses publication. Existing proclock tests exercise process exclusion, deadline behavior and dead-holder recovery. No campaign lock is introduced into the atomic primitive, avoiding a dependency on the higher-level writers that already use atomic publication. Traced the append callers in usage/logs and their existing failure boundaries.

**Bounded opens.** `open_text` retries only Windows permission/share failures while opening: four delays totaling 50ms, then a final attempt. The limit bounds retry delay rather than promising a hard deadline for an operating-system call. It does not retry an already-open stream, partial reads, decoding failures, missing files, or POSIX permission failures. UTF-8/error-policy and universal-newline behavior remain unchanged. Config, strict record/header and scene reader migrations use this boundary. Read-only target refusal and permanent publication failure remain tested despite the newly retried ambiguous Windows access-denied error.

**Volume limit.** `GetVolumePathNameW` locates the target volume before `GetVolumeInformationW` reports the component limit. The code counts UTF-16 units rather than Python code points, leaves unavailable metadata inconclusive, and probes without creating/opening a sidecar. Tests cover a fitting and oversized non-BMP filename, ordinary long paths, metadata-query failure, non-255 reported limits, and preserved POSIX behavior.

**Fixture integrity.** The portability changes retain or strengthen the original invariants: unsafe trailing-dot names are really created and enumerated using extended Windows paths; image-case collisions retain both inventoried spellings without overwriting the first fixture; archive/source-hash assertions compare exact bytes; unchanged-file timestamps compare the filesystem's actual stored timestamp; the slow-provider budget test still asserts partial success, cancellation and a budget-exhausted dossier, while allowing filesystem setup time. Invalid image API rejection and hidden/unservable assertions remain active even when Windows cannot create the stranded invalid filename. The timeout harness continues to require the hung test name and absence of a passing result. Reader classification in the lock guard is restricted to the two actual read helpers, with alias/rebinding assertions; unknown atomic APIs remain writes.

## Independent native probes

A disposable Temp probe held the actual append lock and attempted an append through uppercase parent and filename aliases. Both spellings resolved to the same lock path. With the wait shortened to 80ms for the probe, contention raised TimeoutError after approximately 81ms and preserved the existing bytes. Releasing the lock allowed the next append to succeed. This uses real native lock contention, not an acquire stub.

The same probe queried the native volume component limit, obtaining **255 UTF-16 units**. Creating a 255-ASCII-unit component succeeded; creating a 256-unit component raised OSError. The repository's native non-BMP and long-path tests also passed.

The scratch probe initially assumed LF-only bytes after a CRT append. That assertion was corrected to compare decoded complete lines, preserving the actual pre-existing Windows text-mode newline behavior; no product defect is claimed from the probe's incorrect newline assumption.

## Verification

Configured frozen mechanical passes:

- Ruff: **1,146 findings, all at baseline**.
- Mypy: **161 findings, all at baseline**.

Frozen primary focused run:

```text
test_atomic.py
 test_pending_reviews_store.py
 test_proclock.py
 test_lock_domain_guard.py
 test_first_run.py
 test_image_migration_plan.py
 test_image_store.py
 test_image_gc.py
 test_changes_store.py
 test_phase_profile.py
 test_profile_scripts.py
 test_unpriced_in_use.py
520 passed, 9 skipped, 1 existing FastAPI/Starlette warning (148.48s)
```

Additional changed-node run covered invalid campaign-image names, byte-preserving inference-retirement archival, the hung-test harness, and partial-success absorb budget exhaustion: **8 passed**, the same existing warning (27.68s).

Total reviewer-run evidence: **528 passed, 9 skipped**, plus the independent native lock/volume probes. Skips are retained as skips, not counted as verification. No assertion was relaxed to obtain these results, and no repository production/test file was changed during review.

## Not reviewed

Explicitly excluded `world_images.py`, `test_group_membership_lifecycle.py`, and S1 implementation/validation documentation from substantive review. Test execution may transitively use these modules but is not their independent audit. No review of subsequent commits, live uncommitted changes, whole-S1 UI/integration, the unrelated excluded source-branch commit, or every unrelated full-gate failure. No full `make check`, Linux/Android physical execution, alternate filesystem/volume mount, cross-device synchronization or unsupported linked-record guarantee is claimed. Cross-device and non-cooperating writers remain outside the documented append contract.

The optional sibling `python-code-doctor/scripts/analyze_diff.py` pass was not run; configured lint/type checks did run and passed. A final whole-S1 independent adversarial gate remains required.
