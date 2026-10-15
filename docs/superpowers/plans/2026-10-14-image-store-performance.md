# Image store performance implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement task by task.

**Goal:** Resolve the redundant reads, writes and sweeps in #477.
**Architecture:** Carry request snapshots through existing readers. Share local
resolution and library-upload validation; memoize only compact scope membership
with the existing file-stamp cache. Keep mutations on their current locks.
**Tech Stack:** Python, pytest, existing store and statcache modules.
**Spec:** docs/superpowers/specs/2026-10-14-image-store-performance-design.md

## Global constraints

No dependency or persisted format changes. Preserve API output, legacy precedence,
fail-closed guards, canonical member indices, root isolation and lock order.
Use synthetic stores and the existing placeholder names only.

## Review focus

- Malformed legacy subject values are reviewed but yield no subject chips.
- Legacy and cross-world URLs resolve with the same serving identity as their tokens.
- Equal captions still clear conflicts and legacy keys, but absent objects cannot confirm saves.
- External object edits and review-only scopes must invalidate scope candidates.
- Failure during scope copy strips partial tags; earlier copy failure does not sweep.

## Task 1: Request snapshots and shared resolution

Files: store/assets.py, image_subjects.py, greeting_images.py, image_collections.py,
overlay.py, image_usage.py; routes/characters.py, greetings.py, world_images.py;
backend/tests/test_image_performance.py and test_image_subjects_store.py.

Interfaces: assets.file_in/image_file return (path, optional image_id), and existing
path-only readers wrap them, retaining promotion recovery and legacy fallback.
read_answered(root, gid, known_cids=None) returns (subjects, reviewed).
_subjects accepts an optional catalog for appearances. Collection Member carries
path, version, optional image_id and optional legacy name; resolve_member reads
one manifest and returns that member or None. available accepts a supplied
manifest. local_image(root, url) returns (path, optional image_id) or None, used by
URL rows and local_path. Existing local_target and member_path reuse resolution.

- [x] Write/read the count regressions and observe their duplicate-read failures.
- [x] Implement the interfaces above and reuse read_descriptions_listed in detail.
- [x] Load world manifests once for usage, only with an existing manifest directory.
- [x] Update the appearances race assertion to the original catalog snapshot.
- [x] Run image performance and existing subjects/collections/overlay/usage suites.
- [x] Include the verified read-path changes in the coherent implementation commit.

## Task 2: Library and caption writes

Files: store/image_collections.py, world_images.py, image_descriptions.py;
backend/tests/test_image_performance.py and existing descriptions/collections tests.

Interfaces: guard_put(wid, name, data) computes format-1 state/reference once and
uses the existing immutable-member and reserved-name rules. Standalone guards
still validate themselves. Caption update callbacks may report confirmed equality
without a write, separately from update's False for a missing object.

- [x] Observe legacy-state double checking and equal-caption write regressions fail.
- [x] Extract shared immutable-member/hash checks; route put_image through guard_put.
- [x] Compare caption equality in the fresh update callback; only confirmed equality
      or a successful write permits legacy cleanup. Existing preconditions run first.
- [x] Add equal-text-with-conflicts, empty-text and missing-object cleanup coverage.
- [x] Run collection, caption and guarded-description suites.

## Task 3: Scope candidates and fork rollback

Files: store/image_scopes.py, fork.py; backend/tests/test_image_performance.py,
test_image_scopes.py and existing fork tests.

Interfaces: private per-sidecar scope projection via memo_stamped, separate pool
bounded by statcache.MAX_ENTRIES. A stamp is captured before read_fresh; the key
includes the root-resolved sidecar path. _sweep takes the scope it selects and
still tests edits against fresh objects before update. _discard receives a flag
set before entering copy_campaign.

- [x] Observe the warmed unrelated-scope and early-fork-cleanup failures.
- [x] Index compact association/review scopes per sidecar and select candidates.
- [x] Mark scope-copy entry before calling it; condition rollback stripping on it.
- [x] Exercise external same-size replacement, review-only scopes, store changes,
      recent/unvouched stamps and partial scope-copy failure.
- [x] Run image and fork tests.

## Task 4: Gate and handoff

- [x] Run every make check target with the worktree interpreter; record failures below.
- [x] Resolve implementation review findings; document unrelated base failures.
- [x] Independent Codex code review; resolve findings.
- [x] Independent adversarial diff-versus-spec review; resolve gaps.
- [x] Prepare the final validation record for a draft PR fixing #477.

Handoff: commit this record, push the issue branch and open the draft PR.

## Evidence ledger

Spec review: Ready, independent Codex reviewer, 2026-10-14.
Baseline: the original 16 performance cases fail for expected excess calls/writes.
Focused final suite: 27 regressions, including cache safety and disappearing-member
error mapping. A reviewer-found 500/404 race was reproduced, fixed and rechecked.
Reader tests: 168 passed. Writer tests: 129 passed. Asset/lifecycle: 188 passed,
1 platform skip. Collection/docs/regression recheck: 112 passed.
Final Ruff1136/mypy161/eslint703 findings remain at baseline. Templates: 551 passed.
Frontend: 5240 tests passed. Related image/gallery/guard suite: 3097 passed,
3 skipped. The final 27 performance/safety regressions pass.

The full local gate is not green:

- Backend coverage run: 21963 passed, 42 skipped, 9 failed, 10 setup errors.
  One failure records recovery of a stalled adapter worker. Coverage reported
  17.57%, below the unchanged 93% floor; the cause of that low report is not
  established. It is not a passing coverage result.
- Pydantic 1 full run: 21959 passed, 48 skipped, 7 failed, 10 setup errors.
  Its dependency check and Pydantic 1.10 assertion passed. The full suite used
  the prepared compatibility venv and a fresh worktree-local --basetemp.
- Both runs reserved the two crash-recovery subprocess cases for serial runs
  because the loaded host exceeded their 20-second startup budget. Those two
  cases and the interrupted adapter case passed serially in the main environment
  (3 passed); both reserved cases passed in the compatibility environment (2 passed).

Representative SQLite evaluation cleanup failures (WinError 32) and the rerank
250ms deadline test also fail against an isolated package and templates from
unchanged base 92a55a50. The exact restored-ledger assertion also reproduces on
that base (1 failed, 9 passed in repeated original tests). Independent review
confirmed the overview and stamp paths are unchanged and the image scope cache
uses a separate pool. These adjacent problems are documented rather than added
to #477. No failing assertion, timeout or coverage floor was weakened.

The isolated --basetemp avoids pytest deleting old shared temporary directories;
a read-only stack of the serial run showed that cleanup after test execution.
Repository worker defaults remain unchanged. Independent code review remains Ready.
Ruling: execute inline in the existing isolated issue branch with independent
review gates. Keep one coherent implementation commit with the consolidated
regression suite; intermediate commits must not carry later tasks' failing tests.
No dependency or persistent-format change is introduced.

Final adversarial diff-versus-spec review: Ready. Scope membership memoization
still inventories/stats every object; the separate 4096-entry pool can thrash on
larger sequential scans. Cold, changed, recent and unvouched entries reparse.
