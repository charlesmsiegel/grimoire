# Image Store Cleanup Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Complete the independent cleanup in #480 without taking over #477-479.

**Architecture:** Pure projections in image_refs reuse already-scanned refs;
image_descriptions owns inherited-caption publication. Existing surface policy,
guards and filesystem reads stay with their current callers.

**Tech Stack:** Python, pytest, existing image sidecar locks; no new dependencies.

**Spec:** docs/superpowers/specs/2026-10-14-image-store-cleanup-design.md

## Global Constraints

- Hold the neighboring-work items listed in the spec.
- Preserve Android compatibility, acyclic module-scope imports and existing
  placeholder names; isolate all test writes under tmp_path / GRIMOIRE_HOME.
- No new directory, object or blob reads in row projections.
- A partial PR uses Refs #480, never Fixes #480.

## Review Focus

- A World lookup failure is tolerated, but a campaign.md failure still raises.
- A bulk subject save cannot publish while another thread holds its sidecar.
- Image-less refs never contribute ids; missing-object refs still do.
- Library policy/order and record policy/order remain separate.
- Inherited-caption guards and unconfirmed campaign fallback survive extraction.

### Task 1: Repair the two missing contracts

**Files:** campaign_images.py, image_subjects.py under backend/src/grimoire/store;
create backend/tests/test_image_cleanup.py for isolated regressions.
**Interfaces:** Existing world_dir(cid) -> Path | None and
write_subjects(root, gid, subjects) -> None keep their signatures.

- [x] Add tests injecting OSError at world_images.images_dir (world_dir returns
  None) and campaigns.read_campaign (world_dir propagates OSError).
- [x] Add a real cross-thread sidecar contention test: prepopulate subjects,
  hold assets.sidecar_lock on another thread, shorten locks.LOCK_TIMEOUT,
  require bulk writes to raise StoreBusy and leave the old bytes unchanged.
  Check both valid and invalid requested keys; validation belongs in the hold.
- [x] Run the new tests and observe the two missing contracts fail.
- [x] Catch OSError only around the World directory lookup. Wrap the whole bulk
  subject validation/publication in its existing subjects-sidecar lock.
- [x] Rerun regressions; verify unlocked retry keeps full-replacement semantics.

### Task 2: Reuse identity projections and caption publication

**Files:** image_refs.py, image_library.py, image_descriptions.py, overlay.py,
campaign_images.py; tests remain in test_image_cleanup.py.
**Interfaces:** Add image_refs.image_ids(refs: dict[str, Ref]) -> dict[str, str]
and name_rows(names: Iterable[str], refs: dict[str, Ref]) -> list[dict].
Add image_descriptions.set_inherited(wdir: Path | None, cdir: Path, name: str,
text: str, names: set[str] | None = None, **guards) -> bool; False means no
resolving inherited object, leaving caller-specific legacy handling untouched.

- [x] Test projections with image-less refs, absent refs and unresolved image
  ids, preserving supplied name order. Run before adding the helpers (red).
- [x] Characterize record/library backlogs using one missing-object placement,
  an image-less focus override and legacy files including a library-reserved
  name. Assert library sorting and each surface's existing policy.
- [x] Implement the pure projections; use name_rows in both backlog builders
  and image_ids in _placed_ids. Keep scanning and sidecar-presence logic intact.
- [x] Extract the inherited-object check and set_in(also_clear=cdir,
  fallback_dir=cdir, **guards) into set_inherited. Both existing callers pass
  their original names and guards and preserve their legacy branches.
- [x] Run test_image_cleanup, test_image_descriptions_overlay,
  test_image_descriptions_store, test_campaign_images_store and
  test_record_art_description_guards. Expected: all pass, with unchanged
  fallback and stale-save behavior.

### Task 3: Independent docs/import cleanup and release checks

**Files:** greeting_images.py docstring; independent test modules for campaign
images, descriptions, image refs/hash/sanitize/store, post images and world
image routes. Exclude collection/migration/GC/maintenance tests.
**Interfaces:** No behavior or signature changes.

- [x] Clarify that a same-world format-1 member uses its library placement for
  object subjects; preserve the complete sidecar-case roster.
- [x] Move function-local imports to module scope, reusing existing imports
  and aliases; retain explanatory comments and fixture bytes.
- [ ] Run focused image tests and make check with the main checkout's Python
  interpreter. If lint findings shrink, run make baseline and commit reductions.
- [x] Run Codex review of the diff, then final adversarial review of diff plus
  spec; resolve findings and rerun checks affected by any changes.
- [ ] Commit the scoped result and create a draft PR describing held items and
  actual verification. Do not close #480 or comment on its issue thread.

## Verification record

- The new regressions were red before implementation (4 failed, 2 passed).
- Focused desktop image verification: 291 passed; the remaining changed test
  modules: 281 passed. A narrowed diagnostic over image surfaces, usage,
  group-play and inference-equivalence tests: 166 passed.
- All four required Codex review gates approved the scoped result.
- Ruff, mypy and ESLint passed their existing baselines; make baseline left
  the baseline files unchanged. Templates: 551 checks passed. Frontend:
  typecheck passed; 241 files / 5,240 tests passed under coverage.
- Full make check remains incomplete: backend failure/error markers preceded
  a Windows asyncio socket-pair stall near 97%. The run was interrupted without
  a final report; neither backend coverage nor the full backend gate is green.
  The interrupted full Android-dependency run is also incomplete. Keep the PR
  in draft and obtain the full-suite result from Linux CI.
